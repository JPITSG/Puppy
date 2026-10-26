"""Spawned-agent surface tests against a stub engine.

Covers the one-shot driver loop (final answer, auto-denied approvals, usage,
failure and no-result paths, sliding inactivity and hard-runtime limits, and
cancel kills), request validation, node resolution, the turn-bound bridge
dispatch with its identity checks, live local/remote limit updates, sweeper
reaping, the runner's synchronous turn-end reaping and shutdown cancels,
controller-chosen relay ids (unconfirmed starts, lost jobs, retained handles,
concurrent fleet compensation), concurrent per-job cancels, settled relay
verdicts, the updater launch gate and teardown blocker, bridge frame sizing
with shortened combined results, ownership leases (lapse, renewal by
contact and by the controller's bulk round, precedence of the job's own
limits), one real Unix-socket round trip, and the HTTP job routes including
idempotent client ids. Optional replies continue a finished agent's own
conversation through a stub engine that keeps one: the next round resumed
with every earlier message, each round counted on its own in the ledger,
refusals before anything is sent, a reply waiting out the previous run's
exit (and a poll or a second reply waiting for it in turn), a turn ending or a
cancel in that wait, retention while the owner may still reply, and the
relayed path - the node's capability, the round a reply begins, a reply lost
on the way never passing the previous answer off as the new one, a node that
forgot the agent - with its route's idempotent rounds and lease renewal.
No real engine is invoked, no network is reached, and no quota is spent.
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.scratch import private_root
TEST_ROOT = str(private_root("spawn-"))
os.environ["PUPPY_DATA"] = os.path.join(TEST_ROOT, "data")

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
if mode in ("chat", "chatlinger", "chathang"):
    # a conversation the engine keeps by its own id: a resumed run (the
    # driver passes the id back) sees every earlier message
    resumed = os.environ.get("STUB_SESSION", "")
    conversation = resumed or "conv-" + os.urandom(6).hex()
    store = os.path.join(os.environ["STUB_DIR"], conversation + ".json")
    if resumed and not os.path.exists(store):
        out({"t": "result", "ok": False, "error": "no conversation " + resumed})
        sys.exit(0)
    history = json.load(open(store)) if resumed else []
    history.append(start["prompt"])
    json.dump(history, open(store, "w"))
    out({"t": "native", "id": conversation})
    if mode == "chathang" and len(history) > 1:
        time.sleep(600)
    out({"t": "a", "text": "round {}: {}".format(len(history), " | ".join(history))})
    out({"t": "result", "ok": True, "usage": {"output_tokens": len(history)}})
    if mode == "chatlinger":
        time.sleep(1.5)     # the process is still on its way out after the answer
elif mode == "ok":
    out({"t": "model", "model": "stub-1"})
    out({"t": "a", "text": "Working on it."})
    out({"t": "approval", "id": "ap1"})
    reply = json.loads(sys.stdin.readline())
    assert reply.get("approve") is False and reply.get("msg")
    out({"t": "a", "text": "FINAL ANSWER: 42 (denied=" + reply["id"] + ")"})
    out({"t": "result", "ok": True, "usage": {"output_tokens": 7}})
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
elif mode == "nofinal":
    out({"t": "result", "ok": True})
elif mode == "limit":
    out({"t": "a", "text": "partial answer cut off"})
    out({"t": "result", "ok": True, "stop_reason": "max_tokens"})
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
                {"value": "ok", "label": "Scripted run", "hint": ""},
                {"value": "chat", "label": "Conversation", "hint": ""}]

    def effort_options(self):
        return [{"value": "", "label": "Default", "hint": ""},
                {"value": "high", "label": "High", "hint": ""}]

    def build_cmd(self, session, first_turn, prompt, pinned_id,
                  browser_mcp=None, system_prompt="", terminal_mcp=None,
                  vnc_mcp=None, spawn_mcp=None):
        return [sys.executable, STUB_PATH]

    def build_env(self, session, first_turn, prompt, pinned_id,
                  browser_mcp=None, system_prompt="", terminal_mcp=None,
                  vnc_mcp=None, spawn_mcp=None):
        # a resumed run gets back the conversation id it reported
        assert first_turn == (not session.get("native_session_id"))
        return {"STUB_MODE": session.get("model") or "ok",
                "STUB_SESSION": session.get("native_session_id") or "",
                "STUB_DIR": TEST_ROOT}

    def initial_stdin(self, session, prompt):
        return [{"start": True, "prompt": prompt}]

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except ValueError:
            return []
        kind = ev.get("t")
        if kind == "native":
            return [{"a": "native_id", "id": ev["id"]}]
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
                "error": ev.get("error", ""),
                "stop_reason": ev.get("stop_reason", "")}}]
        return []

    def approval_payload(self, request_id, behavior, original_input,
                         message="", updated_permissions=None, request=None):
        return {"approve": behavior == "allow", "id": request_id,
                "msg": message}


def request_for(mode, cwd, max_runtime_s=60, idle_timeout_s=60):
    return {"engine": "fake", "model": mode, "effort": "",
            "permission_mode": "standard",
            "prompt": "Answer the question.", "cwd": cwd,
            "idle_timeout_s": idle_timeout_s,
            "max_runtime_s": max_runtime_s}


async def wait_done(job, seconds=30.0):
    await asyncio.wait_for(job.done.wait(), timeout=seconds)


async def test_one_shot_paths(cwd):
    job = spawn_exec.manager().start_job(request_for("ok", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "done", (job.status, job.error)
    assert "FINAL ANSWER: 42 (denied=ap1)" in job.answer
    assert "Working on it." not in job.answer
    assert job.denials == 1
    assert job.model_used == "stub-1"
    assert job.usage == {"output_tokens": 7}
    assert "cost_usd" not in job.payload()
    text = spawn_exec.job_text(job.payload(), "testnode")
    assert "UNTRUSTED SPAWNED-AGENT OUTPUT" in text
    assert "1 approval(s) auto-denied" in text
    assert "7 output tokens" in text

    job = spawn_exec.manager().start_job(
        request_for("nofinal", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "incomplete"
    assert "without a final answer" in job.error and not job.answer

    job = spawn_exec.manager().start_job(
        request_for("limit", cwd), ("remote",))
    await wait_done(job)
    assert job.status == "incomplete" and job.stop_reason == "max_tokens"
    assert job.answer == "partial answer cut off"
    assert job.payload()["stop_reason"] == "max_tokens"

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
    assert "timeout_s" not in prepared
    await refused(dict(good, timeout_s=300), "timeout_s is unsupported")
    await refused({}, "requires an engine")
    await refused(dict(good, engine="nope"), "unknown engine")
    await refused(dict(good, effort="ultra"), "does not offer effort")
    await refused(dict(good, permission_mode="root"),
                  "does not offer permission mode")
    await refused(dict(good, prompt="  "), "non-empty prompt")
    await refused(dict(good, cwd=os.path.join(cwd, "missing")),
                  "does not exist")
    await refused(dict(good, max_runtime_s=-1), "max_runtime_s must be between")
    await refused(dict(good, max_runtime_s="soon"), "whole number")
    await refused(dict(good, idle_timeout_s=-1),
                  "idle_timeout_s must be between")
    await refused(dict(good, max_runtime_s=spawn_exec.MAX_TIMEOUT_S + 1),
                  "max_runtime_s must be between")
    await refused(dict(good, max_runtime_s=300.5), "whole number")
    await refused(dict(good, timeout_s=300, max_runtime_s=600),
                  "timeout_s is unsupported")
    original = config.timeout_values()
    try:
        config.set_timeouts({"spawn_runtime_seconds": 21600, "spawn_idle_seconds": 0})
        inherited = await spawn_exec.prepare_request(good)
        assert inherited["max_runtime_s"] == 21600 and inherited["idle_timeout_s"] == 0
        explicit = await spawn_exec.prepare_request(dict(good, max_runtime_s=0, idle_timeout_s=1))
        assert explicit["max_runtime_s"] == 0 and explicit["idle_timeout_s"] == 1
        config.set_timeouts({"spawn_runtime_seconds": 3600})
        assert inherited["max_runtime_s"] == 21600
    finally:
        config.set_timeouts(original)
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
               ("BUILD-NODE.LAN", "http://192.0.2.9:1", '["http://192.0.2.9:1"]',
                "token", 1, '["spawn-exec"]', time.time()))
    remote_bid = spawn_exec.resolve_target("build-node.lan")["bid"]
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
        assert "BUILD-NODE.LAN (#{})".format(remote_bid) in str(exc)
    else:
        raise AssertionError("unknown node accepted")

    # a reference is a display name (any case or spacing) or the #id targets
    # shows; a name that fits more than one node is refused naming every
    # candidate, never resolved to the first match
    instance = str(config.get("instance_name"))
    assert spawn_exec.resolve_target("#0")["bid"] == 0
    assert spawn_exec.resolve_target("# {}".format(remote_bid))["name"] == \
        "BUILD-NODE.LAN"
    assert spawn_exec.resolve_target(" build-NODE.lan ")["bid"] == remote_bid
    assert spawn_exec.resolve_target("Local")["bid"] == 0
    assert spawn_exec.resolve_target("This  Node")["bid"] == 0
    assert spawn_exec.resolve_target(instance.upper())["bid"] == 0
    for bad in ("#999", "#x", "#"):
        try:
            spawn_exec.resolve_target(bad)
        except spawn_exec.SpawnError as exc:
            assert "unknown node id" in str(exc) and \
                "BUILD-NODE.LAN (#{})".format(remote_bid) in str(exc), str(exc)
        else:
            raise AssertionError("bad node id accepted: " + bad)

    def insert_backend(name):
        return db.execute(
            "INSERT INTO backends(name,url,urls,token,protocol,capabilities,"
            "created_at) VALUES(?,?,?,?,?,?,?)",
            (name, "http://192.0.2.10:1", '["http://192.0.2.10:1"]',
             "token", 1, '["spawn-exec"]', time.time()))
    twin = insert_backend("build-node.lan")
    shadow = insert_backend(instance)
    alias = insert_backend("Local")
    try:
        for name, ids in (("Build-Node.lan", (remote_bid, twin)),
                          (instance, (0, shadow)), ("local", (0, alias))):
            try:
                spawn_exec.resolve_target(name)
            except spawn_exec.SpawnError as exc:
                assert "ambiguous" in str(exc) and all(
                    "(#{})".format(bid) in str(exc) for bid in ids), str(exc)
            else:
                raise AssertionError("ambiguous node name resolved: " + name)
        assert spawn_exec.resolve_target("#{}".format(twin))["bid"] == twin
        assert spawn_exec.resolve_target("#{}".format(shadow))["bid"] == shadow
        # the controller refuses to create such collisions in the first place
        assert "already the name of backend #{}".format(remote_bid) in \
            backends.node_name_conflict("Build-Node.Lan", exclude_bid=twin)
        assert "reserved" in backends.node_name_conflict("This Node")
        assert "own instance name" in backends.node_name_conflict(
            instance.upper())
        assert "cannot start with '#'" in backends.node_name_conflict("#1")
        assert "required" in backends.node_name_conflict("  ")
        assert "already the name of backend" in \
            backends.instance_name_conflict("build-node.lan")
        assert "reserved" in backends.instance_name_conflict("local")
        assert backends.instance_name_conflict("something-new") == ""
    finally:
        db.execute("DELETE FROM backends WHERE id IN (?,?,?)",
                   (twin, shadow, alias))
    assert backends.node_name_conflict("build-node.lan", exclude_bid=remote_bid) == ""
    assert backends.node_name_conflict("fresh-name") == ""
    assert spawn_exec.resolve_target("build-node.lan")["bid"] == remote_bid
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
    remote_bid = spawn_exec.resolve_target("build-node.lan")["bid"]
    remote_id = "abcdef12"
    spawn_exec.manager().remote[remote_id] = {
        "bid": remote_bid, "node": "BUILD-NODE.LAN", "session_id": sid,
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
    assert "BUILD-NODE.LAN" in relayed["text"] and "hard runtime 6000s" in \
        relayed["text"]

    targets = await spawn_exec.targets_for_turn(session, {})
    assert "{} (#0, this session's node)".format(
        config.get("instance_name")) in targets["text"]
    assert "BUILD-NODE.LAN (#{}, online)".format(remote_bid) in targets["text"]
    assert "or its #id" in targets["text"]
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
    # the largest schema-valid prompt crosses the bridge whole, whatever its
    # script: UTF-8 frames, and the cap sized for six-byte escapes
    for prompt in ("\u6f22\u5b57" * 60000, "\x01" * spawn_exec.MAX_PROMPT_CHARS,
                   "\U0001f436" * spawn_exec.MAX_PROMPT_CHARS):
        assert len(prompt) == spawn_exec.MAX_PROMPT_CHARS
        try:
            await loop.run_in_executor(
                None, spawn_agent._bridge_call, "spawn",
                {"prompt": prompt, "node": "nowhere", "cwd": cwd})
        except spawn_agent.SpawnAgentError as exc:
            assert "unknown node" in str(exc), str(exc)[:200]
        else:
            raise AssertionError("unknown node accepted over the socket")
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


def fake_requests(behaviour: dict, calls: list):
    """A stand-in for spawn_exec._node_request driven by ``behaviour``:
    per-method callables that return a payload or raise a SpawnError."""
    async def fake(channel, method, path, body=None, timeout_s=60.0):
        calls.append({"method": method, "path": path, "body": body,
                      "timeout_s": timeout_s, "bid": channel["bid"]})
        handler = behaviour.get(method)
        if handler is None:
            raise AssertionError("unexpected {} {}".format(method, path))
        outcome = handler(path, body)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    return fake


def finished_job(job_id, answer, **extra):
    job = {"id": job_id, "status": "done", "elapsed_s": 5, "answer": answer,
           "error": "", "usage": {"output_tokens": 3}}
    job.update(extra)
    return {"ok": True, "job": job}


async def slow(seconds, outcome):
    await asyncio.sleep(seconds)
    return outcome


def running_job(job_id, **extra):
    job = {"id": job_id, "status": "running", "elapsed_s": 1,
           "idle_timeout_s": 600, "max_runtime_s": 7200,
           "idle_remaining_s": 599, "hard_remaining_s": 7199,
           "last_progress_age_s": 0, "last_progress_kind": "job started"}
    job.update(extra)
    return {"ok": True, "job": job}


def unreached():
    return spawn_exec.SpawnError("BUILD-NODE.LAN: node is unreachable", 502,
                                 unreached=True)


def set_remote_capabilities(bid, capabilities):
    db.execute("UPDATE backends SET capabilities=? WHERE id=?",
               (json.dumps(capabilities), bid))


class quiet_sweeper:
    """Keep the background sweeper out of a test that runs it by hand."""

    def __enter__(self):
        self.interval = spawn_exec.SWEEP_INTERVAL_S
        spawn_exec.SWEEP_INTERVAL_S = 3600
        self._restart()
        return self

    def __exit__(self, *_exc):
        spawn_exec.SWEEP_INTERVAL_S = self.interval
        self._restart()

    @staticmethod
    def _restart():
        mgr = spawn_exec.manager()
        if mgr._sweeper is not None:
            mgr._sweeper.cancel()
            mgr._sweeper = None
        mgr.ensure_sweeper()


def conversation_of(job):
    """Every message the stub engine's conversation for this job holds."""
    with open(os.path.join(TEST_ROOT, job.native_id + ".json"),
              encoding="utf-8") as handle:
        return json.load(handle)


