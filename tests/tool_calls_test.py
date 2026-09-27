#!/usr/bin/env python3
"""Crash/turn cleanup of missing tool results; no engine services or quota."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("tool-calls-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, main, runner, snapshots, tool_calls
from puppy.drivers import get_driver
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


def session(name):
    return db.create_session(name, "claude", str(ROOT), "", "", "", "default")


def call(sid, ident):
    return db.add_event(sid, "tool_use", {
        "tool_use_id": ident, "tool": "Read", "input": {"file_path": "/demo/file"}})


def recovered(sid):
    return [ev for ev in db.get_events(sid, limit=1000)
            if ev["kind"] == "tool_result" and ev["data"].get("interrupted")]


def cold_start_checks():
    """A writer dies without cleanup; fresh full/headless processes recover WAL."""
    seed = '''
import os
from puppy import config, db
config.ensure_dirs(); config.load()
sid = db.create_session("Interrupted fixture", "claude", "/demo", "", "", "", "default")
db.touch_session(sid, status="running")
db.add_event(sid, "tool_use", {"tool": "Read", "tool_use_id": "lost", "input": {}})
os._exit(23)
'''
    for name, boot in (("full", "from puppy.main import initialize_runtime; initialize_runtime()"),
                       ("backend", "from backend.puppy_backend.cli import _self_test; _self_test()")):
        env = dict(os.environ, PUPPY_DATA=str(ROOT / (name + "-crash")))
        died = subprocess.run([sys.executable, "-c", seed], cwd=str(BASE), env=env,
                              capture_output=True, text=True, timeout=20)
        assert died.returncode == 23, died.stderr
        check = boot + '''
from puppy import db
events = db.get_events(1)
assert db.get_session(1)["status"] == "idle"
assert [e["kind"] for e in events] == ["tool_use", "tool_result"]
assert events[1]["data"]["tool_use_id"] == "lost"
assert events[1]["data"]["interrupted"] is True
'''
        for _ in range(2):
            start = subprocess.run([sys.executable, "-c", check], cwd=str(BASE), env=env,
                                   capture_output=True, text=True, timeout=30)
            assert start.returncode == 0, (name, start.stdout, start.stderr)


def recovery_checks():
    running, old = session("Crashed"), session("Old idle history")
    call(running, "known")
    db.add_event(running, "tool_result", {
        "tool_use_id": "known", "is_error": False, "content": "Kept verbatim"})
    call(running, "lost")
    # A later prompt/result cannot make an earlier missing tool result real.
    db.add_event(running, "user", {"text": "Continue after the restart"})
    db.add_event(running, "result", {"ok": True})
    call(old, "known")  # identities belong to their session, never globally
    for status in ("completed", "failed", "stopped"):
        call(old, status)
        db.add_event(old, "info", {"subtype": "task", "status": status,
                                  "tool_use_id": status})
    db.touch_session(running, status="running")
    db.touch_session(old, archived=1)
    before = {sid: db.get_events(sid) for sid in (running, old)}
    metadata = {sid: db.get_session(sid) for sid in (running, old)}
    connection = db.connect()
    # A crash/storage failure midway through recovery cannot leave half a
    # batch committed or clear the activity flags before the results exist.
    connection.execute("CREATE TEMP TRIGGER refuse_recovery BEFORE INSERT ON events "
                       "WHEN NEW.session_id={} BEGIN SELECT RAISE(ABORT, 'test failure'); END".format(old))
    try:
        tool_calls.recover_runtime()
        raise AssertionError("injected failure was swallowed")
    except sqlite3.IntegrityError:
        pass
    assert {sid: db.get_events(sid) for sid in before} == before
    assert db.get_session(running)["status"] == "running"
    connection.execute("DROP TRIGGER refuse_recovery")
    # Both executable entry points call this actual shared initializer.
    with patch.object(main, "setup_logging"):
        main.initialize_runtime()
    for sid, ident in ((running, "lost"), (old, "known")):
        events = db.get_events(sid)
        assert events[:len(before[sid])] == before[sid], "existing history was rewritten"
        assert len(recovered(sid)) == 1
        result = recovered(sid)[0]["data"]
        assert result["tool_use_id"] == ident and result["is_error"] is True
        assert "restarted" in result["content"] and "unknown" in result["content"]
        assert db.get_session(sid) == dict(metadata[sid], status="idle")
    once = {sid: db.get_events(sid) for sid in before}
    tool_calls.recover_runtime()
    assert {sid: db.get_events(sid) for sid in before} == once

    # Restoring a valid snapshot has no surviving engine either. Repair the
    # staged database, leaving the running source untouched until commit.
    call(old, "snapshot-missing")
    candidate = ROOT / "candidate.db"
    db.backup_to(str(candidate))
    manifest = {"scratch_saved": [], "scratch_missing": []}
    assert snapshots._prepare_scratch(candidate, ROOT, manifest) == []
    with sqlite3.connect(str(candidate)) as restored:
        data = [json.loads(row[0]) for row in restored.execute(
            "SELECT payload FROM events WHERE session_id=? AND kind='tool_result'", (old,))]
        assert len([r for r in data if r.get("tool_use_id") == "snapshot-missing"]) == 1
        assert "restored" in data[-1]["content"]
        assert tool_calls.recover(restored, tool_calls.RESTARTED) == 0
    assert not any(ev["data"]["tool_use_id"] == "snapshot-missing" for ev in recovered(old))
    db.delete_session(running)
    db.delete_session(old)


FIXTURE = r'''
import json,os,sys,time
mode=sys.argv[1].splitlines()[-1]
sys.stdin.readline()
def send(data): print(json.dumps(data),flush=True)
send({"type":"assistant","message":{"content":[
    {"type":"tool_use","id":"kept","name":"Read","input":{"file_path":"/demo/kept"}},
    {"type":"tool_use","id":"pending","name":"Bash","input":{"command":"demo command"}}]}})
send({"type":"user","message":{"content":[
    {"type":"tool_result","tool_use_id":"kept","content":"A real result"}]}})
if mode=="eof":
    time.sleep(.2)
    os._exit(2)
if mode in ("complete","missing"):
    if mode=="complete":
        send({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"pending","content":"Finished"}]}})
    send({"type":"result","subtype":"success","is_error":False,"result":"Done"})
for line in sys.stdin: pass
'''


async def wire_checks(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    script = ROOT / "fixture.py"
    script.write_text(FIXTURE)
    driver = get_driver("claude")
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        for mode in ("complete", "missing", "eof", "interrupt", "timeout", "cancel", "exception"):
            sid = session(mode)
            hub = runner.hub(sid)
            processes = []
            launch = asyncio.create_subprocess_exec

            async def capture_process(*args, **kwargs):
                process = await launch(*args, **kwargs)
                processes.append(process)
                return process

            class BrokenTransport:
                def environment(self): return {}

                async def read_actions(self, proc):
                    line = await proc.stdout.readline()
                    actions = driver.parse_line(line.decode(), {})
                    if b'"type": "user"' in line:
                        raise RuntimeError("synthetic transport failure")
                    return actions

                async def close(self):
                    raise RuntimeError("synthetic close failure")

            with ExitStack() as stack:
                stack.enter_context(patch.object(asyncio, "create_subprocess_exec", side_effect=capture_process))
                stack.enter_context(patch.object(driver, "build_cmd", side_effect=
                    lambda _session, _first, prompt, *_args, **_kw: [sys.executable, str(script), prompt]))
                if mode == "exception":
                    stack.enter_context(patch.object(driver, "turn_transport", return_value=BrokenTransport()))
                previous_timeout = config.get("sessions.turn_timeout")
                if mode == "timeout":
                    config.set_value("sessions.turn_timeout", 1)
                try:
                    ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
                    assert (await ws.receive_json(timeout=3))["type"] == "snapshot"
                    assert hub.send_message(mode) == {"queued": False}
                    events, endings = [], []
                    queued = False
                    while True:
                        try:
                            frame = await ws.receive_json(timeout=15)
                        except asyncio.TimeoutError:
                            raise AssertionError((factory.__module__, mode, hub.status,
                                                  db.get_events(sid), hub.stderr_tail))
                        if frame["type"] == "event":
                            event = frame["event"]
                            events.append(event)
                            if event["kind"] == "tool_result" and event["data"]["tool_use_id"] == "kept" and not queued:
                                # No cleanup until there is evidence the owner
                                # ended: a genuinely live call stays pending.
                                if mode in ("interrupt", "timeout", "cancel"):
                                    assert recovered(sid) == []
                                if mode == "interrupt":
                                    await hub.interrupt()
                                elif mode == "cancel":
                                    hub.turn_task.cancel()
                                elif mode == "eof":
                                    assert hub.send_message("complete") == {"queued": True}
                                queued = True
                        elif frame["type"] == "turn_done":
                            endings.append(frame)
                            if not frame.get("continued"):
                                break
                    try:
                        await hub.turn_task
                    except asyncio.CancelledError:
                        assert mode == "cancel"
                    assert processes and all(proc.returncode is not None for proc in processes)
                    assert hub.proc is None and hub.status == "idle"
                    missing = recovered(sid)
                    assert len(missing) == (0 if mode == "complete" else 2 if mode == "exception" else 1), (mode, events)
                    if missing:
                        assert all(ev in events for ev in missing), "closure must reach live watchers"
                        assert all("unknown" in ev["data"]["content"] for ev in missing)
                    if mode == "eof":
                        next_prompt = next(ev for ev in events if ev["kind"] == "user" and ev["data"]["text"] == "complete")
                        assert missing[0]["seq"] < next_prompt["seq"], "cleanup leaked into queued work"
                        assert len(endings) == 2
                    await ws.close()
                    ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
                    snap = await ws.receive_json(timeout=3)
                    assert snap["status"] == "idle"
                    assert [ev for ev in snap["events"] if ev["data"].get("interrupted")] == missing
                    await ws.close()
                    response = await client.get("/api/sessions/{}/events".format(sid), headers=headers)
                    assert response.status == 200
                    assert (await response.json())["events"] == db.get_events(sid)
                finally:
                    config.set_value("sessions.turn_timeout", previous_timeout)
                    if hub.turn_task and not hub.turn_task.done():
                        await hub.kill()
                        await hub.turn_task
                    runner.drop_hub(sid)
                    db.delete_session(sid)


async def run():
    config.load()
    cold_start_checks()
    recovery_checks()
    with ExitStack() as stack:
        for name in ("browser_agent", "terminal_agent", "vnc_agent", "spawn_agent", "session_agent"):
            stack.enter_context(patch.object(getattr(runner, name), "turn_mcp", return_value=None))
        stack.enter_context(patch.object(runner.session_links, "prepare_turn", AsyncMock()))
        stack.enter_context(patch.object(runner.system_prompts, "turn_prompt", return_value=""))
        stack.enter_context(patch.object(runner.notify, "session_finished"))
        await wire_checks(build_app)
        await wire_checks(backend_app)
    print("PASS: atomic/idempotent restart and restore recovery; completion, EOF, queued turns, "
          "interrupt, timeout, cancellation and transport failure on both runtimes; HTTP and reconnect history")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(ROOT)
