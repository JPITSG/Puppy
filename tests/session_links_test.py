#!/usr/bin/env python3
"""Session references, communication and coordination without model calls,
including a session reading and searching its own history (by ["this"] or its
own reference, past the mentions' boundary) through the events and the turns
views, and never sending anything to itself."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import shutil
import threading
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
from puppy import workers as pools


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
    page = await links.dispatch(a, "turn", "read", {"refs": [ref], "view": "events",
                                                    "after_seq": 1, "limit": 1})
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
    async def fake_remote(bid, method, args, source=""):
        if bid == 0:
            return await original(bid, method, args, source=source)
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
        result = await links.broker(source, "read", {"refs": [peer + "/9"], "view": "events"})
        assert result["results"][0]["events"][0]["text"] == "remote answer"
        # a node that cannot be reached is no news beside a session that answered
        assert "unavailable" not in result, result
        # but it is where it costs coverage: a search of every session, a
        # session not found, and the listing
        everything = await links.broker(source, "search", {"refs": ["all"], "query": "remote"})
        assert everything["unavailable"][0]["node"] == "Offline", everything
        missing = await links.broker(source, "read", {"refs": ["b" * 32 + "/4"]})
        assert "on a node not reached now (Offline)" in missing["unavailable"][0]["error"], missing
        listed = await links.broker(source, "sessions", {})
        assert listed["unavailable"][0]["node"] == "Offline", listed
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
        assert {"session-references", "session-turns-view", "session-communication",
                "session-coordination"} <= set(app["puppy_capabilities"])
    print("fleet routing/coverage, all engine bridges, MCP stdio, and both runtime registrations OK")


async def own_history():
    """A turn reads and searches its own conversation: by ["this"] or its
    own reference, whatever the mentions selected, through the events view
    and the turns view - each prompt with its turn's final answer and none of
    the tool output - while "all" stays every other session and nothing is
    ever sent to the session itself."""
    a, b, d = session("Bug hunt"), session("Helper"), session("Unmentioned")
    own, rb, rd = links.reference(a), links.reference(b), links.reference(d)
    add = lambda kind, data: db.add_event(a, kind, data)["seq"]
    p1 = add("user", {"text": "Fix the login redirect loop"})
    add("tool_use", {"tool_use_id": "t1", "tool": "Bash", "input": {"command": "npm test"}})
    add("tool_result", {"tool_use_id": "t1", "content": "noise " * 4000})
    a1 = add("assistant", {"text": "Fixed the redirect loop. I also noticed a bug: the logout button double-fires."})
    add("result", {"ok": True})
    p2 = add("user", {"text": "Look at the export timeout"})
    add("assistant", {"text": "Reading the export worker."})
    p3 = add("user", {"text": "also check the CSV", "steering": True})
    a2 = add("assistant", {"text": "The export timeout bug is still open: the worker never retries."})
    add("result", {"ok": True})
    p4 = add("user", {"text": "Try the migration"})
    add("assistant", {"text": "Starting the migration."})
    a4 = add("result", {"ok": False, "error": "engine exploded"})
    p5 = add("user", {"text": "L" * 5000})
    a5 = add("assistant", {"text": "A" * 9000})
    add("result", {"ok": True})
    p6 = add("user", {"text": "Current question"})
    a6 = add("assistant", {"text": "Working on it"})
    search.reconcile()
    await links.prepare_turn(a, "own", "scan this session's history")
    listed = (await links.dispatch(a, "own", "sessions", {}))["sessions"]
    assert listed[0]["ref"] == own and listed[0]["this"] is True, listed[0]
    assert not any(row.get("this") for row in listed[1:])
    turns = await links.dispatch(a, "own", "read", {"refs": ["this"], "view": "turns"})
    page = turns["results"][0]
    assert page["view"] == "turns" and page["session"]["title"] == "Bug hunt"
    assert page["total_prompts"] == 6 and page["next_after_seq"] is None
    rows = page["turns"]
    assert [row["prompt"]["seq"] for row in rows] == [p1, p2, p3, p4, p5, p6]
    assert rows[0]["prompt"]["text"] == "Fix the login redirect loop"
    assert rows[0]["answer"]["seq"] == a1 and rows[0]["answer"]["outcome"] == "ok"
    assert "double-fires" in rows[0]["answer"]["text"]
    assert "noise" not in json.dumps(rows), "the turns view carries no tool output"
    assert rows[1]["answer"]["seq"] == a2 and "still open" in rows[1]["answer"]["text"]
    assert rows[2]["steered"] is True and "answer" not in rows[2]
    assert rows[3]["answer"] == dict(rows[3]["answer"], seq=a4, outcome="bad", text="engine exploded")
    assert rows[4]["prompt"]["truncated"] and rows[4]["prompt"]["chars"] == 5000
    # a long text keeps its start and its end, and names the call that reads it whole
    for piece, cap, letter in ((rows[4]["prompt"], links.TURN_PROMPT_CHARS, "L"),
                               (rows[4]["answer"], links.TURN_ANSWER_CHARS, "A")):
        assert len(piece["text"]) <= cap and piece["text"].startswith(letter * 100)
        assert piece["text"].endswith(letter * 100) and "characters left out" in piece["text"]
        assert piece["whole"] == {"view": "events", "after_seq": piece["seq"] - 1, "limit": 1}
    assert "whole" not in rows[0]["prompt"], "a text shown whole needs no call"
    assert rows[4]["answer"]["seq"] == a5 and rows[4]["answer"]["chars"] == 9000
    assert rows[5]["answer"] == dict(rows[5]["answer"], seq=a6, outcome="unfinished", text="Working on it")
    assert rows[0]["answer"]["source"] == links.source_link(own, a1)
    # paged by after_seq, in pages of turns, to the same rows
    seen, after = [], 0
    while after is not None:
        got = (await links.dispatch(a, "own", "read", {"refs": ["this"], "view": "turns",
                                                       "limit": 2, "after_seq": after}))["results"][0]
        seen.extend(row["prompt"]["seq"] for row in got["turns"])
        after = got["next_after_seq"]
    assert seen == [p1, p2, p3, p4, p5, p6], seen
    # a page stops before its text budget, and goes on where it stopped
    with patch.object(links, "TURNS_BUDGET", 5000):
        cut = (await links.dispatch(a, "own", "read", {"refs": ["this"], "view": "turns"}))["results"][0]
    assert [row["prompt"]["seq"] for row in cut["turns"]] == [p1, p2, p3, p4], cut
    assert cut["next_after_seq"] == p4
    # a node's refusal is named per node, as for any read
    refused = await links.dispatch(a, "own", "read", {"refs": ["this"], "view": "turns", "before_seq": 5})
    assert "after_seq only" in json.dumps(refused["unavailable"]), refused
    await rejected(links.node_call("read", {"refs": [own], "view": "turns", "limit": 51}), "integer")
    await rejected(links.node_call("read", {"refs": [own], "view": "pages"}), "events or turns")
    # the events view and search reach it too, by ["this"] or its own reference
    events = (await links.dispatch(a, "own", "read", {"refs": [own], "view": "events",
                                                      "limit": 2}))["results"][0]
    assert [event["seq"] for event in events["events"]] == [p6, a6]
    # the turns are the default view; events' own cursors still mean events
    assert (await links.dispatch(a, "own", "read", {"refs": [own]}))["results"][0]["view"] == "turns"
    assert "events" in (await links.dispatch(a, "own", "read", {
        "refs": [own], "before_seq": a6}))["results"][0]
    assert links.read_view({"offset": 40}) == "events" and links.read_view({}) == "turns"
    found = await links.dispatch(a, "own", "search", {"refs": ["this"], "query": "double-fires"})
    assert found["results"][0]["results"][0]["sid"] == a
    # past the mentions' boundary: another session mentioned, its own still readable
    await links.prepare_turn(a, "scoped", "@Session {}:{} compare".format(db.node_uuid(), rb))
    assert (await links.dispatch(a, "scoped", "read", {"refs": ["this"], "limit": 1}))["results"]
    assert (await links.dispatch(a, "scoped", "read", {"refs": [own], "limit": 1}))["results"]
    await rejected(links.dispatch(a, "scoped", "read", {"refs": [rd]}), "outside")
    # "all" is every other session once the user selects all of them
    await links.prepare_turn(a, "every", "@session all find bugs")
    every = await links.dispatch(a, "every", "search", {"query": "bug"})
    assert own not in json.dumps(every["results"]) and every["selected"] >= 2
    # mentioned by the user, it is read with the mentions; it is never sent to
    await links.prepare_turn(a, "self", "@Session {}:{} list the open bugs".format(db.node_uuid(), own))
    assert links._scopes[(a, "self")]["refs"] == [own]
    mine = await links.dispatch(a, "self", "read", {"view": "turns", "limit": 1})
    assert mine["results"][0]["turns"][0]["prompt"]["seq"] == p1
    await rejected(links.dispatch(a, "self", "send", {"request_id": "me", "text": "hi"}),
                   "other sessions")
    await rejected(links.dispatch(a, "self", "send", {"refs": ["this"], "request_id": "me2", "text": "hi"}),
                   "cannot send a request to the originating session")
    await rejected(links.dispatch(a, "self", "send", {"refs": [own], "request_id": "me3", "text": "hi"}),
                   "cannot send a request to the originating session")
    await rejected(links.dispatch(a, "self", "coordinate", {"request_id": "w", "steps": [
        {"id": "s", "refs": [own], "action": "task", "text": "x", "after": []}]}), "originating session")
    # mentioned beside another, a send reusing the mentions goes to the other alone
    dest = ControlledSession(b)
    try:
        await links.prepare_turn(a, "both", "@Session {node}:{own} @Session {node}:{rb} ask".format(
            node=db.node_uuid(), own=own, rb=rb))
        sent = await links.dispatch(a, "both", "send", {"request_id": "implicit", "text": "Any open bugs?"})
        assert [result["target"] for result in sent["results"]] == [rb], sent
        assert len(dest.started) == 1
        dest.finish("None here")
    finally:
        dest.patch.stop()
    # a node that cannot read by turns is asked for nothing it cannot answer
    with patch.object(links.backends, "node_channel",
                      lambda bid: {"name": "Old", "capabilities": ["session-references"]}):
        await rejected(links.remote_call(5, "read", {"refs": [rd], "view": "turns"}), "cannot read by turns")
    tools = {tool["name"]: tool for tool in session_agent.TOOLS}
    assert tools["read"]["inputSchema"]["properties"]["view"]["enum"] == ["events", "turns"]
    assert '[\\"this\\"]' in json.dumps(session_agent.TOOL_INSTRUCTIONS)
    print("own history: sessions, the turns and events views, search, paging, budget, scope, all, "
          "and no requests to itself OK")


async def many_readers():
    """A turn's parallel reads, and its sub-agents', take their turns: two at a
    time, never the threads the console reads with, and one that has waited too
    long or finds too many ahead of it is told so. A search of many sessions is
    one search, and answers each one in the order they were named."""
    a = session("Reader")
    ids = [session("Read {}".format(index)) for index in range(6)]
    for index, sid in enumerate(ids):
        for number in range(index):
            db.add_event(sid, "assistant", {"text": "harvest note {} {}".format(index, number)})
    search.reconcile()
    refs = [links.reference(sid) for sid in ids]
    gate, reading = threading.Event(), []

    def slow(sid, args):
        reading.append(sid)
        gate.wait(10)
        return {"events": [], "sid": sid}
    with patch.object(links, "_events", slow):
        calls = [asyncio.ensure_future(links.node_call("read", {"refs": [ref], "view": "events"},
                                                       source="turn"))
                 for ref in refs * 3]
        for _ in range(200):
            if len(reading) == 2:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert len(reading) == 2 and links._reads.running == 2, reading
        assert pools.history.state()["running"] == 2 and not pools.history.state()["waiting"]
        # the console's own reads go past them
        assert await asyncio.wait_for(pools.history.run(lambda: "console"), 2) == "console"
        with patch.object(links, "READS_WAIT", 0.1):
            await rejected(links.node_call("read", {"refs": [refs[0]]}), "too many reads")
        with patch.object(search, "RELAYED_WAIT", 0.1):
            await rejected(links.node_call("read", {"refs": [refs[0]]}, relayed=True), "too many reads")
        gate.set()
        answers = await asyncio.gather(*calls)
    assert [answer["sid"] for answer in answers] == ids * 3
    assert links._reads.running == 0
    # many sessions, one search
    passes = []
    real = search._collect

    def counted(*args):
        passes.append(args[-1])
        return real(*args)
    with patch.object(search, "_collect", counted):
        found = await links.node_call("search", {"refs": list(reversed(refs)), "query": "harvest",
                                                 "limit": 2, "kinds": ["assistant"]}, source="turn")
    assert passes == [list(reversed(ids))], passes
    assert [row["sid"] for row in found["results"]] == list(reversed(ids))[:-1]     # the first has none
    assert [row["total"] for row in found["results"]] == [5, 4, 3, 2, 1]
    for row in found["results"]:
        assert row["session"]["ref"] == links.reference(row["sid"]) and row["ok"] is True
        assert len(row["matches"]) == min(2, row["total"]) and row["offset"] == 0
        assert all(match["source"] == links.source_link(links.reference(row["sid"]), match["seq"])
                   for match in row["matches"])
    assert found["searched"] == found["selected"] == 6 and found["next_session_offset"] is None
    later = await links.node_call("search", {"refs": refs, "query": "harvest", "limit": 2, "offset": 2})
    assert {row["sid"]: len(row["matches"]) for row in later["results"]} == \
        {ids[1]: 0, ids[2]: 0, ids[3]: 1, ids[4]: 2, ids[5]: 2}
    # a stopped or refused search reaches the turn as what was not searched
    with patch.object(search, "SEARCH_SECONDS", 0.0):
        await links.prepare_turn(a, "turn", "@Session {}:{} look".format(db.node_uuid(), refs[0]))
        answer = await links.dispatch(a, "turn", "search", {"query": "harvest"})
    assert answer["results"] == [] and answer["unavailable"][0]["refs"] == [refs[0]], answer
    assert "fewer sessions" in answer["unavailable"][0]["error"], answer
    await rejected(links.node_call("search", {"refs": refs, "query": "--"}), "at least one term")
    links.end_turn(a, "turn")
    print("many readers: lanes for reads, one pass for many sessions, and what a stopped search says")


async def agent_results():
    """What a turn's tools answer: short IDs and mentions taken as refs, pages
    that fit a tool result, sessions without the fields that say nothing to
    an agent, requests and workflows newest first with their answers bounded
    and their ends kept, and a turn's answer kept by its end."""
    a, b, c = session("Asker"), session("Writer"), session("Reviewer")
    origin, rb, rc = links.reference(a), links.reference(b), links.reference(c)
    data = await links.catalog(force=True)
    rows = {row["ref"]: row for row in data["sessions"]}
    code, mention = rows[rb]["short_id"], rows[rb]["mention"]
    for event in range(30):
        db.add_event(b, "assistant", {"text": "{} ".format(event) + "w" * 20000})
    await links.prepare_turn(a, "agent", "look around")
    # a short ID, its mention or a lower-case one is the session it names
    for ref in (code, mention, code.lower(), " " + mention + " "):
        page = await links.dispatch(a, "agent", "read", {"refs": [ref], "view": "events"})
        assert page["results"][0]["session"]["ref"] == rb, (ref, page)
    await rejected(links.dispatch(a, "agent", "read", {"refs": ["ZZZZ"]}), "unknown session ID ZZZZ")
    await rejected(links.dispatch(a, "agent", "read", {"refs": ["Session-plan"]}), "reference")
    assert links.expand_refs(["this", "all", rb.upper(), code]) == ["this", "all", rb]
    assert links.expand_refs(["ALL", "This"]) == ["all", "this"]
    # every page fits a tool result whole, a long message read through its cursor
    events = await links.dispatch(a, "agent", "read", {"refs": [rb], "view": "events"})
    assert len(json.dumps(events)) < links.EVENTS_BUDGET + 8000, len(json.dumps(events))
    assert all(len(item["text"]) <= links.EVENT_CHARS for item in events["results"][0]["events"])
    alone = await links.dispatch(a, "agent", "read", {"refs": [rb], "view": "events",
                                                       "after_seq": 1, "limit": 1})
    assert len(alone["results"][0]["events"][0]["text"]) > links.EVENT_CHARS, "a message alone reads in one page"
    turns = await links.dispatch(a, "agent", "read", {"refs": [rb]})
    assert turns["results"][0]["view"] == "turns" and len(json.dumps(turns)) < links.TURNS_BUDGET + 8000
    # sessions as an agent reads them
    listed = await links.dispatch(a, "agent", "sessions", {})
    for row in listed["sessions"] + [events["results"][0]["session"]]:
        assert not set(row) & {"side_question", "steering", "bid", "id", "node"}, row
        assert row["ref"] and row["title"]
    assert "unavailable" not in listed, "no node is unreachable here"
    # a turn's answer is its end: the earliest messages go when it runs long
    dest = ControlledSession(b)
    try:
        with patch.object(actions, "ANSWER_LIMIT", 3000):
            sent = await links.dispatch(a, "agent", "send", {
                "refs": [code], "text": "Write it up", "request_id": "write"})
            assert sent["results"][0]["target"] == rb, sent
            for part in range(5):
                db.add_event(b, "assistant", {"text": "part {} ".format(part) + "p" * 1000})
            dest.finish("CONCLUSION: ship it")
        record = actions.get(actions.OUTBOX_PREFIX, sent["id"])
        kept = await actions.operate(origin, "wait", {"id": sent["id"], "wait_s": 0})
        answer = kept["results"][0]["answer"]
        assert answer.startswith(actions.EARLIER_LEFT_OUT) and answer.endswith("CONCLUSION: ship it"), answer[-200:]
        assert "part 0 " not in answer and len(answer) <= 3000 + len(actions.EARLIER_LEFT_OUT)
        # a long answer as an agent reads it: its start and its end, and its length
        with patch.object(links, "AGENT_ANSWER_CHARS", 1500):
            waited = await links.dispatch(a, "agent", "wait", {"id": sent["id"], "wait_s": 0})
        result = waited["results"][0]
        assert result["answer"].endswith("CONCLUSION: ship it") and len(result["answer"]) <= 1500
        assert "characters left out" in result["answer"] and result["answer_chars"] == len(answer)
        assert result["source"] == links.source_link(rb, record["results"][0].get("end_seq") or 0) or \
            result["source"].startswith("/#session=" + rb)
        assert set(waited) <= {"id", "action", "status", "created_at", "deadline", "workflow", "results"}
        # listed newest first, statuses without answers
        for index in range(3):
            with patch.object(actions.time, "time", lambda index=index: 1000.0 + index):
                await links.dispatch(a, "agent", "send", {
                    "refs": [rb], "text": "later {}".format(index), "request_id": "later-{}".format(index)})
        listing = await links.dispatch(a, "agent", "requests", {})
        stamps = [item["created_at"] for item in listing["requests"]]
        assert stamps == sorted(stamps, reverse=True) and listing["total"] == 4, listing
        assert listing["requests"][0]["id"] == sent["id"], "the newest request comes first"
        assert all("answer" not in row for item in listing["requests"] for row in item["results"])
        assert listing["requests"][0]["results"][0]["answer_chars"] == len(answer)
    finally:
        dest.patch.stop()
    # workflows likewise; a dependency hands on an answer's end, not only its start
    workers = [ControlledSession(sid) for sid in (b, c)]
    try:
        for queued in list(workers[0].hub.queue):
            workers[0].hub.queue.remove(queued)
        workers[0].hub.status = "idle"
        workers[0].hub._start_queue_if_ready()
        while workers[0].hub.status == "running":
            workers[0].finish("cleared")
        made = await links.dispatch(a, "agent", "coordinate", {
            "request_id": "chain", "title": "Write then review", "steps": [
                {"id": "write", "refs": [code], "action": "task", "text": "Write", "after": []},
                {"id": "review", "refs": [rows[rc]["mention"]], "action": "task", "text": "Review",
                 "after": ["write"]}]})
        assert [step["id"] for step in made["steps"]] == ["write", "review"], made
        workers[0].finish("w" * 20000 + " FINAL VERDICT")
        await coordination.advance(made["id"])
        assert "FINAL VERDICT" in workers[1].started[-1], "the dependency's end is handed on"
        listed = await links.dispatch(a, "agent", "workflows", {})
        assert listed["workflows"][0]["id"] == made["id"] and listed["total"] >= 1
        assert all("answer" not in row for step in listed["workflows"][0]["steps"]
                   for row in step["results"])
        one = await links.dispatch(a, "agent", "workflow", {"id": made["id"]})
        assert one["steps"][0]["results"][0]["answer"].endswith("FINAL VERDICT"), one
        assert "unavailable" not in one and "source" not in one
    finally:
        for worker in workers:
            worker.patch.stop()
    links.end_turn(a, "agent")
    print("agent results: short IDs and mentions as refs, pages that fit, lean sessions, "
          "requests and workflows newest first, answers kept by their ends")