def spawn_refs(job_id):
    """The ledger refs a job's runs were counted under."""
    refs = set()
    for row in db.query("SELECT value FROM meta WHERE key GLOB 'token_usage.*'"):
        refs.update(item[0] for item in json.loads(row[0])["rows"]
                    if item[0].startswith("spawn:{}:".format(job_id)))
    return refs


async def reply_refused(session, turn_id, params, needle):
    try:
        await spawn_exec.reply_for_turn(session, turn_id, params)
    except spawn_exec.SpawnError as exc:
        assert needle in str(exc), (needle, str(exc))
        return exc
    raise AssertionError("reply accepted: " + needle)


async def test_turn_end_reaping(cwd):
    """A turn's end reaps its delegates synchronously, before the runner
    moves on to the next queued prompt, and shutdown tells relayed jobs to
    stop instead of forgetting them."""
    mgr = spawn_exec.manager()
    remote_bid = spawn_exec.resolve_target("build-node.lan")["bid"]
    backends._mark_backend_online(remote_bid)
    # keep the sweeper out of this: only the runner's own hook may reap
    previous_interval = spawn_exec.SWEEP_INTERVAL_S
    spawn_exec.SWEEP_INTERVAL_S = 3600
    if mgr._sweeper is not None:
        mgr._sweeper.cancel()
        mgr._sweeper = None
    mgr.ensure_sweeper()
    original_node_request = spawn_exec._node_request

    sid = db.create_session("reap test", "fake", cwd, "active", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    session = db.get_session(sid)
    deletes = []
    behaviour = {"DELETE": lambda path, body: deletes.append(
        {"path": path, "turn_id": hub._active_turn_id,
         "status": hub.status}) or {"ok": True}}
    calls = []
    spawn_exec._node_request = fake_requests(behaviour, calls)
    try:
        hub.queue.append("second prompt")
        hub._start_turn("first prompt")
        first_task = hub.turn_task
        for _ in range(100):
            if hub._active_turn_id and hub.proc is not None:
                break
            await asyncio.sleep(0.05)
        assert hub._active_turn_id and hub.proc is not None, "turn never started"
        turn_id = hub._active_turn_id
        orphan = mgr.start_job(request_for("hang", cwd), ("turn", sid, turn_id))
        handle = spawn_exec._register_remote(session, turn_id,
                                             {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
                                             "ab12cd34")
        assert spawn_exec.turn_job_ids(sid, turn_id) == [orphan.id, "ab12cd34"]
        await asyncio.wait_for(first_task, timeout=40)
        # by the time the first turn's task is over, the delegate is dead and
        # the relayed one was told to stop - not merely scheduled for a sweep
        assert orphan.status == "cancelled", orphan.status
        assert orphan.task.done()
        assert "turn that spawned this agent ended" in orphan.error
        assert deletes == [{"path": "spawn/ab12cd34", "turn_id": "",
                            "status": "running"}], deletes
        assert "ab12cd34" not in mgr.remote
        assert spawn_exec.turn_job_ids(sid, turn_id) == []
        for _ in range(400):
            if hub.status == "idle" and not hub.queue:
                break
            await asyncio.sleep(0.05)
        assert hub.status == "idle" and not hub.queue, (hub.status, hub.queue)

        # an unreachable node keeps the handle: the turn's end tried once, the
        # sweeper retries at its own pace and lets go once acknowledged
        calls.clear()
        behaviour["DELETE"] = lambda path, body: unreached()
        handle = spawn_exec._register_remote(
            session, "turn-e", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "ab12cd35")
        await spawn_exec.end_turn(sid, "turn-e")
        assert mgr.remote.get("ab12cd35") is handle
        assert handle["retry_at"] > time.monotonic() and \
            not handle["abandoning"]
        assert len(calls) == 1 and calls[0]["method"] == "DELETE"
        await mgr._sweep_once()          # too early to retry
        assert len(calls) == 1
        handle["retry_at"] = 0.0
        behaviour["DELETE"] = lambda path, body: spawn_exec.SpawnError(
            "unknown spawn job", 404)   # the node answered: gone for good
        await mgr._sweep_once()
        for _ in range(20):
            if "ab12cd35" not in mgr.remote:
                break
            await asyncio.sleep(0.05)
        assert "ab12cd35" not in mgr.remote and len(calls) == 2
        # a handle nobody could cancel is dropped only once the job cannot
        # possibly be alive any more on the node's own limits
        stale = spawn_exec._register_remote(
            session, "turn-e", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "ab12cd36")
        stale["registered_clock"] = time.monotonic() - \
            spawn_exec.ABANDON_GIVE_UP_S - 1
        calls.clear()
        await mgr._sweep_once()
        assert "ab12cd36" not in mgr.remote and calls == []

        # A long unlimited relay cannot be forgotten as soon as its turn ends:
        # the lease can only expire after its LAST renewal, not job creation.
        behaviour["DELETE"] = lambda path, body: unreached()
        long_job = spawn_exec._register_remote(
            session, "turn-e", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "ab12cd37", lease=True)
        long_job["registered_clock"] -= 100000
        await spawn_exec.end_turn(sid, "turn-e")
        assert long_job["abandoned_clock"] > time.monotonic() - 5
        long_job["retry_at"] = 0
        await mgr._sweep_once()
        assert mgr.remote.get("ab12cd37") is long_job
        await asyncio.sleep(0.05)
        long_job["abandoned_clock"] -= spawn_exec.ABANDON_GIVE_UP_S + 1
        long_job["retry_at"] = 0
        await mgr._sweep_once()
        assert "ab12cd37" not in mgr.remote

        # shutdown: local jobs killed and relayed ones cancelled (concurrently,
        # bounded) before the handles are forgotten
        behaviour["DELETE"] = lambda path, body: {"ok": True}
        calls.clear()
        local = mgr.start_job(request_for("hang", cwd), ("turn", sid, "turn-s"))
        spawn_exec._register_remote(session, "turn-s",
                                    {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
                                    "ab12cd37")
        spawn_exec._register_remote(session, "turn-s",
                                    {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
                                    "ab12cd38")
        started = time.monotonic()
        await mgr.shutdown()
        assert time.monotonic() - started < spawn_exec.SHUTDOWN_GRACE_S
        assert local.status == "cancelled" and local.task.done()
        assert sorted(call["path"] for call in calls) == \
            ["spawn/ab12cd37", "spawn/ab12cd38"], calls
        assert mgr.remote == {} and mgr.jobs == {}

        # a caller cancelled while it waits for a job's end is itself
        # cancelled, not silently satisfied by the job's own cancellation
        victim = spawn_exec.SpawnJob(request_for("hang", cwd),
                                     ("turn", sid, "turn-c"))

        async def stubborn():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(1.0)   # a slow teardown
                raise
        victim.task = asyncio.ensure_future(stubborn())
        mgr.jobs[victim.id] = victim
        canceller = asyncio.ensure_future(mgr.cancel(victim, "stop"))
        await asyncio.sleep(0.1)
        assert victim.task.cancelled() is False and not victim.task.done()
        canceller.cancel()
        try:
            await canceller
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("caller cancellation was swallowed")
        await asyncio.wait({victim.task})
        assert victim.task.cancelled()
        victim._finish("cancelled", "test over")
        mgr.jobs.pop(victim.id, None)
    finally:
        spawn_exec._node_request = original_node_request
        spawn_exec.SWEEP_INTERVAL_S = previous_interval
        hub.status = "idle"
        hub._active_turn_id = ""
    print("turn-end reaping, retry, and shutdown ok")


async def test_relay_start(cwd):
    """Relayed starts are tracked before they are transmitted."""
    mgr = spawn_exec.manager()
    remote_bid = spawn_exec.resolve_target("build-node.lan")["bid"]
    backends._mark_backend_online(remote_bid)
    capable = [protocol.SPAWN_EXEC_CAPABILITY, protocol.SPAWN_LIMITS_CAPABILITY,
               protocol.SPAWN_CLIENT_IDS_CAPABILITY, protocol.SPAWN_OWNER_LEASE_CAPABILITY]
    set_remote_capabilities(remote_bid, capable)
    sid = db.create_session("relay test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-r"
    session = db.get_session(sid)
    calls = []
    behaviour = {}
    original_node_request = spawn_exec._node_request
    spawn_exec._node_request = fake_requests(behaviour, calls)
    params = {"prompt": "remote work", "node": "build-node.lan", "cwd": cwd,
              "wait_s": 30}
    try:
        # the node honors the controller's id and the handle keeps it
        behaviour["POST"] = lambda path, body: running_job(body["job_id"])
        started = await spawn_exec.start_for_turn(session, "turn-r", params)
        post = calls[-1]
        assert post["method"] == "POST" and post["body"]["job_id"]
        assert "idle_timeout_s" not in post["body"] and "max_runtime_s" not in post["body"]
        assert post["body"]["wait_s"] == spawn_exec.REMOTE_START_WAIT_S
        assert post["timeout_s"] <= 45, post["timeout_s"]
        confirmed_id = post["body"]["job_id"]
        assert confirmed_id in started["text"] and "not ready yet" in \
            started["text"]
        assert confirmed_id in mgr.remote and \
            mgr.remote[confirmed_id]["turn_id"] == "turn-r"

        # the answer never arrives: the start is reported unconfirmed, still
        # under an id the turn can wait for
        behaviour["POST"] = lambda path, body: unreached()
        unconfirmed = await spawn_exec.start_for_turn(session, "turn-r", params)
        pending_id = calls[-1]["body"]["job_id"]
        assert pending_id in unconfirmed["text"], unconfirmed
        assert "not confirmed" in unconfirmed["text"], unconfirmed
        assert pending_id in mgr.remote
        assert spawn_agent._timeout_message(
            {"session_id": sid, "turn_id": "turn-r"}).count(pending_id) == 1
        assert confirmed_id in spawn_agent._timeout_message(
            {"session_id": sid, "turn_id": "turn-r"})
        assert "if an agent was started" in spawn_agent._timeout_message(
            {"session_id": sid, "turn_id": "turn-none"})
        # ... and a node that answers "no such job" ends it for good
        behaviour["GET"] = lambda path, body: spawn_exec.SpawnError(
            "unknown spawn job", 404)
        lost = await spawn_exec.wait_for_turn(
            session, "turn-r", {"jobs": [pending_id], "wait_s": 1})
        assert "Status: lost" in lost["text"] and "no spawned agent" in \
            lost["text"], lost
        # the verdict settles on the handle: readable again, no longer
        # outstanding, and dropped at the turn's end without a round trip
        assert mgr.remote[pending_id]["final"]["status"] == "lost"
        assert pending_id not in spawn_exec.turn_job_ids(sid, "turn-r")
        calls.clear()
        again = await spawn_exec.wait_for_turn(
            session, "turn-r", {"jobs": [pending_id], "wait_s": 1})
        assert "Status: lost" in again["text"] and calls == []
        mgr.remote.pop(pending_id)
        # whereas an unreachable node keeps the last known state and the handle
        behaviour["GET"] = lambda path, body: unreached()
        still = await spawn_exec.wait_for_turn(
            session, "turn-r", {"jobs": [confirmed_id], "wait_s": 1})
        assert "status unavailable" in still["text"] and confirmed_id in \
            mgr.remote

        # a refusal the node itself answered leaves nothing to track
        behaviour["POST"] = lambda path, body: spawn_exec.SpawnError(
            "this node is already running too many spawned agents", 429)
        before = set(mgr.remote)
        try:
            await spawn_exec.start_for_turn(session, "turn-r", params)
        except spawn_exec.SpawnError as exc:
            assert "too many" in str(exc)
        else:
            raise AssertionError("refused start reported as success")
        assert set(mgr.remote) == before

        # a cancel the node did not acknowledge keeps the handle for retry
        behaviour["DELETE"] = lambda path, body: unreached()
        try:
            await spawn_exec.cancel_for_turn(session, "turn-r",
                                             {"jobs": [confirmed_id]})
        except spawn_exec.SpawnError:
            pass
        else:
            raise AssertionError("failed cancel reported as success")
        assert confirmed_id in mgr.remote
        behaviour["DELETE"] = lambda path, body: spawn_exec.SpawnError(
            "unknown spawn job", 404)
        cancelled = await spawn_exec.cancel_for_turn(
            session, "turn-r", {"jobs": [confirmed_id]})
        assert "Cancelled spawned agent" in cancelled["text"]
        assert confirmed_id not in mgr.remote

        # cancels run concurrently and report per job: a node that cannot be
        # reached keeps that handle (retried at the turn's end) while the
        # others are released, and one slow node never holds the rest
        for job_id in ("cc000001", "cc000002", "cc000003"):
            spawn_exec._register_remote(session, "turn-r",
                                        {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
                                        job_id)
        behaviour["DELETE"] = lambda path, body: (
            unreached() if path.endswith("cc000002")
            else slow(0.4, {"ok": True}))
        started_at = time.monotonic()
        mixed = await spawn_exec.cancel_for_turn(
            session, "turn-r", {"jobs": ["cc000001", "cc000002", "cc000003"]})
        assert time.monotonic() - started_at < 0.75
        assert "Cancelled spawned agent cc000001 on BUILD-NODE.LAN." in mixed["text"]
        assert "Could not cancel spawned agent cc000002" in mixed["text"]
        assert "Cancelled spawned agent cc000003 on BUILD-NODE.LAN." in mixed["text"]
        assert "cc000002" in mgr.remote and "cc000001" not in mgr.remote \
            and "cc000003" not in mgr.remote
        mgr.remote.pop("cc000002")

        # a relayed verdict stays readable for the rest of the turn without
        # another round trip, so a shortened combined result can be re-read
        # one agent at a time
        spawn_exec._register_remote(session, "turn-r",
                                    {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
                                    "dd000001")
        behaviour["GET"] = lambda path, body: finished_job(
            "dd000001", "the remote verdict")
        calls.clear()
        first_read = await spawn_exec.wait_for_turn(
            session, "turn-r", {"jobs": ["dd000001"], "wait_s": 1})
        assert "the remote verdict" in first_read["text"]
        assert len(calls) == 1 and "dd000001" in mgr.remote
        assert mgr.remote["dd000001"]["final"]["status"] == "done"
        second_read = await spawn_exec.wait_for_turn(
            session, "turn-r", {"jobs": ["dd000001"], "wait_s": 1})
        assert "the remote verdict" in second_read["text"]
        assert len(calls) == 1
        assert spawn_exec.turn_job_ids(sid, "turn-r").count("dd000001") == 0
        calls.clear()
        await spawn_exec.end_turn(sid, "turn-r")
        assert "dd000001" not in mgr.remote and calls == []

        # a fleet with one failed start cancels the rest concurrently
        counter = {"n": 0}

        def fleet_post(path, body):
            counter["n"] += 1
            if counter["n"] == 2:
                return spawn_exec.SpawnError("capacity", 429)
            return running_job(body["job_id"])
        behaviour["POST"] = fleet_post
        behaviour["DELETE"] = lambda path, body: {"ok": True}
        calls.clear()
        try:
            await spawn_exec.start_for_turn(
                session, "turn-r", dict(params, count=3))
        except spawn_exec.SpawnError as exc:
            assert "capacity" in str(exc)
        else:
            raise AssertionError("partial fleet reported as success")
        posted = [call["body"]["job_id"] for call in calls
                  if call["method"] == "POST"]
        deleted = sorted(call["path"] for call in calls
                         if call["method"] == "DELETE")
        assert len(posted) == 3 and len(deleted) == 2, calls
        assert all(job_id not in mgr.remote for job_id in posted)

        # the controller asks a lease-capable node for a lease, renews every
        # live handle of an active turn in one bulk request per node, leaves
        # ended turns and settled verdicts alone, and settles ids the node
        # answered it never had
        leased_caps = capable + [protocol.SPAWN_OWNER_LEASE_CAPABILITY]
        set_remote_capabilities(remote_bid, leased_caps)
        behaviour["POST"] = lambda path, body: running_job(body["job_id"])
        calls.clear()
        await spawn_exec.start_for_turn(session, "turn-r", params)
        leased_id = calls[-1]["body"]["job_id"]
        assert calls[-1]["body"]["lease_s"] == spawn_exec.REMOTE_LEASE_S
        assert mgr.remote[leased_id]["lease"] is True
        ended = spawn_exec._register_remote(
            session, "turn-gone", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "1ea5e0aa", lease=True)
        old_lost = spawn_exec._register_remote(
            session, "turn-r", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "1ea5e0bb", lease=True)
        old_lost["registered_clock"] -= 120
        fresh_unknown = spawn_exec._register_remote(
            session, "turn-r", {"bid": remote_bid, "name": "BUILD-NODE.LAN"},
            "1ea5e0cc", lease=True)
        renewals = []

        def renew(path, body):
            assert path == "spawn/renew"
            renewals.append(sorted(body["jobs"]))
            return {"ok": True, "renewed": [leased_id], "finished": [],
                    "starting": [], "unknown": ["1ea5e0bb", "1ea5e0cc"]}
        behaviour["POST"] = renew
        # the ended turn's handle is the sweeper's to cancel (here on a node
        # it cannot reach), never the renewal round's to keep alive
        behaviour["DELETE"] = lambda path, body: unreached()
        mgr._next_renew = 0.0
        await mgr._sweep_once()
        await asyncio.wait_for(mgr._renew_task, timeout=5)
        assert renewals == [sorted([leased_id, "1ea5e0bb", "1ea5e0cc"])], \
            renewals
        assert old_lost["final"]["status"] == "lost"
        assert fresh_unknown["final"] is None     # its start may be in flight
        for _ in range(20):
            if not ended["abandoning"]:
                break
            await asyncio.sleep(0.05)
        assert ended["final"] is None and "1ea5e0aa" in mgr.remote
        assert ended["retry_at"] > time.monotonic()
        assert mgr._next_renew > time.monotonic()
        await mgr._sweep_once()                   # not due again yet
        assert len(renewals) == 1
        # an unreachable node is left for its own lease to time out
        behaviour["POST"] = lambda path, body: unreached()
        mgr._next_renew = 0.0
        await mgr._sweep_once()
        await asyncio.wait_for(mgr._renew_task, timeout=5)
        assert mgr.remote[leased_id]["final"] is None
        for job_id in (leased_id, "1ea5e0aa", "1ea5e0bb", "1ea5e0cc"):
            mgr.remote.pop(job_id, None)
        set_remote_capabilities(remote_bid, capable)
        behaviour["POST"] = lambda path, body: running_job(body["job_id"])
        await spawn_exec.start_for_turn(session, "turn-r", params)
        assert calls[-1]["body"]["lease_s"] == spawn_exec.REMOTE_LEASE_S
        assert mgr.remote[calls[-1]["body"]["job_id"]]["lease"] is True
        mgr.remote.pop(calls[-1]["body"]["job_id"])

        # A response cannot substitute its own job identity. Keep the issued
        # handle so turn cleanup still owns the uncertain request.
        behaviour["POST"] = lambda path, body: running_job("deadbeef")
        try:
            await spawn_exec.start_for_turn(session, "turn-r", params)
        except spawn_exec.SpawnError as exc:
            assert "unexpected" in str(exc)
        else:
            raise AssertionError("accepted a substituted remote job id")
        issued = calls[-1]["body"]["job_id"]
        assert issued in mgr.remote and "deadbeef" not in mgr.remote
        mgr.remote.pop(issued)
    finally:
        spawn_exec._node_request = original_node_request
        set_remote_capabilities(remote_bid, capable)
        for job_id in list(mgr.remote):
            if mgr.remote[job_id]["session_id"] == sid:
                mgr.remote.pop(job_id)
        hub.status = "idle"
        hub._active_turn_id = ""
    print("relayed start tracking, lost jobs, and fleet compensation ok")


async def test_replies(cwd):
    """A finished agent's own conversation continues only when its turn
    replies: the engine resumes it with every earlier message, each round is
    a run of its own, and what cannot take a reply refuses before anything is
    sent."""
    mgr = spawn_exec.manager()
    sid = db.create_session("reply test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-c"
    session = db.get_session(sid)

    def started_with(prompt):
        return [job for job in mgr.jobs.values()
                if job.owner == ("turn", sid, "turn-c") and job.native_id and
                conversation_of(job)[0] == prompt]

    with quiet_sweeper():
        try:
            assert any(tool["name"] == "reply" and
                       tool["inputSchema"]["required"] == ["jobs", "message"]
                       for tool in spawn_agent.TOOLS)
            first = await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "Remember kiwi.", "model": "chat",
                                    "wait_s": 20})
            assert "round 1: Remember kiwi." in first["text"], first
            assert "(round" not in first["text"]
            job = started_with("Remember kiwi.")[0]
            assert job.native_id.startswith("conv-") and job.round == 1
            assert job.payload()["round"] == 1 and job.payload()["replyable"]
            first_ref = "spawn:{}:{}".format(job.id, int(job.created_at))
            await asyncio.wait({job.task}, timeout=10)
            # taking its time over the first round costs the next one nothing:
            # each round's runtime counts from its own start
            job.created_clock -= 100000

            # the reply resumes the same conversation, through the bridge, and
            # its answer is collected like wait's
            replied = await spawn_agent._dispatch({
                "session_id": sid, "turn_id": "turn-c", "method": "reply",
                "params": {"jobs": [job.id], "message": "Which fruit?",
                           "wait_s": 20}})
            assert "Spawned agent {} on {} (round 2)".format(
                job.id, config.get("instance_name")) in replied["text"], replied
            assert "round 2: Remember kiwi. | Which fruit?" in replied["text"]
            assert job.round == 2 and job.status == "done", job.status
            assert job.usage == {"output_tokens": 2}
            assert conversation_of(job) == ["Remember kiwi.", "Which fruit?"]
            assert spawn_refs(job.id) == {first_ref, "spawn:{}:{}#2".format(
                job.id, int(job.created_at))}, spawn_refs(job.id)
            again = await spawn_exec.wait_for_turn(
                session, "turn-c", {"jobs": [job.id], "wait_s": 1})
            assert "round 2: Remember kiwi. | Which fruit?" in again["text"]

            # what this node can tell refuses the call before anything is sent
            await reply_refused(session, "turn-c",
                                {"jobs": [job.id], "message": "  "},
                                "non-empty message")
            await reply_refused(session, "turn-c", {"jobs": [job.id]},
                                "non-empty message")
            await reply_refused(
                session, "turn-c",
                {"jobs": [job.id],
                 "message": "x" * (spawn_exec.MAX_PROMPT_CHARS + 1)},
                "cannot exceed")
            await reply_refused(session, "turn-other",
                                {"jobs": [job.id], "message": "hi"},
                                "belongs to this turn")
            await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "One-shot question",
                                    "model": "ok", "wait_s": 20})
            plain = [item for item in mgr.jobs.values()
                     if item.owner == ("turn", sid, "turn-c") and
                     item.model == "ok"][0]
            assert plain.payload()["replyable"] is False
            await reply_refused(session, "turn-c",
                                {"jobs": [plain.id], "message": "More?"},
                                "has no conversation to continue")
            await reply_refused(session, "turn-c",
                                {"jobs": [job.id, plain.id], "message": "More?"},
                                "has no conversation to continue")
            assert job.round == 2 and conversation_of(job) == \
                ["Remember kiwi.", "Which fruit?"]

            # an agent still working takes no reply until its answer is in
            await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "Start.", "model": "chathang",
                                    "wait_s": 20})
            hanging = started_with("Start.")[0]
            assert hanging.status == "done"
            busy = await spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [hanging.id], "message": "Go on.",
                                    "wait_s": 1})
            assert "(round 2)" in busy["text"] and "Status: running" in \
                busy["text"], busy
            assert hanging.payload()["hard_remaining_s"] > 7000
            await reply_refused(session, "turn-c",
                                {"jobs": [hanging.id], "message": "And?"},
                                "still working on its answer")
            assert "Cancelled spawned agent" in (await spawn_exec.cancel_for_turn(
                session, "turn-c", {"jobs": [hanging.id]}))["text"]
            assert hanging.status == "cancelled"
            await reply_refused(session, "turn-c",
                                {"jobs": [hanging.id], "message": "And?"},
                                "belongs to this turn")

            # a reply waits out the previous run's exit, so the engine resumes
            # a complete conversation; meanwhile a second reply is refused and
            # a wait follows the reply to the round it begins
            await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "Note mango.",
                                    "model": "chatlinger", "wait_s": 20})
            linger = started_with("Note mango.")[0]
            assert linger.status == "done" and not linger.task.done()
            began = time.monotonic()
            pending = asyncio.ensure_future(spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [linger.id],
                                    "message": "Which fruit?", "wait_s": 20}))
            await asyncio.sleep(0.2)
            assert linger.replying and linger.round == 1
            await reply_refused(session, "turn-c",
                                {"jobs": [linger.id], "message": "Twice?"},
                                "still working on its answer")
            waited = await spawn_exec.wait_for_turn(
                session, "turn-c", {"jobs": [linger.id], "wait_s": 20})
            assert "round 2: Note mango. | Which fruit?" in waited["text"], \
                waited
            answered = await pending
            assert time.monotonic() - began >= 1.0
            assert "round 2: Note mango. | Which fruit?" in answered["text"]

            # the turn ending while a reply waits ends that reply
            pending = asyncio.ensure_future(spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [linger.id], "message": "Lost?",
                                    "wait_s": 20}))
            await asyncio.sleep(0.2)
            assert linger.replying
            hub._active_turn_id = ""
            try:
                await pending
            except spawn_exec.SpawnError as exc:
                assert "has ended" in str(exc), str(exc)
            else:
                raise AssertionError("a reply began after its turn ended")
            hub._active_turn_id = "turn-c"
            assert linger.round == 2 and not linger.replying

            # and so does a cancel
            third = await spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [linger.id], "message": "Third?",
                                    "wait_s": 20})
            assert "round 3: Note mango. | Which fruit? | Third?" in \
                third["text"], third
            pending = asyncio.ensure_future(spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [linger.id], "message": "Fourth?",
                                    "wait_s": 20}))
            await asyncio.sleep(0.2)
            assert linger.replying
            await spawn_exec.cancel_for_turn(session, "turn-c",
                                             {"jobs": [linger.id]})
            try:
                await pending
            except spawn_exec.SpawnError as exc:
                assert "was cancelled" in str(exc), str(exc)
            else:
                raise AssertionError("a cancelled agent took a reply")
            assert linger.round == 3 and mgr.get(linger.id) is None
            await asyncio.wait({linger.task}, timeout=10)
            assert conversation_of(linger) == \
                ["Note mango.", "Which fruit?", "Third?"]

            # several agents take one message, each in its own conversation
            await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "Pick a number.", "model": "chat",
                                    "count": 2, "wait_s": 20})
            pair = started_with("Pick a number.")
            assert len(pair) == 2
            both = await spawn_exec.reply_for_turn(
                session, "turn-c", {"jobs": [item.id for item in pair],
                                    "message": "Explain it.", "wait_s": 20})
            assert "2 of 2 finished." in both["text"], both
            assert both["text"].count(
                "round 2: Pick a number. | Explain it.") == 2, both
            assert len({item.native_id for item in pair}) == 2

            # one agent that cannot take it is named ahead of the answers of
            # those that did
            await spawn_exec.start_for_turn(
                session, "turn-c", {"prompt": "Name a tree.",
                                    "model": "chatlinger", "wait_s": 20})
            slow_exit = started_with("Name a tree.")[0]
            quick = started_with("Remember kiwi.")[0]
            settle = spawn_exec.REPLY_SETTLE_S
            spawn_exec.REPLY_SETTLE_S = 0.2
            try:
                mixed = await spawn_exec.reply_for_turn(
                    session, "turn-c", {"jobs": [slow_exit.id, quick.id],
                                        "message": "Why?", "wait_s": 20})
            finally:
                spawn_exec.REPLY_SETTLE_S = settle
            assert mixed["text"].startswith(
                "Could not reply to spawned agent {}: spawned agent '{}' is "
                "still shutting down".format(slow_exit.id, slow_exit.id)), mixed
            assert "round 3: Remember kiwi. | Which fruit? | Why?" in \
                mixed["text"], mixed
            assert slow_exit.round == 1 and not slow_exit.replying
            await reply_refused(
                session, "turn-c", {"jobs": [slow_exit.id, hanging.id],
                                    "message": "?"}, "belongs to this turn")

            # a finished agent stays while its turn may still reply, and goes
            # the usual way once the turn is over
            await asyncio.wait({slow_exit.task}, timeout=10)
            quick.finished_at = time.time() - spawn_exec.PURGE_AFTER_S - 5
            await mgr._sweep_once()
            assert mgr.get(quick.id) is quick
            hub._active_turn_id = ""
            hub.status = "idle"
            await mgr._sweep_once()
            assert mgr.get(quick.id) is None
        finally:
            hub.status = "idle"
            hub._active_turn_id = ""
            for item in list(mgr.jobs.values()):
                if item.owner[:2] == ("turn", sid):
                    await mgr.cancel(item, "test over")
                    mgr.jobs.pop(item.id, None)
    print("optional replies to local agents ok")


