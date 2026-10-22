#!/usr/bin/env python3
"""Node timeout API, durable validation/rollback and unlimited idle lifecycles."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
TEST_ROOT = private_root("timeouts-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, browser, config, db, protocol, terminal, timeouts
from puppy import web as webui
from backend.puppy_backend.app import build_app as backend_app


def validation():
    config.load()
    assert config.timeout_values() == config.TIMEOUT_DEFAULTS
    before = config.export_data()
    for field in config.TIMEOUT_PATHS:
        for value in (-1, True, None, "0", 1.5, float("inf"), float("nan"), config.MAX_TIMEOUT_SECONDS + 1):
            try:
                config.set_timeouts({field: value})
            except ValueError:
                pass
            else:
                raise AssertionError((field, value))
            assert config.export_data() == before
    for invalid in ({}, {"unknown": 0}, [], None):
        try:
            config.set_timeouts(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    path = Path(config.CONFIG_PATH)
    disk = path.read_bytes()
    with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
        try:
            config.set_timeouts({"turn_seconds": 0, "browser_idle_seconds": 10})
        except OSError:
            pass
        else:
            raise AssertionError("failed persistence reported success")
    assert config.export_data() == before and path.read_bytes() == disk
    zeros = {key: 0 for key in config.TIMEOUT_PATHS}
    assert config.set_timeouts(zeros) == zeros
    config._config = None
    assert config.timeout_values() == zeros
    exported = config.export_data()
    assert config.normalize_import(exported) == exported
    for section, field in (("spawn", "max_runtime"), ("spawn", "idle_timeout"),
                           ("browser", "idle_timeout"), ("terminal", "idle_timeout")):
        outdated = copy.deepcopy(exported)
        del outdated[section][field]
        try:
            config.normalize_import(outdated)
        except ValueError:
            pass
        else:
            raise AssertionError("old shape accepted")
    config.set_timeouts(config.TIMEOUT_DEFAULTS)


def background_limit():
    """The seventh field: an optional meta record beside config.json's six,
    validated and rolled back with them, refused rather than repaired."""
    db.connect()
    assert db.meta_get(timeouts.BACKGROUND_KEY) is None
    assert timeouts.background_wait_seconds() == 86400
    payload = timeouts.payload()
    assert payload["values"] == {**config.TIMEOUT_DEFAULTS, "background_wait_seconds": 86400}
    assert payload["defaults"] == payload["values"] and payload["max_seconds"] == config.MAX_TIMEOUT_SECONDS
    for value in (-1, True, None, "0", 1.5, float("inf"), config.MAX_TIMEOUT_SECONDS + 1):
        try:
            timeouts.update({"background_wait_seconds": value})
        except ValueError:
            pass
        else:
            raise AssertionError(value)
        assert db.meta_get(timeouts.BACKGROUND_KEY) is None
    for invalid in ({}, {"unknown": 0}, {"background_wait_seconds": 5, "unknown": 0}, None):
        try:
            timeouts.update(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    # nothing is written when any field of the patch is refused
    try:
        timeouts.update({"background_wait_seconds": 5, "turn_seconds": -1})
    except ValueError:
        pass
    else:
        raise AssertionError("partial patch accepted")
    assert db.meta_get(timeouts.BACKGROUND_KEY) is None and config.get("sessions.turn_timeout") == 7200
    assert timeouts.update({"background_wait_seconds": 0})["background_wait_seconds"] == 0
    assert db.meta_get(timeouts.BACKGROUND_KEY) == {"format": 1, "seconds": 0}
    # config.json failing to save puts the record back as it was
    with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
        try:
            timeouts.update({"background_wait_seconds": 600, "turn_seconds": 60})
        except OSError:
            pass
        else:
            raise AssertionError("failed persistence reported success")
    assert db.meta_get(timeouts.BACKGROUND_KEY) == {"format": 1, "seconds": 0}
    assert config.get("sessions.turn_timeout") == 7200
    db.meta_del(timeouts.BACKGROUND_KEY)
    with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
        try:
            timeouts.update({"background_wait_seconds": 600, "turn_seconds": 60})
        except OSError:
            pass
    assert db.meta_get(timeouts.BACKGROUND_KEY) is None, "an absent record stays absent"
    both = timeouts.update({"background_wait_seconds": 43200, "turn_seconds": 3600})
    assert both["background_wait_seconds"] == 43200 and both["turn_seconds"] == 3600
    # every other persisted shape is refused, at startup and in a backup
    for bad in ({"format": 1}, {"format": 2, "seconds": 5}, {"format": 1, "seconds": 1.0},
                {"format": 1, "seconds": -1}, {"format": 1, "seconds": True},
                {"format": 1, "seconds": 5, "extra": 1}, [1], 5):
        db.meta_set(timeouts.BACKGROUND_KEY, bad)
        for check in (lambda: timeouts.validate_persisted(db.connect()),
                      timeouts.background_wait_seconds):
            try:
                check()
            except ValueError:
                pass
            else:
                raise AssertionError(bad)
    db.meta_del(timeouts.BACKGROUND_KEY)
    timeouts.update(timeouts.defaults())
    assert timeouts.values() == timeouts.defaults()
    db.meta_del(timeouts.BACKGROUND_KEY)


async def api_contract(factory):
    app = factory()
    # Only exercise the real authenticated routes; no probes, engine CLIs or
    # periodic workers are needed for settings persistence.
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.TIMEOUT_SETTINGS_CAPABILITY in app["puppy_capabilities"]
    async with TestClient(TestServer(app)) as client:
        for method in (client.get, client.patch):
            response = await method("/api/timeouts")
            assert response.status == 401
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        response = await client.get("/api/timeouts", headers=headers)
        payload = await response.json()
        assert response.status == 200 and payload["timeouts"] == timeouts.payload()
        assert payload["timeouts"]["values"]["background_wait_seconds"] == 86400
        response = await client.patch("/api/timeouts", headers=headers,
                                      json={"background_wait_seconds": 0})
        assert response.status == 200, await response.text()
        assert (await response.json())["timeouts"]["values"]["background_wait_seconds"] == 0
        response = await client.patch("/api/timeouts", headers=headers,
                                      json={"background_wait_seconds": 1.5})
        assert response.status == 400
        assert timeouts.background_wait_seconds() == 0
        with patch.object(browser, "idle_settings_changed") as b, patch.object(terminal, "idle_settings_changed") as t:
            response = await client.patch("/api/timeouts", headers=headers, json={
                "turn_seconds": 0, "spawn_runtime_seconds": 21600,
                "spawn_idle_seconds": 0, "browser_idle_seconds": 0, "terminal_idle_seconds": 0,
                "vnc_idle_seconds": 0})
            assert response.status == 200, await response.text()
            assert b.call_count == t.call_count == 1
        response = await client.patch("/api/timeouts", headers=headers, json={"spawn_runtime_seconds": -1})
        assert response.status == 400
        assert config.get("spawn.max_runtime") == 21600
        with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
            response = await client.patch("/api/timeouts", headers=headers, json={"spawn_runtime_seconds": 0})
            assert response.status == 500
        assert config.get("spawn.max_runtime") == 21600
        async def engines(*args, **kwargs):
            return []
        with patch.object(webui, "_engines_payload", engines):
            response = await client.get("/api/engines", headers=headers)
            assert (await response.json())["timeouts"] == timeouts.payload()
            if app["puppy_role"] == "full":
                for route in ("/api/settings", "/api/state"):
                    response = await client.get(route, headers=headers)
                    assert (await response.json())["timeouts"] == timeouts.payload()
        timeouts.update(timeouts.defaults())


async def idle_lifecycles():
    for module, cls, key in ((browser, browser.Manager, "browser_idle_seconds"),
                             (terminal, terminal.TerminalInstance, "terminal_idle_seconds")):
        class Idle:
            _arm_idle = cls._arm_idle
            _cancel_idle = cls._cancel_idle
            idle_task = None
            running = True
            viewers = {}
            stopped = asyncio.Event()

            async def stop(self, reason):
                self.running = False
                self.stopped.set()

        instance = Idle()
        with patch.object(module, "_manager", SimpleNamespace(instances={"DEMO": instance})):
            config.set_timeouts({key: 1})
            module.idle_settings_changed()
            first = instance.idle_task
            assert first is not None
            config.set_timeouts({key: 0})
            module.idle_settings_changed()
            assert instance.idle_task is None
            await asyncio.sleep(1.05)
            assert instance.running and not instance.stopped.is_set()
            config.set_timeouts({key: 1})
            instance.viewers = {"viewer": object()}
            module.idle_settings_changed()
            assert instance.idle_task is None
            instance.viewers = {}
            module.idle_settings_changed()
            await asyncio.wait_for(instance.stopped.wait(), 2)
            assert not instance.running
            instance._cancel_idle()
    config.set_timeouts(config.TIMEOUT_DEFAULTS)


async def main():
    try:
        validation()
        background_limit()
        db.connect()
        auth.create_user("timeout-test", "test-password")
        await api_contract(webui.build_app)
        await api_contract(backend_app)
        await idle_lifecycles()
        print("timeout settings tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
