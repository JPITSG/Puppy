#!/usr/bin/env python3
"""Bounded prompt batches through both authenticated runtimes; no engines."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("session-loop-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, cli_upgrade, config, db, protocol, runner
from puppy.web import build_app
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
    finally:
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
