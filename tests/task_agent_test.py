#!/usr/bin/env python3
"""Apply to Main when done and the task MCP bridge, on real Git repositories.

Git's merge of Main into a task copy, the worker that applies, hands real
conflicts back for bounded rounds and folds by itself - in the order tasks
finished, with contention between them - Main counted idle only while its
turn waits in a task tool, both bridge roles, the routes on both runtimes,
the persisted record and the stdio server. No engine, network or quota.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))
from tests.scratch import private_root

ROOT = private_root("task-agent-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from puppy import config, db, runner, session_tasks as tasks, task_agent, workspaces  # noqa: E402

LINES = "".join("line {}\n".format(n) for n in range(1, 13))
APP = {}   # the worker reads only the snapshot flag from its app


def start(hub, prompt):
    """A turn that runs nothing: the prompt is recorded, the task works."""
    hub.status = "running"
    hub.interrupted = False
    hub._active_prompt_text = prompt
    hub.test_seq = db.add_event(hub.id, "user", {"text": prompt})["seq"]


def finish(sid, text="Implemented and checked.", status="ok"):
    hub = runner.hub(sid)
    db.add_event(sid, "assistant", {"text": text})
    db.add_event(sid, "result", {"ok": status == "ok"})
    tasks.finished(sid, status, hub.test_seq)
    hub.status = "idle"


async def rejected(call, contains):
    try:
        await call
    except (tasks.TaskError, task_agent.TaskAgentError) as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError("expected rejection: " + contains)


def git(cwd, *args):
    return tasks._git(cwd, *args).decode()


def project(name, files=None):
    path = ROOT / name
    path.mkdir()
    git(path, "init", "--quiet")
    for rel, text in (files or {"a.txt": LINES, "b.txt": "b\n"}).items():
        (path / rel).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "Initial")
    return path, db.create_session(name, "codex", str(path), "", "", "#e0784f", "workspace-write")


async def new_task(parent, key, auto=True):
    with patch.object(runner.SessionHub, "_start_turn", start):
        row = await tasks.create(parent, {"prompt": "Feature " + key, "request_id": key,
                                          "auto_apply": auto})
    return row["id"], Path(row["cwd"])


def edit(path, line, text):
    lines = path.read_text().splitlines(True)
    lines[line - 1] = text + "\n"
    path.write_text("".join(lines))


def rows(sid, subtype=None):
    out = []
    for event in db.get_events(sid, limit=500):
        if event["kind"] == "info" and (subtype is None or event["data"].get("subtype") == subtype):
            out.append(event["data"])
    return out


def texts(sid):
    return [row.get("text", "") for row in rows(sid)]


async def worker_pass():
    with patch.object(runner.SessionHub, "_start_turn", start):
        await tasks._auto_pass(APP)


async def clean_apply_and_fold():
    path, parent = project("clean")
    sid, copy = await new_task(parent, "clean")
    assert tasks.auto_record(sid) == {"format": 1, "armed_at": tasks.auto_record(sid)["armed_at"],
                                      "rounds": 0, "resolving": False}
    assert tasks.public(sid)["auto_apply"]["phase"] == "working"
    (copy / "b.txt").write_text("b from the task\n")
    (copy / "new.txt").write_text("new\n")
    await worker_pass()                       # still working: nothing happens
    assert (path / "b.txt").read_text() == "b\n"
    main_index = (path / ".git" / "index").read_bytes()
    finish(sid)
    assert tasks.public(sid)["auto_apply"]["phase"] == "waiting"
    await worker_pass()
    assert (path / "b.txt").read_text() == "b from the task\n" and (path / "new.txt").is_file()
    assert db.get_session(sid) is None and tasks.auto_record(sid) is None
    assert (path / ".git" / "index").read_bytes() == main_index, "Main's index is never touched"
    applied = [row for row in rows(parent, "session_task") if row.get("task_id") == sid]
    assert applied[-1]["text"] == "Task changes applied automatically: Feature clean"
    assert "b.txt" in applied[-1]["files"] and applied[-1]["auto"] is True
    archive = tasks.folded(parent, 1)[0]
    assert archive["task_id"] == sid and "b.txt" in archive["applied_files"]
    print("PASS: a finished task set to apply when done is applied, folded and closed; Main's index untouched")


async def merged_with_main_drift():
    path, parent = project("drift")
    sid, copy = await new_task(parent, "drift")
    edit(copy / "a.txt", 6, "line 6 from the task")
    edit(path / "a.txt", 4, "line 4 from Main")     # inside the task hunk's context
    finish(sid)
    patch_text = tasks._changes(db.get_session(sid), tasks.record(sid))[0]
    assert subprocess.run(["git", "apply", "--check", "-"], cwd=path, input=patch_text,
                          capture_output=True).returncode == 1, "the plain patch no longer fits"
    await worker_pass()
    text = (path / "a.txt").read_text()
    assert "line 4 from Main\n" in text and "line 6 from the task\n" in text
    assert db.get_session(sid) is None
    assert any(t.endswith("merged with Main's newer changes") for t in texts(parent))
    print("PASS: Main's newer changes beside the task's are merged by Git and applied")


async def conflict_rounds():
    path, parent = project("rounds")
    sid, copy = await new_task(parent, "rounds")
    edit(copy / "a.txt", 2, "line 2 from the task")
    edit(path / "a.txt", 2, "line 2 from Main")
    finish(sid)
    before = tasks.record(sid)["base"]
    await worker_pass()
    assert (path / "a.txt").read_text().count("line 2 from Main") == 1, "Main is untouched by a conflict"
    auto = tasks.auto_record(sid)
    assert auto["rounds"] == 1 and auto["resolving"] is True
    merged = (copy / "a.txt").read_text()
    assert "<<<<<<< refs/puppy/sync/task\nline 2 from the task\n=======\nline 2 from Main\n" \
           ">>>>>>> refs/puppy/sync/main\n" in merged
    assert tasks._unmerged_paths(db.get_session(sid)) == ["a.txt"]
    assert tasks.record(sid)["base"] != before == git(copy, "rev-parse", "refs/puppy/sync/base").strip()
    assert git(copy, "rev-parse", "refs/puppy/base").strip() == tasks.record(sid)["base"]
    prompt = runner.hub(sid)._active_prompt_text
    assert "round 1 of at most 8" in prompt and "a.txt (both sides changed it)" in prompt
    assert "apply_when_done tool with enabled false" in prompt and str(path) in prompt
    assert tasks.public(sid)["auto_apply"]["phase"] == "resolving"
    assert "Resolving conflicts with Main in task: Feature rounds · round 1 of 8" in texts(parent)
    sync = rows(sid, tasks.SYNC_SUBTYPE)[-1]
    assert sync["conflicts"] == ["a.txt"] and "1 conflicting" in sync["text"]
    # left unresolved: the next round asks again without merging again
    finish(sid, "I looked at it.")
    await worker_pass()
    assert tasks.auto_record(sid)["rounds"] == 2
    assert "not resolved yet" in runner.hub(sid)._active_prompt_text
    # added but still carrying Puppy's markers: refused, asked again
    git(copy, "add", "a.txt")
    finish(sid, "Added it.")
    await worker_pass()
    assert tasks.auto_record(sid)["rounds"] == 3 and "a.txt" in runner.hub(sid)._active_prompt_text
    assert (path / "a.txt").read_text().count("<<<<<<<") == 0
    # resolved for real: applied with both sides, then folded
    (copy / "a.txt").write_text(LINES.replace("line 2\n", "line 2 from Main and the task\n"))
    git(copy, "add", "a.txt")
    finish(sid, "Resolved and checked.")
    await worker_pass()
    final = (path / "a.txt").read_text()
    assert "line 2 from Main and the task\n" in final and "<<<<" not in final
    assert final.count("line 2") == 1 and db.get_session(sid) is None
    print("PASS: a real conflict is merged into the task with Git's markers and unmerged stages, "
          "asked again while unresolved or still marked, and applied once resolved")


async def rounds_bound_and_switch_off():
    path, parent = project("bound")
    sid, copy = await new_task(parent, "bound")
    edit(copy / "a.txt", 3, "task")
    edit(path / "a.txt", 3, "main")
    finish(sid)
    tasks._save_auto(sid, dict(tasks.auto_record(sid), rounds=tasks.AUTO_ROUNDS))
    await worker_pass()
    assert tasks.auto_record(sid) is None and db.get_session(sid) is not None
    gave_up = [t for t in texts(parent) if t.startswith("Task not applied automatically")]
    assert gave_up and "after 8 resolution rounds" in gave_up[-1]
    assert (path / "a.txt").read_text().count("main\n") == 1
    # switched off: a finished task stays for review
    other, other_copy = await new_task(parent, "off")
    (other_copy / "b.txt").write_text("off\n")
    finish(other)
    await tasks.set_auto_apply(parent, other, False)
    await worker_pass()
    assert db.get_session(other) is not None and (path / "b.txt").read_text() == "b\n"
    await rejected(tasks.set_auto_apply(parent, other, "yes"), "true or false")
    await rejected(tasks.set_auto_apply(parent + 999, other, True), "does not belong")
    # a failed turn waits for a successful one
    await tasks.set_auto_apply(parent, other, True)
    finish(other, "Broke", "error")
    assert tasks.public(other)["auto_apply"]["phase"] == "paused"
    await worker_pass()
    assert db.get_session(other) is not None
    finish(other)
    await worker_pass()
    assert db.get_session(other) is None and (path / "b.txt").read_text() == "off\n"
    # nothing to apply: folded without an apply row
    empty, _ = await new_task(parent, "empty")
    finish(empty, "Nothing needed changing.")
    before = len([t for t in texts(parent) if t.startswith("Task changes applied")])
    await worker_pass()
    assert db.get_session(empty) is None and tasks.folded(parent, 1)[0]["task_id"] == empty
    assert len([t for t in texts(parent) if t.startswith("Task changes applied")]) == before
    print("PASS: the rounds are bounded, a switched-off task waits for review, a failed turn "
          "for a successful one, and a task with nothing to apply is folded")


async def busy_main_and_parked_wait():
    path, parent = project("busy")
    sid, copy = await new_task(parent, "busy")
    (copy / "b.txt").write_text("busy\n")
    finish(sid)
    main = runner.hub(parent)
    main.status = "running"
    try:
        await worker_pass()
        assert db.get_session(sid) is not None and (path / "b.txt").read_text() == "b\n"
        info = tasks.public(sid)["auto_apply"]
        assert info["phase"] == "waiting" and info["note"] == "waiting for Main to be idle"
        # Main's turn waits in a task tool: its engine is blocked on that call,
        # so the apply goes ahead, and the wait cannot return until it is over
        loop = asyncio.get_running_loop()
        applying, release = asyncio.Event(), threading.Event()
        original = tasks._git_run

        def slow(cwd, *args, **kwargs):
            if str(cwd) == str(path) and args[:2] == ("apply", "--binary"):
                loop.call_soon_threadsafe(applying.set)
                assert release.wait(10)
            return original(cwd, *args, **kwargs)
        waiting = tasks.parked(parent)
        await waiting.__aenter__()
        with patch.object(tasks, "_git_run", slow):
            worker = asyncio.create_task(worker_pass())
            await asyncio.wait_for(applying.wait(), 10)
            leaving = asyncio.create_task(waiting.__aexit__(None, None, None))
            await asyncio.sleep(0.3)
            assert not leaving.done() and tasks.apply_held(parent)
            release.set()
            await worker
            await asyncio.wait_for(leaving, 5)
        assert (path / "b.txt").read_text() == "busy\n" and db.get_session(sid) is None
        assert not tasks.apply_held(parent) and not tasks._parked
    finally:
        main.status = "idle"
    print("PASS: a busy Main holds the apply back; Main's turn waiting in a task tool lets it "
          "through and its wait returns only after the apply")


async def contention():
    path, parent = project("contention")
    a, ap = await new_task(parent, "A")
    b, bp = await new_task(parent, "B")
    c, cp = await new_task(parent, "C")
    edit(ap / "a.txt", 2, "line 2 by A")
    edit(bp / "a.txt", 2, "line 2 by B")
    edit(cp / "a.txt", 9, "line 9 by C")
    for sid in (a, b, c):
        finish(sid)
    await worker_pass()
    text = (path / "a.txt").read_text()
    assert "line 2 by A\n" in text and "line 9 by C\n" in text and "by B" not in text
    assert db.get_session(a) is None and db.get_session(c) is None
    assert tasks.auto_record(b)["rounds"] == 1
    # B's round merges the Main every clean task already reached
    merged = (bp / "a.txt").read_text()
    assert "line 2 by B\n=======\nline 2 by A\n" in merged and "line 9 by C\n" in merged
    print("PASS: tasks that fit apply first in the order they finished; a conflicting one gets "
          "its round against Main as the others left it")


async def sync_main_for_the_task():
    path, parent = project("sync")
    sid, copy = await new_task(parent, "sync", auto=False)
    edit(copy / "a.txt", 5, "line 5 by the task")
    edit(path / "a.txt", 10, "line 10 by Main")
    hub = runner.hub(sid)
    assert hub.status == "running"            # the task's own turn calls it
    result = await tasks.sync_main(parent, sid)
    assert result["changed"] and not result["conflicts"] and not result["from_commit"]
    text = (copy / "a.txt").read_text()
    assert "line 5 by the task\n" in text and "line 10 by Main\n" in text
    look = await tasks.changes(parent, sid)
    assert look["applies"] is True and "a.txt" in look["files"] and "line 10 by Main" not in \
        look["patch"].decode(), "the review diff stays the task's own"
    again = await tasks.sync_main(parent, sid)
    assert not again["changed"]
    # Main busy and clean: its last commit is read instead of half-written files
    git(path, "commit", "-qam", "Main work")
    main = runner.hub(parent)
    main.status = "running"
    try:
        committed = await tasks.sync_main(parent, sid)
        assert committed["from_commit"] and not committed["changed"]
        (path / "b.txt").write_text("dirty\n")
        await rejected(tasks.sync_main(parent, sid), "uncommitted changes")
    finally:
        main.status = "idle"
    # a conflicting sync from the task's own turn leaves markers to resolve
    edit(copy / "a.txt", 5, "line 5 again by the task")
    edit(path / "a.txt", 5, "line 5 by Main")
    result = await tasks.sync_main(parent, sid)
    assert result["conflicts"] == ["a.txt"] and "both sides changed it" in result["described"]
    look = await tasks.changes(parent, sid)
    assert look["unmerged"] == ["a.txt"]
    await rejected(tasks.sync_main(parent, sid), "unresolved Git conflicts")
    finish(sid)
    print("PASS: a task merges Main into its own copy while working, from the last commit when "
          "Main is busy and clean, and refuses again until its conflicts are resolved")


async def persisted_shape():
    path, parent = project("shape")
    sid, _ = await new_task(parent, "shape")
    connection = db.connect()
    tasks.validate_persisted(connection)
    key = tasks.AUTO_PREFIX + str(sid)
    good = db.meta_get(key)
    for bad in (dict(good, extra=1), dict(good, format=2), dict(good, rounds=-1),
                dict(good, rounds=tasks.AUTO_ROUNDS + 1), dict(good, resolving=1),
                dict(good, armed_at="now"), [good]):
        db.meta_set(key, bad)
        try:
            tasks.validate_persisted(connection)
        except tasks.TaskError:
            pass
        else:
            raise AssertionError("accepted " + json.dumps(bad))
    db.meta_set(key, good)
    db.meta_set(tasks.AUTO_PREFIX + str(parent), good)    # Main is not a task
    try:
        tasks.validate_persisted(connection)
    except tasks.TaskError:
        pass
    else:
        raise AssertionError("an apply-when-done record outside a task was accepted")
    db.meta_apply(delete_keys=(tasks.AUTO_PREFIX + str(parent),))
    tasks.validate_persisted(connection)
    finish(sid)
    runner.hub(sid).status = "idle"
    workspaces.remove_temporary(db.get_session(sid))
    runner.drop_hub(sid)
    db.delete_session(sid)
    assert db.meta_get(key) is None, "the record goes with its task"
    print("PASS: the record's exact shape is required, refused rather than repaired, and "
          "deleted with its task")


def bridge(sid, turn):
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = turn

    async def call(method, **params):
        result = await task_agent._dispatch({"session_id": sid, "turn_id": turn,
                                             "method": method, "params": params})
        return result["text"]
    return call


async def bridge_main():
    path, parent = project("bridge")
    one, one_copy = await new_task(parent, "one", auto=False)
    two, _ = await new_task(parent, "two", auto=False)
    call = bridge(parent, "turn-main")
    listing = await call("tasks")
    assert "#{} Feature one - working".format(one) in listing and "#{} Feature two".format(two) in listing
    assert task_agent._resolve(parent, ["#{}".format(one), "Feature two"]) == [one, two]
    assert task_agent._resolve(parent, ["@Task-Feature-one-{}".format(one)]) == [one]
    assert task_agent._resolve(parent, ["all"]) == [one, two]
    await rejected(call("read", task="nothing"), "No task named")
    await rejected(call("read", task="#999999"), "No task #999999")
    db.touch_session(two, name="Feature one")
    await rejected(call("read", task="Feature one"), "Several tasks are named")
    db.touch_session(two, name="Feature two")
    page = json.loads(await call("read", task=str(one)))
    assert page["events"][-1]["text"] == "Feature one"
    await rejected(call("status"), "not a task tool in Main")
    # new_task: engine choices refused with the valid ones, then a task armed
    from puppy.drivers.base import Driver
    with patch.object(Driver, "resolved_binary", lambda self: "/bin/true"), \
            patch.object(runner.SessionHub, "_start_turn", start):
        await rejected(call("new_task", prompt="x", engine="nope"), "engines:")
        await rejected(call("new_task", prompt="x", effort="ludicrous"), "efforts:")
        text = await call("new_task", prompt="Write the docs", name="Docs", apply_when_done=True)
        assert "Created task #" in text and "applies to main when done" in text.lower()
        again = await call("new_task", prompt="Write the docs", name="Docs", apply_when_done=True)
        assert again.split(";")[0] == text.split(";")[0], "a repeat finds the same task"
    docs = task_agent._resolve(parent, ["Docs"])[0]
    assert tasks.auto_record(docs) is not None
    # send, stop
    assert "queued behind" in await call("send", tasks=["#{}".format(one)], text="Also this")
    assert runner.hub(one).queue == ["Also this"]
    runner.hub(one).queue.clear()
    with patch.object(runner.SessionHub, "interrupt", AsyncMock()):
        assert "stopping its current work" in await call("stop", tasks=["#{}".format(one)])
    # wait until finished, and until folded while Puppy applies beside it
    edit(one_copy / "a.txt", 7, "line 7 by one")
    finish(one)
    done = await call("wait", tasks=["#{}".format(one)], until="finished", wait_s=1)
    assert "Done (finished)" in done
    blocked = await call("wait", tasks=["#{}".format(one)], until="folded", wait_s=1)
    assert "it is not set to apply when done" in blocked
    assert "on; it is finished" in await call("apply_when_done", tasks=["#{}".format(one)])
    with patch.object(runner.SessionHub, "_start_turn", start):
        waiting = asyncio.create_task(call("wait", tasks=["#{}".format(one)], until="folded", wait_s=10))
        for _ in range(50):
            if tasks._parked.get(parent):
                break
            await asyncio.sleep(0.05)
        assert tasks._parked.get(parent), "Main's wait parks its turn"
        await tasks._auto_pass(APP)
        folded = await asyncio.wait_for(waiting, 15)
    assert "Done (folded into Main)" in folded and "folded into Main and closed" in folded
    assert "line 7 by one\n" in (path / "a.txt").read_text()
    # remove refuses unapplied changes unless discarding them is asked for
    (Path(db.get_session(two)["cwd"]) / "b.txt").write_text("unapplied\n")
    finish(two)
    removed = None
    try:
        await call("remove", tasks=["#{}".format(two)])
    except task_agent.TaskAgentError as error:
        removed = str(error)
    assert removed and "not applied to Main (b.txt)" in removed
    assert "removed and folded" in await call("remove", tasks=["#{}".format(two)], discard_changes=True)
    assert db.get_session(two) is None
    # Tasks off for the session: the tools say so
    finish(docs)
    await tasks.durable_workspace_operation(tasks.remove(parent, docs, True), parent)
    await tasks.set_enabled(parent, False)
    await rejected(call("tasks"), "turned off")
    await tasks.set_enabled(parent, True)
    runner.hub(parent)._active_turn_id = "another"
    await rejected(call("tasks"), "no longer running")
    runner.hub(parent).status = "idle"
    print("PASS: Main's tools list, name, read, create (choices refused with their values), send, "
          "stop, wait (until folded, applying beside the wait), apply when done and remove")


async def bridge_task():
    path, parent = project("bridge-task")
    sid, copy = await new_task(parent, "self", auto=False)
    sibling, sibling_copy = await new_task(parent, "sibling", auto=False)
    call = bridge(sid, "turn-task")
    await rejected(call("new_task", prompt="x"), "not a task tool inside a task")
    listing = await call("tasks")
    assert "#{} Feature self (this task)".format(sid) in listing
    status = await call("status")
    assert "No changes since its review baseline" in status and "Apply to Main when done: off." in status
    edit(copy / "a.txt", 11, "line 11 by the task")
    edit(path / "a.txt", 11, "line 11 by Main")
    status = await call("status")
    assert "Applies to Main's files as they stand: no" in status and "sync_main" in status
    merged = await call("sync_main")
    assert "a.txt (both sides changed it)" in merged and "<<<<<<< refs/puppy/sync/task" in merged
    assert "unresolved conflicts" in (await call("status")).lower()
    text = (copy / "a.txt").read_text().replace("<<<<<<< refs/puppy/sync/task\n", "")
    text = text.replace("line 11 by the task\n=======\n", "").replace(">>>>>>> refs/puppy/sync/main\n", "")
    (copy / "a.txt").write_text(text.replace("line 11 by Main", "line 11 by both"))
    git(copy, "add", "a.txt")
    assert "Applies to Main's files as they stand: yes" in await call("status")
    assert "on: once this turn ends" in await call("apply_when_done")
    assert tasks.auto_record(sid) is not None
    await rejected(call("wait", tasks=["#{}".format(sid)]), "this task itself")
    edit(sibling_copy / "b.txt", 1, "sibling")
    finish(sibling)
    assert "Done (finished)" in await call("wait", tasks=["all"], wait_s=1)
    runner.hub(sid).test_seq = db.add_event(sid, "user", {"text": "x"})["seq"]
    finish(sid)
    await worker_pass()
    assert "line 11 by both\n" in (path / "a.txt").read_text() and db.get_session(sid) is None
    print("PASS: a task's tools: status, sync_main with markers to resolve, apply when done for "
          "itself and waiting on its siblings")


async def stdio_server():
    for role, names in (("main", task_agent.MAIN_TOOLS), ("task", task_agent.TASK_TOOLS)):
        wire = "\n".join(json.dumps(message) for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "sync_main" if role == "main" else "new_task", "arguments": {}}},
        )) + "\n"
        env = dict(os.environ, PYTHONPATH=str(BASE), PUPPY_TASKS_ROLE=role,
                   PUPPY_TASKS_SOCKET="/nonexistent", PUPPY_TASKS_SESSION_ID="1",
                   PUPPY_TASKS_TURN_ID="t")
        output = subprocess.check_output([sys.executable, "-m", "puppy.task_agent"],
                                         input=wire, env=env, text=True, timeout=20)
        replies = [json.loads(line) for line in output.splitlines()]
        assert replies[0]["result"]["instructions"] == task_agent.instructions(role)
        assert {tool["name"] for tool in replies[1]["result"]["tools"]} == {tool["name"] for tool in names}
        assert replies[2]["error"]["code"] == -32603, "the other role's tool is not served"
    assert "This conversation is a Puppy task" in task_agent.instructions("task")
    assert config.DEFAULT_TASKS_SYSTEM_PROMPT in task_agent.instructions("main")
    print("PASS: the stdio server answers each role with its own tools and instructions")


async def routes():
    from aiohttp.test_utils import TestClient, TestServer
    from puppy.web import build_app
    from puppy_backend.app import build_app as backend_app
    path, parent = project("routes")
    for build in (build_app, backend_app):
        app = build()
        app.on_startup.clear()
        app.on_shutdown.clear()
        app.cleanup_ctx.clear()
        assert {"session-task-auto-apply", "session-task-agent"} <= set(app["puppy_capabilities"])
        client = TestClient(TestServer(app), headers={"X-Puppy-Token": config.get("auth.api_token")})
        await client.start_server()
        try:
            prefix = "/api/sessions/{}/tasks".format(parent)
            with patch.object(runner.SessionHub, "_start_turn", start):
                for bad in ("yes", 1, None):
                    response = await client.post(prefix, json={"prompt": "Bad", "request_id": "bad",
                                                               "auto_apply": bad})
                    assert response.status == 409 and "auto_apply" in (await response.json())["error"]
                response = await client.post(prefix, json={"prompt": "Over HTTP", "request_id":
                                                           app["puppy_role"], "auto_apply": True})
                data = await response.json()
                assert response.status == 200, data
            sid = data["session"]["id"]
            assert data["session"]["task"]["auto_apply"]["phase"] == "working"
            route = "{}/{}/auto-apply".format(prefix, sid)
            response = await client.post(route, json={"enabled": "no"})
            assert response.status == 409 and "true or false" in (await response.json())["error"]
            response = await client.post(route, json={"enabled": False})
            data = await response.json()
            assert response.status == 200 and data["session"]["task"]["auto_apply"] is None
            response = await client.post(route, json={"enabled": True})
            assert (await response.json())["session"]["task"]["auto_apply"]["rounds"] == 0
            listed = await (await client.get(prefix)).json()
            assert next(t for t in listed["tasks"] if t["id"] == sid)["task"]["auto_apply"]
            # the editable policy rides the system-prompt settings
            prompts = (await (await client.get("/api/system-prompt")).json())["system_prompt"]
            assert prompts["tasks"] == prompts["tasks_default"] == config.DEFAULT_TASKS_SYSTEM_PROMPT
            response = await client.patch("/api/system-prompt", json={"tasks": 42})
            assert response.status == 400
            response = await client.patch("/api/system-prompt", json={"tasks": "Own policy"})
            assert (await response.json())["system_prompt"]["tasks"] == "Own policy"
            assert config.get("system_prompt.tasks") == "Own policy"
            config.set_system_prompts("", config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT,
                                      config.DEFAULT_BROWSER_SYSTEM_PROMPT,
                                      config.DEFAULT_TERMINAL_SYSTEM_PROMPT,
                                      config.DEFAULT_VNC_SYSTEM_PROMPT,
                                      config.DEFAULT_SPAWN_SYSTEM_PROMPT)
            assert config.get("system_prompt.tasks") == "Own policy", "left out, it is kept"
            response = await client.patch("/api/system-prompt",
                                          json={"tasks": config.DEFAULT_TASKS_SYSTEM_PROMPT})
            assert response.status == 200
            finish(sid)
            await tasks.durable_workspace_operation(tasks.remove(parent, sid, True), parent)
        finally:
            await client.close()
        print("PASS: {} routes: creation's auto_apply, the switch, the list's record and the "
              "editable task policy".format(app["puppy_role"]))


async def descriptors():
    """Every driver hands the bridge to its engine with its policy."""
    from puppy.drivers.claude import ClaudeDriver
    from puppy.drivers.codex import CodexDriver
    from puppy.drivers.opencode import OpenCodeDriver
    task_agent._server = object()
    try:
        descriptor = task_agent.turn_mcp(5, "turn", "task")
        assert descriptor["env"]["PUPPY_TASKS_ROLE"] == "task"
        assert task_agent.turn_mcp(5, "turn", "") is None
        session = {"cwd": str(ROOT)}
        claude = ClaudeDriver().build_cmd(session, True, "p", "t", task_mcp=descriptor)
        servers = json.loads(claude[claude.index("--mcp-config") + 1])["mcpServers"]
        assert servers["puppy_tasks"]["args"] == ["-m", "puppy.task_agent"]
        assert config.DEFAULT_TASKS_SYSTEM_PROMPT in claude[claude.index("--append-system-prompt") + 1]
        codex = CodexDriver()
        argv = codex.build_cmd(session, True, "p", "t", task_mcp=descriptor)
        assert any(arg.startswith("mcp_servers.puppy_tasks.command=") for arg in argv)
        prompt = codex.turn_context(session, True, "p", "t", task_mcp=descriptor)["prompt"]
        assert "<puppy_task_policy>\n" + config.DEFAULT_TASKS_SYSTEM_PROMPT in prompt
        opencode = OpenCodeDriver().turn_context(session, True, "p", "t", task_mcp=descriptor)
        assert [server["name"] for server in opencode["mcp_servers"]] == ["puppy_tasks"]
        assert codex.build_cmd(session, True, "p", "t", task_mcp=descriptor, tool={"tool": "compact"}) \
            == [codex.binary, "app-server", "--stdio"], "a maintenance turn gets no bridges"
    finally:
        task_agent._server = None
    # roles follow the session
    path, parent = project("roles")
    sid, _ = await new_task(parent, "roles", auto=False)
    assert task_agent.role_for(db.get_session(parent)) == "main"
    assert task_agent.role_for(db.get_session(sid)) == "task"
    await rejected(tasks.set_enabled(parent, False), "Remove all")
    finish(sid)
    print("PASS: claude, codex and opencode carry the task bridge and its policy; roles follow "
          "the session")


async def main():
    config.ensure_dirs()
    from puppy.drivers.base import Driver
    with patch.object(Driver, "refresh_model_options", AsyncMock()):
        await clean_apply_and_fold()
        await merged_with_main_drift()
        await conflict_rounds()
        await rounds_bound_and_switch_off()
        await busy_main_and_parked_wait()
        await contention()
        await sync_main_for_the_task()
        await persisted_shape()
        await bridge_main()
        await bridge_task()
        await descriptors()
        await routes()
    await stdio_server()


if __name__ == "__main__":
    import shutil
    try:
        asyncio.run(main())
    finally:
        for row in db.list_sessions(include_archived=True):
            if workspaces.is_temporary(row):
                workspaces.remove_temporary(row)
        shutil.rmtree(ROOT)
