#!/usr/bin/env python3
"""Session references, communication and coordination without model calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import shutil
import time
import subprocess
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("session-links-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from puppy import config, db, runner, search, session_agent, session_links as links
from puppy import session_actions as actions, session_coordination as coordination, session_aliases as aliases


def session(name):
    return db.create_session(name, "codex", str(ROOT), "", "", "#e0784f", "ask")


async def rejected(awaitable, text=""):
    try:
        await awaitable
    except (ValueError, session_agent.SessionAgentError) as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("expected rejection")


async def references():
    a, b, c = session("Origin"), session("API work"), session("Old decisions")
    db.touch_session(c, archived=1)
    db.add_event(b, "assistant", {"text": "Token expiry is one hour"})
    db.add_event(c, "assistant", {"text": "Token expiry was proposed as two hours"})
    long = "x" * 42000
    db.add_event(b, "tool_result", {"content": long})
    search.reconcile()
    ref = links.reference(b)
    await links.prepare_turn(a, "turn", "@Session {}:{} compare this".format(db.node_uuid(), ref))
    got = await links.dispatch(a, "turn", "search", {"query": "expiry"})
    assert got["selected"] == 1, got
    assert got["results"][0]["results"][0]["sid"] == b
    await rejected(links.dispatch(a, "turn", "read", {"refs": [links.reference(c)]}), "outside")
    page = await links.dispatch(a, "turn", "read", {"refs": [ref], "after_seq": 1, "limit": 1})
    text = page["results"][0]["events"][0]["text"]
    offset = page["results"][0]["events"][0]["next_offset"]
    while offset is not None:
        page = await links.dispatch(a, "turn", "read", {"refs": [ref], "after_seq": 1, "limit": 1, "offset": offset})
        event = page["results"][0]["events"][0]
        text += event["text"]
        offset = event["next_offset"]
    assert text == long
    db.touch_session(b, name="Renamed")
    assert (await links.dispatch(a, "turn", "read", {"refs": [ref]}))["results"][0]["session"]["title"] == "Renamed"
    links.end_turn(a, "turn")
    await links.prepare_turn(a, "later", "What about that decision?")
    assert links._scopes[(a, "later")]["refs"] == [ref]
    await links.prepare_turn(a, "all", "@session all find expiry")
    got = await links.dispatch(a, "all", "search", {"query": "expiry"})
    assert got["selected"] == 2 and got["results"][0]["searched"] == 2
    paged = await links.dispatch(a, "all", "search", {"query": "expiry", "session_limit": 1})
    assert paged["next_session_offset"] == 1 and paged["results"][0]["searched"] == 1
    tail = await links.dispatch(a, "all", "search", {"query": "expiry", "session_limit": 1, "session_offset": 1})
    assert tail["next_session_offset"] is None and tail["results"][0]["searched"] == 1
    links.validate_persisted(db.connect())
    await rejected(links.node_call("read", {"refs": ["bad"]}), "reference")
    await rejected(links.node_call("read", {"refs": [ref], "limit": True}), "integer")
    await rejected(session_agent._dispatch({"session_id": a, "turn_id": "ended", "method": "read", "params": {}}), "no longer running")
    print("references, scope, pagination, archived, rename and ownership OK")
    return a, b, c


async def short_references():
    a, b, c = [session(name) for name in ("Short origin", "Project plan", "Project plan")]
    rb, rc = links.reference(b), links.reference(c)
    with patch.object(aliases, "_random_code", side_effect=["A7K2", "A7K2", "9QMX"]):
        codes = aliases.allocate([rb, rc])
    assert codes == {rb: "A7K2", rc: "9QMX"}
    # Concurrent callers reserve the same identity once, including other nodes.
    peer_ref = "e" * 32 + "/9"
    results = await asyncio.gather(*(asyncio.to_thread(aliases.allocate, [peer_ref]) for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert results[0][peer_ref] not in codes.values()
    data = await links.catalog(force=True)
    row = next(row for row in data["sessions"] if row["ref"] == rb)
    assert row["mention"] == "@Session-Project-plan-A7K2"
    await links.prepare_turn(a, "short", row["mention"].lower())
    assert links._scopes[(a, "short")]["refs"] == [rb]
    db.touch_session(b, name="Renamed project")
    await links.prepare_turn(a, "rename", row["mention"])
    assert links._scopes[(a, "rename")]["refs"] == [rb]
    assert aliases.allocate([rb])[rb] == "A7K2"
    await links.prepare_turn(a, "several", row["mention"] + " @Session-Whatever-9QMX")
    assert links._scopes[(a, "several")]["refs"] == [rb, rc]
    await rejected(links.prepare_turn(a, "unknown", "@Session-Missing-ZZZZ"), "unknown session ID")
    assert (a, "unknown") not in links._scopes
    assert links.load_record("session_references." + str(a))["refs"] == [rb, rc]
    await links.prepare_turn(a, "short-all", data["all_mention"])
    assert links._scopes[(a, "short-all")]["refs"] == ["all"]
    assert aliases.mention(" Żółć / 東京 & plan ", "A7K2") == "@Session-Żółć-東京-plan-A7K2"
    assert aliases.mention("***", "A7K2") == "@Session-Session-A7K2"
    # A restart reads the same durable allocation; deletion never frees its code.
    db.delete_session(b)
    assert aliases.resolve(["a7k2"]) == [rb]
    with patch.object(aliases, "_random_code", return_value="A7K2"):
        assert aliases.allocate(["d" * 32 + "/1"])["d" * 32 + "/1"] != "A7K2"
    with patch.object(aliases, "CAPACITY", len(aliases.reservations(db.connect()))):
        try:
            aliases.allocate(["d" * 32 + "/2"])
        except ValueError as exc:
            assert "reserved" in str(exc)
        else:
            raise AssertionError("exhaustion must fail")
    # A backend resolves aliases on its controller through the authenticated relay.
    remote_origin = session("Remote origin")
    controller = "f" * 32
    class Relay:
        closed = False
        async def send_json(self, message):
            assert message["method"] == "resolve" and message["params"] == {"codes": ["A7K2"]}
            assert message["sid"] == remote_origin and message["turn_id"] == "relayed"
            links._pending[message["id"]][1].set_result({"format": 1, "controller": controller, "refs": [peer_ref]})
    with patch.object(links, "_app", {"puppy_role": "backend"}), patch.dict(links._relays, {controller: Relay()}, clear=True):
        await links.prepare_turn(remote_origin, "relayed", "@Session-Project-A7K2")
    assert links._scopes[(remote_origin, "relayed")] == {"format": 1, "controller": controller, "refs": [peer_ref]}
    other_origin = session("Ambiguous origin")
    with patch.object(links, "_app", {"puppy_role": "backend"}), patch.dict(links._relays, {controller: Relay(), "c" * 32: Relay()}, clear=True):
        await rejected(links.prepare_turn(other_origin, "ambiguous", "@Session-Project-A7K2"), "several consoles")
        await rejected(links.prepare_turn(remote_origin, "ambiguous-again", "@Session-Project-A7K2"), "several consoles")
    # Invalid persisted rows are rejected without repair, including duplicate refs.
    bad_key = aliases.PREFIX + "ZZZZ"
    for raw in ('{}', '{"format":true,"ref":"all"}', json.dumps({"format": 1, "ref": rb})):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?)", (bad_key, raw))
        try:
            links.validate_persisted(db.connect())
        except ValueError:
            pass
        else:
            raise AssertionError("invalid registry accepted")
        assert db.query_one("SELECT value FROM meta WHERE key=?", (bad_key,))["value"] == raw
        db.execute("DELETE FROM meta WHERE key=?", (bad_key,))
    links.validate_persisted(db.connect())
    print("short IDs: collision/concurrency, names, rename, deletion, exhaustion, relay and exact persistence OK")


class ControlledSession:
    """Real queue control with a manually completed engine turn."""
    def __init__(self, sid):
        self.sid = sid
        self.hub = runner.hub(sid)
        self.started = []
        self.patch = patch.object(self.hub, "_start_turn", self.start)
        self.patch.start()

    def start(self, prompt):
        self.started.append(prompt)
        self.hub.status = "running"
        self.hub.interrupted = False
        self.hub._active_prompt_text = prompt
        self.hub._active_turn_id = "fake-turn-{}".format(len(self.started))
        self.seq = db.add_event(self.sid, "user", {"text": prompt})["seq"]
        actions.turn_started(self.sid, prompt, self.hub._active_turn_id, self.seq)

    def finish(self, text="Done", ok=True):
        db.add_event(self.sid, "assistant", {"text": text})
        db.add_event(self.sid, "result", {"ok": ok})
        actions.turn_finished(self.sid, self.hub._active_prompt_text, "ok" if ok else "error", self.seq)
        self.hub.status = "idle"
        self.hub._active_prompt_text = ""
        self.hub._active_turn_id = ""
        self.hub._start_queue_if_ready()


async def communication():
    a, b, c = session("Coordinator"), session("Backend"), session("Frontend")
    origin, rb, rc = links.reference(a), links.reference(b), links.reference(c)
    dest = ControlledSession(b)
    other = ControlledSession(c)
    try:
        dest.hub.send_message("Unrelated work")
        args = {"refs": [rb], "text": "Implement endpoint", "request_id": "api", "timeout_s": 100}
        sent = await actions.send(origin, args)
        assert len(dest.started) == 1 and len(dest.hub.queue) == 1
        same = await actions.send(origin, args)
        assert same["id"] == sent["id"] and len(dest.hub.queue) == 1
        await rejected(actions.send(origin, dict(args, text="Different")), "different")
        dest.finish("Unrelated answer")
        assert len(dest.started) == 2
        dest.finish("Endpoint implemented")
        done = await actions.operate(origin, "wait", {"id": sent["id"], "wait_s": 0})
        assert done["status"] == "completed", done
        assert done["results"][0]["answer"] == "Endpoint implemented", done
        receipt = actions.get(actions.INBOX_PREFIX, done["results"][0]["id"])
        payload = {key: receipt[key] for key in ("id", "source", "source_title", "target", "action", "text", "deadline", "expected_turn_id")}
        with patch.object(actions.time, "time", lambda: receipt["deadline"] + 10):
            assert (await actions.receive(payload))["status"] == "completed"
        await rejected(actions.operate(rc, "cancel", {"id": sent["id"]}), "belongs")
        # Cancelling one queued request preserves unrelated work ahead of it.
        dest.hub.send_message("Next unrelated task")
        pending = await actions.send(origin, dict(args, request_id="cancel-me"))
        original = actions._call_ref
        async def offline(ref, method, params, catalog=None):
            if method == "request_cancel":
                raise TimeoutError("destination offline")
            return await original(ref, method, params, catalog)
        with patch.object(actions, "_call_ref", offline):
            cancelled = await actions.operate(origin, "cancel", {"id": pending["id"]})
        assert cancelled["status"] == "cancelling" and dest.hub.queue
        # A normal reconciliation remembers the earlier cancellation intent.
        actions.restore_tracking()
        cancelled = await actions.operate(origin, "wait", {"id": pending["id"], "wait_s": 0})
        assert cancelled["status"] == "cancelled"
        assert (await actions.operate(origin, "wait", {"id": pending["id"], "wait_s": 0}))["status"] == "cancelled"
        assert dest.hub._active_prompt_text == "Next unrelated task" and not dest.hub.interrupted
        assert not dest.hub.queue
        dest.finish()
        # An idle engine answers a question as a queued conversational request.
        question = await actions.send(origin, {"refs": [rc], "request_id": "question", "action": "question", "text": "What did we decide?"})
        assert len(other.started) == 1 and "Request type: question" in other.started[0]
        other.finish("Use an hour")
        assert (await actions.operate(origin, "wait", {"id": question["id"], "wait_s": 0}))["status"] == "completed"
        # A lost transport reply is recovered by the original destination id.
        original = actions._call_ref
        async def lose_reply(ref, method, params, catalog=None):
            result = await original(ref, method, params, catalog)
            if method == "request":
                raise TimeoutError("reply lost")
            return result
        with patch.object(actions, "_call_ref", lose_reply):
            lost = await actions.send(origin, dict(args, request_id="lost-reply"))
        assert lost["results"][0]["status"] == "unconfirmed"
        dest.finish("Recovered answer")
        assert (await actions.operate(origin, "wait", {"id": lost["id"], "wait_s": 0}))["results"][0]["answer"] == "Recovered answer"
        # Active requests cannot form a waiting cycle.
        sent = await actions.send(origin, dict(args, request_id="cycle"))
        await rejected(actions.send(rb, {"refs": [origin], "text": "loop", "request_id": "loop"}), "cycle")
        dest.finish()
        await actions.operate(origin, "wait", {"id": sent["id"], "wait_s": 0})
        # A stale compare token refuses control without touching a newer turn.
        dest.hub.send_message("New turn")
        stale = await actions.send(origin, {"refs": [rb], "action": "stop", "request_id": "stop-stale", "expected_turn_ids": {rb: "old"}})
        assert stale["results"][0]["status"] == "rejected" and not dest.hub.interrupted
        dest.finish()
        links.validate_persisted(db.connect())
        print("communication: queue order, answers, duplicate/lost replies, cancellation, questions and cycle guards OK")
    finally:
        dest.patch.stop()
        other.patch.stop()


async def workflows():
    a, b, c, d = [session(name) for name in ("Orchestrator", "API", "UI", "Review")]
    origin = links.reference(a)
    rb, rc, rd = [links.reference(sid) for sid in (b, c, d)]
    workers = [ControlledSession(sid) for sid in (b, c, d)]
    spec = {"request_id": "plan", "title": "Build and review", "steps": [
        {"id": "api", "refs": [rb], "action": "task", "text": "Build API", "after": []},
        {"id": "ui", "refs": [rc], "action": "task", "text": "Build UI", "after": []},
        {"id": "review", "refs": [rd], "action": "task", "text": "Review both", "after": ["api", "ui"]},
    ]}
    try:
        created = await coordination.create(origin, spec, None)
        assert [len(w.started) for w in workers] == [1, 1, 0]
        again = await coordination.create(origin, spec, None)
        assert created["id"] == again["id"] and len(workers[0].started) == 1
        workers[0].finish("API result")
        await coordination.advance(created["id"])
        assert not workers[2].started
        workers[1].finish("UI result")
        # Clear process-local locks to simulate recovering only persisted ids.
        coordination._locks.clear()
        await coordination.advance(created["id"])
        assert len(workers[2].started) == 1
        assert "API result" in workers[2].started[0] and "UI result" in workers[2].started[0]
        await coordination.advance(created["id"])
        assert len(workers[2].started) == 1
        workers[2].finish("Review complete")
        async def unavailable_source(*args):
            return False
        with patch.object(actions, "_publish", unavailable_source):
            final = await coordination.advance(created["id"])
        assert final["status"] == "completed", final
        assert not actions.get(coordination.PREFIX, created["id"])["notified"]
        coordination._active.clear()
        coordination.restore_tracking()
        assert created["id"] in coordination._active
        await coordination.advance(created["id"])
        assert actions.get(coordination.PREFIX, created["id"])["notified"]
        assert created["id"] not in coordination._active
        bad = json.loads(json.dumps(spec))
        bad["request_id"] = "bad"
        bad["steps"][0]["after"] = ["review"]
        await rejected(coordination.create(origin, bad, None), "cycle")
        failed = await coordination.create(origin, dict(spec, request_id="failure"), None)
        workers[0].finish("API failed", ok=False)
        workers[1].finish("UI complete")
        result = await coordination.advance(failed["id"])
        assert result["steps"][2]["status"] == "blocked", result
        cancelled = await coordination.create(origin, dict(spec, request_id="cancel"), None)
        result = await coordination.operate(origin, "cancel_workflow", {"id": cancelled["id"]})
        assert result["status"] == "cancelled", result
        assert result["steps"][2]["status"] == "cancelled"
        links.validate_persisted(db.connect())
        print("coordination: parallel branches, dependencies, result handoff, restart identity, failure and cancellation OK")
    finally:
        for worker in workers:
            worker.patch.stop()


async def fleet_and_drivers():
    source = links.reference(session("Fleet origin"))
    peer = "a" * 32
    original = links.remote_call
    async def fake_remote(bid, method, args):
        if bid == 0:
            return await original(bid, method, args)
        if bid == 2:
            raise links.SessionLinkError("offline")
        if method == "sessions":
            return {"node": peer, "sessions": [{"ref": peer + "/9", "id": 9,
                "node": peer, "node_name": "Peer", "title": "Remote history", "cwd": "/project",
                "engine": "codex", "status": "idle", "archived": True}]}
        if method == "read":
            assert args["refs"] == [peer + "/9"]
            return {"events": [{"text": "remote answer", "source": links.source_link(peer + "/9", 2)}]}
        raise AssertionError(method)
    with patch.object(links, "_app", {"puppy_role": "full"}), \
            patch.object(links.backends, "list_backends", lambda: [{"id": 1, "name": "Peer"}, {"id": 2, "name": "Offline"}]), \
            patch.object(links, "remote_call", fake_remote):
        data = await links.catalog()
        assert any(row["ref"] == peer + "/9" and row["archived"] for row in data["sessions"])
        result = await links.broker(source, "read", {"refs": [peer + "/9"]})
        assert result["results"][0]["events"][0]["text"] == "remote answer"
        assert result["unavailable"][0]["node"] == "Offline"
    from puppy.drivers import get_driver
    descriptor = {"name": "puppy_session", "command": sys.executable,
                  "args": ["-m", "puppy.session_agent"], "env": {}, "engine_guidance": "Session policy marker"}
    row = db.get_session(int(source.split("/")[1]))
    for key in ("claude", "codex", "opencode"):
        driver = get_driver(key)
        argv = driver.build_cmd(row, True, "hello", "test-turn", session_mcp=descriptor)
        ctx = driver.turn_context(row, True, "hello", "test-turn", session_mcp=descriptor)
        env = driver.build_env(row, True, "hello", "test-turn", session_mcp=descriptor)
        assert "puppy_session" in json.dumps([argv, ctx, env]), key
        assert "Session policy marker" in json.dumps([argv, ctx, env]), key
        if key != "opencode":
            argv = driver.build_cmd(row, False, "", "tool-turn", session_mcp=descriptor, tool={"tool": "compact"})
            assert "puppy_session" not in json.dumps(argv), key
    messages = "\n".join(json.dumps({"jsonrpc": "2.0", "id": idx, "method": method,
        "params": {"protocolVersion": "2025-06-18"} if idx == 1 else {}})
        for idx, method in ((1, "initialize"), (2, "tools/list"))) + "\n"
    output = subprocess.check_output([sys.executable, "-m", "puppy.session_agent"],
                                     input=messages, text=True, timeout=10)
    replies = [json.loads(line) for line in output.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "Puppy sessions"
    assert {row["name"] for row in replies[1]["result"]["tools"]} >= {"read", "send", "coordinate", "workflow", "cancel_workflow"}
    from puppy.web import build_app
    sys.path.insert(0, str(BASE / "backend"))
    from puppy_backend.app import build_app as backend_app
    for app in (build_app(), backend_app()):
        paths = {route.resource.canonical for route in app.router.routes()}
        assert {"/api/session-links/node", "/api/session-links/action", "/api/ws/session-links"} <= paths
        assert {"session-references", "session-communication", "session-coordination"} <= set(app["puppy_capabilities"])
    print("fleet routing/coverage, all engine bridges, MCP stdio, and both runtime registrations OK")


async def main():
    config.ensure_dirs()
    await references()
    await short_references()
    await communication()
    await workflows()
    await fleet_and_drivers()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        search.close_for_tests()
        shutil.rmtree(str(ROOT))
