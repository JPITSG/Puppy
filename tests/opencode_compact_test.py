#!/usr/bin/env python3
"""Native compaction transport, runner recovery and both execution APIs.

Fixture HTTP replies reproduce OpenCode 1.18.30's public API, including its
misleading `true` on failed/empty summaries. The runner owns real disposable
process groups; no installed engine, external service or quota is used.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("opencode-compact-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, handoff, runner
from puppy.drivers import all_drivers, get_driver
from puppy.drivers import opencode_compact as compact


async def until(predicate, timeout=10):
    async def wait():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), timeout)


async def finished(hub):
    await until(lambda: hub.status == "idle")
    if hub.turn_task:
        await hub.turn_task


class Conversation:
    def __init__(self, mode="success", model="", native=True):
        self.id = db.create_session("Compaction fixture", "opencode", str(ROOT),
                                    model, "", "", "auto")
        self.sid = "ses_fixture_{}".format(self.id)
        if native:
            db.touch_session(self.id, native_session_id=self.sid)
        db.add_event(self.id, "user", {"text": "Remember the invented lantern plan"})
        db.add_event(self.id, "assistant", {"text": "The invented lantern plan is ready"})
        self.mode, self.posts, self.reads, self.aborts = mode, [], [], 0
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.transport = None
        self.rows = [{"info": {"id": "msg_0", "sessionID": self.sid, "role": "assistant"},
                      "parts": [{"type": "text", "text": "Previous reply"}]}]

    def append_summary(self, body):
        mid = "msg_{}".format(len(self.rows))
        user = {"info": {"id": mid, "role": "user", "sessionID": self.sid,
                         "model": {k: body[k] for k in ("providerID", "modelID")}},
                "parts": [{"type": "compaction", "auto": False}]}
        answer = {"info": {"id": mid + "_summary", "role": "assistant", "sessionID": self.sid,
                           "parentID": mid, "summary": True, "finish": "stop", "time": {"completed": 123},
                           "providerID": "summary-provider", "modelID": "override-summary-model",
                           "cost": .012, "tokens": {"input": 40, "output": 5, "reasoning": 2,
                                                    "cache": {"read": 3, "write": 4}}},
                  "parts": [{"type": "text", "text": "A useful summary of the lantern plan"}]}
        if self.mode == "error":
            answer["info"]["error"] = {"name": "APIError", "data": {"message": "Fixture provider refused"}}
        if self.mode == "empty":
            answer["parts"][0]["text"] = " \n "
        if self.mode == "unfinished":
            answer["info"].pop("finish")
        if self.mode == "aborted":
            answer["info"]["error"] = {"name": "MessageAbortedError", "data": {}}
        if self.mode == "wrong_parent":
            answer["info"]["parentID"] = "another-request"
        if self.mode == "wrong_model":
            user["info"]["model"]["modelID"] = "another-model"
        if self.mode == "tool":
            answer["parts"].append({"type": "tool"})
        self.rows += [user, answer]
        if self.mode == "concurrent":
            extra = copy.deepcopy(answer)
            extra["info"]["id"] += "_extra"
            self.rows.append(extra)


class API:
    def __init__(self):
        self.cases = {}
        self.driver = get_driver("opencode")
        self.original_context = self.driver.turn_context

    def case(self, *args, **kwargs):
        case = Conversation(*args, **kwargs)
        self.cases[case.sid] = case
        return case, runner.hub(case.id)

    def context(self, session, *args, **kwargs):
        ctx = self.original_context(session, *args, **kwargs)
        transport = self.driver.turn_transport(ctx)
        assert transport is not None
        self.cases[transport.sid].transport = transport
        return ctx

    def command(self, *args, **kwargs):
        # A real runner-owned process publishes the same readiness contract.
        # stdout noise after readiness must never be interpreted as actions.
        code = ("import os,signal; "
                "assert os.environ['OPENCODE_SERVER_USERNAME']=='puppy'; "
                "assert len(os.environ['OPENCODE_SERVER_PASSWORD'])>=32; "
                "print({!r},flush=True); "
                "print('unrelated native logging',flush=True); signal.pause()"
                ).format(compact.READY_PREFIX + self.url)
        return compact._guarded_command([sys.executable, "-I", "-c", code])

    async def handle(self, request):
        case = self.cases[request.match_info["sid"]]
        expected = BasicAuth("puppy", case.transport.password).encode()
        if request.headers.get("Authorization") != expected:
            raise web.HTTPUnauthorized()
        assert request.query["directory"] == str(ROOT)
        action = request.match_info.get("action", "")
        if not action:
            if case.mode == "missing":
                raise web.HTTPNotFound()
            native = {"id": case.sid, "directory": str(ROOT),
                      "model": {"providerID": "fixture", "id": "chat/model"}}
            if case.mode == "revert":
                native["revert"] = {"messageID": "pending-undo"}
            if case.mode == "no_model":
                native.pop("model")
            if case.mode == "wrong_directory":
                native["directory"] = str(ROOT / "elsewhere")
            return web.json_response(native)
        if action == "abort":
            case.aborts += 1
            case.release.set()
            return web.json_response(True)
        if action == "summarize":
            # An independent SQLite reader proves the checkpoint committed
            # before *any* request capable of changing native context.
            with sqlite3.connect("file:{}?mode=ro".format(config.DB_PATH), uri=True) as conn:
                assert conn.execute("SELECT native_session_id FROM sessions WHERE id=?",
                                    (case.id,)).fetchone() == ("",)
            body = await request.json()
            assert set(body) == {"providerID", "modelID", "auto"} and body["auto"] is False
            case.posts.append(body)
            case.started.set()
            if case.mode == "wait":
                await case.release.wait()
            if case.mode != "unchanged":
                case.append_summary(body)
            if case.mode == "lost_reply":
                request.transport.close()
                return web.Response()
            return web.json_response(True)
        assert action == "message" and request.method == "GET"
        case.reads.append(dict(request.query))
        if case.mode == "bad_page":
            return web.json_response({"unexpected": "schema"})
        if case.mode == "redirect":
            raise web.HTTPFound("http://192.0.2.1/never-follow")
        if case.mode == "oversize":
            return web.Response(body=b" " * (compact.RESPONSE_BYTES + 1))
        rows = case.rows
        before = request.query.get("before")
        if before:
            rows = rows[:next(i for i, r in enumerate(rows) if r["info"]["id"] == before)]
        # Page size 1 forces verification to cross two boundaries.
        limit = 1 if case.mode == "paginated" else int(request.query["limit"])
        selected = rows[-limit:]
        headers = {"X-Next-Cursor": selected[0]["info"]["id"]} if len(rows) > limit else {}
        return web.json_response(selected, headers=headers)


async def check_cases(api):
    driver = api.driver
    session = {"cwd": str(ROOT), "model": "", "native_session_id": "ses_test"}
    tool = {"tool": "compact"}
    cmd = driver.build_cmd(session, False, "do not send", "pinned", tool=tool)
    assert cmd[5:] == ["serve", "--hostname", "127.0.0.1", "--port", "0", "--mdns", "false"]
    env = driver.build_env(session, False, "ignored", "pin", tool=tool,
                          system_prompt="NEVER_INCLUDE", browser_mcp={"engine_guidance": "NEVER_INCLUDE"})
    assert "NEVER_INCLUDE" not in json.dumps(env)
    assert driver.initial_stdin(session, "ignored", tool=tool) == []
    assert driver.turn_transport(driver.turn_context(session, False, "hello", "pin")) is None
    assert [o["value"] for o in driver.tool_options()] == ["compact"]
    for value in ("https://127.0.0.1:1", "http://example.com:80", "http://127.0.0.1:1/path",
                  "http://user:pass@127.0.0.1:1", "http://127.0.0.1:99999"):
        try:
            compact.endpoint(compact.READY_PREFIX + value)
        except compact.CompactionError:
            pass
        else:
            raise AssertionError(value)

    with patch.object(driver, "build_cmd", api.command), patch.object(driver, "turn_context", api.context):
        for mode in ("success", "paginated", "lost_reply", "error", "empty", "unfinished",
                     "aborted", "wrong_parent", "wrong_model", "tool", "concurrent", "unchanged",
                     "revert", "missing", "no_model", "wrong_directory", "bad_page", "redirect", "oversize"):
            case, hub = api.case(mode)
            assert hub.request_tool("compact") == {"ok": True, "queued": False}
            # Pausing the waiting prompt lets successful compaction settle
            # without starting an unrelated engine fixture.
            assert hub.send_message("waiting prompt")["queued"]
            hub.paused_queue = {0}
            hub._broadcast_queue()
            await finished(hub)
            events = db.get_events(case.id)
            results = [e["data"] for e in events if e["kind"] == "result"]
            assert len(results) == 1, (mode, events)
            result = results[0]
            success = mode in ("success", "paginated", "lost_reply")
            assert result["ok"] is success, (mode, result)
            assert result["tool"] == "compact"
            assert "context_used" not in result
            assert "cost_usd" not in result
            current = db.get_session(case.id)
            if success:
                assert current["native_session_id"] == case.sid
                assert result["usage"] == {"input_tokens": 40, "output_tokens": 7,
                    "reasoning_output_tokens": 2, "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 4}
                assert hub.queue == ["waiting prompt"] and not hub.held
            elif case.posts:
                assert current["native_session_id"] == ""
                assert not hub.queue and hub.held == ["waiting prompt"]
                assert db.meta_get("session_queue.{}".format(case.id))["held"] == hub.held
                assert handoff.needs_handoff(current)
                assert "lantern plan" in handoff.build(current)
                assert "context_reset" in json.dumps(events)
            else:
                assert current["native_session_id"] == case.sid
                assert hub.queue == ["waiting prompt"]
            assert len(case.posts) <= 1  # even a lost HTTP reply is never retried
            assert case.transport.task.done() and case.transport.process.returncode is not None
            hub.clear_queue()
        print("PASS: native outcome verification, usage, pagination, no POST retry, bounded responses and preflight refusals")

        # Different sessions keep their passwords/model selections/outcomes apart.
        cases = [api.case(model=model) for model in ("other-provider/org/model", "")]
        for case, hub in cases:
            hub.request_tool("compact")
        await asyncio.gather(*(finished(hub) for _, hub in cases))
        assert cases[0][0].transport.password != cases[1][0].transport.password
        assert cases[0][0].posts[0]["modelID"] == "org/model"
        assert cases[1][0].posts[0]["modelID"] == "chat/model"
        print("PASS: concurrent maintenance turns isolate native session, credentials and selected/default model")

        case, hub = api.case()
        assert not hub.request_tool("compact")["queued"]
        assert hub.request_tool("compact")["queued"]
        assert hub.request_tool("compact")["duplicate"]
        await finished(hub)
        assert len(case.posts) == 2 and db.get_session(case.id)["native_session_id"] == case.sid

        # An acknowledgement is required before the HTTP mutation. Failure
        # to commit it leaves native context intact, even with work waiting.
        touch = db.touch_session
        def refuse_checkpoint(sid, **fields):
            if fields.get("native_session_id") == "":
                raise OSError("fixture checkpoint storage failure")
            return touch(sid, **fields)
        case, hub = api.case()
        with patch.object(db, "touch_session", refuse_checkpoint), patch.object(runner.log, "exception"):
            hub.request_tool("compact")
            await finished(hub)
        assert not case.posts and db.get_session(case.id)["native_session_id"] == case.sid
        assert case.transport.task.done() and case.transport.process.returncode is not None

        # Runner read deadlines must not cancel/reissue the native POST.
        read = compact.NativeCompaction.read_actions
        reads = []
        async def quick_read(transport, process):
            reads.append(transport)
            return await asyncio.wait_for(read(transport, process), .02)
        case, hub = api.case("wait")
        with patch.object(compact.NativeCompaction, "read_actions", quick_read):
            hub.request_tool("compact")
            await asyncio.wait_for(case.started.wait(), 5)
            await until(lambda: len(reads) >= 5)
            assert len(case.posts) == 1 and not case.transport.task.done()
            await hub.interrupt()
            await finished(hub)
        case, hub = api.case()
        hub.request_tool("compact")
        await hub.interrupt()  # before process startup/preflight
        await finished(hub)
        assert not case.posts and db.get_session(case.id)["native_session_id"] == case.sid
        print("PASS: ordered/deduplicated compaction queue, failed durable checkpoint, read timeouts and early stop")

        for stop in ("cancel", "restart", "task_cancel", "process_exit"):
            case, hub = api.case("wait")
            hub.request_tool("compact")
            hub.send_message("held after interruption")
            await asyncio.wait_for(case.started.wait(), 5)
            assert not hub.steering_state()["ready"]
            proc = hub.proc
            try:
                if stop == "cancel":
                    await hub.interrupt()
                elif stop == "restart":
                    # Restore the actual persisted queue while the native
                    # checkpoint is detached, as startup does after a crash.
                    restored = runner.SessionHub(case.id)
                    assert restored.held == ["held after interruption"]
                    assert handoff.needs_handoff(db.get_session(case.id))
                    await hub.interrupt()
                elif stop == "task_cancel":
                    hub.turn_task.cancel()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                if stop == "task_cancel":
                    await asyncio.gather(hub.turn_task, return_exceptions=True)
                else:
                    await finished(hub)
                assert db.get_session(case.id)["native_session_id"] == ""
                assert hub.held == ["held after interruption"] and not hub.queue
                assert proc.returncode is not None and case.transport.task.done()
                if stop in ("cancel", "restart"):
                    assert case.aborts == 1
            finally:
                case.release.set()
        old = config.get("sessions.turn_timeout")
        config.set_value("sessions.turn_timeout", 1)
        try:
            case, hub = api.case("wait")
            hub.request_tool("compact")
            hub.send_message("held after timeout")
            await finished(hub)
            assert hub.held == ["held after timeout"]
            assert not db.get_session(case.id)["native_session_id"]
        finally:
            case.release.set()
            config.set_value("sessions.turn_timeout", old)
        print("PASS: native stop, deadline, process death, cancelled runner and restart checkpoint all hold queued work")


async def check_http(api):
    from puppy.web import build_app
    from backend.puppy_backend.app import build_app as backend_app
    apps = [build_app(), backend_app()]
    with ExitStack() as stack:
        for driver in all_drivers():
            stack.enter_context(patch.object(driver, "status", AsyncMock(return_value={"installed": True})))
            stack.enter_context(patch.object(driver, "refresh_model_options", AsyncMock()))
        stack.enter_context(patch.object(api.driver, "build_cmd", api.command))
        stack.enter_context(patch.object(api.driver, "turn_context", api.context))
        for app in apps:
            app.on_startup.clear()
            async with TestClient(TestServer(app)) as client:
                case, hub = api.case()
                path = "/api/sessions/{}/tool".format(case.id)
                headers = {"X-Puppy-Token": config.get("auth.api_token")}
                response = await client.get("/api/engines", headers=headers)
                payload = await response.json()
                engine = next(e for e in payload["engines"] if e["key"] == "opencode")
                assert [t["value"] for t in engine["tool_options"]] == ["compact"]
                response = await client.post(path, json={"tool": "compact"})
                assert response.status == 401
                response = await client.post(path, json={"tool": "compact"}, headers=headers)
                assert response.status == 200, await response.text()
                await finished(hub)
                assert db.get_session(case.id)["native_session_id"] == case.sid
                response = await client.post(path, json={"tool": "undo"}, headers=headers)
                assert response.status == 409, await response.text()
                fresh, _ = api.case(native=False)
                response = await client.post("/api/sessions/{}/tool".format(fresh.id),
                                            json={"tool": "compact"}, headers=headers)
                assert response.status == 409, await response.text()
    print("PASS: full console and shared headless execution routes run compact and refuse undo/empty context")


async def check_owner_loss():
    def alive(pid):
        try:
            return Path("/proc/{}/stat".format(pid)).read_text().rsplit(") ", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False

    # A native launcher and grandchild which ignore graceful termination.
    child = """import os, signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
