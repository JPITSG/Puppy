#!/usr/bin/env python3
"""Images a tool gives back to the model.

The one reader every driver uses (Claude Code's own block, the MCP and ACP
block, ACP's wrapper, nesting, an image by URL only, the bounds), each driver
handing the pictures over beside its text, the node storing them beside the
session's uploads under their own id form (the type read from the bytes, a
private file, never swept as an orphan, gone with the session) and the
preview route serving them while the discard route refuses them, then a
fixture engine speaking claude's stream-json through the real runner on both
runtimes: the stored event naming its pictures, the bytes served back
exactly, and a result whose image did not decode keeping its text alone. No
installed engine, network or model quota is used.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch
import zlib


def png(width=3, height=2, colour=(200, 40, 40)) -> bytes:
    """A real PNG, built with zlib alone."""
    row = b"\x00" + bytes(colour) * width
    body = zlib.compress(row * height)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", body) + chunk(b"IEND", b""))


PNG = png()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + struct.pack("<I", 20) + b"WEBPVP8 " + b"\x00" * 12
b64 = lambda raw: base64.b64encode(raw).decode()


def fixture():
    """An engine that reads an image, takes a screenshot with a caption and
    an undecodable picture, and ends the turn."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
    send({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_read", "name": "Read",
         "input": {"file_path": "/tmp/shots/10-crop.png"}},
        {"type": "tool_use", "id": "toolu_shot", "name": "mcp__puppy_browser__screenshot",
         "input": {}}]}})
    send({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": b64(PNG)}}]},
        {"type": "tool_result", "tool_use_id": "toolu_shot", "content": [
            {"type": "text", "text": "Captured the viewport"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": b64(JPEG)}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": b64(b"not a picture")}}]},
    ]}})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-session", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("tool-images-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import config, db, protocol, runner, uploads  # noqa: E402
from puppy.drivers import base, get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402


