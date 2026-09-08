#!/usr/bin/env python3
"""No-quota tests for saved engine choices, session initialization and queues."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("engine-defaults-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, engine_defaults, runner
from puppy.drivers import all_drivers, get_driver
from puppy.drivers.claude import parse_model_catalog
from puppy.web import build_app

MODELS = [
    {"value": "", "label": "Default", "effort_options": [{"value": "", "label": "Default"}]},
    {"value": "provider/precise", "label": "Precise", "effort_options": [
        {"value": "", "label": "Default"}, {"value": "high", "label": "High"}]},
    {"value": "provider/quick", "label": "Quick", "effort_options": [
        {"value": "", "label": "Default"}, {"value": "low", "label": "Low"}]},
]


async def main():
    try:
        config.load()
        db.connect()
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        with ExitStack() as stack:
            for driver in all_drivers():
                stack.enter_context(patch.object(driver, "refresh_model_options", AsyncMock()))
                stack.enter_context(patch.object(driver, "status", AsyncMock(return_value={"installed": True})))
                stack.enter_context(patch.object(driver, "model_options", return_value=MODELS))
                stack.enter_context(patch.object(driver, "allow_custom_model", False))
            wake = stack.enter_context(patch("puppy.web.state_stream.wake"))
            stack.enter_context(patch("puppy.web.usage_refresh.maybe_refresh", AsyncMock()))
            app = build_app()
            app.on_startup.clear()  # explicit HTTP checks; no periodic probes
            async with TestClient(TestServer(app)) as client:
                async def request(method, path, body=None, status=200, authed=True):
                    response = await client.request(method, "/api/" + path,
                        json=body, headers=headers if authed else {})
                    data = await response.json()
                    assert response.status == status, (response.status, data)
                    return data

                await request("GET", "engines/codex/defaults", authed=False, status=401)
                await request("PUT", "engines/codex/defaults", {}, authed=False, status=401)
                await request("GET", "engines/unknown/defaults", status=404)
                assert "engine-defaults" in (await request("GET", "ping"))["capabilities"]

                original = db.create_session("Existing", "codex", str(ROOT), "", "", "", "read-only")
                before_session = db.get_session(original)
                for driver in all_drivers():
                    key = driver.key
                    path = "engines/{}/defaults".format(key)
                    fresh = (await request("GET", path))["engine"]
                    assert fresh["session_defaults"] == fresh["factory_defaults"] == engine_defaults.factory(driver)
                    chosen = {"permission_mode": driver.permission_options()[-1]["value"],
                              "model": "provider/precise", "effort": "high"}
                    saved = (await request("PUT", path, chosen))["engine"]
                    assert saved["session_defaults"] == chosen
                    assert config.get("engines.defaults." + key) == chosen
                    for invalid in ({}, [], {**chosen, "extra": True}, {**chosen, "effort": None},
                                    {**chosen, "model": "retired"}, {**chosen, "model": "provider/quick"},
                                    {**chosen, "permission_mode": "invalid"}, {**chosen, "effort": " low "}):
                        await request("PUT", path, invalid, status=400)
                        assert config.get("engines.defaults." + key) == chosen
                    created = (await request("POST", "sessions", {"engine": key, "cwd": str(ROOT)}))["session"]
                    assert {field: created[field] for field in chosen} == chosen
                    explicit = (await request("POST", "sessions", {
                        "engine": key, "cwd": str(ROOT), "model": "", "effort": "",
                        "permission_mode": driver.default_permission()}))["session"]
                    assert explicit["model"] == explicit["effort"] == ""
                    assert explicit["permission_mode"] == driver.default_permission()
                    switched = (await request("POST", "sessions/{}/switch".format(explicit["id"]),
                                              {"engine": key}))["session"]
                    assert {field: switched[field] for field in chosen} == chosen
                assert db.get_session(original) == before_session
                wake.assert_any_call("engines")
                engines = (await request("GET", "engines"))["engines"]
                assert all(engine["session_defaults"]["model"] == "provider/precise" for engine in engines)

                # Real Claude catalog normalization reaches the API unchanged.
                # Saving/creating with an alternate spelling preserves it and
                # validates effort against that model, not the global union.
                claude = get_driver("claude")
                original_defaults = dict(config.get("engines.defaults.claude"))
                alias_models = parse_model_catalog([
                    {"value": "fable", "displayName": "Fable",
                     "resolvedModel": "claude-fable-5-1", "supportedEffortLevels": ["max"]},
                    {"value": "quick", "displayName": "Quick", "supportedEffortLevels": ["low"]},
                ])
                with patch.object(claude, "model_options", return_value=alias_models), \
                        patch.object(claude, "allow_custom_model", True):
                    alias_choices = {"model": "fable[1m]", "effort": "max", "permission_mode": "auto"}
                    saved_alias = (await request("PUT", "engines/claude/defaults", alias_choices))["engine"]
                    assert saved_alias["session_defaults"] == alias_choices
                    assert "fable[1m]" in saved_alias["model_options"][1]["aliases"]
                    await request("PUT", "engines/claude/defaults", {**alias_choices, "effort": "low"}, status=400)
                    created_alias = (await request("POST", "sessions", {"engine": "claude", "cwd": str(ROOT)}))["session"]
                    assert {key: created_alias[key] for key in alias_choices} == alias_choices
                await request("PUT", "engines/claude/defaults", original_defaults)

                # The queue captures all three choices at request time. Later
                # saves cannot rewrite the switch or the prompts before it.
                queued_id = db.create_session("Queued", "codex", str(ROOT), "", "", "", "read-only")
                hub = runner.hub(queued_id)
                hub.queue.append("Already waiting")
                queued = await request("POST", "sessions/{}/switch".format(queued_id), {"engine": "claude"})
                assert queued["queued"] is True
                captured = json.dumps(hub.queue)
                choices = engine_defaults.values(get_driver("claude"))
                await request("PUT", "engines/claude/defaults", {**choices, "model": "provider/quick", "effort": "low"})
                assert json.dumps(hub.queue) == captured
                assert hub.queue[-1]["fields"]["model"] == "provider/precise"
                switch = hub.queue[-1]
                hub.clear_queue()
                hub._apply_queued_item(switch)
                applied = db.get_session(queued_id)
                assert {field: applied[field] for field in choices} == choices

                # Catalog drift remains visible on read, and cannot silently
                # initialize or reseed a session with a different model.
                codex = get_driver("codex")
                with patch.object(codex, "model_options", return_value=MODELS[:1]):
                    payload = await request("GET", "engines/codex/defaults")
                    assert payload["engine"]["session_defaults"]["model"] == "provider/precise"
                    await request("POST", "sessions", {"engine": "codex", "cwd": str(ROOT)}, status=400)
                    await request("POST", "sessions/{}/switch".format(original), {"engine": "codex"}, status=400)
                    reset = payload["engine"]["factory_defaults"]
                    await request("PUT", "engines/codex/defaults", reset)
                    await request("POST", "sessions", {"engine": "codex", "cwd": str(ROOT)})
                assert db.get_session(original) == before_session

                app["puppy_snapshot_busy"] = "export"
                await request("PUT", "engines/codex/defaults", reset, status=503)
                app["puppy_snapshot_busy"] = None

            # Persistence and failed-write rollback use the same current shape.
            before = config.export_data()
            with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
                try:
                    config.set_engine_defaults("claude", reset)
                except OSError:
                    pass
                else:
                    raise AssertionError("write failure was hidden")
            assert config.export_data() == before
            config._config = None
            assert config.load() == before
        print("engine defaults tests passed")
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