async def test_relay_replies(cwd):
    """A relayed agent is replied to through its node under the round the
    reply begins; a reply lost on the way never passes the previous answer
    off as the new one."""
    mgr = spawn_exec.manager()
    remote_bid = spawn_exec.resolve_target("build-node.lan")["bid"]
    backends._mark_backend_online(remote_bid)
    capable = [protocol.SPAWN_EXEC_CAPABILITY, protocol.SPAWN_LIMITS_CAPABILITY,
               protocol.SPAWN_CLIENT_IDS_CAPABILITY,
               protocol.SPAWN_OWNER_LEASE_CAPABILITY]
    set_remote_capabilities(remote_bid, capable)
    sid = db.create_session("relay reply test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-y"
    session = db.get_session(sid)
    node = {"bid": remote_bid, "name": "BUILD-NODE.LAN"}
    calls, behaviour = [], {}
    original_node_request = spawn_exec._node_request
    spawn_exec._node_request = fake_requests(behaviour, calls)

    def answered(job_id, answer, round_=1, replyable=True):
        handle = spawn_exec._register_remote(session, "turn-y", node, job_id,
                                             lease=True)
        handle["final"] = finished_job(job_id, answer, round=round_,
                                       replyable=replyable)["job"]
        handle["finished_at"] = time.time()
        return handle

    def reply_post(path, body):
        assert path.startswith("spawn/") and path.endswith("/reply"), path
        return running_job(path.split("/")[1], round=body["round"])

    def ask(job_ids, message, wait_s=5):
        return spawn_exec.reply_for_turn(
            session, "turn-y", {"jobs": job_ids, "message": message,
                                "wait_s": wait_s})

    with quiet_sweeper():
        mgr._next_renew = time.monotonic() + 3600
        try:
            handle = answered("ae000001", "first answer")
            # a node from before replies is named and sent nothing
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000001"], "message": "Why?"},
                                "upgrade its Puppy backend")
            assert calls == []
            set_remote_capabilities(
                remote_bid, capable + [protocol.SPAWN_REPLY_CAPABILITY])

            behaviour["POST"] = reply_post
            behaviour["GET"] = lambda path, body: finished_job(
                "ae000001", "second answer", round=2, replyable=True)
            replied = await ask(["ae000001"], "Why?")
            assert calls[0]["method"] == "POST" and \
                calls[0]["path"] == "spawn/ae000001/reply"
            assert calls[0]["body"] == {"message": "Why?", "round": 2,
                                        "wait_s": 0}, calls[0]
            assert calls[0]["timeout_s"] <= 45
            assert calls[1]["method"] == "GET" and \
                calls[1]["path"] == "spawn/ae000001?wait_s=5", calls[1]
            assert "Spawned agent ae000001 on BUILD-NODE.LAN (round 2)" in \
                replied["text"] and "second answer" in replied["text"], replied
            assert handle["round"] == 2 and \
                handle["final"]["answer"] == "second answer"

            # the reply never reached the node: its report of the round
            # before says so, and the way forward is named
            calls.clear()
            behaviour["POST"] = lambda path, body: unreached()
            lost = await ask(["ae000001"], "Sure?")
            assert calls[0]["body"]["round"] == 3
            assert "your last reply did not reach this agent" in \
                lost["text"] and "second answer" in lost["text"], lost
            assert handle["round"] == 2 and handle["final"]["reply_note"]
            # sent again, it begins the same round
            calls.clear()
            behaviour["POST"] = reply_post
            behaviour["GET"] = lambda path, body: finished_job(
                "ae000001", "third answer", round=3, replyable=True)
            resent = await ask(["ae000001"], "Sure?")
            assert calls[0]["body"]["round"] == 3
            assert "third answer" in resent["text"] and \
                "did not reach" not in resent["text"], resent
            assert "reply_note" not in handle["final"]

            # it did arrive although its answer did not: still working
            calls.clear()
            behaviour["POST"] = lambda path, body: unreached()
            behaviour["GET"] = lambda path, body: running_job(
                "ae000001", round=4)
            unsure = await ask(["ae000001"], "More?", wait_s=1)
            assert "Status: running" in unsure["text"] and \
                "did not reach" not in unsure["text"], unsure
            assert handle["final"] is None and handle["round"] == 4
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000001"], "message": "Again?"},
                                "still working on its answer")
            assert spawn_exec.turn_job_ids(sid, "turn-y") == ["ae000001"]
            # a late report of an earlier round is never this round's answer
            spawn_exec._note_remote_outcome(finished_job(
                "ae000001", "third answer", round=3)["job"])
            assert handle["final"] is None
            behaviour["GET"] = lambda path, body: finished_job(
                "ae000001", "fourth answer", round=4, replyable=True)
            settled = await spawn_exec.wait_for_turn(
                session, "turn-y", {"jobs": ["ae000001"], "wait_s": 1})
            assert "fourth answer" in settled["text"] and \
                handle["final"]["round"] == 4

            # a node that forgot the agent ends its conversation for good
            calls.clear()
            behaviour["POST"] = lambda path, body: spawn_exec.SpawnError(
                "unknown spawn job", 404)
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000001"], "message": "Still there?"},
                                "no longer has spawned agent")
            assert handle["final"]["status"] == "lost" and \
                handle["final"]["replyable"] is False and handle["round"] == 4
            calls.clear()
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000001"], "message": "Still there?"},
                                "has no conversation to continue")
            assert calls == []

            # a refusal the node answered leaves the agent as it was
            refused_one = answered("ae000002", "b answer")
            behaviour["POST"] = lambda path, body: spawn_exec.SpawnError(
                "this node is already running too many spawned agents", 429)
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000002"], "message": "x"},
                                "too many")
            assert refused_one["final"]["answer"] == "b answer" and \
                refused_one["round"] == 1

            # one refusal is named ahead of the others' new answers
            answered("ae000003", "c answer")
            behaviour["POST"] = lambda path, body: (
                spawn_exec.SpawnError("busy", 429) if "ae000002" in path
                else reply_post(path, body))
            behaviour["GET"] = lambda path, body: finished_job(
                "ae000003", "c second", round=2, replyable=True)
            mixed = await ask(["ae000002", "ae000003"], "Both?")
            assert mixed["text"].startswith(
                "Could not reply to spawned agent ae000002 on BUILD-NODE.LAN: "
                "busy"), mixed
            assert "c second" in mixed["text"] and "(round 2)" in mixed["text"]

            # nothing is sent while any agent named cannot take it
            answered("ae000004", "no conversation", replyable=False)
            spawn_exec._register_remote(session, "turn-y", node, "ae000005",
                                        lease=True)
            calls.clear()
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000003", "ae000004"], "message": "?"},
                                "has no conversation to continue")
            await reply_refused(session, "turn-y",
                                {"jobs": ["ae000005"], "message": "?"},
                                "still working on its answer")
            assert calls == []

            # the leases renewed are the running agents' and the finished
            # ones that may still be replied to
            held = mgr._leased_handles()[remote_bid]
            assert sorted(held) == ["ae000002", "ae000003", "ae000005"], held
            mgr.remote.pop("ae000005")

            # a finished agent's verdict stays while its turn runs
            third = mgr.remote["ae000003"]
            third["finished_at"] = time.time() - spawn_exec.PURGE_AFTER_S - 5
            await mgr._sweep_once()
            assert mgr.remote.get("ae000003") is third
            hub._active_turn_id = ""
            hub.status = "idle"
            calls.clear()
            await mgr._sweep_once()
            assert "ae000003" not in mgr.remote and calls == []
            assert mgr.remote["ae000002"]["final"]["answer"] == "b answer"
        finally:
            spawn_exec._node_request = original_node_request
            set_remote_capabilities(remote_bid, capable)
            mgr._next_renew = 0.0
            for job_id in list(mgr.remote):
                if mgr.remote[job_id]["session_id"] == sid:
                    mgr.remote.pop(job_id)
            hub.status = "idle"
            hub._active_turn_id = ""
    print("optional replies to relayed agents ok")


