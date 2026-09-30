#!/usr/bin/env python3
"""Bounded prompt batches through both authenticated runtimes; no engines."""
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("session-loop-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, cli_upgrade, config, db, protocol, runner
from puppy.web import build_app
from puppy.drivers import all_drivers, get_driver
from backend.puppy_backend.app import build_app as backend_app


async def contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.SESSION_LOOPS_CAPABILITY in app["puppy_capabilities"]
    sid = db.create_session("Loop preview", "claude", str(ROOT), "", "", "", "default")
    hub = runner.hub(sid)
    route = "/api/sessions/{}/loop".format(sid)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    started = []

    def start(text):
        if app["puppy_role"] == "full":
            assert app.get("puppy_mutations", 0) == 1
        started.append(text)
        hub.status = "running"

    async with TestClient(TestServer(app)) as client:
        response = await client.post(route, json={"text": "Repeat", "iterations": 3})
        assert response.status == 401
        with patch.object(hub, "_start_turn", side_effect=start):
            for body in (None, [], {}, {"text": "", "iterations": 3},
                         {"text": "   ", "iterations": 3}, {"text": 7, "iterations": 3},
                         {"text": "x" * (db.MAX_DRAFT_CHARS + 1), "iterations": 2},
                         *({"text": "Repeat", "iterations": value}
                           for value in (None, False, True, 0, -1, 101, 2.5, "3"))):
                response = await client.post(route, headers=headers, json=body)
                assert response.status == 400, (body, response.status)
                assert not started and not hub.queue
            if app["puppy_role"] == "full":
                app["puppy_snapshot_busy"] = "export"
                response = await client.post(route, headers=headers, json={"text": "Repeat", "iterations": 3})
                assert response.status == 503 and not started and not hub.queue
                app["puppy_snapshot_busy"] = None
            with patch.object(cli_upgrade, "is_running", return_value=True):
                response = await client.post(route, headers=headers, json={"text": "Repeat", "iterations": 3})
                assert response.status == 400 and not started and not hub.queue

            # The initial request starts once and persists all remaining copies.
            response = await client.post(route, headers=headers, json={"text": "  Repeat\nthis prompt  ", "iterations": 3})
            assert await response.json() == {"queued": False, "iterations": 3}
            assert started == ["Repeat\nthis prompt"]
            assert hub.queue == ["Repeat\nthis prompt"] * 2
            assert db.meta_get("session_queue.{}".format(sid))["queue"] == hub.queue
            assert app.get("puppy_mutations", 0) == 0
            # All copies use the normal queue controls and survive a restart as held.
            assert hub.set_queue_paused(0, hub.queue[0], True)["ok"]
            restored = runner.SessionHub(sid)
            assert restored.held == ["Repeat\nthis prompt"] * 2 and not restored.queue
            assert hub.unqueue(0, hub.queue[0]) == {"ok": True}
            assert len(hub.queue) == 1 and not hub.paused_queue
            await hub.edit_queued(0, hub.queue[0])
            assert not hub.queue and db.get_session_draft(sid)["text"] == "Repeat\nthis prompt"
            # A busy session queues every iteration behind existing work.
            hub.send_message("Earlier work")
            response = await client.post(route, headers=headers, json={"text": "Next pass", "iterations": 3})
            assert await response.json() == {"queued": True, "iterations": 3}
            assert hub.queue == ["Earlier work"] + ["Next pass"] * 3
            assert len(started) == 1
            hub.clear_queue()
            hub.status = "idle"
            response = await client.post(route, headers=headers, json={"text": "Once", "iterations": 1})
            assert await response.json() == {"queued": False, "iterations": 1}
            assert started[-1] == "Once" and not hub.queue
            # The upper bound is accepted as one ordered batch.
            response = await client.post(route, headers=headers, json={"text": "Bounded", "iterations": 100})
            assert response.status == 200 and hub.queue == ["Bounded"] * 100
            hub.clear_queue()
    hub.status = "idle"
    print("PASS: {} loops: auth, bounds, refusal, one start plus queued copies, existing order, persistence and queue controls".format(app["puppy_role"]))


async def main():
    try:
        config.load()
        db.connect()
        auth.create_user("loop-test", "test-password")
        await contract(build_app)
        await contract(backend_app)
        with ExitStack() as stack:
            for driver in all_drivers():
                stack.enter_context(patch.object(driver, "refresh_model_options", new=AsyncMock()))
                stack.enter_context(patch.object(driver, "allow_custom_model", False))
                stack.enter_context(patch.object(driver, "model_options", return_value=[
                    {"value": "", "label": "Default", "effort_options": [{"value": ""}]},
                    {"value": "quick", "label": "Quick", "effort_options": [{"value": ""}, {"value": "low"}]},
                    {"value": "precise", "label": "Precise", "effort_options": [{"value": ""}, {"value": "high"}]}]))
                stack.enter_context(patch.object(driver, "fast_mode_tier", side_effect=lambda model: "fast" if model == "precise" else ""))
            await configured_contract(build_app)
            await configured_contract(backend_app)
    finally:
        shutil.rmtree(ROOT)


async def configured_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.SESSION_LOOP_CONFIG_CAPABILITY in app["puppy_capabilities"]
    permission = get_driver("claude").default_permission()
    sid = db.create_session("Configured loop", "claude", str(ROOT), "precise", "high", "", permission)
    db.touch_session(sid, native_session_id="existing-context", fast_mode=1)
    hub = runner.hub(sid)
    route = "/api/sessions/{}/loop".format(sid)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    configurations = [
        {"engine": "claude", "model": "quick", "effort": "low"},
        {"engine": "codex", "model": "precise", "effort": "high"},
        {"engine": "claude", "model": "precise", "effort": "high"}]
    started = []

    def start(text):
        started.append((text, db.get_session(sid)))
        hub.status = "running"

    async with TestClient(TestServer(app)) as client:
        async def post(rows, count=3):
            return await client.post(route, headers=headers, json={
                "text": "Review again", "iterations": count, "configurations": rows})

        with patch.object(hub, "_start_turn", side_effect=start):
            # A bad final choice must never leave earlier iterations running
            # or change the session. Explicit null is not a legacy request.
            for rows in (None, {}, [], configurations[:2], configurations + configurations,
                         configurations[:2] + [dict(configurations[2], engine="missing")],
                         configurations[:2] + [dict(configurations[2], model="retired")],
                         configurations[:2] + [dict(configurations[2], effort="low")],
                         configurations[:2] + [dict(configurations[2], model=7)],
                         configurations[:2] + [dict(configurations[2], model="x" * 257)],
                         configurations[:2] + [dict(configurations[2], model="x\x00")],
                         configurations[:2] + [dict(configurations[2], extra="ignored?")]):
                response = await post(rows)
                assert response.status == 400, await response.text()
                assert not started and not hub.queue
                assert db.get_session(sid)["native_session_id"] == "existing-context"
                assert db.get_session(sid)["model"] == "precise"
                assert db.meta_get("session_queue.{}".format(sid)) is None
            with patch.object(cli_upgrade, "is_running", side_effect=lambda key: key == "codex"):
                response = await post(configurations)
                assert response.status == 400 and not started and not hub.queue

            response = await post(configurations)
            assert await response.json() == {"queued": False, "iterations": 3}
            assert len(started) == 1
            first = started[0][1]
            assert (first["engine"], first["model"], first["effort"]) == ("claude", "quick", "low")
            assert first["native_session_id"] == "existing-context", "same engine keeps its native conversation"
            assert first["permission_mode"] == permission and not first["fast_mode"]
            assert [row["kind"] if isinstance(row, dict) else row for row in hub.queue] == [
                "engine", "Review again", "engine", "Review again"]
            persisted = db.meta_get("session_queue.{}".format(sid))
            assert runner.validate_queue_record(persisted) == persisted
            assert persisted["queue"] == hub.queue
            for expected in configurations[1:]:
                hub.status = "idle"
                assert hub._start_queue_if_ready()
                current = started[-1][1]
                assert {key: current[key] for key in expected} == expected
                assert current["native_session_id"] == "", "switch uses the normal fresh context/handoff"
                assert not current["fast_mode"]
                assert current["permission_mode"] == get_driver(expected["engine"]).default_permission()
            assert len(started) == 3 and not hub.queue

            # Equal choices need no switch or redundant config rows. Existing
            # queued choices still determine the baseline, never the live turn.
            hub.queue_config({"engine": "claude", "model": "quick", "effort": "low"})
            hub.send_message("Earlier work")
            before = list(hub.queue)
            response = await post([configurations[0]] * 3)
            assert await response.json() == {"queued": True, "iterations": 3}
            assert hub.queue == before + ["Review again"] * 3
            assert len(started) == 3
            hub.clear_queue()

            same_engine = [configurations[0], configurations[2]]
            response = await post(same_engine, count=2)
            assert response.status == 200
            assert [item["kind"] if isinstance(item, dict) else item for item in hub.queue] == [
                "config", "Review again", "config", "Review again"]
            for expected in same_engine:
                hub.status = "idle"
                assert hub._start_queue_if_ready()
                assert {key: started[-1][1][key] for key in expected} == expected
            assert not hub.queue

            # Ordered changes remain the same exact persisted shapes and are
            # parked together with the prompts at restart.
            response = await post(configurations)
            assert response.status == 200
            before = list(hub.queue)
            restored = runner.SessionHub(sid)
            assert restored.held == before and not restored.queue
            assert runner.validate_queue_record(db.meta_get("session_queue.{}".format(sid)))
            hub.held = restored.held
            hub.queue.clear()
            hub.held.clear()
            hub._persist_queue()
    hub.status = "idle"
    print("PASS: {} configured loops: atomic refusal, per-model effort, upgrade guard, ordered configs/switches, native context, permissions/Fast and restart".format(app["puppy_role"]))


if __name__ == "__main__":
    asyncio.run(main())
