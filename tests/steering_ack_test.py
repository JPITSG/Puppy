#!/usr/bin/env python3
"""Real runner/process shutdown with native steering and side-question frames.

Replays OpenCode ACP and Claude stream-json response ordering without running
an installed engine, reaching an external service or spending model quota.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import select
import shutil
import sys
import time
from unittest.mock import AsyncMock, patch


def fixture(options, log_path):
    """A JSONL native service: responses can precede or follow its result."""
    engine = options["engine"]

    def log(kind, **fields):
        with open(log_path, "a") as handle:
            handle.write(json.dumps(dict(at=time.monotonic(), kind=kind, **fields)) + "\n")

    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        value = json.loads(line)
        log("request", value=value)
        return value

    def send(value):
        print(json.dumps(value), flush=True)

    def wait(duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))[0]:
                line = sys.stdin.readline()
                if not line:
                    log("eof")
                    return True
                log("unexpected-request", value=json.loads(line))
        return False

    if engine == "opencode":
        init = read()
        assert init["method"] == "initialize"
        send({"jsonrpc": "2.0", "id": init["id"], "result": {"protocolVersion": 1}})
        session = read()
        assert session["method"] in ("session/new", "session/resume")
        send({"jsonrpc": "2.0", "id": session["id"], "result": {"sessionId": "native-session"}})
        prompt = read()
        assert prompt["method"] == "session/prompt"
        text = prompt["params"]["prompt"][0]["text"]
    else:
        init = read()
        assert init["request"]["subtype"] == "initialize"
        send({"type": "control_response", "response": {
            "subtype": "success", "request_id": init["request_id"], "response": {}}})
        prompt = read()
        text = prompt["message"]["content"][0]["text"]
        send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
        send(prompt)  # native user replay opens steering

    second = text == "second prompt"
    count = 0 if second else options.get("count", 1)
    steers = [read() for _ in range(count)]
    question = read() if options.get("question") and not second else None
    if question:
        assert question["request"]["subtype"] == "side_question"

    def reply(event):
        kind = event["kind"]
        log(kind, index=event.get("index"))
        if kind == "steer":
            steer = steers[event.get("index", 0)]
            if engine == "opencode":
                send({"jsonrpc": "2.0", "id": steer["id"], **(
                    {"error": {"code": -32600, "message": "native steering refusal"}}
                    if event.get("reject") else {"result": {"stopReason": "end_turn"}})})
            else:
                send(steer)
        elif kind == "question":
            send({"type": "control_response", "response": {
                "subtype": "success", "request_id": question["request_id"],
                "response": {"response": "side answer", "synthetic": False}}})
        elif kind == "resume":
            send({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
            send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
            result()

    def result():
        log("result", second=second)
        if engine == "opencode":
            send({"jsonrpc": "2.0", "id": prompt["id"], **(
                {"error": {"code": -32603, "message": "native prompt failure"}}
                if options.get("result_error") else {"result": {"stopReason": "end_turn"}})})
        else:
            send({"type": "result", "subtype": "success", "is_error": False,
                  "session_id": "native-session", "num_turns": 1,
                  "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})

    for event in options.get("before", []) if not second else []:
        reply(event)
    if options.get("background") and not second:
        send({"type": "system", "subtype": "background_tasks_changed", "tasks": [
            {"task_id": "background-fixture", "task_type": "local_bash", "description": "fixture wait"}]})
    result()
    if options.get("exit") and not second:
        log("exit")
        return
    for event in options.get("after", []) if not second else []:
        if wait(event.get("delay", 0)):
            return  # premature EOF makes the parent's acknowledgement checks fail
        reply(event)
    assert wait(5), "runner never closed stdin"


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture(json.loads(sys.argv[2]), sys.argv[3])
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("steering-ack-test-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from puppy import config, db, runner
from puppy.drivers import get_driver, opencode


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 8)


async def check(name, engine="opencode", **options):
    options = dict(options, engine=engine)
    driver = get_driver(engine)
    sid = db.create_session(name, engine, str(ROOT), "", "", "", "default")
    hub = runner.hub(sid)
    log_path = ROOT / (name + ".jsonl")
    messages = []
    publish = hub.broadcast

    def capture(message):
        messages.append(message)
        publish(message)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), "--fixture", json.dumps(options), str(log_path)]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(hub, "broadcast", side_effect=capture))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", options.get("steer_grace", .3)))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", options.get("question_grace", .6)))
        try:
            assert hub.send_message("first prompt") == {"queued": False}
            count = options.get("count", 1)
            if count or options.get("question"):
                await until(lambda: hub.steering_state()["ready"])
            turn_id = hub._active_turn_id
            for index in range(count):
                sent = await hub.steer("direction " + str(index), "steer-" + str(index), turn_id)
                assert sent.get("ok"), sent
            if options.get("question"):
                asked = await hub.ask("why?", "question", turn_id)
                assert asked.get("ok"), asked
            if options.get("queue"):
                assert hub.send_message("second prompt") == {"queued": True}
            if options.get("check_result_state"):
                await until(lambda: hub._turn_result_seen)
                assert not hub.side_question_state()["ready"]
                assert (await hub.ask("too late?", "late-question", turn_id)).get("error")
            if options.get("background"):
                await until(lambda: hub._bg_wait_since is not None)
                assert hub.side_question_state()["ready"], "background work still allows side questions"
            if options.get("interrupt"):
                await until(lambda: hub._turn_result_seen)
                assert not hub.steering_state()["ready"]
                assert (await hub.steer("too late", "late", turn_id)).get("error")
                await hub.interrupt()
            await until(lambda: hub.status == "idle")
            await hub.turn_task
            rows = [json.loads(line) for line in log_path.read_text().splitlines()]
            assert not any(r["kind"] == "unexpected-request" for r in rows), rows
            endings = [r for r in rows if r["kind"] in ("eof", "exit")]
            assert len(endings) == (2 if options.get("queue") else 1), "fixture failed before clean shutdown"
            events = db.get_events(sid)
            results = [e for e in events if e["kind"] == "result"]
            assert len(results) == (2 if options.get("queue") else 1), events
            assert all(r["data"]["ok"] is (not options.get("result_error")) for r in results), results
            assert not hub.queue and not hub.held
            expected = options.get("expected", ["accepted"] * count)
            for index, status in enumerate(expected):
                rid = "steer-" + str(index)
                statuses = [m["status"] for m in messages if m.get("type") == "steer_status" and m["request_id"] == rid]
                assert statuses == ["sent", status], (name, statuses)
                errors = [e for e in events if e["kind"] == "error" and e["data"].get("request_id") == rid]
                assert len(errors) == int(status == "rejected"), (name, errors)
                if errors:
                    native = any(e.get("index", 0) == index and e.get("reject")
                                 for e in options.get("before", []) + options.get("after", []))
                    assert ("native steering refusal" in errors[0]["data"]["text"]) == native
                    if not native:
                        assert errors[0]["data"]["text"].startswith("Could not confirm steering:")
                if not options.get("queue"):
                    retry = await hub.steer("direction " + str(index), rid, turn_id)
                    assert retry["duplicate"] and retry["status"] == status
            if options.get("question"):
                assert hub._side_questions["question"]["status"] == options.get("question_expected", "answered")
            if options.get("queue"):
                accepted = next(i for i, m in enumerate(messages) if m.get("type") == "steer_status" and m.get("status") == "accepted")
                continued = next(i for i, m in enumerate(messages) if m.get("type") == "turn_done" and m.get("continued"))
                assert accepted < continued
            if options.get("immediate"):
                final = next(r["at"] for r in rows if r["kind"] == "result")
                end = next(r["at"] for r in rows if r["kind"] in ("eof", "exit"))
                assert end - final < 1, (name, end - final)
            if options.get("deadline"):
                final = next(r["at"] for r in rows if r["kind"] == "result")
                elapsed = endings[0]["at"] - final
                grace = options.get("steer_grace", .3)
                assert grace <= elapsed < grace + 1, (name, elapsed)
                assert "did not acknowledge steering within" in hub._steer_receipts["steer-0"]["error"]
            print("PASS: " + name, flush=True)
        finally:
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
            runner.drop_hub(sid)


async def main():
    config.load()
    config.set_value("sessions.turn_timeout", 15)
    db.connect()
    with ExitStack() as stack:
        for name in ("browser_agent", "terminal_agent", "vnc_agent", "spawn_agent", "session_agent"):
            stack.enter_context(patch.object(getattr(runner, name), "turn_mcp", return_value=None))
        stack.enter_context(patch.object(runner.session_links, "prepare_turn", AsyncMock()))
        stack.enter_context(patch.object(runner.system_prompts, "turn_prompt", return_value=""))
        stack.enter_context(patch.object(runner.notify, "session_finished"))
        stack.enter_context(patch.object(opencode, "_session_totals", return_value=None))
        await check("ack-before-result", before=[{"kind": "steer"}], steer_grace=3, immediate=True)
        await check("ack-after-result", after=[{"kind": "steer", "delay": .05}], steer_grace=3, immediate=True)
        await check("ack-in-next-line", after=[{"kind": "steer"}])
        await check("multiple-out-of-order", count=3, after=[
            {"kind": "steer", "index": 2, "delay": .04},
            {"kind": "steer", "index": 0, "delay": .04},
            {"kind": "steer", "index": 1, "delay": .04}])
        await check("mixed-native-replies", count=2, expected=["accepted", "rejected"], after=[
            {"kind": "steer", "delay": .03}, {"kind": "steer", "index": 1, "reject": True, "delay": .03}])
        await check("duplicate-replies-stay-terminal", count=2, expected=["rejected", "accepted"], after=[
            {"kind": "steer", "reject": True, "delay": .02},
            {"kind": "steer", "delay": .02},
            {"kind": "steer", "index": 1, "delay": .02}])
        await check("failed-prompt-still-drains-ack", result_error=True,
                    after=[{"kind": "steer", "delay": .04}])
        await check("no-ack-deadline", expected=["rejected"], deadline=True)
        await check("eof-before-ack", exit=True, expected=["rejected"], steer_grace=3, immediate=True)
        await check("no-steer-no-wait", count=0, steer_grace=3, immediate=True)
        await check("stop-during-ack-wait", interrupt=True, expected=["rejected"], steer_grace=3, immediate=True)
        await check("queue-after-ack", queue=True, after=[{"kind": "steer", "delay": .05}])
        await check("steer-then-side-answer", "claude", question=True, after=[
            {"kind": "steer", "delay": .04}, {"kind": "question", "delay": .04}])
        await check("side-answer-then-steer", "claude", question=True, after=[
            {"kind": "question", "delay": .04}, {"kind": "steer", "delay": .04}])
        await check("side-answer-after-steer-timeout", "claude", question=True, steer_grace=.05,
                    expected=["rejected"], after=[
                        {"kind": "steer", "delay": .15}, {"kind": "question", "delay": .03}])
        await check("steer-after-side-answer-timeout", "claude", question=True, question_grace=.05,
                    question_expected="failed", after=[{"kind": "steer", "delay": .15}])
        await check("side-answer-only", "claude", count=0, question=True,
                    after=[{"kind": "question", "delay": .04}])
        await check("no-new-question-after-result", "claude", check_result_state=True,
                    after=[{"kind": "steer", "delay": .1}])
        await check("ack-during-background-wait", "claude", background=True,
                    after=[{"kind": "steer", "delay": .04}, {"kind": "resume", "delay": .04}])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        assert ROOT.parent == BASE / "data/tests" and ROOT.name.startswith("steering-ack-test-")
        assert not ROOT.is_symlink() and ROOT.stat().st_uid == os.getuid()
        shutil.rmtree(ROOT)
