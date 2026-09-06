#!/usr/bin/env python3
"""Completion outcome routing, authenticated settings, and persistence checks."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
TEST_ROOT = private_root("notify-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, backends, config, db, notify
from puppy import web as webui
from backend.puppy_backend.app import build_app as backend_app


def validation():
    config.load()
    before = config.export_data()
    disk = Path(config.CONFIG_PATH).read_bytes()
    for patch_value in ({"command": "old"}, {"success_command": None},
                        {"failure_command": "x" * 1001}, {"failure_command": "bad\x00"},
                        {"backend": -1}, {"backend": True}, {"enabled": "yes"}):
        try:
            config.set_notify(patch_value)
        except ValueError:
            pass
        else:
            raise AssertionError(patch_value)
    with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
        try:
            config.set_notify({"success_command": "success", "failure_command": "failure"})
        except OSError:
            pass
        else:
            raise AssertionError("write failure was ignored")
    assert config.export_data() == before and Path(config.CONFIG_PATH).read_bytes() == disk
    old = config.export_data()
    old["notify"] = {"enabled": True, "backend": 0, "command": "old"}
    Path(config.CONFIG_PATH).write_text(json.dumps(old))
    config._config = None
    try:
        config.load()
    except ValueError:
        pass
    else:
        raise AssertionError("previous persisted shape was accepted")
    assert json.loads(Path(config.CONFIG_PATH).read_text()) == old
    Path(config.CONFIG_PATH).write_bytes(disk)
    config.load()


async def routing():
    config.set_notify({"enabled": True, "backend": 0,
                       "success_command": "success {session} {status}",
                       "failure_command": "failure {session} {status}"})
    info = {"session": "sample's project", "duration": "42", "status": "ok"}
    with patch.object(notify, "run_local", new_callable=AsyncMock) as local, \
            patch.object(backends, "notify_exec", new_callable=AsyncMock) as remote:
        local.return_value = remote.return_value = {"ok": True}
        await notify.dispatch(info)
        assert local.call_args.args[0] == "success 'sample'\"'\"'s project' ok"
        assert local.call_args.args[1]["duration_hms"] == "0:42"
        config.set_notify({"backend": 7})
        await notify.dispatch({**info, "status": "error"})
        assert remote.call_args.args[0] == 7
        assert remote.call_args.args[1] == "failure 'sample'\"'\"'s project' error"
        for status in ("interrupted", "unknown"):
            assert (await notify.dispatch({**info, "status": status}))["skipped"]
        config.set_notify({"success_command": ""})
        assert notify.public_state() == {"configured": True, "enabled": True}
        assert (await notify.dispatch(info))["skipped"]
        assert local.await_count == remote.await_count == 1
        # Explicit Test uses an unsaved command, regardless of the enabled switch.
        config.set_notify({"enabled": False})
        assert not notify.active()
        await notify.dispatch({**info, "status": "error"},
                              override={"backend": 0, "command": "draft {status}"})
        assert local.call_args.args[0] == "draft error"
        config.set_notify({"failure_command": ""})
        assert notify.public_state() == {"configured": False, "enabled": False}

    # Local completions and durable remote history use the same selector.
    config.set_notify({"enabled": True, "backend": 0,
                       "success_command": "success", "failure_command": "failure"})
    session = {"id": 91, "name": "Example", "engine": "codex", "cwd": str(TEST_ROOT)}
    with patch.object(notify, "run_local", new_callable=AsyncMock, return_value={"ok": True}) as run:
        for status in ("ok", "error", "interrupted"):
            notify.session_finished(session, status, 2)
            await asyncio.sleep(0)
        assert [call.args[0] for call in run.call_args_list] == ["success", "failure"]
        config.set_notify({"enabled": False})
        notify.session_finished(session, "error", 2)
        await asyncio.sleep(0)
        assert run.await_count == 2
        config.set_notify({"enabled": True})
        records = notify.completion_events(0)["completions"]
        stream = "a" * 32
        db.meta_set(notify._cursor_key(8), {"version": 1, "stream_id": stream, "cursor": 0})
        payload = {"ok": True, "stream_id": stream, "cursor": records[-1]["seq"],
                   "truncated": False, "completions": records}
        with patch.object(backends, "fetch_completion_events", new_callable=AsyncMock,
                          return_value=payload):
            await notify._poll_backend({"id": 8, "name": "Remote demo"})
            assert [call.args[0] for call in run.call_args_list[2:]] == ["success", "failure", "failure"]
            assert notify._load_cursor(8)["cursor"] == records[-1]["seq"]
            config.set_notify({"failure_command": ""})
            db.meta_set(notify._cursor_key(8), {"version": 1, "stream_id": stream, "cursor": 0})
            await notify._poll_backend({"id": 8, "name": "Remote demo"})
            assert run.await_count == 6  # Only success runs on the second read.
            assert notify._load_cursor(8)["cursor"] == records[-1]["seq"]
        notify.forget_backend(8)
    db.execute("DELETE FROM meta WHERE key=? OR key=?",
               (notify.COMPLETION_LOG_KEY, notify._completion_key(91)))


async def api_contract(factory, controller):
    app = factory()
    app.on_startup.clear(); app.on_shutdown.clear(); app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        paths = ["notify/exec"] + (["notify", "notify/toggle", "notify/test"] if controller else [])
        for path in paths:
            response = await client.post("/api/" + path, json={})
            assert response.status == 401, (path, response.status)
        # The remote execution contract remains the same on both runtimes.
        for status in ("ok", "error"):
            response = await client.post("/api/notify/exec", headers=headers, json={
                "command": 'printf "%s" "$PUPPY_STATUS"', "info": {"status": status}})
            assert response.status == 200
            assert (await response.json())["output"] == status
        if not controller:
            return
        config.set_notify({"enabled": False, "backend": 0, "success_command": "", "failure_command": ""})
        response = await client.get("/api/notify", headers=headers)
        assert (await response.json())["settings"] == notify.settings()
        body = {"backend": 0, "success_command": " success ", "failure_command": " failure "}
        response = await client.post("/api/notify", headers=headers, json=body)
        assert response.status == 200
        assert (await response.json())["settings"] == {
            "enabled": False, "backend": 0, "success_command": "success", "failure_command": "failure"}
        config._config = None
        assert notify.settings()["failure_command"] == "failure"
        response = await client.post("/api/notify/toggle", headers=headers, json={"enabled": True})
        assert response.status == 200 and notify.active()
        for body in ({"backend": 0, "command": "old"}, {"backend": 0, "success_command": "only"},
                     {"backend": 0, "success_command": "x" * 1001, "failure_command": ""},
                     {"backend": True, "success_command": "", "failure_command": ""}):
            response = await client.post("/api/notify", headers=headers, json=body)
            assert response.status == 400, await response.text()
        before = config.export_data()
        with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
            response = await client.post("/api/notify", headers=headers, json={
                "backend": 0, "success_command": "changed", "failure_command": "changed"})
            assert response.status == 500
        assert config.export_data() == before
        config.set_notify({"enabled": False})
        before = config.export_data()
        for status in ("ok", "error"):
            response = await client.post("/api/notify/test", headers=headers, json={
                "backend": 0, "status": status,
                "command": 'printf "%s/%s" {status} "$PUPPY_STATUS"'})
            assert response.status == 200
            assert (await response.json())["output"] == status + "/" + status
        assert config.export_data() == before
        for body in ({"backend": 0, "command": "true"},
                     {"backend": 0, "command": "true", "status": "interrupted"},
                     {"backend": 0, "command": "", "status": "error"}):
            response = await client.post("/api/notify/test", headers=headers, json=body)
            assert response.status == 400
        for success, failure in (("", "failure"), ("success", ""), ("", "")):
            response = await client.post("/api/notify", headers=headers, json={
                "backend": 0, "success_command": success, "failure_command": failure})
            assert response.status == 200
            assert notify.configured() == bool(success or failure)
            assert notify.settings()["enabled"] is False


async def main():
    try:
        validation()
        db.connect()
        auth.create_user("notify-test", "test-password")
        await routing()
        await api_contract(webui.build_app, True)
        await api_contract(backend_app, False)
        print("notify tests passed")
    finally:
        shutil.rmtree(TEST_ROOT)


if __name__ == "__main__":
    asyncio.run(main())