async def test_upgrade_gate(cwd):
    """An engine updater and a spawn launch can no longer interleave, and a
    job still tearing down keeps blocking that engine's updater."""
    from puppy import cli_upgrade
    mgr = spawn_exec.manager()
    sid = db.create_session("gate test", "fake", cwd, "", "", "blue",
                            "standard")
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = "turn-g"
    session = db.get_session(sid)
    driver = drivers._DRIVERS["fake"]

    async def claim_during_refresh():
        # the updater wins the slot while prepare_request awaits the catalog
        cli_upgrade._runs["fake"] = {"state": "running"}
    driver.dynamic_model_options = True
    driver.refresh_model_options = claim_during_refresh
    before = mgr.running_count()
    try:
        try:
            await spawn_exec.start_for_turn(
                session, "turn-g", {"prompt": "race", "model": "hang",
                                    "wait_s": 0})
        except spawn_exec.SpawnError as exc:
            assert "being upgraded" in str(exc) and exc.status == 409
        else:
            raise AssertionError("spawn launched under a running updater")
        assert mgr.running_count() == before
    finally:
        cli_upgrade._runs.pop("fake", None)
        del driver.refresh_model_options
        driver.dynamic_model_options = False
        hub.status = "idle"
        hub._active_turn_id = ""

    settled = spawn_exec.SpawnJob(request_for("ok", cwd), ("remote",))
    settled._finish("done")
    settled.task = asyncio.get_event_loop().create_future()
    mgr.jobs[settled.id] = settled
    try:
        assert not settled.running
        assert any(item["name"] == "spawned agent " + settled.id
                   for item in runner.engine_blockers("fake"))
        assert not runner.engine_blockers("other")
        settled.task.set_result(None)
        assert not any(item["name"] == "spawned agent " + settled.id
                       for item in runner.engine_blockers("fake"))
    finally:
        mgr.jobs.pop(settled.id, None)
    print("upgrade launch gate and teardown blocker ok")