def test_reader():
    anthropic = {"type": "image", "source": {"type": "base64", "media_type": "image/PNG", "data": "AAAA"}}
    mcp = {"type": "image", "data": "BBBB", "mimeType": "image/jpeg"}
    acp = {"type": "content", "content": {"type": "image", "data": "CCCC", "mimeType": "image/webp"}}
    assert base.tool_images([{"type": "text", "text": "x"}, anthropic]) == [{"type": "image/png", "data": "AAAA"}]
    assert base.tool_images({"content": [mcp], "isError": False}) == [{"type": "image/jpeg", "data": "BBBB"}]
    assert base.tool_images([acp, {"type": "text", "text": "y"}]) == [{"type": "image/webp", "data": "CCCC"}]
    # in order, at any depth the reader goes to, never an image given by URL
    nested = {"a": [{"b": {"c": [mcp]}}], "z": anthropic}
    assert [item["data"] for item in base.tool_images(nested)] == ["BBBB", "AAAA"]
    assert base.tool_images([{"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}]) == []
    assert base.tool_images([{"type": "image", "data": ""}, {"type": "image", "data": 5}]) == []
    assert base.tool_images("text") == [] and base.tool_images(None) == []
    deep = mcp
    for _ in range(10):
        deep = [deep]
    assert base.tool_images(deep) == []
    # bounded: so many per result, so large each
    assert len(base.tool_images([mcp] * 20)) == base.TOOL_IMAGE_LIMIT
    # a text far past what the byte limit can decode from is never kept; the
    # exact decoded size is the store's to check
    with patch.object(base, "TOOL_IMAGE_MAX_BYTES", 6):
        assert base.tool_images([{"type": "image", "data": "A" * 13, "mimeType": "image/png"}]) == []
        assert len(base.tool_images([{"type": "image", "data": "A" * 12, "mimeType": "image/png"}])) == 1
    # the text keeps its placeholder where the picture was
    assert base.stringify_content([{"type": "text", "text": "Shot"}, anthropic]) == "Shot\n[image]"
    print("PASS: one reader for Claude, MCP and ACP image blocks, in order and bounded, never an image by URL")


def test_drivers():
    claude = get_driver("claude")
    line = json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "Captured"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(PNG)}}]},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "plain"}]}})
    events = [act for act in claude.parse_line(line, {}) if act.get("a") == "event"]
    assert events[0]["data"] == {"tool_use_id": "toolu_1", "content": "Captured\n[image]",
                                 "is_error": False, "images": [{"type": "image/png", "data": b64(PNG)}]}
    assert "images" not in events[1]["data"], events

    codex = get_driver("codex")
    item = {"id": "mcp-1", "type": "mcpToolCall", "server": "puppy_browser", "tool": "screenshot",
            "arguments": {}, "status": "completed",
            "result": {"content": [{"type": "text", "text": "Browser A91O"},
                                   {"type": "image", "data": b64(PNG), "mimeType": "image/png"}]}}
    result = codex._item_completed(item, {})[-1]["data"]
    # the text reads as Claude's does, never the base64 spelled out
    assert result["content"] == "Browser A91O\n[image]" and b64(PNG)[:12] not in result["content"]
    assert result["images"] == [{"type": "image/png", "data": b64(PNG)}]
    plain = dict(item, id="mcp-2", result={"content": [{"type": "text", "text": "ok"}]})
    result = codex._item_completed(plain, {})[-1]["data"]
    assert "images" not in result and '"text": "ok"' in result["content"], result

    opencode = get_driver("opencode")
    ctx = {}
    opencode._tool_update({"toolCallId": "call-1", "kind": "read", "title": "read",
                           "status": "in_progress", "rawInput": {"filePath": "/tmp/a.png"}}, ctx)
    actions = opencode._tool_update({
        "toolCallId": "call-1", "kind": "read", "status": "completed",
        "content": [{"type": "content", "content": {"type": "image", "data": b64(PNG),
                                                   "mimeType": "image/png"}}]}, ctx)
    result = [act for act in actions if act.get("kind") == "tool_result"][-1]["data"]
    assert result["content"] == "[image]" and result["images"] == [{"type": "image/png", "data": b64(PNG)}]
    print("PASS: claude, codex and opencode hand a tool's pictures over beside its text")


def test_storage():
    sid = db.create_session("Storage fixture", "claude", str(ROOT), "", "", "", "default")
    try:
        stored = uploads.store_tool_images(sid, [
            {"type": "image/png", "data": b64(PNG)},
            {"type": "image/png", "data": b64(JPEG)},          # the bytes decide, not the claim
            {"type": "image/gif", "data": b64(GIF)},
            {"type": "image/webp", "data": b64(WEBP)},
            {"type": "image/png", "data": b64(b"<svg xmlns='http://www.w3.org/2000/svg'/>")},
            {"type": "image/png", "data": "@@not base64@@"},
            {"type": "image/png", "data": "QUJD="},            # bad padding
            "not a dict",
        ])
        assert [item["type"] for item in stored] == ["image/png", "image/jpeg", "image/gif", "image/webp"], stored
        assert [item["size"] for item in stored] == [len(PNG), len(JPEG), len(GIF), len(WEBP)]
        for item, raw, name in zip(stored, (PNG, JPEG, GIF, WEBP),
                                   ("image.png", "image.jpg", "image.gif", "image.webp")):
            assert uploads.TOOL_IMAGE_ID.fullmatch(item["id"]) and not uploads.UPLOAD_ID.fullmatch(item["id"])
            target = uploads._validated_upload_file(sid, item["id"])
            assert target.name == name and target.read_bytes() == raw
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        with patch.object(base, "TOOL_IMAGE_MAX_BYTES", len(PNG) - 1):
            assert uploads.store_tool_images(sid, [{"type": "image/png", "data": b64(PNG)}]) == []
        assert len(uploads.store_tool_images(sid, [{"type": "image/png", "data": b64(PNG)}] * 20)) == \
            base.TOOL_IMAGE_LIMIT
        # a write that fails leaves the picture out instead of raising
        root = Path(config.DATA_DIR).resolve() / "uploads" / str(sid)
        before = sorted(child.name for child in root.iterdir())
        with patch.object(uploads.os, "open", side_effect=OSError("disk full")):
            assert uploads.store_tool_images(sid, [{"type": "image/png", "data": b64(PNG)}]) == []
        assert sorted(child.name for child in root.iterdir()) == before, "and leaves no directory behind"
        # old enough to sweep, named by no message: an upload goes, a tool's
        # picture never was an upload and stays until the session does
        stray = root / "1000000000000-0123456789"
        stray.mkdir(mode=0o700)
        (stray / "left.png").write_bytes(PNG)
        old = time.time() - uploads.ORPHAN_AGE_SECONDS - 60
        for child in root.iterdir():
            os.utime(str(child), (old, old))
        assert uploads.sweep_orphans(sid) == 1 and not stray.exists()
        assert all((root / item["id"]).is_dir() for item in stored)
    finally:
        uploads.remove_session_storage(sid)
        db.delete_session(sid)
    assert not (Path(config.DATA_DIR).resolve() / "uploads" / str(sid)).exists()
    print("PASS: pictures stored privately by their own bytes beside the uploads, bounded, never swept, gone with the session")