grandchild = os.fork()
if grandchild:
    print(str(os.getpid()) + ' ' + str(grandchild), flush=True)
while True:
    signal.pause()
"""
    guard = compact._guarded_command([sys.executable, "-I", "-c", child])
    (ROOT / "subprocess.py").write_text("raise RuntimeError('must not import code from the project')\n")
    for death in (False, True):
        owner_code = """import signal, subprocess, sys
p = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
print(p.pid, flush=True)
print(p.stdout.readline().decode().strip(), flush=True)
signal.pause()
"""
        proc = await asyncio.create_subprocess_exec(
            *([sys.executable, "-I", "-c", owner_code] + guard if death else guard),
            cwd=str(ROOT), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True)
        pgid = int(await proc.stdout.readline()) if death else proc.pid
        try:
            descendants = [int(p) for p in (await proc.stdout.readline()).split()]
            assert len(descendants) == 2 and all(alive(p) for p in descendants)
            if death:
                proc.kill()
            else:
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 5)
            await until(lambda: not any(alive(p) for p in [pgid] + descendants), 5)
        finally:
            for group in (pgid, proc.pid):
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await proc.wait()
    print("PASS: stdin EOF and hard-killed owner reap the entire maintenance process group, including a stubborn grandchild")


async def main():
    config.load()
    db.connect()
    await check_owner_loss()
    api = API()
    app = web.Application()
    app.router.add_route("*", "/session/{sid}", api.handle)
    app.router.add_route("*", "/session/{sid}/{action}", api.handle)
    async with TestServer(app) as server:
        api.url = str(server.make_url("/")).rstrip("/")
        try:
            await check_cases(api)
            await check_http(api)
        finally:
            for case in api.cases.values():
                case.release.set()
            for hub in list(runner._hubs.values()):
                hub.clear_queue()
                if hub.status == "running":
                    await hub.kill()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