def test_bridge_sizing():
    """Schema-valid payloads fit the bridge whole; an oversized combined
    result is shortened per agent, never replaced by an id-less error."""
    biggest = {"session_id": "1", "turn_id": "t", "method": "spawn",
               "params": {"prompt": "\x01" * spawn_exec.MAX_PROMPT_CHARS,
                          "cwd": "/" + "d" * 4000, "node": "n" * 80,
                          "count": 12}}
    assert len(spawn_agent._encode(biggest)) <= spawn_agent.MAX_REQUEST
    small = {"ok": True, "result": {"text": "fine", "entries": [("x", "y")]}}
    wire = json.loads(spawn_agent._wire_response(small))
    assert wire == {"ok": True, "result": {"text": "fine"}}

    answer = ("\u6f22\u5b57" * 20000)[:spawn_exec.ANSWER_LIMIT]
    entries = []
    for index in range(spawn_exec.MAX_WAIT_JOBS):
        payload = finished_job("ee{:06x}".format(index), answer)["job"]
        payload.update(engine="fake", cwd="/tmp", tool_calls=2)
        entries.append((payload, "BUILD-NODE.LAN"))
    full = spawn_exec.jobs_text(entries)
    assert len(full.encode("utf-8")) > spawn_agent.MAX_RESPONSE
    wire = spawn_agent._wire_response(
        {"ok": True, "result": spawn_exec._rendered(entries)})
    assert len(wire) <= spawn_agent.MAX_RESPONSE
    decoded = json.loads(wire)
    assert decoded["ok"] is True and set(decoded["result"]) == {"text"}
    text = decoded["result"]["text"]
    assert "Answers were shortened" in text
    for payload, _node in entries:
        assert payload["id"] in text
        assert "call wait with jobs [\"{}\"] alone".format(payload["id"]) \
            in text
    assert text.count("more characters omitted") == spawn_exec.MAX_WAIT_JOBS
    # the first shortening step that fits is the one used, so as much of
    # every answer as possible survives
    assert "\u6f22\u5b57" * 4000 in text
    # a lone answer can never be too large, so it is never shortened
    single = spawn_agent._wire_response(
        {"ok": True, "result": spawn_exec._rendered(entries[:1])})
    assert "shortened" not in json.loads(single)["result"]["text"]
    # a reply's note on an agent it could not reach leads the text however
    # it is rendered
    note = "Could not reply to spawned agent ffffffff: gone"
    noted = json.loads(spawn_agent._wire_response(
        {"ok": True, "result": spawn_exec._rendered(entries, note=note)}))
    assert noted["result"]["text"].startswith(note + "\n\nSpawned agents: ")
    assert "Answers were shortened" in noted["result"]["text"]
    print("bridge sizing and shortened results ok")


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
            "/api/spawn/{}".format(live_id), json={"max_runtime_s": spawn_exec.MAX_TIMEOUT_S + 1})
        assert response.status == 400
        response = await client.delete("/api/spawn/{}".format(live_id))
        assert response.status == 200
        response = await client.get("/api/spawn/00000000")
        assert response.status == 404

        # a controller-chosen id is honored, idempotent, and never collides
        # with a job somebody else owns
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "job_id": "XYZ"})
        assert response.status == 400
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "job_id": "c0ffee01"})
        body = await response.json()
        assert response.status == 200 and body["job"]["id"] == "c0ffee01", body
        first = spawn_exec.manager().get("c0ffee01")
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "job_id": "c0ffee01"})
        body = await response.json()
        assert response.status == 200 and body["job"]["id"] == "c0ffee01"
        assert spawn_exec.manager().get("c0ffee01") is first
        assert spawn_exec.manager().running_count() == 1
        response = await client.delete("/api/spawn/c0ffee01")
        assert response.status == 200
        mine = spawn_exec.manager().start_job(
            request_for("hang", cwd), ("turn", 1, "t"), job_id="c0ffee02")
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "job_id": "c0ffee02"})
        assert response.status == 409
        await spawn_exec.manager().cancel(mine, "test over")
        spawn_exec.manager().jobs.pop("c0ffee02", None)

        # a poll or cancel for an id whose start is still validating waits
        # for that start instead of answering "unknown" (and orphaning it)
        original_prepare = spawn_exec.prepare_request

        async def slow_prepare(body):
            await asyncio.sleep(0.8)
            return await original_prepare(body)

        async def post(job_id):
            return await client.post("/api/spawn", json={
                "engine": "fake", "model": "hang", "prompt": "hi",
                "cwd": cwd, "wait_s": 0, "job_id": job_id})
        spawn_exec.prepare_request = slow_prepare
        try:
            starting = asyncio.ensure_future(post("c0ffee03"))
            await asyncio.sleep(0.2)
            response = await client.get("/api/spawn/c0ffee03")
            body = await response.json()
            assert response.status == 200 and body["job"]["status"] == \
                "running", body
            assert (await starting).status == 200
            response = await client.delete("/api/spawn/c0ffee03")
            assert response.status == 200

            starting = asyncio.ensure_future(post("c0ffee04"))
            await asyncio.sleep(0.2)
            response = await client.delete("/api/spawn/c0ffee04")
            assert response.status == 200
            assert (await response.json())["job"]["status"] == "cancelled"
            assert (await starting).status == 200
            assert spawn_exec.manager().get("c0ffee04") is None
        finally:
            spawn_exec.prepare_request = original_prepare
        assert spawn_exec.manager()._start_locks == {}

        # ownership leases: a relayed job whose controller stops renewing is
        # stopped once the lease lapses, while contact of any kind keeps it
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "lease_s": 2})
        assert response.status == 400   # below the floor: refused, not clamped
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "lease_s": spawn_exec.MIN_LEASE_S,
            "job_id": "1ea5e001"})
        body = await response.json()
        assert response.status == 200, body
        assert body["job"]["lease_s"] == spawn_exec.MIN_LEASE_S
        assert 0 < body["job"]["lease_remaining_s"] <= spawn_exec.MIN_LEASE_S
        renewed_until = time.monotonic() + spawn_exec.MIN_LEASE_S + 2.5
        while time.monotonic() < renewed_until:
            response = await client.post("/api/spawn/renew",
                                         json={"jobs": ["1ea5e001", "00000000"]})
            renewal = await response.json()
            assert response.status == 200, renewal
            assert renewal["renewed"] == ["1ea5e001"], renewal
            assert renewal["unknown"] == ["00000000"], renewal
            await asyncio.sleep(1.0)
        response = await client.get("/api/spawn/1ea5e001")
        body = await response.json()
        assert body["job"]["status"] == "running", body   # renewals held it
        leased = spawn_exec.manager().get("1ea5e001")
        await asyncio.sleep(spawn_exec.MIN_LEASE_S + 1.5)
        await wait_done(leased, 10.0)
        assert leased.status == "abandoned", leased.status
        assert "stopped renewing its ownership lease" in leased.error
        response = await client.post("/api/spawn/renew",
                                     json={"jobs": ["1ea5e001"]})
        assert (await response.json())["finished"] == ["1ea5e001"]
        response = await client.delete("/api/spawn/1ea5e001")
        assert response.status == 200
        # a poll is contact too; a job's own limits outrank a lapsed lease
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "lease_s": spawn_exec.MIN_LEASE_S,
            "job_id": "1ea5e002"})
        assert response.status == 200
        polled = spawn_exec.manager().get("1ea5e002")
        await asyncio.sleep(spawn_exec.MIN_LEASE_S - 1.5)
        response = await client.get("/api/spawn/1ea5e002?wait_s=0")
        assert (await response.json())["job"]["status"] == "running"
        assert polled.lease_deadline > time.monotonic() + \
            spawn_exec.MIN_LEASE_S - 1.0
        polled.lease_clock -= spawn_exec.MIN_LEASE_S     # lease lapsed
        assert polled.expiry()[0] == "abandoned"
        polled.idle_timeout_s = 1                          # own limit too
        assert polled.expiry()[0] == "timeout"
        await spawn_exec.manager().cancel(polled, "test over")
        spawn_exec.manager().jobs.pop("1ea5e002", None)
        # an unleased job never expires this way
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": "hang", "prompt": "hi", "cwd": cwd,
            "wait_s": 0, "job_id": "1ea5e003"})
        body = await response.json()
        assert response.status == 200 and "lease_s" not in body["job"]
        assert spawn_exec.manager().get("1ea5e003").lease_deadline is None
        response = await client.delete("/api/spawn/1ea5e003")
        assert response.status == 200
        response = await client.post("/api/spawn/renew", json={"jobs": []})
        assert response.status == 400
        response = await client.post("/api/spawn/renew", json=[1])
        assert response.status == 400
    finally:
        await client.close()
    print("spawn HTTP routes ok")


