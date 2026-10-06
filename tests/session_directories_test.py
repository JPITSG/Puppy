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
from puppy import config, db, runner, session_directories as directories
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


async def until(predicate):
    for _ in range(200):
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
                await asyncio.sleep(.05)
                before = len(calls)
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
                monitor.wake.set()
                await until(lambda: not directories._records)
                assert not monitor.watch.names
        finally:
            await worker.aclose()
            assert monitor.watch.fd == -1 and directories._monitor is None
            for sid in ids:
                db.delete_session(sid)
    print("PASS: {} {}".format(factory.__module__, "periodic fallback" if fallback else "filesystem events"))


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
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