async def selection_boundary():
    """refs ["all"] is the user's selection: the sessions they mentioned,
    kept across turns until they mention others, and every other session
    only with none mentioned or after @Session-All. A ref past it is refused
    naming what was selected and how only the user widens it, the listing
    marks the selection, and "[all]" written as the notation reads as all."""
    a, b, c, d = session("Picker"), session("Chosen one"), session("Chosen two"), session("Left out")
    own, rb, rc, rd = (links.reference(x) for x in (a, b, c, d))
    for sid in (b, c, d):
        db.add_event(sid, "assistant", {"text": "boundary marker in {}".format(sid)})
    search.reconcile()
    data = await links.catalog(force=True)
    rows = {row["ref"]: row for row in data["sessions"]}
    await links.prepare_turn(a, "pick", "{} and {} compare".format(rows[rb]["mention"], rows[rc]["mention"]))
    hits = lambda got: {row["sid"] for value in got["results"] for row in value["results"]}
    for refs in (["all"], "all", "[all]", ["[all]"], ['"all"'], [" ALL "]):
        got = await links.dispatch(a, "pick", "search", {"refs": refs, "query": "boundary"})
        assert got["selected"] == 2 and hits(got) == {b, c}, (refs, got)
        assert "unavailable" not in got, got
    assert links.expand_refs(["[this]", "[all]"]) == ["this", "all"]
    # the selection outlives the turn that made it
    links.end_turn(a, "pick")
    await links.prepare_turn(a, "later", "and now the other one")
    got = await links.dispatch(a, "later", "search", {"refs": ["all"], "query": "boundary"})
    assert hits(got) == {b, c}, got
    try:
        await links.dispatch(a, "later", "read", {"refs": [rd]})
    except ValueError as exc:
        text = str(exc)
    else:
        raise AssertionError("a session outside the selection was read")
    assert "outside the sessions the user selected" in text, text
    assert rows[rd]["mention"] in text and rows[rb]["mention"] in text and rows[rc]["mention"] in text
    assert data["all_mention"] in text and "Only the user widens" in text, text
    assert rows[rd]["mention"] not in text.split("(")[1], "the refused session is not listed as selected"
    listing = await links.dispatch(a, "later", "sessions", {})
    marked = {row["ref"] for row in listing["sessions"] if row.get("selected")}
    assert marked == {rb, rc}, listing
    assert listing["sessions"][0].get("this") and "selected" not in listing["sessions"][0]
    assert data["all_mention"] in listing["selection"], listing
    # a send and a workflow stay inside it too, saying the same
    await rejected(links.dispatch(a, "later", "send", {"refs": [rd], "request_id": "out", "text": "hi"}),
                   "destination " + rows[rd]["mention"] + " is outside the sessions the user selected")
    await rejected(links.dispatch(a, "later", "coordinate", {"request_id": "wf-out", "steps": [
        {"id": "s", "refs": [rd], "action": "task", "text": "x", "after": []}]}),
        "workflow destination " + rows[rd]["mention"] + " is outside")
    assert links.selection_refs({"refs": [own, rb]}, [rb, rc, rd, own], own) == [rb]
    assert links.selection_refs({"refs": ["all"]}, [rb, own, rd], own) == [rb, rd]
    assert links.selection_refs(None, [rb, own], own) == [rb]
    # only this conversation selected: ["all"] says what it can read instead
    await links.prepare_turn(a, "self", rows[own]["mention"] + " what did we decide")
    await rejected(links.dispatch(a, "self", "search", {"refs": ["all"], "query": "boundary"}),
                   "no session but this conversation")
    await rejected(links.dispatch(a, "self", "read", {"refs": [rd]}),
                   "(none but this conversation)")
    # @Session-All, or nothing mentioned yet, selects every other session
    await links.prepare_turn(a, "every", data["all_mention"] + " look everywhere")
    got = await links.dispatch(a, "every", "search", {"refs": ["all"], "query": "boundary", "session_limit": 50})
    assert {b, c, d} <= hits(got) and a not in hits(got), got
    listing = await links.dispatch(a, "every", "sessions", {})
    assert "selection" not in listing and not any(row.get("selected") for row in listing["sessions"])
    e = session("Fresh")
    await links.prepare_turn(e, "fresh", "nothing mentioned")
    got = await links.dispatch(e, "fresh", "search", {"refs": "[all]", "query": "boundary", "session_limit": 50})
    assert {b, c, d} <= hits(got), got
    assert (await links.dispatch(e, "fresh", "read", {"refs": [rd], "view": "events"}))["results"]
    print("refs [\"all\"] is the user's selection, refused refs name it, the listing marks it")


async def main():
    config.ensure_dirs()
    await references()
    await short_references()
    await communication()
    await own_history()
    await many_readers()
    await workflows()
    await agent_results()
    await selection_boundary()
    await fleet_and_drivers()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        search.close_for_tests()
        shutil.rmtree(str(ROOT))
