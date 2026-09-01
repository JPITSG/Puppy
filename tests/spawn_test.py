"""Spawned-agent surface tests against a stub engine.

Covers the one-shot driver loop (final answer, auto-denied approvals, usage,
failure and no-result paths, sliding inactivity and hard-runtime limits, and
cancel kills), request validation, node resolution, the turn-bound bridge
dispatch with its identity checks, live local/remote limit updates, sweeper
reaping, one real Unix-socket round trip, and the HTTP job routes.
No real engine is invoked, no network is reached, and no quota is spent.
"""
import asyncio
import json
import os
import sys
import tempfile
import time

TEST_ROOT = tempfile.mkdtemp(prefix="puppy-spawn-test-")
os.environ["PUPPY_DATA"] = os.path.join(TEST_ROOT, "data")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from puppy import backends, config, db, drivers, protocol, runner, \
    spawn_agent, spawn_exec  # noqa: E402
from puppy.drivers.base import Driver  # noqa: E402

STUB_PATH = os.path.join(TEST_ROOT, "stub_engine.py")
STUB_SOURCE = r'''
import json, os, sys, time

def out(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()

start = json.loads(sys.stdin.readline())
assert start.get("start") is True and start.get("prompt")
mode = os.environ.get("STUB_MODE", "ok")
if mode == "ok":
    out({"t": "model", "model": "stub-1"})
    out({"t": "a", "text": "Working on it."})
    out({"t": "approval", "id": "ap1"})
    reply = json.loads(sys.stdin.readline())
    assert reply.get("approve") is False and reply.get("msg")
    out({"t": "a", "text": "FINAL ANSWER: 42 (denied=" + reply["id"] + ")"})
    out({"t": "result", "ok": True, "usage": {"output_tokens": 7},
         "cost": 0.0125})
elif mode == "fail":
    out({"t": "result", "ok": False, "error": "stub exploded"})
elif mode == "noresult":
    out({"t": "a", "text": "partial thoughts"})
    sys.exit(3)
elif mode == "active":
    for n in range(1, 5):
        time.sleep(0.45)
        out({"t": "progress", "tokens": n})
    out({"t": "a", "text": "active run finished"})
    out({"t": "result", "ok": True})
elif mode == "activehang":
    n = 0
    while True:
        time.sleep(0.35)
        n += 1
        out({"t": "progress", "tokens": n})
elif mode == "noise":
    while True:
        time.sleep(0.25)
        out({"t": "status", "text": "Thinking..."})
elif mode == "hang":
    time.sleep(600)
'''


class FakeDriver(Driver):
    key = "fake"
    label = "Fake"
    binary = sys.executable
    uses_stdin_stream = True

    def permission_options(self):
        return [{"value": "standard", "label": "Standard", "hint": ""},
                {"value": "loose", "label": "Loose", "hint": ""}]

    def default_permission(self):
        return "standard"

    def model_options(self):
        return [{"value": "", "label": "Default", "hint": ""},
                {"value": "ok", "label": "Scripted run", "hint": ""}]

    def effort_options(self):
        return [{"value": "", "label": "Default", "hint": ""},
                {"value": "high", "label": "High", "hint": ""}]

    def build_cmd(self, session, first_turn, prompt, pinned_id,
                  browser_mcp=None, system_prompt="", terminal_mcp=None,
                  spawn_mcp=None):
        return [sys.executable, STUB_PATH]

    def build_env(self, session, first_turn, prompt, pinned_id,
                  browser_mcp=None, system_prompt="", terminal_mcp=None,
                  spawn_mcp=None):
        return {"STUB_MODE": session.get("model") or "ok"}

    def initial_stdin(self, session, prompt):
        return [{"start": True, "prompt": prompt}]

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except ValueError:
            return []
        kind = ev.get("t")
        if kind == "model":
            return [{"a": "model", "model": ev["model"]}]
        if kind == "a":
            return [{"a": "event", "kind": "assistant",
                     "data": {"text": ev["text"]}}]
        if kind == "approval":
            return [{"a": "approval", "req": {"request_id": ev["id"],
                                              "tool_name": "shell",
                                              "input": {"command": "rm"}}}]
        if kind == "progress":
            return [{"a": "transient", "msg": {
                "type": "thinking_tokens", "tokens": ev["tokens"]}}]
        if kind == "status":
            return [{"a": "transient", "msg": {
                "type": "status", "text": ev["text"]}}]
        if kind == "result":
            return [{"a": "result", "data": {
                "ok": ev["ok"], "usage": ev.get("usage"),
                "cost_usd": ev.get("cost"), "error": ev.get("error", "")}}]
        return []

    def approval_payload(self, request_id, behavior, original_input,
                         message="", updated_permissions=None, request=None):
        return {"approve": behavior == "allow", "id": request_id,
                "msg": message}


