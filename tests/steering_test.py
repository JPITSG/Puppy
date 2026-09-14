#!/usr/bin/env python3
"""Attachment steering through authenticated HTTP/WebSocket on both runtimes.

Real uploads, transcripts and driver payloads, with native acknowledgement
fixtures. No installed engine, external network or model quota is used.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from urllib.parse import quote

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("steering-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, runner, uploads
from puppy.drivers import get_driver
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app


class Sink:
    def __init__(self):
        self.messages = []

    def is_closing(self):
        return False

    def write(self, value):
        self.messages.append(json.loads(value))

    async def drain(self):
        pass


async def frame(ws, kind):
    while True:
        value = await ws.receive_json(timeout=3)
        if value["type"] == kind:
            return value


async def contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        for engine in ("claude", "codex", "opencode"):
            for socket in (False, True):
                sid = db.create_session("Steering fixture", engine, str(ROOT), "", "", "", "default")
                driver, hub, sink = get_driver(engine), runner.hub(sid), Sink()
                hub.proc = SimpleNamespace(stdin=sink, returncode=None)
                hub._proc_ready = True
                hub.status = "running"
                hub._active_turn_id, hub._turn_generation = "fixture-turn", 1
                hub._driver_ctx = {
                    "initial_user_replayed": True, "initial_prompt": "Original task", "pending_steers": [],
                    "phase": "running" if engine == "codex" else "prompt",
                    "thread_id": "native-thread", "turn_id": "native-turn", "session_id": "native-session",
                    "tool": "", "completed": False,
                }
                route = "/api/sessions/{}".format(sid)
                markers, paths = [], []
                for name, mime in (("notes (final), v2.txt", "text/plain"), ("diagram.png", "image/png")):
                    response = await client.post(route + "/upload", data=b"fixture", headers={
                        **headers, "X-Puppy-Filename": quote(name), "Content-Type": mime})
                    assert response.status == 200
                    upload = await response.json()
                    paths.append(Path(upload["path"]))
                    markers.append((uploads.ATTACH_IMAGE_PREFIX + upload["path"] + uploads.ATTACH_IMAGE_SUFFIX)
                                   if mime == "image/png" else
                                   uploads.ATTACH_FILE_PREFIX + upload["path"] + " (" + name + ", 7 B)" + uploads.ATTACH_FILE_SUFFIX)
                ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
                snapshot = await frame(ws, "snapshot")
                assert snapshot["steering"]["ready"]

                async def send(body):
                    if socket:
                        await ws.send_json({"type": "steer", **body})
                        return await frame(ws, "steer_complete")
                    response = await client.post(route + "/steer", json=body, headers=headers)
                    return await response.json()

                for index, text in enumerate(("Inspect these\n\n" + "\n".join(markers), *markers)):
                    rid = "attachment-{}".format(index)
                    body = {"text": text, "request_id": rid, "expected_turn_id": "fixture-turn"}
                    rejected = await client.post(route + "/steer", json=body)
                    assert rejected.status == 401
                    reply = await send(body)
                    assert reply.get("ok") and reply["status"] == "sent", reply
                    payload = sink.messages[-1]
                    blocks = payload["message"]["content"] if engine == "claude" else \
                        payload["params"]["input" if engine == "codex" else "prompt"]
                    assert blocks == [{"type": "text", "text": text}]
                    assert not hub.queue
                    retry = await send(body)
                    assert retry["duplicate"] and len(sink.messages) == index + 1
                    assert "different text" in (await send({**body, "text": "Different"}))["error"]
                    assert "changed" in (await send({**body, "request_id": "stale", "expected_turn_id": "old"}))["error"]
                    native = payload if engine == "claude" else {"id": payload["id"], "result":
                        {"turnId": "native-turn"} if engine == "codex" else {"stopReason": "end_turn"}}
                    actions = driver.parse_line(json.dumps(native), hub._driver_ctx)
                    acknowledgements = [a for a in actions if a.get("a") == "steer_result"]
                    assert len(acknowledgements) == 1 and acknowledgements[0]["ok"]
                    hub._handle_steer_result(acknowledgements[0])
                    assert (await send(body))["status"] == "accepted"
                    events = [e for e in db.get_events(sid) if e["kind"] == "user"]
                    assert len(events) == index + 1
                    assert events[-1]["data"]["text"] == text and events[-1]["data"]["steering"] is True
                for path in paths:
                    assert uploads.upload_is_referenced(sid, path.parent.name)
                    os.utime(path.parent, (0, 0))
                    deleted = await client.delete(route + "/upload/" + path.parent.name, headers=headers)
                    assert deleted.status == 200 and path.is_file(), "sent files cannot be discarded"
                assert uploads.discard_abandoned(sid, markers) == 0
                assert uploads.sweep_orphans(sid) == 0
                hub._turn_result_seen = True
                late = await send({"text": markers[0], "request_id": "late", "expected_turn_id": "fixture-turn"})
                assert "completed" in late["error"] and len(sink.messages) == 3
                await ws.close()
                hub.status, hub.proc = "idle", None
                runner.drop_hub(sid)
    print("PASS: " + factory.__module__ + " attachments on all three drivers over HTTP/WebSocket; identity guards, native ack fixtures and file retention")


async def main():
    config.load()
    config.set_value("auth.api_token", "attachment-test-token-0123456789abcdef")
    db.connect()
    await contract(build_app)
    await contract(backend_app)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT)
