#!/usr/bin/env python3
"""No-quota tests for the managed headless-browser surface.

A stub "chromium" speaks just enough of the --remote-debugging-pipe CDP
contract (fds 3/4, NUL-framed JSON) to exercise the probe, the node-owned
enable gate, sandbox flag selection, the screencast/input websocket, viewer
input scaling, URL normalization, disable-while-running, and crash reporting.
No real browser is installed or launched and nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="browser-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
STUB_LOG = TEST_ROOT / "stub-log"
STUB_LOG.mkdir(mode=0o700)
os.environ["PUPPY_BROWSER_STUB_LOG"] = str(STUB_LOG)

from puppy import browser, config, db  # noqa: E402
from puppy.web import build_app  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import base64
import json
import os
import signal
import sys

LOG = os.environ["PUPPY_BROWSER_STUB_LOG"]

if sys.argv[1:] == ["--version"]:
    print("StubChrome " + os.environ.get("PUPPY_BROWSER_STUB_VERSION", "152.0.0.1"))
    raise SystemExit(0)

with open(os.path.join(LOG, "argv.json"), "w") as f:
    json.dump(sys.argv, f)

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

FRAME = base64.b64encode(b"stub-jpeg-frame-bytes").decode()
PAGE = {"targetId": "stub-page-1", "type": "page", "title": "stub",
        "url": "about:blank", "attached": False}


def send(message):
    os.write(4, json.dumps(message).encode() + b"\0")


def record(name, payload):
    with open(os.path.join(LOG, name), "a") as f:
        f.write(json.dumps(payload) + "\n")


buffer = b""
casting = False
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        raise SystemExit(0)
    buffer += chunk
    while b"\0" in buffer:
        raw, buffer = buffer.split(b"\0", 1)
        msg = json.loads(raw.decode())
        method = msg.get("method", "")
        params = msg.get("params") or {}
        result = {}
        if method == "Browser.getVersion":
            result = {"product": "StubChrome/152"}
        elif method == "Target.getTargets":
            result = {"targetInfos": [PAGE]}
        elif method == "Target.attachToTarget":
            result = {"sessionId": "stub-sess-1"}
        elif method == "Target.createTarget":
            result = {"targetId": "stub-page-1"}
        elif method == "Page.captureScreenshot":
            result = {"data": FRAME}
        elif method == "Page.getNavigationHistory":
            result = {"currentIndex": 1, "entries": [
                {"id": 1, "url": "about:blank"}, {"id": 2, "url": PAGE["url"]}]}
        elif method == "Page.startScreencast":
            casting = True
        elif method == "Page.stopScreencast":
            casting = False
        elif method == "Page.navigate":
            url = params.get("url", "")
            record("navigations.jsonl", {"url": url})
            if url == "stub://die":
                send({"id": msg.get("id"), "result": {}})
                raise SystemExit(4)
            PAGE["url"] = url
            send({"method": "Target.targetInfoChanged", "params": {"targetInfo": dict(PAGE)}})
            result = {"frameId": "f1"}
        elif method.startswith("Input."):
            record("input.jsonl", {"method": method, "params": params})
        elif method == "Browser.close":
            send({"id": msg.get("id"), "result": {}})
            raise SystemExit(0)
        if msg.get("id") is not None:
            send({"id": msg.get("id"), "result": result})
        if method == "Page.startScreencast" and casting:
            for n in (11, 12):
                send({"method": "Page.screencastFrame", "sessionId": "stub-sess-1",
                      "params": {"data": FRAME, "sessionId": n,
                                 "metadata": {"deviceWidth": 1280, "deviceHeight": 800,
                                              "offsetTop": 0, "pageScaleFactor": 1}}})
'''


def read_lines(name):
    path = STUB_LOG / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def read_json(response):
    try:
        return await response.json()
    except Exception:
        return {"raw": await response.text()}


async def wait_for(predicate, timeout=8.0, message="condition"):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for " + message)


async def collect_ws(ws, texts, frames):
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            texts.append(json.loads(msg.data))
        elif msg.type == aiohttp.WSMsgType.BINARY:
            frames.append(msg.data)
        else:
            break


async def main() -> None:
    stub = TEST_ROOT / "stub-chromium"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o700)
    os.environ["PUPPY_BROWSER_BIN"] = str(stub)

    config.load()
    db.connect()
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    try:
        async with aiohttp.ClientSession() as http:
            # probe: stub advertises a modern version
            async with http.get(url + "/api/browser/status", headers=headers) as r:
                status = await read_json(r)
                assert r.status == 200, status
                assert status["supported"] is True and status["available"] is True, status
                assert status["enabled"] is False and status["running"] is False, status
                assert "StubChrome" in status["product"], status
                expected_sandbox = "no-sandbox" if os.geteuid() == 0 else "sandboxed"
                assert status["sandbox"] == expected_sandbox, status

            # capability + additive ping metadata
            async with http.get(url + "/api/ping", headers=headers) as r:
                ping = await read_json(r)
                assert "browser" in ping["capabilities"], ping
                assert ping["browser"] == {"enabled": False}, ping

            # a too-old binary is refused at enable time with the probed reason
            os.environ["PUPPY_BROWSER_STUB_VERSION"] = "100.0.0.0"
            browser.invalidate_probe()
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": True}) as r:
                refusal = await read_json(r)
                assert r.status == 409, refusal
                assert "old" in refusal["error"], refusal
            os.environ.pop("PUPPY_BROWSER_STUB_VERSION")
            browser.invalidate_probe()

            # non-boolean bodies are rejected
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": "yes"}) as r:
                assert r.status == 400

            # enable for real
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": True}) as r:
                enabled = await read_json(r)
                assert r.status == 200 and enabled["enabled"] is True, enabled
            assert config.get("browser.enabled") is True

            # viewer websocket: status text, frames, input forwarding
            texts, frames = [], []
            ws = await http.ws_connect(url + "/api/ws/browser", headers=headers)
            reader = asyncio.ensure_future(collect_ws(ws, texts, frames))
            await wait_for(lambda: frames, message="first frame")
            await wait_for(lambda: any(t.get("type") == "status" for t in texts),
                           message="status message")
            assert frames[0] == b"stub-jpeg-frame-bytes", frames[0][:40]
            meta = next(t for t in texts if t.get("type") == "frame_meta")
            assert meta["width"] == 1280 and meta["height"] == 800, meta

            argv = json.loads((STUB_LOG / "argv.json").read_text())
            assert "--remote-debugging-pipe" in argv and "--headless=new" in argv, argv
            assert ("--no-sandbox" in argv) is (os.geteuid() == 0), argv
            # and the non-root argv never carries the flag
            assert "--no-sandbox" not in browser.launch_argv("/x", "/p", as_root=False)
            assert "--no-sandbox" in browser.launch_argv("/x", "/p", as_root=True)

            # normalized input coordinates scale by the streamed viewport
            await ws.send_json({"type": "mouse", "kind": "down", "nx": 0.5, "ny": 0.25,
                                "button": "left", "clickCount": 1})
            await ws.send_json({"type": "mouse", "kind": "up", "nx": 0.5, "ny": 0.25,
                                "button": "left", "clickCount": 1})
            await ws.send_json({"type": "key", "kind": "down", "key": "a", "text": "a"})
            await ws.send_json({"type": "insert_text", "text": "hello"})
            inputs = await wait_for(
                lambda: read_lines("input.jsonl") if len(read_lines("input.jsonl")) >= 4
                else None, message="forwarded input")
            press = next(i for i in inputs if i["params"].get("type") == "mousePressed")
            assert press["params"]["x"] == 640 and press["params"]["y"] == 200, press
            key = next(i for i in inputs if i["method"] == "Input.dispatchKeyEvent")
            assert key["params"]["text"] == "a", key
            insert = next(i for i in inputs if i["method"] == "Input.insertText")
            assert insert["params"]["text"] == "hello", insert

            # bare hostnames gain a scheme; LAN-ish suffixes stay cleartext
            await ws.send_json({"type": "navigate", "url": "openhab.lan/start"})
            navs = await wait_for(lambda: read_lines("navigations.jsonl"),
                                  message="navigation")
            assert navs[0]["url"] == "http://openhab.lan/start", navs

            async with http.get(url + "/api/browser/status", headers=headers) as r:
                running = await read_json(r)
                assert running["running"] is True and running["viewers"] == 1, running

            # disabling while running stops the instance and informs viewers
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": False}) as r:
                disabled = await read_json(r)
                assert r.status == 200 and disabled["enabled"] is False, disabled
            await wait_for(lambda: any(t.get("type") == "gone" for t in texts),
                           message="gone notice after disable")
            await ws.close()
            reader.cancel()
            async with http.get(url + "/api/browser/status", headers=headers) as r:
                stopped = await read_json(r)
                assert stopped["running"] is False, stopped

            # a disabled node refuses viewers outright
            ws2 = await http.ws_connect(url + "/api/ws/browser", headers=headers)
            refused = await ws2.receive()
            assert refused.type == aiohttp.WSMsgType.TEXT and \
                "disabled" in json.loads(refused.data)["text"]
            await ws2.close()

            # crash while streaming surfaces as "gone" and cleans up
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": True}) as r:
                assert r.status == 200
            texts3, frames3 = [], []
            ws3 = await http.ws_connect(url + "/api/ws/browser", headers=headers)
            reader3 = asyncio.ensure_future(collect_ws(ws3, texts3, frames3))
            await wait_for(lambda: frames3, message="frame before crash")
            await ws3.send_json({"type": "navigate", "url": "stub://die"})
            await wait_for(lambda: any(t.get("type") == "gone" for t in texts3),
                           message="gone notice after crash")
            await ws3.close()
            reader3.cancel()
            manager = browser.manager()
            await wait_for(lambda: not manager.running, message="manager stopped")

            # the whole config, browser toggle included, round-trips an export
            exported = config.export_data()
            assert exported["browser"]["enabled"] is True, exported
            assert config.normalize_import(exported)["browser"]["enabled"] is True
    finally:
        await browser.shutdown()
        await runner.cleanup()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    print("browser tests passed")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