async def test_reply_routes(cwd):
    """The node's reply route: a controller's own finished agents only, the
    round a reply begins answered again rather than begun twice, a poll and a
    second reply waiting for a reply on its way, and a renewed lease keeping a
    finished agent for a reply."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    spawn_exec.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    mgr = spawn_exec.manager()

    async def start(job_id, model, prompt):
        response = await client.post("/api/spawn", json={
            "engine": "fake", "model": model, "prompt": prompt, "cwd": cwd,
            "wait_s": 20, "lease_s": spawn_exec.MIN_LEASE_S, "job_id": job_id})
        body = await response.json()
        assert response.status == 200 and body["job"]["status"] == "done", body
        return body["job"]

    async def reply(job_id, body):
        response = await client.post("/api/spawn/{}/reply".format(job_id),
                                     json=body)
        return response.status, await response.json()

    with quiet_sweeper():
        try:
            first = await start("c4a70001", "chat", "Remember plum.")
            assert first["round"] == 1 and first["replyable"] is True
            job = mgr.get("c4a70001")
            await asyncio.wait({job.task}, timeout=10)
            status, body = await reply("c4a70001", {
                "message": "Which fruit?", "round": 2, "wait_s": 20})
            assert status == 200 and body["job"]["round"] == 2, body
            assert body["job"]["answer"] == \
                "round 2: Remember plum. | Which fruit?", body
            # the same round asked again is that round, not a third run
            status, body = await reply("c4a70001", {
                "message": "Which fruit?", "round": 2})
            assert status == 200 and body["job"]["round"] == 2 and \
                job.round == 2
            assert conversation_of(job) == ["Remember plum.", "Which fruit?"]
            status, body = await reply("c4a70001", {"message": "Skip",
                                                    "round": 4})
            assert status == 409 and "is at round 2" in body["error"], body
            for bad in ({"message": "x", "round": True},
                        {"message": "x", "round": "3"},
                        {"message": "x", "round": 1},
                        {"message": " ", "round": 3}, {"round": 3}):
                status, body = await reply("c4a70001", bad)
                assert status == 400, (bad, body)
            response = await client.post("/api/spawn/c4a70001/reply",
                                         data="nope")
            assert response.status == 400
            status, body = await reply("00000000", {"message": "x"})
            assert status == 404
            # a turn's own agent is that turn's to reply to, not a controller's
            mine = mgr.start_job(dict(request_for("chat", cwd),
                                      prompt="Turn work."),
                                 ("turn", 1, "t"), job_id="c4a70002")
            await wait_done(mine)
            status, body = await reply("c4a70002", {"message": "x",
                                                    "round": 2})
            assert status == 404
            mgr.jobs.pop("c4a70002", None)

            # a finished agent stays while its controller renews the lease
            job.finished_at = time.time() - spawn_exec.PURGE_AFTER_S - 5
            job.lease_clock -= spawn_exec.MIN_LEASE_S + 1
            response = await client.post("/api/spawn/renew",
                                         json={"jobs": ["c4a70001"]})
            assert (await response.json())["finished"] == ["c4a70001"]
            await mgr._sweep_once()
            assert mgr.get("c4a70001") is job
            job.lease_clock -= spawn_exec.MIN_LEASE_S + 1
            await mgr._sweep_once()
            assert mgr.get("c4a70001") is None

            # a poll and the same reply again, while a reply waits out the
            # previous run's exit, both meet the round it begins
            await start("c4a70003", "chatlinger", "Remember fig.")
            lingering = mgr.get("c4a70003")
            assert not lingering.task.done()
            sent = asyncio.ensure_future(reply("c4a70003", {
                "message": "Which?", "round": 2, "wait_s": 0}))
            await asyncio.sleep(0.2)
            assert lingering.replying
            again = asyncio.ensure_future(reply("c4a70003", {
                "message": "Which?", "round": 2, "wait_s": 0}))
            response = await client.get("/api/spawn/c4a70003?wait_s=0")
            polled = await response.json()
            assert polled["job"]["round"] == 2, polled
            for status, body in (await sent, await again):
                assert status == 200 and body["job"]["round"] == 2, body
            await wait_done(lingering)
            assert lingering.round == 2 and \
                conversation_of(lingering) == ["Remember fig.", "Which?"]

            # an agent still working takes no reply
            await start("c4a70004", "chathang", "Start.")
            hanging = mgr.get("c4a70004")
            await asyncio.wait({hanging.task}, timeout=10)
            status, body = await reply("c4a70004", {"message": "Go on.",
                                                    "round": 2})
            assert status == 200 and body["job"]["status"] == "running"
            status, body = await reply("c4a70004", {"message": "And?",
                                                    "round": 3})
            assert status == 409 and "still working" in body["error"], body
            response = await client.delete("/api/spawn/c4a70004")
            assert response.status == 200
            assert hanging.status == "cancelled"
        finally:
            for job_id in ("c4a70001", "c4a70003", "c4a70004"):
                job = mgr.get(job_id)
                if job is not None:
                    await mgr.cancel(job, "test over")
                    mgr.jobs.pop(job_id, None)
            await client.close()
    print("spawn reply route ok")


async def test_unlimited(cwd):
    job = spawn_exec.manager().start_job(
        request_for("hang", cwd, idle_timeout_s=0, max_runtime_s=0), ("remote",))
    # Advance ages far past the former two-hour cap without extending a wait.
    job.created_clock -= 100000
    job.last_progress_clock -= 100000
    await asyncio.sleep(0.2)
    assert job.running and job.proc is not None
    payload = job.payload()
    assert payload["idle_remaining_s"] is None and payload["hard_remaining_s"] is None
    json.dumps(payload, allow_nan=False)
    assert "inactivity limit unlimited" in spawn_exec.job_text(payload, "Demo")
    assert "hard runtime unlimited" in spawn_exec.jobs_text([(payload, "Demo"), (payload, "Peer")])
    job.update_limits({"max_runtime_s": 200000})
    assert job.hard_deadline > time.monotonic()
    job.update_limits({"max_runtime_s": 0, "idle_timeout_s": 0})
    assert job.deadline == float("inf")
    job.lease_s = 5
    job.lease_clock = time.monotonic() - 6
    assert job.expiry()[0] == "abandoned"  # unlimited does not abandon ownership
    job.lease_s = 0
    await spawn_exec.manager().cancel(job, "test stop")
    assert job.status == "cancelled"
    for idle, runtime, needle in ((0, 1, "hard runtime"), (1, 0, "no recognized")):
        job = spawn_exec.manager().start_job(
            request_for("hang", cwd, idle_timeout_s=idle, max_runtime_s=runtime), ("remote",))
        await wait_done(job, seconds=5)
        assert job.status == "timeout" and needle in job.error
    # Turning an already waiting unlimited job back into a finite one wakes it.
    job = spawn_exec.manager().start_job(
        request_for("hang", cwd, idle_timeout_s=0, max_runtime_s=0), ("remote",))
    await asyncio.sleep(0.1)
    job.update_limits({"idle_timeout_s": 1})
    await wait_done(job, seconds=5)
    assert job.status == "timeout"


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
        await test_unlimited(cwd)
        await test_validation(cwd)
        await test_turn_dispatch(cwd)
        await test_parallel(cwd)
        await test_turn_end_reaping(cwd)
        await test_relay_start(cwd)
        await test_replies(cwd)
        await test_relay_replies(cwd)
        await test_upgrade_gate(cwd)
        test_bridge_sizing()
        await test_http_routes(cwd)
        await test_reply_routes(cwd)
    finally:
        await spawn_exec.manager().shutdown()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    print("spawn tests passed")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