async def frame(ws, kind, timeout=15):
    while True:
        value = await ws.receive_json(timeout=timeout)
        if value["type"] == kind:
            return value


async def image_turn(client, headers, sid):
    driver = get_driver("claude")
    hub = runner.hub(sid)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), "--fixture"]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
        try:
            await frame(ws, "snapshot")
            assert hub.send_message("look at these") == {"queued": False}
            live = []
            while len(live) < 2:
                event = (await frame(ws, "event"))["event"]
                if event["kind"] == "tool_result":
                    live.append(event)
            deadline = time.monotonic() + 20
            while hub.status != "idle":
                assert time.monotonic() < deadline, "the fixture turn did not end"
                await asyncio.sleep(.02)
            await hub.turn_task
        finally:
            await ws.close()
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    return live


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        sid = db.create_session("Image fixture", "claude", str(ROOT), "", "", "", "default")
        try:
            live = await image_turn(client, headers, sid)
            stored = {e["data"]["tool_use_id"]: e for e in db.get_events(sid) if e["kind"] == "tool_result"}
            read, shot = stored["toolu_read"]["data"], stored["toolu_shot"]["data"]
            # the event names its pictures, never carries them, live or stored
            assert [e["data"] for e in live] == [read, shot]
            assert read["content"] == "[image]" and len(read["images"]) == 1
            assert read["images"][0]["type"] == "image/png" and read["images"][0]["size"] == len(PNG)
            assert shot["content"] == "Captured the viewport\n[image]\n[image]"
            assert [image["type"] for image in shot["images"]] == ["image/jpeg"], "the undecodable one is left out"
            assert "data" not in json.dumps(stored["toolu_read"]["data"]["images"])
            for image, raw, kind in ((read["images"][0], PNG, "image/png"), (shot["images"][0], JPEG, "image/jpeg")):
                path = "/api/sessions/{}/upload/{}".format(sid, image["id"])
                async with client.get(path, headers=headers) as response:
                    assert response.status == 200, await response.text()
                    assert response.headers["Content-Type"] == kind
                    assert response.headers["X-Content-Type-Options"] == "nosniff"
                    assert response.headers["Cache-Control"] == "private, no-store"
                    assert await response.read() == raw
                # the composer's discard route never takes a tool's picture
                async with client.delete(path, headers=headers) as response:
                    assert response.status in (404, 405), response.status
                assert uploads._validated_upload_file(sid, image["id"]).read_bytes() == raw
            for bad in ("t123-0123456789", "x1790890786930-9cf6575096", "t1790890786930-9CF6575096"):
                async with client.get("/api/sessions/{}/upload/{}".format(sid, bad), headers=headers) as response:
                    assert response.status in (400, 404), (bad, response.status)
            missing = "t1000000000000-0123456789"
            async with client.get("/api/sessions/{}/upload/{}".format(sid, missing), headers=headers) as response:
                assert response.status == 404
            async with client.get("/api/sessions/{}/upload/{}".format(sid, read["images"][0]["id"])) as response:
                assert response.status == 401, "the bytes are the session's, behind its sign-in"
            await native_image_contract(client, headers, sid)
            # A cached tool picture is no longer available after its file goes.
            target = uploads._validated_upload_file(sid, read["images"][0]["id"])
            target.unlink()
            for method in (client.get, client.head):
                async with method("/api/sessions/{}/upload/{}?preview=1".format(
                        sid, read["images"][0]["id"]), headers=headers) as response:
                    assert response.status in (404, 409)
        finally:
            runner.drop_hub(sid)
            uploads.remove_session_storage(sid)
            db.delete_session(sid)
    print("PASS: " + factory.__module__ + " stores a turn's tool pictures, names them on the event and serves them back exactly")


