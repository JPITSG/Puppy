#!/usr/bin/env python3
"""Real authenticated sockets on both runtimes: presence and draft races.

No engine runs, background probes, external network, or subscription quota.
"""
import asyncio
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("draft-presence-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, config, db, protocol, runner
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


async def frame(ws, kind):
    while True:
        value = await ws.receive_json(timeout=2)
        if value["type"] == kind:
            return value


async def contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.SESSION_DRAFT_PRESENCE_CAPABILITY in app["puppy_capabilities"]
    sid = db.create_session("Draft demo", "claude", str(ROOT), "", "", "", "default")
    other = db.create_session("Separate chat", "claude", str(ROOT), "", "", "", "default")
    hub = runner.hub(sid)
    route = "/api/ws/session/{}".format(sid)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        try:
            await client.ws_connect(route)
        except WSServerHandshakeError as exc:
            assert exc.status == 401
        else:
            raise AssertionError("anonymous socket accepted")
        a = await client.ws_connect(route, headers=headers)
        b = await client.ws_connect(route, headers=headers)
        isolated = await client.ws_connect("/api/ws/session/{}".format(other), headers=headers)
        for ws in (a, b, isolated):
            snapshot = await frame(ws, "snapshot")
            assert snapshot["draft_presence"] == {"version": 1, "count": 0}
        before = db.get_session_draft(sid)
        await a.send_json({"type": "typing", "active": True})
        assert (await frame(a, "typing"))["count"] == 0
        assert (await frame(b, "typing"))["count"] == 1
        late = await client.ws_connect(route, headers=headers)
        assert (await frame(late, "snapshot"))["draft_presence"]["count"] == 1
        await b.send_json({"type": "typing", "active": True})
        assert (await frame(a, "typing"))["count"] == 1
        assert (await frame(b, "typing"))["count"] == 1
        assert (await frame(late, "typing"))["count"] == 2
        # Renewals are quiet and never write drafts/transcripts.
        await a.send_json({"type": "typing", "active": True})
        await a.send_json({"type": "typing", "active": "false"})
        await a.send_json({"type": "draft", "text": "First device", "client_id": "a",
                           "client_seq": 1, "expected_revision": 0})
        accepted = await frame(a, "draft")
        assert accepted["revision"] == 1 and accepted["text"] == "First device"
        assert (await frame(b, "draft"))["revision"] == 1
        assert (await frame(late, "draft"))["revision"] == 1
        assert len(hub._typists) == 2
        assert db.get_events(sid) == [] and before["revision"] == 0
        assert db.get_session_draft(other)["revision"] == 0
        # Both editors began at revision zero; the later write must fail,
        # including for identical text. Only the requester sees the conflict.
        for text in ("Second device", "First device"):
            await b.send_json({"type": "draft", "text": text, "client_id": "b",
                               "client_seq": 2, "expected_revision": 0})
            conflict = await frame(b, "draft_conflict")
            assert conflict["revision"] == 1 and conflict["text"] == "First device"
            assert conflict["client_seq"] == 2
            assert db.get_session_draft(sid)["text"] == "First device"
        # Explicit review uses the revision it actually displayed.
        await b.send_json({"type": "draft", "text": "Reviewed draft", "client_id": "b",
                           "client_seq": 3, "expected_revision": 1})
        assert (await frame(b, "draft"))["revision"] == 2
        await frame(a, "draft")
        await frame(late, "draft")
        for invalid in (None, True, -1, "2", 2.5):
            await b.send_json({"type": "draft", "text": "Invalid", "client_id": "b",
                               "client_seq": 4, "expected_revision": invalid})
            error = await frame(b, "draft_error")
            assert error["client_seq"] == 4
            assert db.get_session_draft(sid)["revision"] == 2
        with patch.object(db, "set_session_draft", side_effect=OSError("disk full")):
            await b.send_json({"type": "draft", "text": "Unsaved", "client_id": "b",
                               "client_seq": 5, "expected_revision": 2})
            assert "save draft" in (await frame(b, "draft_error"))["error"]
        assert db.get_session_draft(sid)["text"] == "Reviewed draft"
        # Refused sends and maintenance gates return the sender's identity;
        # no optimistic UI clear or stuck outstanding-write slot is needed.
        send = {"type": "message", "text": "Keep this", "draft": "Keep this",
                "draft_guarded": True, "draft_client_id": "b", "draft_client_seq": 8}
        with patch.object(hub, "send_message", return_value={"error": "engine busy"}):
            await b.send_json(send)
            error = await frame(b, "draft_send_error")
            assert error["client_seq"] == 8 and error["accepted"] is False
        app["puppy_snapshot_busy"] = "test"
        await b.send_json(send)
        assert "backup" in (await frame(b, "draft_send_error"))["error"]
        await b.send_json({"type": "draft", "text": "Keep this", "client_seq": 9,
                           "expected_revision": 2})
        assert (await frame(b, "draft_error"))["client_seq"] == 9
        app["puppy_snapshot_busy"] = None
        with patch.object(hub, "send_message", return_value={"ok": True}), \
                patch.object(hub, "consume_draft", return_value={"error": "disk full"}):
            await b.send_json(send)
            assert (await frame(b, "draft_send_error"))["accepted"] is True
        # Exact-value consumption cannot erase the other editor's draft.
        with patch.object(hub, "send_message", return_value={"ok": True}):
            await a.send_json({"type": "message", "text": "Private local draft",
                               "draft": "Private local draft", "draft_client_id": "a",
                               "draft_client_seq": 6})
            result = await frame(a, "draft")
            assert result["consumed"] is False and result["text"] == "Reviewed draft"
        # A tab close immediately removes its presence; a crashed or sleeping
        # client is covered by the expiry even if its socket remains open.
        await a.close()
        assert (await frame(b, "typing"))["count"] == 0
        assert (await frame(late, "typing"))["count"] == 1
        with patch.object(runner, "TYPING_LEASE_SECONDS", 0.04):
            await b.send_json({"type": "typing", "active": True})
            assert (await frame(late, "typing"))["count"] == 0
        assert hub._typists == {}
        await b.close()
        await late.close()
        await isolated.close()
    assert not hub.watchers and not hub._typists
    # Neither leases nor collision variants add any durable schema or events.
    assert db.get_events(sid) == []
    print("PASS: {} draft revisions, collisions, typing, isolation, expiry and auth".format(app["puppy_role"]))


async def main():
    try:
        config.load()
        db.connect()
        auth.create_user("draft-test", "test-password")
        await contract(build_app)
        await contract(backend_app)
    finally:
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