def request_for(mode, cwd, timeout_s=60, idle_timeout_s=60,
                max_runtime_s=None):
    max_runtime_s = timeout_s if max_runtime_s is None else max_runtime_s
    return {"engine": "fake", "model": mode, "effort": "",
            "permission_mode": "standard",
            "prompt": "Answer the question.", "cwd": cwd,
            "idle_timeout_s": idle_timeout_s,
            "max_runtime_s": max_runtime_s,
            "timeout_s": max_runtime_s}


async def wait_done(job, seconds=30.0):
    await asyncio.wait_for(job.done.wait(), timeout=seconds)


async def test_one_shot_paths(cwd):
    job = spawn_exec.manager().start_job(request_for("ok", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "done", (job.status, job.error)
    assert "FINAL ANSWER: 42 (denied=ap1)" in job.answer
    assert "Working on it." in job.answer
    assert job.denials == 1
    assert job.model_used == "stub-1"
    assert job.usage == {"output_tokens": 7}
    assert job.cost_usd == 0.0125
    text = spawn_exec.job_text(job.payload(), "testnode")
    assert "UNTRUSTED SPAWNED-AGENT OUTPUT" in text
    assert "1 approval(s) auto-denied" in text
    assert "7 output tokens" in text

    job = spawn_exec.manager().start_job(request_for("fail", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "failed" and "stub exploded" in job.error

    job = spawn_exec.manager().start_job(
        request_for("noresult", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "failed"
    assert "without a result" in job.error and "exit 3" in job.error
    assert "partial thoughts" in job.answer
    text = spawn_exec.job_text(job.payload(), "testnode")
    assert "Partial output before the failure:" in text

    # Silence expires the renewable lease before the hard runtime.
    job = spawn_exec.manager().start_job(
        request_for("hang", cwd, idle_timeout_s=1,
                    max_runtime_s=5), ("remote",))
    running_text = spawn_exec.job_text(job.payload(), "testnode")
    assert "inactivity limit 1s" in running_text and \
        "hard runtime 5s" in running_text and job.id in running_text
    await wait_done(job, seconds=30.0)
    assert job.status == "timeout", (job.status, job.error)
    assert "no recognized engine progress for 1s" in job.error

    # Positive progress renews the inactivity lease, allowing total runtime to
    # exceed that lease without weakening the absolute ceiling.
    job = spawn_exec.manager().start_job(
        request_for("active", cwd, idle_timeout_s=1,
                    max_runtime_s=5), ("remote",))
    await wait_done(job, seconds=30.0)
    assert job.status == "done", (job.status, job.error)
    assert job.progress_seq >= 4 and job.last_progress_kind == \
        "assistant output"
    assert "active run finished" in job.answer

    job = spawn_exec.manager().start_job(
        request_for("activehang", cwd, idle_timeout_s=1,
                    max_runtime_s=2), ("remote",))
    await wait_done(job, seconds=30.0)
    assert job.status == "timeout", (job.status, job.error)
    assert "2s hard runtime ceiling" in job.error

    # A provider cannot keep a dead run alive forever by repeating one static
    # status heartbeat; only the first changed value renews the lease.
    job = spawn_exec.manager().start_job(
        request_for("noise", cwd, idle_timeout_s=1,
                    max_runtime_s=5), ("remote",))
    await wait_done(job, seconds=30.0)
    assert job.status == "timeout", (job.status, job.error)
    assert "no recognized engine progress for 1s" in job.error
    assert job.progress_seq == 1

    # Replacing a live limit wakes a pending stdout read immediately; a newly
    # shortened deadline must not wait for the ordinary 30-second poll.
    job = spawn_exec.manager().start_job(request_for("hang", cwd), ("remote",))
    for _ in range(100):
        if job.proc is not None:
            break
        await asyncio.sleep(0.05)
    job.update_limits({"idle_timeout_s": 1})
    await wait_done(job, seconds=5.0)
    assert job.status == "timeout" and \
        "no recognized engine progress for 1s" in job.error

    job = spawn_exec.manager().start_job(request_for("hang", cwd), ("remote",))
    for _ in range(100):
        if job.proc is not None:
            break
        await asyncio.sleep(0.05)
    pid = job.proc.pid
    await spawn_exec.manager().cancel(job, "test cancel")
    assert job.status == "cancelled" and "test cancel" in job.error
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("cancelled engine process survived")
    print("one-shot execution paths ok")


async def test_validation(cwd):
    async def refused(body, needle):
        try:
            await spawn_exec.prepare_request(body)
        except spawn_exec.SpawnError as exc:
            assert needle in str(exc), (needle, str(exc))
            return
        raise AssertionError("accepted invalid request: " + needle)

    good = {"engine": "fake", "prompt": "hi", "cwd": cwd}
    prepared = await spawn_exec.prepare_request(dict(good))
    assert prepared["permission_mode"] == "standard"
    assert prepared["idle_timeout_s"] == \
        spawn_exec.DEFAULT_IDLE_TIMEOUT_S == 600
    assert prepared["max_runtime_s"] == \
        spawn_exec.DEFAULT_MAX_RUNTIME_S == 7200
    assert prepared["timeout_s"] == prepared["max_runtime_s"]
    legacy = await spawn_exec.prepare_request(dict(good, timeout_s=300))
    assert legacy["max_runtime_s"] == legacy["timeout_s"] == 300
    await refused({}, "requires an engine")
    await refused(dict(good, engine="nope"), "unknown engine")
    await refused(dict(good, effort="ultra"), "does not offer effort")
    await refused(dict(good, permission_mode="root"),
                  "does not offer permission mode")
    await refused(dict(good, prompt="  "), "non-empty prompt")
    await refused(dict(good, cwd=os.path.join(cwd, "missing")),
                  "does not exist")
    await refused(dict(good, timeout_s=1), "timeout_s must be between")
    await refused(dict(good, timeout_s="soon"), "whole number")
    await refused(dict(good, idle_timeout_s=1),
                  "idle_timeout_s must be between")
    await refused(dict(good, max_runtime_s=7201),
                  "max_runtime_s must be between")
    await refused(dict(good, max_runtime_s=300.5), "whole number")
    await refused(dict(good, timeout_s=300, max_runtime_s=600),
                  "must match")
    try:
        spawn_exec._validated_limit_update({"jobs": ["0123abcd"]})
    except spawn_exec.SpawnError as exc:
        assert "pass idle_timeout_s and/or max_runtime_s" in str(exc)
    else:
        raise AssertionError("empty limit update accepted")

    assert spawn_exec.resolve_target("")["bid"] == 0
    assert spawn_exec.resolve_target(
        config.get("instance_name"))["bid"] == 0
    db.execute("INSERT INTO backends(name,url,urls,token,protocol,"
               "capabilities,created_at) VALUES(?,?,?,?,?,?,?)",
               ("NAS.LAN", "http://192.0.2.9:1", '["http://192.0.2.9:1"]',
                "token", 1, '["spawn-exec"]', time.time()))
    remote_bid = spawn_exec.resolve_target("nas.lan")["bid"]
    assert remote_bid >= 1
    backends._mark_backend_online(remote_bid)
    try:
        spawn_exec._limits_channel(remote_bid)
    except spawn_exec.SpawnError as exc:
        assert "cannot change live" in str(exc)
    else:
        raise AssertionError("old backend offered live spawn limits")
    db.execute("UPDATE backends SET capabilities=? WHERE id=?", (
        json.dumps([protocol.SPAWN_EXEC_CAPABILITY,
                    protocol.SPAWN_LIMITS_CAPABILITY]), remote_bid))
    assert spawn_exec._limits_channel(remote_bid)["bid"] == remote_bid
    try:
        spawn_exec.resolve_target("missing-node")
    except spawn_exec.SpawnError as exc:
        assert "NAS.LAN" in str(exc)
    else:
        raise AssertionError("unknown node accepted")
    print("validation and node resolution ok")


async def test_turn_dispatch(cwd):
    sid = db.create_session("spawn test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-1"
    session = db.get_session(sid)

    try:
        await spawn_exec.start_for_turn(session, "turn-1",
                                        {"prompt": "hi", "node": "nowhere"})
    except spawn_exec.SpawnError as exc:
        assert "unknown node" in str(exc)
    else:
        raise AssertionError("unknown node accepted by spawn")

    started = await spawn_exec.start_for_turn(
        session, "turn-1", {"prompt": "One-shot question", "model": "ok",
                            "wait_s": 20})
    assert "FINAL ANSWER: 42" in started["text"], started
    # engine defaulted from the session, cwd from the session directory
    assert "fake" in started["text"] and cwd in started["text"]

    slow = await spawn_exec.start_for_turn(
        session, "turn-1", {"prompt": "hang please", "model": "hang",
                            "idle_timeout_s": 900,
                            "max_runtime_s": 3600, "wait_s": 0})
    assert "not ready yet" in slow["text"]
    job_id = None
    for job in spawn_exec.manager().jobs.values():
        if job.owner == ("turn", sid, "turn-1") and job.running:
            job_id = job.id
    assert job_id and job_id in slow["text"]

    try:
        await spawn_exec.update_limits_for_turn(
            session, "turn-2", {"jobs": [job_id], "idle_timeout_s": 1200})
    except spawn_exec.SpawnError as exc:
        assert "belongs to this turn" in str(exc)
    else:
        raise AssertionError("foreign turn changed spawn limits")
    updated = await spawn_agent._dispatch({
        "session_id": sid, "turn_id": "turn-1", "method": "update_limits",
        "params": {"jobs": [job_id], "idle_timeout_s": 1200,
                   "max_runtime_s": 5400},
    })
    assert "inactivity limit 1200s" in updated["text"] and \
        "hard runtime 5400s" in updated["text"]
    live = spawn_exec.manager().get(job_id)
    assert live.idle_timeout_s == 1200 and live.max_runtime_s == 5400

    try:
        await spawn_exec.wait_for_turn(session, "turn-2", {"job": job_id})
    except spawn_exec.SpawnError as exc:
        assert "belongs to this turn" in str(exc)
    else:
        raise AssertionError("foreign turn read a spawn job")
    waited = await spawn_exec.wait_for_turn(
        session, "turn-1", {"job": job_id, "wait_s": 1})
    assert "running for" in waited["text"]
    cancelled = await spawn_exec.cancel_for_turn(
        session, "turn-1", {"job": job_id})
    assert "Cancelled spawned agent" in cancelled["text"]
    assert spawn_exec.manager().get(job_id) is None

    # A cross-node update is relayed through the additive PATCH capability and
    # retains the originating turn's ownership check.
    remote_bid = spawn_exec.resolve_target("nas.lan")["bid"]
    remote_id = "abcdef12"
    spawn_exec.manager().remote[remote_id] = {
        "bid": remote_bid, "node": "NAS.LAN", "session_id": sid,
        "turn_id": "turn-1",
    }
    original_node_request = spawn_exec._node_request

    async def fake_node_request(channel, method, path, body=None,
                                timeout_s=60.0):
        assert channel["bid"] == remote_bid
        assert method == "PATCH" and path == "spawn/" + remote_id
        assert body == {"idle_timeout_s": 1500, "max_runtime_s": 6000}
        return {"ok": True, "job": {
            "id": remote_id, "status": "running",
            "idle_timeout_s": body["idle_timeout_s"],
            "max_runtime_s": body["max_runtime_s"],
        }}

    spawn_exec._node_request = fake_node_request
    try:
        relayed = await spawn_exec.update_limits_for_turn(
            session, "turn-1", {"jobs": [remote_id],
                                "idle_timeout_s": 1500,
                                "max_runtime_s": 6000})
    finally:
        spawn_exec._node_request = original_node_request
        spawn_exec.manager().remote.pop(remote_id, None)
    assert "NAS.LAN" in relayed["text"] and "hard runtime 6000s" in \
        relayed["text"]

    targets = await spawn_exec.targets_for_turn(session, {})
    assert config.get("instance_name") in targets["text"]
    assert "NAS.LAN" in targets["text"]
    # scope the engine listing to the stub so no real CLI probe runs here
    saved_drivers = dict(drivers._DRIVERS)
    drivers._DRIVERS.clear()
    drivers._DRIVERS["fake"] = saved_drivers["fake"]
    try:
        local_detail = await spawn_exec.targets_for_turn(
            session, {"node": config.get("instance_name")})
    finally:
        drivers._DRIVERS.clear()
        drivers._DRIVERS.update(saved_drivers)
    assert "fake" in local_detail["text"]
    assert "permission modes: standard, loose" in local_detail["text"]

    # the sweeper kills a job whose spawning turn ended
    orphan = spawn_exec.manager().start_job(
        request_for("hang", cwd), ("turn", sid, "turn-1"))
    hub._active_turn_id = ""
    hub.status = "idle"
    await spawn_exec.manager()._sweep_once()
    await wait_done(orphan)
    assert orphan.status == "cancelled"
    assert "turn that spawned this agent ended" in orphan.error

    # bridge dispatch enforces the turn identity end to end
    try:
        await spawn_agent._dispatch({"session_id": sid, "turn_id": "turn-1",
                                     "method": "targets", "params": {}})
    except spawn_agent.SpawnAgentError as exc:
        assert "no longer running" in str(exc)
    else:
        raise AssertionError("inactive turn used the spawn bridge")
    hub.status = "running"
    hub._active_turn_id = "turn-1"
    listed = await spawn_agent._dispatch(
        {"session_id": sid, "turn_id": "turn-1",
         "method": "targets", "params": {}})
    assert "Nodes reachable" in listed["text"]

    # one real Unix-socket round trip through the MCP child's client helper
    assert spawn_agent.turn_mcp(sid, "turn-1") is None
    await spawn_agent.start(None)
    descriptor = spawn_agent.turn_mcp(sid, "turn-1")
    assert descriptor and descriptor["name"] == "puppy_spawn"
    assert descriptor["env"]["PUPPY_SPAWN_TURN_ID"] == "turn-1"
    os.environ["PUPPY_SPAWN_SOCKET"] = descriptor["env"]["PUPPY_SPAWN_SOCKET"]
    os.environ["PUPPY_SPAWN_SESSION_ID"] = \
        descriptor["env"]["PUPPY_SPAWN_SESSION_ID"]
    os.environ["PUPPY_SPAWN_TURN_ID"] = descriptor["env"]["PUPPY_SPAWN_TURN_ID"]
    loop = asyncio.get_event_loop()
    over_socket = await loop.run_in_executor(
        None, spawn_agent._bridge_call, "targets", {})
    assert "Nodes reachable" in over_socket["text"]
    await spawn_agent.stop(None)
    hub.status = "idle"
    hub._active_turn_id = ""
    print("turn-bound dispatch, reaping, and bridge socket ok")


async def test_parallel(cwd):
    sid = db.create_session("fleet test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-f"
    session = db.get_session(sid)

    for bad, needle in ((0, "between 1 and"), (13, "between 1 and"),
                        ("x", "whole number")):
        try:
            await spawn_exec.start_for_turn(session, "turn-f",
                                            {"prompt": "hi", "count": bad})
        except spawn_exec.SpawnError as exc:
            assert needle in str(exc), (bad, str(exc))
        else:
            raise AssertionError("count accepted: {!r}".format(bad))

    fleet = await spawn_exec.start_for_turn(
        session, "turn-f", {"prompt": "count please", "model": "ok",
                            "count": 3, "wait_s": 25})
    assert "3 of 3 finished." in fleet["text"], fleet
    assert fleet["text"].count("=== agent ") == 3, fleet
    assert fleet["text"].count("FINAL ANSWER: 42") == 3, fleet

    slow = await spawn_exec.start_for_turn(
        session, "turn-f", {"prompt": "hang", "model": "hang", "count": 2,
                            "wait_s": 0})
    assert "0 of 2 finished." in slow["text"], slow
    assert "Still running:" in slow["text"], slow
    assert slow["text"].count("inactivity 600s") == 2, slow
    assert slow["text"].count("hard runtime 7200s") == 2, slow
    ids = [job.id for job in spawn_exec.manager().jobs.values()
           if job.owner == ("turn", sid, "turn-f") and job.running]
    assert len(ids) == 2, ids
    updated = await spawn_exec.update_limits_for_turn(
        session, "turn-f", {"jobs": ids, "idle_timeout_s": 1800,
                            "max_runtime_s": 6000})
    assert updated["text"].count("Updated spawned agent") == 2, updated
    assert all(spawn_exec.manager().get(job_id).idle_timeout_s == 1800 and
               spawn_exec.manager().get(job_id).max_runtime_s == 6000
               for job_id in ids)
    waited = await spawn_exec.wait_for_turn(
        session, "turn-f", {"jobs": ids, "wait_s": 1})
    assert "0 of 2 finished." in waited["text"], waited
    try:
        await spawn_exec.wait_for_turn(session, "turn-f",
                                       {"jobs": ids + ["zzzzzzzz"]})
    except spawn_exec.SpawnError as exc:
        assert "invalid spawned-agent job id" in str(exc)
    else:
        raise AssertionError("invalid job id accepted")
    cancelled = await spawn_exec.cancel_for_turn(
        session, "turn-f", {"jobs": ids})
    assert cancelled["text"].count("Cancelled spawned agent") == 2, cancelled
    for job_id in ids:
        assert spawn_exec.manager().get(job_id) is None

    previous_cap = spawn_exec.MAX_RUNNING_JOBS
    spawn_exec.MAX_RUNNING_JOBS = 1
    try:
        await spawn_exec.start_for_turn(
            session, "turn-f", {"prompt": "hang", "model": "hang",
                                "count": 2, "wait_s": 0})
    except spawn_exec.SpawnError as exc:
        assert "too many spawned agents" in str(exc)
    else:
        raise AssertionError("capacity cap not enforced")
    finally:
        spawn_exec.MAX_RUNNING_JOBS = previous_cap
    hub.status = "idle"
    hub._active_turn_id = ""
    print("parallel fleet start/wait/cancel and caps ok")


async def test_http_routes(cwd):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    spawn_exec.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/spawn", json={
            "engine": "fake", "prompt": "hi", "cwd": cwd, "timeout_s": 5})
        body = await response.json()
        assert response.status == 400 and "timeout_s" in body["error"]
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "ok", "prompt": "hi", "cwd": cwd,
            "wait_s": 20})
        body = await response.json()
        assert response.status == 200, body
        job = body["job"]
        assert job["status"] == "done" and "FINAL ANSWER" in job["answer"]
        response = await client.get("/api/spawn/{}".format(job["id"]))
        assert response.status == 200
        assert (await response.json())["job"]["status"] == "done"
        response = await client.patch("/api/spawn/{}".format(job["id"]),
                                      json={"idle_timeout_s": 900})
        assert response.status == 409
        response = await client.delete("/api/spawn/{}".format(job["id"]))
        assert response.status == 200
        response = await client.get("/api/spawn/{}".format(job["id"]))
        assert response.status == 404

        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "idle_timeout_s": 300, "max_runtime_s": 3600, "wait_s": 0})
        body = await response.json()
        assert response.status == 200 and body["job"]["status"] == "running", body
        live_id = body["job"]["id"]
        response = await client.patch("/api/spawn/{}".format(live_id), json={})
        assert response.status == 400
        response = await client.patch(
            "/api/spawn/{}".format(live_id),
            json={"idle_timeout_s": 1200, "max_runtime_s": 5400})
        changed = await response.json()
        assert response.status == 200, changed
        assert changed["job"]["idle_timeout_s"] == 1200
        assert changed["job"]["max_runtime_s"] == 5400
        response = await client.patch(
            "/api/spawn/{}".format(live_id), json={"max_runtime_s": 7201})
        assert response.status == 400
        response = await client.delete("/api/spawn/{}".format(live_id))
        assert response.status == 200
        response = await client.get("/api/spawn/00000000")
        assert response.status == 404
    finally:
        await client.close()
    print("spawn HTTP routes ok")


async def main():
    config.load()
    db.connect()
    with open(STUB_PATH, "w", encoding="utf-8") as handle:
        handle.write(STUB_SOURCE)
    drivers._DRIVERS["fake"] = FakeDriver()
    cwd = os.path.join(TEST_ROOT, "project")
    os.makedirs(cwd, exist_ok=True)
    try:
        await test_one_shot_paths(cwd)
        await test_validation(cwd)
        await test_turn_dispatch(cwd)
        await test_parallel(cwd)
        await test_http_routes(cwd)
    finally:
        await spawn_exec.manager().shutdown()
    print("spawn tests passed")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