async def native_image_contract(client, headers, sid):
    assert protocol.TOOL_IMAGE_FILES_CAPABILITY in protocol.execution_capabilities()
    target = ROOT / "native image.png"
    target.write_bytes(PNG)
    driver = get_driver("codex")

    def record(path, kind="imageView", failed=False):
        actions = driver._item_completed({"type": kind, "id": "native-{}".format(time.time_ns()),
                                          "path": path, "status": "failed" if failed else "completed"}, {})
        events = [db.add_event(sid, act["kind"], act["data"]) for act in actions if act.get("a") == "event"]
        assert events[-1]["kind"] == "tool_result" and "images" not in events[-1]["data"]
        return "/api/sessions/{}/tool-image/{}".format(sid, events[-1]["seq"])

    path = record(str(target))
    for method in (client.get, client.head):
        async with method(path, headers=headers) as response:
            assert response.status == 200, await response.text()
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Content-Length"] == str(len(PNG))
            assert response.headers["Cache-Control"] == "private, no-store"
            assert await response.read() == (PNG if method == client.get else b"")
    async with client.get(path) as response:
        assert response.status == 401
    relative = record(target.name, "view_image")
    async with client.get(relative, headers=headers) as response:
        assert response.status == 200 and await response.read() == PNG
    other = db.create_session("Other image fixture", "codex", str(ROOT), "", "", "", "default")
    try:
        async with client.get(path.replace("/{}/".format(sid), "/{}/".format(other)), headers=headers) as response:
            assert response.status == 404, "event ids cannot reach another session's paths"
    finally:
        db.delete_session(other)
    for refused in (record(str(target), failed=True), record(str(target), "readFile"),
                    path.rsplit("/", 1)[0] + "/999999"):
        async with client.get(refused, headers=headers) as response:
            assert response.status == 404
    with patch.object(base, "TOOL_IMAGE_MAX_BYTES", len(PNG) - 1):
        async with client.get(path, headers=headers) as response:
            assert response.status == 404, "file previews share the image byte bound"
    target.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    async with client.get(path, headers=headers) as response:
        assert response.status == 404, "a .png name never makes non-raster bytes an image"
    target.unlink()
    for method in (client.get, client.head):
        async with method(path, headers=headers) as response:
            assert response.status == 404
    other_file = ROOT / "other-image.png"
    other_file.write_bytes(PNG)
    target.symlink_to(other_file)
    async with client.get(path, headers=headers) as response:
        assert response.status == 404, "a substituted symlink cannot change the served file"
    target.unlink()
    os.mkfifo(target)
    async with client.get(path, headers=headers) as response:
        assert response.status == 404, "a replaced FIFO cannot hang the preview worker"
    target.unlink()
    print("PASS: native imageView history previews its recorded file, private and bounded, gone on deletion or replacement")


async def main():
    config.load()
    config.set_value("auth.api_token", "tool-images-test-token-0123456789abcdef")
    db.connect()
    test_reader()
    test_drivers()
    test_storage()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
