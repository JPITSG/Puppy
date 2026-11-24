#!/usr/bin/env python3
"""Real filesystem events, recovery, fallback and payloads on both runtimes."""
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import sys
import threading
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("session-directories-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import agent_notes, config, db, runner, session_directories as directories, session_git
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


async def until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(.01)
    raise AssertionError("directory update did not arrive")


async def contract(factory, fallback=False):
    root = ROOT / ("fallback" if fallback else factory.__module__)
    project = root / "parent" / "project"
    project.mkdir(parents=True)
    absent = root / "missing" / "nested"
    link = root / "alias"
    link.symlink_to(project, target_is_directory=True)
    ids = [db.create_session("Demo", "codex", str(p), "", "", "", "default")
           for p in (project, project, absent, link)]
    app = factory()
    assert directories._lifecycle in app.cleanup_ctx
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    sent, calls = [], []
    original_inspect = directories.inspect
    loop_thread = threading.get_ident()

    def inspect(cwd):
        assert threading.get_ident() != loop_thread, "filesystem read on event loop"
        calls.append(cwd)
        return original_inspect(cwd)

    def no_watches(watch):
        watch.fd = -1
        watch.names = {}

    with ExitStack() as stack:
        stack.enter_context(patch.object(directories, "inspect", inspect))
        stack.enter_context(patch.object(directories, "CHECK_SECONDS", .1 if fallback else 3600))
        stack.enter_context(patch.object(directories, "DEBOUNCE_SECONDS", .01))
        stack.enter_context(patch.object(runner, "publish_state", side_effect=sent.append))
        if fallback:
            stack.enter_context(patch.object(directories._Inotify, "__init__", no_watches))
        worker = directories._lifecycle(app)
        await worker.__anext__()
        monitor = directories._monitor
        try:
            assert directories.record(project) is None
            if not fallback:
                assert monitor.watch.fd >= 0, "event lane requires Linux inotify"
            async with TestClient(TestServer(app)) as client:
                await until(lambda: len(sent) > 0)
                assert calls.count(str(project)) == 1, "same cwd must be checked only once"
                expected = {ids[0]: True, ids[1]: True, ids[2]: False, ids[3]: True}
                headers = {"X-Puppy-Token": config.get("auth.api_token")}
                response = await client.get("/api/sessions", headers=headers)
                assert response.status == 200
                rows = (await response.json())["sessions"]
                assert {s["id"]: s["cwd_available"] for s in rows} == expected
                response = await client.get("/api/sessions/{}".format(ids[2]), headers=headers)
                assert (await response.json())["session"]["cwd_available"] is False
                assert (await client.get("/api/sessions")).status == 401

                # Unrelated files and content edits generate no events we
                # care about. Payload reads never stat these directories.
                before = None
                while not fallback and before != len(calls):    # every pass asked for so far is over
                    before = len(calls)
                    await asyncio.sleep(.15)
                (project / "file").write_text("first")
                (project / "file").write_text("second")
                (root / "unrelated").mkdir()
                directories.record(str(project))
                runner.sessions_payload()
                await asyncio.sleep(.04)
                if not fallback:
                    assert len(calls) == before, calls

                # Moving an ancestor invalidates both the path and symlink,
                # without waiting for the periodic pass (one hour here).
                project.parent.rename(root / "moved")
                await until(lambda: directories.record(str(project)) is False and
                            directories.record(str(link)) is False)
                (root / "moved").rename(project.parent)
                await until(lambda: directories.record(str(project)) is True and
                            directories.record(str(link)) is True)
                absent.mkdir(parents=True)
                await until(lambda: directories.record(str(absent)) is True)
                absent.rmdir()
                absent.write_text("not a directory")
                await until(lambda: directories.record(str(absent)) is False)
                absent.unlink()
                absent.mkdir()
                await until(lambda: directories.record(str(absent)) is True)

                link.unlink()
                link.symlink_to(root / "gone")
                await until(lambda: directories.record(str(link)) is False)
                (root / "gone").mkdir()
                await until(lambda: directories.record(str(link)) is True)
                assert any(s["cwd_available"] is False for msg in sent for s in msg["sessions"])
                assert all(s["cwd_available"] is True for s in sent[-1]["sessions"])

                # A payload requested from a search thread wakes a previously
                # unknown path; no periodic pass or filesystem event needed.
                later = ROOT / "data"
                late_id = db.create_session("Later", "codex", str(later), "", "", "", "default")
                ids.append(late_id)
                value = await asyncio.get_running_loop().run_in_executor(None, directories.record, str(later))
                assert value is None
                await until(lambda: directories.record(str(later)) is True)

                # Cache and watches forget removed sessions on the next pass.
                for sid in ids:
                    db.delete_session(sid)
                monitor.again()
                await until(lambda: not directories._records)
                assert not monitor.watch.names
        finally:
            await worker.aclose()
            assert monitor.watch.fd == -1 and directories._monitor is None
            for sid in ids:
                db.delete_session(sid)
    print("PASS: {} {}".format(factory.__module__, "periodic fallback" if fallback else "filesystem events"))


async def seen_there(factory):
    """What else the list says of a folder is what was last seen there: its
    agent notes, live, and where it really is. A folder whose check does not
    come back keeps nobody else's answer, and its own is taken in whenever it
    comes; a check that outlasts the monitor keeps the watches' descriptor to
    itself."""
    root = ROOT / ("seen-" + factory.__module__.replace(".", "-"))
    project, slow = root / "project", root / "slow"
    project.mkdir(parents=True)
    slow.mkdir()
    (project / "AGENTS.md").write_text("notes")
    link = root / "alias"
    link.symlink_to(project, target_is_directory=True)
    ids = [db.create_session("Seen", "codex", str(p), "", "", "", "default") for p in (project, slow, link)]
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    gate, held, sent, fd = threading.Event(), threading.Event(), [], None
    original = directories.inspect

    def inspect(cwd):
        if cwd == str(slow):
            held.set()
            gate.wait(20)
        return original(cwd)
    # nobody has looked: nothing is known, and asking goes nowhere
    with patch.object(os, "stat", side_effect=AssertionError("a payload went there")):
        assert agent_notes.present(str(project)) == []
        assert directories.real(str(link)) == str(link)
    directories.noted(str(project), ["CLAUDE.md"])
    directories.reached(str(link), "/elsewhere")
    assert agent_notes.present(str(project)) == [] and directories.real(str(link)) == str(link)
    with ExitStack() as stack:
        stack.enter_context(patch.object(directories, "CHECK_SECONDS", 3600))
        stack.enter_context(patch.object(directories, "DEBOUNCE_SECONDS", .01))
        stack.enter_context(patch.object(directories, "PROMPT_SECONDS", .1))
        stack.enter_context(patch.object(directories, "inspect", inspect))
        stack.enter_context(patch.object(runner, "publish_state", side_effect=sent.append))
        worker = directories._lifecycle(app)
        await worker.__anext__()
        monitor = directories._monitor
        try:
            await until(lambda: directories.record(str(project)) is True)
            assert held.is_set() and directories.record(str(slow)) is None
            row = lambda: {s["id"]: s for s in runner.sessions_payload()["sessions"]}
            assert row()[ids[0]]["agent_notes"] == row()[ids[2]]["agent_notes"] == ["AGENTS.md"]
            assert row()[ids[1]]["agent_notes"] == []
            assert directories.real(str(link)) == str(project)
            # its notes come and go on the list by themselves, through a link too
            before = len(sent)
            (project / "CLAUDE.md").symlink_to("AGENTS.md")
            await until(lambda: agent_notes.present(str(link)) == ["AGENTS.md", "CLAUDE.md"])
            assert len(sent) > before
            (project / "AGENTS.md").unlink()
            await until(lambda: agent_notes.present(str(project)) == [])
            # so do other folders, all that time
            project.rename(root / "moved")
            await until(lambda: directories.record(str(project)) is False)
            (root / "moved").rename(project)
            await until(lambda: directories.record(str(project)) is True)
            link.unlink()
            (root / "other").mkdir()
            link.symlink_to(root / "other")
            await until(lambda: directories.real(str(link)) == str(root / "other"))
            assert directories.record(str(slow)) is None and list(monitor.checking) == [str(slow)]
            # what somebody has just seen there needs no look of its own
            directories.noted(str(slow), ["AGENTS.md"])
            directories.reached(str(slow), "/elsewhere")
            assert agent_notes.present(str(slow)) == ["AGENTS.md"] and directories.real(str(slow)) == "/elsewhere"
            # it is taken in whenever it comes, and asks nobody anything anew
            # (nor does anything else now: a suite beside this one may touch
            # the folders they share)
            changed, calm, looked = monitor.watch.changed, 0, []
            monitor.watch.changed = lambda: changed() and False
            while calm < 20:    # every pass that was asked for is over
                await asyncio.sleep(.02)
                calm = 0 if monitor.wanted or monitor.wake.is_set() or len(monitor.checking) > 1 else calm + 1
            with patch.object(directories, "inspect", lambda cwd: looked.append(cwd) or original(cwd)):
                gate.set()
                await until(lambda: directories.record(str(slow)) is True)
                await asyncio.sleep(.2)
            assert not looked, looked
            monitor.watch.changed = changed
            assert agent_notes.present(str(slow)) == [] and directories.real(str(slow)) == str(slow)
            # one more that does not come back, across the end
            gate.clear()
            held.clear()
            slow.rename(root / "gone")
            await until(held.is_set)
            monitor.watch._borrow()
            fd = monitor.watch.fd
        finally:
            gate.set() if fd is None else None
            await asyncio.wait_for(worker.aclose(), 5)
            if fd is not None:
                assert monitor.watch.fd == -1 and monitor.watch.leftover == fd
                os.fstat(fd)    # still theirs
                monitor.watch._return()
                assert monitor.watch.leftover == -1
                try:
                    os.fstat(fd)
                except OSError:
                    pass
                else:
                    raise AssertionError("the watches' descriptor was left open")
            gate.set()
            for sid in ids:
                db.delete_session(sid)
    print("PASS: {} shows notes and real places as last seen, live, and a check that hangs keeps nobody".format(
        factory.__module__))


async def paused_sessions(factory):
    """A session whose working folder is gone is paused on its node, with the
    real monitor and real filesystem events: a message, a session tool and a
    held prompt's re-send are refused with the reason and start nothing; a
    queue that comes up holds as it stands, applies nothing and says nothing
    is waiting to start; a task's copy pauses like a folder, a scratch Main
    never does (its next turn makes it a new folder). When the folder comes
    back the waiting queue moves on by itself, its first prompt starting."""
    from puppy import session_tasks
    root = ROOT / ("paused-" + factory.__module__.replace(".", "-"))
    project = root / "project"
    project.mkdir(parents=True)
    sid = db.create_session("Paused", "claude", str(project), "", "", "", "default")
    tid = db.create_session("Task", "claude", str(project / "copy"), "", "", "", "default",
                            workspace_kind="temporary")
    scratch = db.create_session("Scratch", "claude", str(root / "scratch-gone"), "", "", "", "default",
                                workspace_kind="temporary")
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    started = []
    hubs = {key: runner.hub(key) for key in (sid, tid, scratch)}
    for key, h in hubs.items():
        h._start_turn = lambda item, h=h, key=key: (started.append((key, item)),
                                                    setattr(h, "status", "running"))
    real_record = session_tasks.record
    with ExitStack() as stack:
        stack.enter_context(patch.object(directories, "CHECK_SECONDS", 3600))
        stack.enter_context(patch.object(directories, "DEBOUNCE_SECONDS", .01))
        stack.enter_context(patch.object(runner, "publish_state"))
        stack.enter_context(patch.object(session_tasks, "record",
                                         lambda key: {"parent": sid} if key == tid else real_record(key)))
        looked = []
        stack.enter_context(patch.object(session_git, "request", looked.append))
        worker = directories._lifecycle(app)
        await worker.__anext__()
        try:
            directories.record(str(project))
            await until(lambda: directories.record(str(project)) is True)
            assert runner.folder_paused(db.get_session(sid)) == ""

            project.rename(root / "elsewhere")
            await until(lambda: directories.record(str(project)) is False)
            await until(lambda: directories.record(str(project / "copy")) is False and
                        directories.record(str(root / "scratch-gone")) is False)
            h = hubs[sid]
            assert runner.folder_paused(db.get_session(sid)) == runner.FOLDER_PAUSED
            assert h.send_message("go on") == {"error": runner.FOLDER_PAUSED}
            assert h.send_loop("again", 2) == {"error": runner.FOLDER_PAUSED}
            assert h.request_tool("compact") == {"error": runner.FOLDER_PAUSED}
            assert not h.queue and not started, "nothing was queued or started"
            h.held = ["held prompt"]
            assert h.requeue_held(0, "held prompt") == {"error": runner.FOLDER_PAUSED}
            assert h.held == ["held prompt"], "a refused re-send stays held"
            h.held = []

            # a turn that ends with prompts waiting holds them as they stand
            h.status = "running"
            h.queue = ["first", "second"]
            assert h._prompt_waiting() is False, "nothing behind the turn could start"
            assert h._take_next_turn() is None
            assert h.status == "idle" and h.queue == ["first", "second"] and not started

            # a task's copy pauses like a folder; a scratch Main never does
            assert hubs[tid].send_message("in the task") == {"error": runner.FOLDER_PAUSED}
            assert runner.folder_paused(db.get_session(scratch)) == ""
            assert hubs[scratch].send_message("make me a folder") == {"queued": False}
            assert started == [(scratch, "make me a folder")]

            # the folder back: the waiting queue moves on by itself, and its
            # Git mark, which said that it could not be checked, is looked at
            assert not looked
            (root / "elsewhere").rename(project)
            await until(lambda: (sid, "first") in started)
            assert h.queue == ["second"] and h.status == "running"
            assert str(project) in looked, looked
            assert runner.folder_paused(db.get_session(sid)) == ""
        finally:
            await worker.aclose()
            for key in (sid, tid, scratch):
                runner.drop_hub(key)
                db.delete_session(key)
    print("PASS: {} pauses a session whose folder is gone and moves its queue on when it is back".format(
        factory.__module__))


async def main():
    try:
        config.load()
        db.connect()
        with patch.object(directories.os, "stat", side_effect=PermissionError):
            assert directories.inspect("/denied") is False
        with patch.object(directories.os, "access", return_value=False):
            assert directories.inspect(str(ROOT)) is False
        await contract(build_app)
        await contract(backend_app)
        await contract(backend_app, fallback=True)
        await seen_there(build_app)
        await seen_there(backend_app)
        await paused_sessions(build_app)
        await paused_sessions(backend_app)
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
