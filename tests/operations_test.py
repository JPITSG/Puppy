#!/usr/bin/env python3
"""Cancellation races, worker ownership, rollback and both authenticated runtimes.

Only private fixtures, local HTTP and stub processes; no engines or network.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("cancel-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer
from puppy import (auth, backends, browser, cli_upgrade, config, db, engine_defaults,
                   notify, operations, runner, session_tasks, snapshots, uploads, workspaces)
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


def identity():
    import secrets
    return secrets.token_hex(20)


async def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition did not settle"
        await asyncio.sleep(.01)


async def control(client, key, method="DELETE"):
    response = await client.request(method, "/api/operations/" + key)
    assert response.status == 200, await response.text()
    return await response.json()


async def core():
    app = web.Application()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    @operations.cancellable
    async def job(request):
        try:
            entered.set()
            await operations.wait(release.wait())
            operations.commit()
            events.append("commit")
            return web.json_response({"ok": True})
        finally:
            cleanup.set()

    app.router.add_post("/work", job)
    operations.register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        key = identity()
        # DELETE-before-POST must prevent the handler from running at all.
        assert (await control(client, key))["state"] == "cancelling"
        response = await client.post("/work", headers={operations.HEADER: key})
        assert response.status == 409 and (await response.json())["cancelled"]
        assert not entered.is_set()
        key = identity()
        request = asyncio.create_task(client.post("/work", headers={operations.HEADER: key}))
        await entered.wait()
        assert (await control(client, key))["state"] == "cancelling"
        response = await request
        assert (await response.json())["cancelled"] and cleanup.is_set() and not events
        # Repeated cancellation and duplicate submission never restart work.
        assert (await control(client, key))["state"] == "finished"
        response = await client.post("/work", headers={operations.HEADER: key})
        assert response.status == 409
        release.set()
        response = await client.post("/work", headers={operations.HEADER: identity()})
        assert response.status == 200 and events == ["commit"]
    finally:
        await client.close()

    # A thread cannot be interrupted by cancelling its asyncio Future. Its
    # owner must wait before releasing locks or reclaiming staging.
    op = operations.Operation()
    token = operations._current.set(op)
    started, finish, stopped = threading.Event(), threading.Event(), threading.Event()
    def thread():
        started.set()
        try:
            finish.wait(5)
            operations.checkpoint()
        finally:
            stopped.set()
    try:
        task = asyncio.create_task(operations.to_thread(thread))
        await until(started.is_set)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        finish.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert stopped.is_set()
        op.cancel()
        try:
            operations.commit()
        except operations.Cancelled:
            pass
        else:
            raise AssertionError("cancelled work was allowed to commit")
    finally:
        finish.set()
        operations._current.reset(token)
    op = operations.Operation()
    token = operations._current.set(op)
    try:
        operations.commit()
        assert op.cancel() == "finishing"
        operations.checkpoint()
    finally:
        operations._current.reset(token)
    # Waiting behind another operation is cancellable without releasing its lock.
    mutex = asyncio.Lock(); await mutex.acquire()
    op = operations.Operation(); token = operations._current.set(op)
    async def waiting():
        async with operations.lock(mutex):
            raise AssertionError("cancelled waiter entered the critical section")
    try:
        task = asyncio.create_task(waiting())
        await asyncio.sleep(.01); op.cancel()
        try:
            await task
        except operations.Cancelled:
            pass
        assert mutex.locked()
        mutex.release()
        assert not mutex._waiters
    finally:
        operations._current.reset(token)
    # Acquisition winning the race still releases that waiter's own lock.
    op = operations.Operation(); token = operations._current.set(op)
    class RacingLock(asyncio.Lock):
        async def acquire(self):
            result = await super().acquire()
            op.cancel()
            return result
    mutex = RacingLock()
    try:
        await waiting()
    except operations.Cancelled:
        pass
    finally:
        operations._current.reset(token)
    assert not mutex.locked()
    print("PASS: early/duplicate cancellation, commit/lock races and drained workers")


async def runtimes():
    config.ensure_dirs()
    db.connect()
    for build in (build_app, backend_app):
        app = build()
        # Exercise real routes without starting engine probes or workers.
        app.on_startup.clear(); app.on_shutdown.clear(); app.cleanup_ctx.clear()
        assert operations.CAPABILITY in app["puppy_capabilities"]
        client = TestClient(TestServer(app), headers={"X-Puppy-Token": config.get("auth.api_token")})
        await client.start_server()
        try:
            async with client.session.delete(str(client.make_url("/api/operations/" + identity())),
                                             headers={"X-Puppy-Token": "wrong"}) as denied:
                assert denied.status == 401
            source = Path(workspaces.create_temporary())
            (source / "file").write_text("original")
            (source / "link").symlink_to("file")
            sid = db.create_session("scratch", "codex", str(source), "", "", "blue", "workspace-write",
                                    workspace_kind="temporary")
            target = ROOT / ("destination-" + identity())
            entered = threading.Event()
            original = operations.copy2
            def slow_copy(*args, **kwargs):
                result = original(*args, **kwargs)
                entered.set()
                while True:
                    operations.checkpoint()
                    time.sleep(.01)
                return result
            key = identity()
            with patch.object(operations, "copy2", side_effect=slow_copy):
                request = asyncio.create_task(client.post("/api/sessions/%s/workspace/move" % sid,
                    json={"destination": str(target)}, headers={operations.HEADER: key}))
                await until(entered.is_set)
                assert session_tasks.busy()
                assert (await control(client, key))["state"] == "cancelling"
                response = await request
                assert response.status == 409 and (await response.json())["cancelled"]
            assert not target.exists() and (source / "file").read_text() == "original"
            assert (source / "link").is_symlink() and db.get_session(sid)["cwd"] == str(source)
            assert not session_tasks.busy()
            workspaces.remove_temporary(db.get_session(sid)); db.delete_session(sid); runner.drop_hub(sid)

            # Task preparation is cancelled before starting any model turn.
            project = ROOT / ("project-" + identity()); project.mkdir()
            session_tasks._git(str(project), "init", "--quiet")
            (project / "file").write_text("main stays untouched")
            session_tasks._git(str(project), "add", "file")
            session_tasks._git(str(project), "commit", "-m", "initial")
            sid = db.create_session("Main", "codex", str(project), "", "", "blue", "workspace-write")
            driver = SimpleNamespace(refresh_model_options=AsyncMock())
            entered.clear()
            real_copy = session_tasks._copy_project
            def slow_project(*args):
                result = real_copy(*args)
                entered.set()
                while True:
                    operations.checkpoint()
                    time.sleep(.01)
                return result
            before = set(workspaces.temporary_root().glob("session-*"))
            key = identity()
            with patch("puppy.drivers.get_driver", return_value=driver), \
                    patch.object(engine_defaults, "for_session", return_value={"model": "", "effort": "", "permission_mode": "workspace-write"}), \
                    patch.object(session_tasks, "_copy_project", side_effect=slow_project):
                request = asyncio.create_task(client.post("/api/sessions/%s/tasks" % sid,
                    json={"prompt": "A task", "request_id": identity()}, headers={operations.HEADER: key}))
                await until(entered.is_set)
                await control(client, key)
                response = await request
                assert response.status == 409 and (await response.json())["cancelled"]
            assert not session_tasks.children(sid) and not session_tasks.busy()
            assert set(workspaces.temporary_root().glob("session-*")) == before
            assert (project / "file").read_text() == "main stays untouched"
            db.delete_session(sid); runner.drop_hub(sid)

            # Browser startup cancels its async dial and closes its new identity.
            instance = SimpleNamespace(browser_id="A1A1", origin="user")
            entered_async = asyncio.Event()
            async def startup():
                entered_async.set()
                await asyncio.Event().wait()
            instance.ensure_started = startup
            registry = SimpleNamespace(close=AsyncMock())
            async def create(_origin):
                return await browser.BrowserRegistry._start_created(registry, instance)
            with patch.object(browser, "manager", return_value=SimpleNamespace(create=create)):
                key = identity()
                request = asyncio.create_task(client.post("/api/browser/instances", json={}, headers={operations.HEADER: key}))
                await entered_async.wait()
                await control(client, key)
                response = await request
                assert response.status == 409 and (await response.json())["cancelled"]
                registry.close.assert_awaited_once()

            # Real catalog/profile cleanup is complete before the reply arrives.
            config.set_value("browser.enabled", True)
            entered_async.clear(); created_ids = []
            async def stalled_browser(instance):
                created_ids.append(instance.browser_id)
                (Path(instance._subdir("profile")) / "partial").write_text("private fixture")
                entered_async.set()
                await asyncio.Event().wait()
            with patch.object(browser.Manager, "ensure_started", new=stalled_browser):
                key = identity()
                request = asyncio.create_task(client.post("/api/browser/instances", json={}, headers={operations.HEADER: key}))
                await entered_async.wait()
                await control(client, key)
                response = await request
                assert response.status == 409 and (await response.json())["cancelled"]
            created_id = created_ids[0]
            assert created_id not in browser.manager().instances
            assert not Path(browser._instance_root(created_id)).exists()
            assert browser.manager().records[created_id]["closed_at"] is not None

            # Cancellation remains usable while snapshots freeze other writes.
            app["puppy_snapshot_busy"] = "export"
            assert (await control(client, identity()))["state"] == "cancelling"
            app["puppy_snapshot_busy"] = None
            print("PASS: %s cancellation routes, copy cleanup, task/browser cleanup and snapshot gate" % app["puppy_role"])
        finally:
            await client.close()


async def backup_cancellation():
    app = build_app()
    app.on_startup.clear(); app.on_shutdown.clear(); app.cleanup_ctx.clear()
    auth.create_user("cancel-user", "private-password")
    cookie = auth.issue_session("cancel-user")
    client = TestClient(TestServer(app), headers={"Cookie": auth.COOKIE_NAME + "=" + cookie})
    await client.start_server()
    source = Path(workspaces.create_temporary()); (source / "original").write_text("keep me")
    sid = db.create_session("Backup", "codex", str(source), "", "", "blue", "workspace-write", workspace_kind="temporary")
    try:
        archive = snapshots.create_archive({})
        before = set(snapshots.work_root().iterdir())
        before_scratch = set(workspaces.temporary_root().iterdir())
        for target, path, kwargs in (
                ("_sha256", "/api/snapshot/export", {"json": {"ui": {}}}),
                ("_prepare_mirrors", "/api/snapshot/import", {"data": Path(archive["path"]).read_bytes()})):
            entered = threading.Event()
            original = getattr(snapshots, target)
            def blocked(*args, **kw):
                original(*args, **kw)
                entered.set()
                while True:
                    operations.checkpoint()
                    time.sleep(.01)
            key = identity()
            with patch.object(snapshots, target, side_effect=blocked):
                request = asyncio.create_task(client.post(path, headers={operations.HEADER: key}, **kwargs))
                await until(entered.is_set)
                assert (await control(client, key))["state"] == "cancelling"
                response = await request
                assert response.status == 409 and (await response.json())["cancelled"]
            assert set(snapshots.work_root().iterdir()) == before
            assert set(workspaces.temporary_root().iterdir()) == before_scratch
            assert db.get_session(sid)["cwd"] == str(source)
            assert (source / "original").read_text() == "keep me"
            assert not app.get("puppy_snapshot_busy")
        snapshots.discard_export(archive)
    finally:
        workspaces.remove_temporary(db.get_session(sid)); db.delete_session(sid)
        await client.close()
    print("PASS: cookie-authenticated backup/restore cancellation and staging cleanup")


async def subprocesses():
    # A shell with a child must lose its whole process group on cancellation.
    pidfile = ROOT / "child-pid"
    op = operations.Operation(); token = operations._current.set(op)
    try:
        task = asyncio.create_task(notify.run_local("sleep 60 & echo $! > '%s'; wait" % pidfile, {}))
        await until(pidfile.exists)
        pid = int(pidfile.read_text())
        op.cancel()
        try:
            await task
        except operations.Cancelled:
            pass
        else:
            raise AssertionError("command was not cancelled")
        await until(lambda: not Path('/proc/%s/stat' % pid).exists() or
                    Path('/proc/%s/stat' % pid).read_text().split()[2] == 'Z')
    finally:
        operations._current.reset(token)
    stop = asyncio.Event()
    task = asyncio.create_task(cli_upgrade._spawn(["/bin/sh", "-c", "echo before-stop; sleep 60"], stop))
    await asyncio.sleep(.15); stop.set()
    try:
        await task
    except cli_upgrade.UpdaterFailure as exc:
        assert "stopped by user" in str(exc) and "before-stop" in exc.output
    else:
        raise AssertionError("updater was not stopped")
    # Cancel during the browser's availability probe, before it owns a profile.
    pidfile = ROOT / "probe-child-pid"
    executable = ROOT / "browser-probe"
    executable.write_text("#!/bin/sh\nsleep 60 &\necho $! > '%s'\nwait\n" % pidfile)
    executable.chmod(0o700)
    task = asyncio.create_task(browser._run_version(str(executable)))
    await until(pidfile.exists)
    pid = int(pidfile.read_text())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("browser probe was not cancelled")
    await until(lambda: not Path('/proc/%s/stat' % pid).exists() or
                Path('/proc/%s/stat' % pid).read_text().split()[2] == 'Z')
    # Stop compares the exact run and no longer paints Stopping after completion.
    driver = SimpleNamespace(key="cancel-fixture")
    record = {"state": "running", "started_at": 123.5, "stop": asyncio.Event()}
    with patch.dict(cli_upgrade._runs, {driver.key: record}), patch.object(cli_upgrade, "supported", return_value=True):
        try:
            cli_upgrade.cancel(driver, 123.0)
        except RuntimeError:
            pass
        else:
            raise AssertionError("stale updater cancellation was accepted")
        assert not record["stop"].is_set()
        cli_upgrade.cancel(driver, 123.5)
        assert cli_upgrade.state(driver)["upgrade_stopping"]
        record["state"] = "idle"
        assert not cli_upgrade.state(driver)["upgrade_stopping"]
    print("PASS: command/probe child cleanup, retained updater transcript and stale Stop guards")


async def attachment_cancellation():
    source = ROOT / "attachment.txt"; source.write_bytes(b"source attachment")
    entered = threading.Event()
    original = operations.copyfileobj
    def copying(*args):
        original(*args)
        entered.set()
        while True:
            operations.checkpoint()
            time.sleep(.01)
    op = operations.Operation(); token = operations._current.set(op)
    try:
        with patch.object(uploads, "_attachment_upload_ids", return_value={"test-upload"}), \
                patch.object(uploads, "_staged_file", return_value=source), \
                patch.object(operations, "copyfileobj", side_effect=copying):
            task = asyncio.create_task(operations.to_thread(uploads.adopt_attachments, 9000, 9001, "prompt"))
            await until(entered.is_set)
            op.cancel()
            try:
                await task
            except operations.Cancelled:
                pass
            else:
                raise AssertionError("attachment copy was not cancelled")
        assert not (Path(config.DATA_DIR) / "uploads/9001/test-upload").exists()
        assert source.read_bytes() == b"source attachment"
    finally:
        operations._current.reset(token)
    print("PASS: cancelled attachment adoption cleans copies and preserves staged originals")


async def relayed_command():
    entered = asyncio.Event()
    fail_cleanup = False
    requests = []
    @web.middleware
    async def authenticated(request, handler):
        assert request.headers.get("X-Puppy-Token") == "private-fixture"
        requests.append((request.method, request.path, request.headers.get(operations.HEADER)))
        return await handler(request)
    @operations.cancellable
    async def command(request):
        entered.set()
        try:
            await operations.wait(asyncio.Event().wait())
        except operations.Cancelled:
            if fail_cleanup:
                raise web.HTTPInternalServerError(text="cleanup failed")
            raise
        return web.json_response({"ok": True})
    app = web.Application(middlewares=[authenticated])
    app.router.add_post("/api/notify/exec", command)
    operations.register(app)
    server = TestServer(app); await server.start_server()
    be = {"id": 700, "name": "Demo", "token": "private-fixture"}
    try:
        async with ClientSession() as client:
            with patch.object(backends, "client", return_value=client), \
                    patch.object(backends, "get_backend", return_value=be), \
                    patch.object(backends, "backend_is_online", return_value=True), \
                    patch.object(backends, "_backend_capabilities", return_value=[operations.CAPABILITY]), \
                    patch.object(backends, "_ordered_backend_urls", return_value=[str(server.make_url("/")).rstrip("/")]), \
                    patch.object(backends, "_publish_active_url"):
                for fail_cleanup in (False, True):
                    entered.clear(); requests.clear()
                    op = operations.Operation(); token = operations._current.set(op)
                    try:
                        task = asyncio.create_task(backends.notify_exec(700, "fixture", {}))
                        await entered.wait()
                        op.cancel()
                        try:
                            await task
                        except operations.Cancelled:
                            assert not fail_cleanup
                        except RuntimeError as exc:
                            assert fail_cleanup and "did not confirm cancellation" in str(exc)
                        else:
                            raise AssertionError("relay ignored cancellation")
                        assert requests[1][0:2] == ("DELETE", "/api/operations/" + requests[0][2])
                    finally:
                        operations._current.reset(token)
    finally:
        await server.close()
    print("PASS: authenticated relayed cancellation drains the remote and preserves cleanup errors")


async def side_question():
    sid = db.create_session("Question", "claude", str(ROOT), "", "", "blue", "default")
    hub = runner.hub(sid)
    data = []
    class Input:
        def is_closing(self): return False
        def write(self, block): data.append(json.loads(block))
        async def drain(self): pass
    hub.proc = SimpleNamespace(stdin=Input(), returncode=None)
    hub.status = "running"; hub._active_turn_id = "turn-1"; hub._turn_generation = 1
    record = {"request_id": "question-12345678", "turn_id": "turn-1", "generation": 1,
              "status": "pending", "question": "Why?", "asked_at": time.time()}
    hub._side_questions[record["request_id"]] = record
    try:
        assert "error" in await hub.cancel_question(record["request_id"], "different-turn")
        with patch.object(hub, "_publish_steering_state"):
            result = await hub.cancel_question(record["request_id"], "turn-1")
            assert result["status"] == "cancelled"
            assert data[0]["type"] == "control_cancel_request"
            assert hub.status == "running" and not hub.interrupted
            assert not hub._side_question_history()
            hub._handle_side_question_result({"request_id": record["request_id"], "ok": True, "text": "late"})
            assert record["status"] == "failed" and record["answer"] == ""
            assert (await hub.cancel_question(record["request_id"], "turn-1"))["ok"]
            assert len(data) == 1
            # A native answer may win while drain yields to the reader.
            record.update(status="pending")
            async def answered_during_drain():
                hub._settle_side_question(record, ok=True, text="answer won")
            hub.proc.stdin.drain = answered_during_drain
            result = await hub.cancel_question(record["request_id"], "turn-1")
            assert result["status"] == "answered" and record["answer"] == "answer won"
            assert hub._side_question_history()[0]["response"] == "answer won"
    finally:
        hub.proc = None; hub.status = "idle"; runner.drop_hub(sid); db.delete_session(sid)
    print("PASS: native side-question withdrawal, late answers and main-turn isolation")


async def main():
    try:
        await core()
        await runtimes()
        await backup_cancellation()
        await subprocesses()
        await attachment_cancellation()
        await relayed_command()
        await side_question()
    finally:
        db.connect().close()
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == '__main__':
    asyncio.run(main())
