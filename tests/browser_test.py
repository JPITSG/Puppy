#!/usr/bin/env python3
"""No-quota tests for the managed headless-browser surface.

A stub "chromium" speaks just enough of the --remote-debugging-pipe CDP
contract (fds 3/4, NUL-framed JSON) to exercise the probe, the node-owned
enable gate, isolated named instances, ID retention, sandbox flag selection,
the screencast/input websocket, pane-driven viewport sizing, navigation-surface
recovery and input scaling, URL normalization, the private per-turn MCP bridge,
hidden model guidance, scoped/searchable accessibility snapshots, observable
action outcomes, bounded inspection/screenshots, form controls, page switching,
redacted diagnostics, background first-use tab events, close-and-replace
behavior, explicit one-chat handoff, session-scoped file inputs, private
download inspection, and crash reporting. No real browser is installed or
launched and nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

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

from puppy import (browser, browser_agent, browser_store, config, db,
                   runner as session_runner, spawn_agent, system_prompts,
                   terminal_agent)  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import CodexDriver  # noqa: E402
from puppy.drivers.opencode import OpenCodeDriver  # noqa: E402
from puppy.web import build_app  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import base64
import json
import os
import re
import signal
import sys

LOG = os.environ["PUPPY_BROWSER_STUB_LOG"]

if sys.argv[1:] == ["--version"]:
    print("StubChrome " + os.environ.get("PUPPY_BROWSER_STUB_VERSION", "152.0.0.1"))
    raise SystemExit(0)

with open(os.path.join(LOG, "argv.jsonl"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "argv": sys.argv}) + "\n")

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

FRAME = base64.b64encode(b"stub-jpeg-frame-bytes").decode()
STALE_FRAME = base64.b64encode(b"stale-screencast-surface").decode()
PAGE = {"targetId": "stub-page-1", "type": "page", "title": "stub",
        "url": "about:blank", "attached": False}
POPUP = {"targetId": "stub-popup-2", "type": "page", "title": "popup",
         "url": "https://popup.example.test/", "attached": False,
         "openerId": PAGE["targetId"]}
PAGE_TEXT = "Stub page Ready Continue Email Region Alerts"
TYPED_VALUE = ""
UPLOADED_FILES = []
SCROLL_Y = 0
STUB_COOKIES = []
SEED_SERIAL = 0
active_target = PAGE["targetId"]


def put_cookie(cookie):
    key = (cookie.get("name"), cookie.get("domain"), cookie.get("path", "/"))
    for index, existing in enumerate(STUB_COOKIES):
        if (existing.get("name"), existing.get("domain"),
                existing.get("path", "/")) == key:
            STUB_COOKIES[index] = cookie
            return
    STUB_COOKIES.append(cookie)


def send(message):
    os.write(4, json.dumps(message).encode() + b"\0")


def record(name, payload):
    with open(os.path.join(LOG, name), "a") as f:
        f.write(json.dumps(payload) + "\n")


buffer = b""
casting = False
viewport_width, viewport_height = 1280, 800
frame_session = 10
fail_next_viewport = False
AX_NODES = [
    {"nodeId": "root", "role": {"value": "RootWebArea"},
     "name": {"value": "Stub page"},
     "childIds": ["button", "input", "region", "file"]},
    {"nodeId": "button", "role": {"value": "button"},
     "name": {"value": "Continue"}, "backendDOMNodeId": 10,
     "properties": [{"name": "focusable", "value": {"value": True}}]},
    {"nodeId": "input", "role": {"value": "textbox"},
     "name": {"value": "Email"}, "backendDOMNodeId": 11,
     "properties": [{"name": "focusable", "value": {"value": True}}]},
    {"nodeId": "region", "role": {"value": "region"},
     "name": {"value": "Preferences"},
     "childIds": ["select", "checkbox"]},
    {"nodeId": "select", "role": {"value": "combobox"},
     "name": {"value": "Region"}, "backendDOMNodeId": 12,
     "properties": [{"name": "focusable", "value": {"value": True}}]},
    {"nodeId": "checkbox", "role": {"value": "checkbox"},
     "name": {"value": "Alerts"}, "backendDOMNodeId": 13,
     "properties": [
         {"name": "focusable", "value": {"value": True}},
         {"name": "checked", "value": {"value": True}}]},
    {"nodeId": "file", "role": {"value": "button"},
     "name": {"value": "Upload file"}, "backendDOMNodeId": 14,
     "properties": [{"name": "focusable", "value": {"value": True}}]},
]
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
        response_error = None
        emit_frame = None
        emit_lifecycle = False
        if method == "Browser.getVersion":
            result = {"product": "StubChrome/152"}
        elif method == "Target.getTargets":
            result = {"targetInfos": [PAGE]}
        elif method == "Target.attachToTarget":
            active_target = params.get("targetId") or PAGE["targetId"]
            result = {"sessionId": "stub-sess-2" if active_target == POPUP["targetId"]
                      else "stub-sess-1"}
        elif method == "Target.createTarget":
            result = {"targetId": "stub-page-1"}
        elif method == "Page.captureScreenshot":
            record("screenshots.jsonl", params)
            result = {"data": FRAME}
        elif method == "Page.getLayoutMetrics":
            result = {"cssContentSize": {"x": 0, "y": 0,
                                         "width": 1200, "height": 1800}}
        elif method == "Accessibility.getRootAXNode":
            record("ax.jsonl", {"method": method, "params": params})
            result = {"node": AX_NODES[0]}
        elif method == "Accessibility.getPartialAXTree":
            record("ax.jsonl", {"method": method, "params": params})
            backend = params.get("backendNodeId")
            result = {"nodes": [node for node in AX_NODES
                                 if node.get("backendDOMNodeId") == backend]}
        elif method == "Accessibility.getChildAXNodes":
            record("ax.jsonl", {"method": method, "params": params})
            parent = next((node for node in AX_NODES
                           if node.get("nodeId") == params.get("id")), {})
            wanted = set(parent.get("childIds") or [])
            result = {"nodes": [node for node in AX_NODES
                                 if node.get("nodeId") in wanted]}
        elif method == "Accessibility.getFullAXTree":
            record("ax.jsonl", {"method": method, "params": params})
            result = {"nodes": AX_NODES}
        elif method == "DOM.getBoxModel":
            result = {"model": {
                "content": [100, 40, 300, 40, 300, 80, 100, 80],
                "border": [98, 38, 302, 38, 302, 82, 98, 82]}}
        elif method == "DOM.scrollIntoViewIfNeeded":
            record("dom.jsonl", {"method": method, "params": params})
        elif method == "DOM.focus":
            record("dom.jsonl", {"method": method, "params": params})
        elif method == "DOM.setFileInputFiles":
            UPLOADED_FILES = list(params.get("files") or [])
            record("file-inputs.jsonl", params)
        elif method == "DOM.resolveNode":
            result = {"object": {"objectId": "node-{}".format(
                params.get("backendNodeId"))}}
        elif method == "Runtime.callFunctionOn":
            declaration = params.get("functionDeclaration") or ""
            backend = int(str(params.get("objectId") or "node-0").rsplit("-", 1)[-1])
            arguments = [item.get("value") for item in params.get("arguments") or []]
            if "puppyInspectElement" in declaration:
                value = {
                    "tag": "button", "visible": True, "inViewport": True,
                    "attributes": {
                        "id": "continue",
                        "href": "https://user:pass@example.test/path?token=secret#fragment",
                    },
                    "box": {"x": 100, "y": 40, "width": 200, "height": 40},
                    "state": {"disabled": False, "checked": None,
                              "selected": False, "required": False,
                              "readOnly": False},
                    "value": None, "valueLength": None,
                    "styles": {"display": "block", "font-family": "Stub Sans",
                               "font-size": "14px", "font-weight": "500"},
                }
            elif "puppyElementState" in declaration:
                value = {"tag": "input", "type": "file" if backend == 14 else "text",
                         "value": None if backend == 14 else TYPED_VALUE,
                         "valueLength": 0 if backend == 14 else len(TYPED_VALUE),
                         "checked": None, "disabled": False}
            elif "puppyFileInputState" in declaration:
                files = []
                for path in UPLOADED_FILES:
                    files.append({"name": os.path.basename(path),
                                  "size": os.path.getsize(path),
                                  "type": "application/octet-stream"})
                value = {"ok": backend == 14, "tag": "input", "type": "file",
                         "count": len(files), "files": files, "disabled": False}
            elif "puppySelectOption" in declaration:
                requested = arguments[0] if arguments[0] is not None else "pl"
                label = arguments[1] if arguments[1] is not None else "Poland"
                value = {"ok": True, "value": requested, "label": label, "index": 1}
            elif "puppySetChecked" in declaration:
                value = {"ok": True, "checked": bool(arguments[0]), "type": "checkbox"}
            else:
                value = None
            record("agent-functions.jsonl", {
                "backend": backend, "arguments": arguments,
                "function": declaration.split("(", 1)[0].split()[-1]})
            result = {"result": {"type": "object", "value": value}}
        elif method == "Runtime.releaseObject":
            pass
        elif method == "Runtime.evaluate":
            expression = params.get("expression") or ""
            record("evaluate.jsonl", {"expression": expression})
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            if "puppyScrollState" in expression:
                value = {"target": "document", "x": 0, "y": SCROLL_Y,
                         "documentX": 0, "documentY": SCROLL_Y}
                result = {"result": {"type": "object", "value": value}}
            elif "puppySharedStorageCapture" in expression:
                if current["url"].startswith("http://ls.example.test"):
                    value = {"origin": "http://ls.example.test",
                             "items": {"token": "local-secret"}}
                else:
                    value = None
                result = {"result": {"type": "object", "value": value}}
            elif expression.startswith("JSON.stringify({url:location.href,title:document.title})"):
                value = json.dumps({"url": current["url"], "title": current["title"]})
                result = {"result": {"type": "string", "value": value}}
            elif "textPresent" in expression and "readyState" in expression:
                match = re.search(r"const wanted=(.*?), absent=(.*?);", expression, re.S)
                wanted = json.loads(match.group(1)) if match else None
                absent = json.loads(match.group(2)) if match else None
                value = {
                    "url": current["url"], "title": current["title"],
                    "readyState": "complete",
                    "textPresent": None if wanted is None else wanted in PAGE_TEXT,
                    "textAbsent": None if absent is None else absent not in PAGE_TEXT,
                }
                result = {"result": {"type": "object", "value": value}}
        elif method == "Emulation.setEmulatedMedia":
            record("media.jsonl", {"features": params.get("features")})
        elif method == "Emulation.setDeviceMetricsOverride":
            if fail_next_viewport:
                fail_next_viewport = False
                record("viewport-fail.jsonl", params)
                response_error = {"message": "renderer is navigating"}
            else:
                record("viewport.jsonl", params)
                width = int(params.get("width") or viewport_width)
                height = int(params.get("height") or viewport_height)
                same_size = width == viewport_width and height == viewport_height
                viewport_width, viewport_height = width, height
                if casting and same_size:
                    emit_frame = (viewport_width, viewport_height, FRAME)
        elif method == "Page.getFrameTree":
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            result = {"frameTree": {"frame": {"id": "f1", "url": current["url"]}}}
        elif method == "Page.setInterceptFileChooserDialog":
            record("file-chooser-intercept.jsonl", params)
        elif method == "Page.setDocumentContent":
            record("document.jsonl", {"frameId": params.get("frameId"),
                                      "html": params.get("html", "")})
            PAGE["title"] = "start page"
        elif method == "Page.getNavigationHistory":
            record("history.jsonl", {})
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            result = {"currentIndex": 1, "entries": [
                {"id": 1, "url": "about:blank"}, {"id": 2, "url": current["url"]},
                {"id": 3, "url": "https://forward.example.test/"}]}
        elif method == "Page.startScreencast":
            record("screencast.jsonl", params)
            casting = True
            emit_frame = (viewport_width, viewport_height, FRAME)
        elif method == "Page.stopScreencast":
            record("screencast-stop.jsonl", {})
            casting = False
        elif method == "Page.navigate":
            url = params.get("url", "")
            record("navigations.jsonl", {"url": url, "pid": os.getpid()})
            if url == "http://stub.invalid/die":
                send({"id": msg.get("id"), "result": {}})
                raise SystemExit(4)
            if url == "http://stub.invalid/slow-nav":
                # A hung commit: no answer, no lifecycle - only later input
                # proves the node's socket loop was never blocked on this.
                continue
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            current["url"] = url
            current["title"] = "navigated"
            if url == "http://cookie.example.test/login":
                put_cookie({"name": "auth", "value": "cookie-secret",
                            "domain": "cookie.example.test", "path": "/",
                            "secure": False, "httpOnly": True, "session": False,
                            "expires": 4102444800.0, "sameSite": "Lax",
                            "size": 17, "sourceScheme": "NonSecure"})
            elif url == "http://cookie.example.test/logout":
                del STUB_COOKIES[:]
            send({"method": "Target.targetInfoChanged",
                  "params": {"targetInfo": dict(current)}})
            result = {"frameId": "f1"}
            emit_lifecycle = True
            if url == "http://stub.invalid/surface-reset":
                emit_frame = (1280, 657, STALE_FRAME)
            elif url == "http://stub.invalid/surface-reset-error":
                fail_next_viewport = True
                emit_frame = (1280, 657, STALE_FRAME)
            elif url == "http://stub.invalid/no-frame":
                emit_frame = None
        elif method in ("Page.reload", "Page.navigateToHistoryEntry"):
            emit_lifecycle = True
            emit_frame = (viewport_width, viewport_height, FRAME)
        elif method == "Storage.getCookies":
            record("cookies-read.jsonl", {"pid": os.getpid(),
                                          "count": len(STUB_COOKIES)})
            result = {"cookies": [dict(cookie) for cookie in STUB_COOKIES]}
        elif method == "Storage.setCookies":
            incoming = params.get("cookies") or []
            record("cookies-set.jsonl", {"pid": os.getpid(), "cookies": incoming})
            for cookie in incoming:
                put_cookie(dict(cookie, session="expires" not in cookie))
        elif method == "Network.deleteCookies":
            record("cookies-delete.jsonl", {"pid": os.getpid(), "params": params})
            STUB_COOKIES[:] = [cookie for cookie in STUB_COOKIES if not (
                cookie.get("name") == params.get("name") and
                cookie.get("domain") == params.get("domain") and
                cookie.get("path", "/") == params.get("path", "/"))]
        elif method == "Page.addScriptToEvaluateOnNewDocument":
            SEED_SERIAL += 1
            record("seed-scripts.jsonl", {"pid": os.getpid(),
                                          "source": params.get("source", "")})
            result = {"identifier": "seed-{}".format(SEED_SERIAL)}
        elif method == "Page.removeScriptToEvaluateOnNewDocument":
            record("seed-removed.jsonl", {"pid": os.getpid(),
                                          "identifier": params.get("identifier")})
        elif method.startswith("Input."):
            record("input.jsonl", {"method": method, "params": params})
            if method == "Input.insertText":
                TYPED_VALUE = str(params.get("text") or "")
            elif method == "Input.dispatchMouseEvent" and \
                    params.get("type") == "mouseWheel":
                SCROLL_Y = max(0, SCROLL_Y + float(params.get("deltaY") or 0))
        elif method == "Browser.close":
            send({"id": msg.get("id"), "result": {}})
            raise SystemExit(0)
        if msg.get("id") is not None:
            if response_error is not None:
                send({"id": msg.get("id"), "error": response_error})
            else:
                send({"id": msg.get("id"), "result": result})
        if emit_lifecycle:
            session_id = "stub-sess-2" if active_target == POPUP["targetId"] \
                else "stub-sess-1"
            send({"method": "Page.frameStartedLoading", "sessionId": session_id,
                  "params": {"frameId": "f1"}})
            send({"method": "Page.domContentEventFired", "sessionId": session_id,
                  "params": {"timestamp": 1}})
            send({"method": "Page.loadEventFired", "sessionId": session_id,
                  "params": {"timestamp": 2}})
            send({"method": "Page.frameStoppedLoading", "sessionId": session_id,
                  "params": {"frameId": "f1"}})
        if emit_frame is not None and casting:
            frame_session += 1
            width, height, data = emit_frame
            send({"method": "Page.screencastFrame", "sessionId": "stub-sess-1",
                  "params": {"data": data, "sessionId": frame_session,
                             "metadata": {"deviceWidth": width, "deviceHeight": height,
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


async def check_cdp_message_isolation() -> None:
    """One excessive CDP reply must not kill or desynchronize the pipe."""
    old_limit = browser.MAX_CDP_BUFFER

    class Owner:
        def __init__(self):
            self.messages = []
            self.oversized = []
            self.lost = 0

        def _on_message(self, message):
            self.messages.append(message)

        def _on_oversized_message(self, prefix, size):
            self.oversized.append((prefix, size))

        def _on_pipe_lost(self):
            self.lost += 1

    owner = Owner()
    protocol = browser._ReadProtocol(owner)
    try:
        browser.MAX_CDP_BUFFER = 64
        protocol.data_received(b'{"id":1,"result":{}}\0')
        protocol.data_received(b'{"id":7,"result":{"blob":"' + b"x" * 45)
        protocol.data_received(b"x" * 45 + b'"}}\0{"id":8,"result":{}}\0')
    finally:
        browser.MAX_CDP_BUFFER = old_limit
    assert [message["id"] for message in owner.messages] == [1, 8], owner.messages
    assert len(owner.oversized) == 1 and owner.oversized[0][0].startswith(b'{"id":7')
    assert owner.oversized[0][1] > 64 and owner.lost == 0, owner.oversized

    # A nested application id in an event is not a browser request id.
    manager = object.__new__(browser.Manager)
    manager.browser_id = "T1T1"
    pending = asyncio.get_event_loop().create_future()
    manager.pending = {91: pending}
    manager._on_oversized_message(
        b'{"method":"Runtime.bindingCalled","params":{"id":91,', 100)
    assert not pending.done() and 91 in manager.pending
    manager._on_oversized_message(b'{"id":91,"result":{"blob":"', 100)
    assert pending.done() and isinstance(pending.exception(), browser.BrowserError)


async def check_bounded_ax_source() -> None:
    """The accessibility walker stops at its cap without a full-tree call."""
    calls = []
    mids = ["mid-{}".format(index) for index in range(100)]
    root = {"nodeId": "root", "childIds": mids,
            "role": {"value": "RootWebArea"}}

    async def call(method, params=None, session="", timeout=0):
        calls.append((method, params or {}))
        if method == "Accessibility.getRootAXNode":
            return {"node": root}
        if method == "Accessibility.getChildAXNodes":
            parent = (params or {}).get("id")
            if parent == "root":
                return {"nodes": [
                    {"nodeId": mid,
                     "childIds": ["{}-leaf-{}".format(mid, leaf)
                                  for leaf in range(100)]}
                    for mid in mids]}
            return {"nodes": [
                {"nodeId": "{}-leaf-{}".format(parent, leaf),
                 "role": {"value": "StaticText"}}
                for leaf in range(100)]}
        raise AssertionError("unexpected AX call " + method)

    manager = object.__new__(browser.Manager)
    manager.browser_id = "A1X1"
    manager.page_session = "ax-session"
    manager.call = call
    nodes, truncated = await manager._agent_ax_source()
    methods = [method for method, _params in calls]
    assert len(nodes) == browser.MAX_AX_SOURCE_NODES, len(nodes)
    assert truncated is True
    assert methods[0] == "Accessibility.getRootAXNode"
    assert "Accessibility.getFullAXTree" not in methods
    assert methods.count("Accessibility.getChildAXNodes") <= 65, len(methods)


async def check_blocking_cleanup_offload() -> None:
    """Filesystem/process reclamation must yield to the server event loop."""
    registry = object.__new__(browser.BrowserRegistry)
    registry.background_tasks = set()
    started = time.monotonic()
    registry._schedule_blocking("test cleanup", time.sleep, 0.25)
    await asyncio.sleep(0.02)
    elapsed = time.monotonic() - started
    assert elapsed < 0.15, "cleanup blocked the event loop for {:.3f}s".format(elapsed)
    if registry.background_tasks:
        await asyncio.gather(*list(registry.background_tasks))


def check_fragmented_bridge_response() -> None:
    """A large fragmented bridge result is scanned once per received chunk."""
    text_value = "z" * (512 * 1024)
    encoded = json.dumps({"ok": True, "result": {"text": text_value}},
                         separators=(",", ":")).encode() + b"\nignored"
    chunks = [encoded[index:index + 128] for index in range(0, len(encoded), 128)]

    class FakeSocket:
        def __init__(self):
            self.chunks = list(chunks)
            self.request = b""

        def settimeout(self, _value):
            pass

        def connect(self, _path):
            pass

        def sendall(self, value):
            self.request = value

        def recv(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

        def close(self):
            pass

    fake = FakeSocket()
    old_socket = browser_agent.socket.socket
    old_env = {key: os.environ.get(key) for key in (
        "PUPPY_BROWSER_SOCKET", "PUPPY_BROWSER_SESSION_ID", "PUPPY_BROWSER_TURN_ID")}
    try:
        browser_agent.socket.socket = lambda *_args, **_kwargs: fake
        os.environ["PUPPY_BROWSER_SOCKET"] = "/tmp/browser-test.sock"
        os.environ["PUPPY_BROWSER_SESSION_ID"] = "12"
        os.environ["PUPPY_BROWSER_TURN_ID"] = "turn-fragments"
        started = time.monotonic()
        result = browser_agent._bridge_call("snapshot", {})
        elapsed = time.monotonic() - started
    finally:
        browser_agent.socket.socket = old_socket
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert result == {"text": text_value}
    assert json.loads(fake.request.decode())[
        "method"] == "snapshot"
    assert elapsed < 1.0, "fragmented bridge response took {:.3f}s".format(elapsed)


class CaptureSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


async def mcp_request(proc, request_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
    await proc.stdin.drain()
    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
    assert raw, "MCP subprocess exited without a response"
    response = json.loads(raw.decode("utf-8"))
    assert response.get("id") == request_id, response
    return response


def check_free_identifiers(ui_source: str) -> None:
    """Class methods must not reach for a name only the constructor has.
    `node --check` parses such a reference happily and a string assert never
    sees it, so a stray `tab.bid` inside buildDom() shipped and took the whole
    console down with "tab is not defined" at the login screen.

    A name is fine when the method takes it as a parameter or declares it
    anywhere in its own body (including nested closures); only a genuinely
    free reference is reported."""
    import re
    lines = ui_source.split("\n")
    watched = ("tab", "session", "view", "anchor", "event")
    offenders = []
    for index, line in enumerate(lines):
        match = re.match(r"^  ([a-zA-Z_$][\w$]*)\((.*?)\)\s*\{\s*$", line)
        if not match or match.group(1) == "constructor":
            continue
        name, params = match.group(1), match.group(2)
        depth, body = 0, []
        for probe in lines[index:]:
            depth += probe.count("{") - probe.count("}")
            body.append(probe)
            if depth <= 0 and len(body) > 1:
                break
        text = "\n".join(body)
        for watch in watched:
            if not re.search(r"(?<![.\w$])" + watch + r"\.", text):
                continue
            declared = (
                re.search(r"(?<![.\w$])" + watch + r"(?![\w$])", params) or
                re.search(r"\b(?:const|let|var)\s+" + watch + r"(?![\w$])", text) or
                re.search(r"\b(?:const|let|var)\s*[\[{][^\n]*?(?<![.\w$])" + watch +
                          r"(?![\w$])", text) or
                re.search(r"\(([^()\n]*?(?<![.\w$])" + watch +
                          r"(?![\w$])[^()\n]*?)\)\s*=>", text) or
                re.search(r"(?<![.\w$])" + watch + r"\s*=>", text) or
                re.search(r"function\s*\w*\s*\([^()\n]*?(?<![.\w$])" + watch +
                          r"(?![\w$])", text))
            if not declared:
                offenders.append("{}() reaches for a free `{}`".format(name, watch))
    assert not offenders, ("free identifiers in class methods:\n  " +
                           "\n  ".join(sorted(set(offenders))))


def check_static_template_styles(ui_source: str) -> None:
    """Static template presentation belongs to app.css, not style attributes."""
    assert 'style="' not in ui_source


def check_reconnect_status(ui_source: str, css_source: str) -> None:
    """A transport outage must overlay, not destroy, model activity text.

    The old close handler called setStatus("Connection lost ..."). A reconnect
    snapshot restores running/idle but carries no ephemeral status text, so the
    warning survived while fresh model output streamed underneath it. Exercise
    the real SessionView status methods in node to keep those states separate.
    """
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    def method(name):
        start = ui_source.index("\n  " + name + "(") + 1
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced SessionView." + name)

    methods = [method(name) for name in (
        "visibleStatusText", "renderStatus", "setReconnecting", "setStatus")]
    script = """
const esc = value => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;");
let stopping = "";
const remoteStoppingMessage = () => stopping;
%s
const proto = {
%s
};
const classes = new Set();
const view = Object.assign(Object.create(proto), {
  tab: {bid: 7},
  reconnecting: false,
  statusText: "thinking 42 tokens",
  statusEl: {innerHTML: ""},
  root: {classList: {toggle(name, on) {
    if (on) classes.add(name); else classes.delete(name);
  }}},
  liveText: "",
  syncLiveStatus() { this.liveText = this.visibleStatusText(); },
  syncHeadOverflow() {},
  updateSteerControl() {},
});
const take = () => ({header: view.statusEl.innerHTML, live: view.liveText,
                     activity: view.statusText, reconnecting: view.reconnecting,
                     metadataHidden: classes.has("transport-lost")});
view.renderStatus();
const before = take();
view.setReconnecting(true);
const lost = take();
view.setStatus("using shell");
const changedWhileLost = take();
stopping = "Backend shutting down…";
view.renderStatus();
const gracefulStop = take();
stopping = "";
view.setReconnecting(false);
const recovered = take();
console.log(JSON.stringify({before, lost, changedWhileLost, gracefulStop, recovered}));
""" % (function("promptStatusBase"), ",\n".join(methods))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:400]
    result = json.loads(proc.stdout.strip())
    assert result["before"]["metadataHidden"] is False, result
    assert "thinking 42 tokens" in result["before"]["header"], result
    assert "Connection lost" in result["lost"]["header"], result
    assert result["lost"]["metadataHidden"] is True, result
    assert result["lost"]["activity"] == "thinking 42 tokens", result
    assert "Connection lost" in result["changedWhileLost"]["header"], result
    assert result["changedWhileLost"]["activity"] == "using shell", result
    assert "Backend shutting down" in result["gracefulStop"]["header"], result
    assert "Connection lost" not in result["gracefulStop"]["header"], result
    assert "using shell" in result["recovered"]["header"], result
    assert "Connection lost" not in result["recovered"]["header"], result
    assert result["recovered"]["metadataHidden"] is False, result
    assert result["recovered"]["live"] == "using shell", result
    assert ".chat.transport-lost .chat-meta-scroll>.chip{display:none}" in css_source


def check_backend_shutdown_notice(ui_source: str) -> None:
    """A lifecycle notice immediately retires only that backend's live state."""
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = "\n".join(function(name) for name in (
        "controllerBackendHealth", "remoteStoppingMessage",
        "retireRemoteSessionActivity", "handleRemoteNodeStopping",
        "clearRemoteNodeStopping"))
    script = r"""
const state = {
  backends: [{id: 7, name: "laptop"}, {id: 8, name: "other"}],
  remoteStopping: {}, remoteOk: {7: true, 8: true}, remoteErrors: {},
  remoteSessions: {
    7: [{id: 11, status: "running", active_since: 100},
        {id: 12, status: "idle", active_since: null}],
    8: [{id: 21, status: "running", active_since: 200}],
  },
  views: {},
};
const remotePollSequence = {7: 3};
const sessionActivityAnchors = new Map([["7:11", 10], ["8:21", 20]]);
let rendered = 0, synced = 0, stopped = 0, cleared = 0, otherStopped = 0;
state.views = {
  matching: {tab: {bid: 7}, handleNodeStopping(message) {
    if (message.includes("restarting")) stopped++;
  }, clearNodeStopping() { cleared++; }},
  other: {tab: {bid: 8}, handleNodeStopping() { otherStopped++; }},
};
const renderTabs = () => { rendered++; };
const syncRemoteStateViews = () => { synced++; };
%s
handleRemoteNodeStopping(7, {type: "node_stopping", reason: "restart"});
const stoppedState = {
  ok: state.remoteOk[7], error: state.remoteErrors[7],
  notice: state.remoteStopping[7], sessions: state.remoteSessions[7],
  other: state.remoteSessions[8][0], localAnchorGone: !sessionActivityAnchors.has("7:11"),
  otherAnchorKept: sessionActivityAnchors.has("8:21"), sequence: remotePollSequence[7],
  rendered, synced, stopped, otherStopped,
};
clearRemoteNodeStopping(7);
const clearedState = {notice: state.remoteStopping[7] || null,
  error: state.remoteErrors[7] || null, cleared};
console.log(JSON.stringify({stoppedState, clearedState}));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    stopped = result["stoppedState"]
    assert stopped["ok"] is False and "restarting" in stopped["error"], stopped
    assert stopped["notice"]["reason"] == "restart", stopped
    assert all(row["status"] == "idle" and row["active_since"] is None
               for row in stopped["sessions"]), stopped
    assert stopped["other"]["status"] == "running", stopped
    assert stopped["localAnchorGone"] and stopped["otherAnchorKept"], stopped
    assert stopped["sequence"] == 4, stopped
    assert stopped["rendered"] == 1 and stopped["synced"] == 1, stopped
    assert stopped["stopped"] == 1 and stopped["otherStopped"] == 0, stopped
    assert result["clearedState"] == {"notice": None, "error": None, "cleared": 1}, result


def check_offline_sidebar_sessions(ui_source: str, css_source: str) -> None:
    """Last-known remote sessions survive reload and remain openable while muted."""
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = "\n".join(function(name) for name in (
        "hydrateBackendLastKnown", "controllerBackendHealth",
        "retireRemoteSessionActivity", "reconcileRemoteState"))
    script = r"""
const cached = {id: 44, name: "cached", status: "running", active_since: 10};
const state = {
  backends: [{id: 7, availability: {state: "offline", reason: "powered off"},
    last_known: {version: 1, sessions: [cached]}}],
  remoteSessions: {}, remoteOk: {}, remoteErrors: {}, remoteStopping: {},
  engCache: {}, remoteEngineErrors: {}, remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {}, remoteUsageRefresh: {}, remoteAutoUpgrade: {},
  remoteUploadSettings: {}, remoteSystemPrompts: {}, remoteBrowser: {},
};
const remotePollSequence = {};
const sessionActivityAnchors = new Map([["7:44", 10]]);
const normalizeUploadSettings = () => null;
const syncRemoteUpdateConnections = () => {};
const clearRemoteNodeStopping = () => {};
%s
hydrateBackendLastKnown(state.backends);
const copied = state.remoteSessions[7][0] !== cached;
const becameOnline = reconcileRemoteState();
console.log(JSON.stringify({
  copied, becameOnline, ok: state.remoteOk[7], error: state.remoteErrors[7],
  session: state.remoteSessions[7][0], sourceStatus: cached.status,
  anchorGone: !sessionActivityAnchors.has("7:44"),
}));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout.strip())
    assert result["copied"] and result["becameOnline"] is False, result
    assert result["ok"] is False and result["error"] == "powered off", result
    assert result["session"]["status"] == "idle", result
    assert result["session"]["active_since"] is None, result
    assert result["sourceStatus"] == "running" and result["anchorGone"], result

    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "const backendUnavailable = !!bid && state.remoteOk[bid] !== true;" in sidebar
    assert '" backend-unavailable"' in sidebar
    assert 'item.dataset.backendAvailability = state.remoteOk[bid] === false ?' in sidebar
    assert "this session can still be opened" in sidebar
    assert "item.disabled" not in sidebar
    assert "openSessionTab(bid, s.id, s);" in sidebar
    assert ".sess-item.backend-unavailable{opacity:.48;filter:grayscale(1)}" in css_source


def check_controller_backend_pooling(ui_source: str) -> None:
    """Controller availability prevents browser probes of offline nodes."""
    def function(name):
        markers = ("function " + name + "(", "async function " + name + "(")
        starts = [ui_source.find(marker) for marker in markers]
        starts = [start for start in starts if start >= 0]
        assert starts, name
        start = min(starts)
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = "\n".join(function(name) for name in (
        "controllerBackendHealth", "backendPoolable", "backendConnectionAllowed",
        "retireRemoteSessionActivity", "reconcileRemoteState",
        "remotePollIsCurrent", "pollRemoteBackend"))
    script = r"""
const state = {
  backends: [
    {id: 7, name: "offline", availability: {
      state: "offline", reason: "connection timed out", checked_at: 10}},
    {id: 8, name: "checking", availability: {
      state: "checking", reason: "checking", checked_at: null}},
    {id: 9, name: "online", availability: {
      state: "online", reason: "", checked_at: 11}},
  ],
  remoteSessions: {}, remoteOk: {7: true}, remoteErrors: {}, remoteStopping: {},
  engCache: {}, remoteEngineErrors: {}, remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {}, remoteUsageRefresh: {}, remoteAutoUpgrade: {},
  remoteUploadSettings: {}, remoteSystemPrompts: {}, remoteBrowser: {},
};
const remotePollSequence = {};
const REMOTE_POLL_TIMEOUT = 5000;
const sessionActivityAnchors = new Map();
let connectionSyncs = 0, apiCalls = 0;
const syncRemoteUpdateConnections = () => { connectionSyncs++; };
let stoppingClears = 0;
const clearRemoteNodeStopping = bid => {
  stoppingClears++;
  delete state.remoteStopping[bid];
  delete state.remoteErrors[bid];
};
const api = async bid => {
  apiCalls++;
  if (bid !== 9) throw new Error("offline backend was pooled");
  const error = new Error("controller reports unavailable");
  error.data = {availability: {
    state: "offline", reason: "recovery probe pending", checked_at: 12}};
  throw error;
};
%s
const becameOnline = reconcileRemoteState();
const poolable = state.backends.map(backend => backendPoolable(backend));
await pollRemoteBackend(state.backends[0], true);
await pollRemoteBackend(state.backends[1], true);
const skippedCalls = apiCalls;
await pollRemoteBackend(state.backends[2], true);
const failedOnline = {ok: state.remoteOk[9], error: state.remoteErrors[9]};
state.backends[2].availability = {state: "online", reason: "", checked_at: 13};
state.remoteStopping[9] = {
  reason: "shutdown", message: "Backend shutting down…", controllerOfflineSeen: false};
const prematureRecovery = reconcileRemoteState();
const heldOffline = state.remoteOk[9] === false && !!state.remoteStopping[9];
state.backends[2].availability = {
  state: "offline", reason: "Backend shutting down", checked_at: 14};
reconcileRemoteState();
const offlineSeen = state.remoteStopping[9].controllerOfflineSeen;
state.backends[2].availability = {state: "online", reason: "", checked_at: 15};
const recoveredAfterController = reconcileRemoteState();
console.log(JSON.stringify({
  becameOnline, connectionSyncs, apiCalls, skippedCalls,
  offline: {ok: state.remoteOk[7], error: state.remoteErrors[7]},
  checkingKnown: Object.prototype.hasOwnProperty.call(state.remoteOk, 8),
  failedOnline, poolable, prematureRecovery, heldOffline, offlineSeen,
  recoveredAfterController, recoveredOnline: state.remoteOk[9], stoppingClears,
}));
""" % source
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout.strip())
    assert result["becameOnline"] is True and result["connectionSyncs"] == 5, result
    assert result["skippedCalls"] == 0 and result["apiCalls"] == 1, result
    assert result["offline"] == {"ok": False, "error": "connection timed out"}, result
    assert result["checkingKnown"] is False, result
    assert result["failedOnline"] == {
        "ok": False, "error": "recovery probe pending"}, result
    assert result["poolable"] == [False, False, True], result
    assert result["prematureRecovery"] is False and result["heldOffline"] is True, result
    assert result["offlineSeen"] is True, result
    assert result["recoveredAfterController"] is True and \
        result["recoveredOnline"] is True and result["stoppingClears"] == 1, result
    assert "if (this.tab.bid && !backendConnectionAllowed(this.tab.bid))" in ui_source
    assert "backendConnectionAllowed(record.backend.id)" in ui_source


def check_backend_last_known_settings(ui_source: str, css_source: str) -> None:
    """A controller snapshot hydrates every node-owned Settings value."""
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = function("normalizeUploadSettings") + "\n" + \
        function("hydrateBackendLastKnown")
    script = r"""
const state={engCache:{},remoteUsageRefresh:{},remoteAutoUpgrade:{},
  remoteUploadSettings:{},remoteBrowser:{},remoteSystemPrompts:{}};
%s
hydrateBackendLastKnown([{id:7,last_known:{version:1,
  engines:[{key:"codex",version:"9.8.7"}],
  usage_refresh:{minutes:15,enabled:true},
  auto_upgrade:{enabled:true,mode:"now",at:"03:30"},
  uploads:{enabled:true,max_file_size_mb:8,max_file_size_bytes:8388608},
  browser:{enabled:true},
  system_prompt:{custom:"remember me"}}}]);
hydrateBackendLastKnown([{id:8,last_known:{version:2,
  usage_refresh:{minutes:99,enabled:true}}}]);
console.log(JSON.stringify({engines:state.engCache[7],usage:state.remoteUsageRefresh[7],
  auto:state.remoteAutoUpgrade[7],uploads:state.remoteUploadSettings[7],
  browser:state.remoteBrowser[7],prompt:state.remoteSystemPrompts[7],
  rejected:state.remoteUsageRefresh[8]||null}));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout.strip())
    assert result == {
        "engines": [{"key": "codex", "version": "9.8.7"}],
        "usage": {"minutes": 15, "enabled": True},
        "auto": {"enabled": True, "mode": "now", "at": "03:30"},
        "uploads": {"enabled": True, "max_file_size_mb": 8,
                    "max_file_size_bytes": 8388608},
        "browser": {"enabled": True},
        "prompt": {"custom": "remember me"},
        "rejected": None,
    }, result
    assert ui_source.count(
        'if (metadata && typeof metadata === "object") current = metadata;') >= 2
    assert "const normalized = normalizeUploadSettings(metadata);" in ui_source
    assert "if (normalized) current = normalized;" in ui_source
    assert "Backend unavailable · showing last known setting" in ui_source
    assert "Backend unavailable · showing last known prompt settings." in ui_source
    assert ".engine-node-offline-values .engine-row{opacity:.48}" in css_source
    assert ".usage-refresh-controls input:disabled{opacity:.5;cursor:not-allowed}" \
        in css_source


def check_interrupted_completion(ui_source: str) -> None:
    """Remote idle detection retires a stopped timer without reporting it.

    Both the session socket and the polling path feed this function, so the
    assertion also covers a phone which learns the outcome only after waking.
    """
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = "\n".join(function(name) for name in (
        "sessionActivityKey", "ingestOneSessionActivity", "reportRemoteCompletion"))
    script = r"""
const state = {notify: {configured: true, enabled: true}};
const sessionActivityAnchors = new Map([
  ["7:1", 1000], ["7:2", 1000], ["7:3", 1000],
]);
const posts = [];
const api = (bid, route, options) => {
  posts.push({bid, route, body: options.body});
  return Promise.resolve({ok: true});
};
%s
ingestOneSessionActivity(7,
  {id: 1, status: "idle", completion_status: "interrupted"}, 20, 5000);
ingestOneSessionActivity(7,
  {id: 2, status: "idle", completion_status: "ok"}, 20, 5000);
// An older backend has no additive outcome and retains the prior behavior.
ingestOneSessionActivity(7, {id: 3, status: "idle"}, 20, 5000);
console.log(JSON.stringify({posts, remaining: sessionActivityAnchors.size}));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["remaining"] == 0, result
    assert [call["body"]["sid"] for call in result["posts"]] == [2, 3], result
    assert all(call["route"] == "notify/fire" for call in result["posts"]), result
    assert "d.active_since, d.server_time, d.completion_status" in ui_source
    assert "d.completion_status);" in ui_source


def check_thinking_icons(ui_source: str) -> None:
    """Dedicated and status-only thinking use one marker for every engine."""
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    start = ui_source.index("\n  syncLiveStatus()") + 1
    brace = ui_source.index("{", start)
    depth = 0
    method = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                method = ui_source[start:index + 1]
                break
    assert method is not None, "unbalanced SessionView.syncLiveStatus"
    script = r"""
class Node {
  constructor(tag,cls="",text="") {
    this.tag=tag;this.className=cls;this.textContent=text;this.children=[];this.parent=null;
    this.attributes={};
  }
  setAttribute(name,value){this.attributes[name]=String(value);}
  appendChild(child){child.parent=this;this.children.push(child);return child;}
  replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];
    children.forEach(child=>this.appendChild(child));}
  replaceWith(child){if(!this.parent)return;const parent=this.parent;
    const i=parent.children.indexOf(this);if(i>=0){parent.children[i]=child;child.parent=parent;}
    this.parent=null;}
  remove(){if(!this.parent)return;const i=this.parent.children.indexOf(this);
    if(i>=0)this.parent.children.splice(i,1);this.parent=null;}
  querySelector(selector){const cls=selector.slice(1);
    for(const child of this.children){if(child.className.split(" ").includes(cls))return child;
      const nested=child.querySelector(selector);if(nested)return nested;}return null;}
  get lastChild(){return this.children[this.children.length-1]||null;}
}
const el=(tag,cls="",text="")=>new Node(tag,cls,text);
%s
%s
%s
%s
const proto={
%s
};
const view=Object.assign(Object.create(proto),{status:"running",statusText:"thinking...",
  liveEl:null,liveKind:null,statusRow:null,inner:el("div"),
  visibleStatusText(){return this.statusText;},atBottom(){return false;},scrollBottom(){}});
const marker=()=>view.statusRow&&view.statusRow.children[0];
view.syncLiveStatus();const codex={cls:marker().className,text:marker().textContent};
view.statusText="using shell";view.syncLiveStatus();
const tool={cls:marker().className,text:marker().textContent};
view.statusText="thinking… 42 tokens";view.syncLiveStatus();
const claude={cls:marker().className,text:marker().textContent};
view.status="idle";view.syncLiveStatus();
console.log(JSON.stringify({codex,tool,claude,idle:view.statusRow,
  classified:[isThinkingStatus("thinking"),isThinkingStatus("Thinking 9 tokens"),
    isThinkingStatus("rethinking"),isThinkingStatus("writing...")]}));
""" % (function("promptStatusBase"), function("promptStatusLabel"),
         function("thinkingIconNode"), function("isThinkingStatus"), method)
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["codex"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["claude"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["tool"] == {"cls": "spinner", "text": ""}, result
    assert result["idle"] is None, result
    assert result["classified"] == [True, True, False, False], result
    assert ui_source.count("sum.appendChild(thinkingIconNode())") == 2


def check_prompt_status_animation(ui_source: str, css_source: str) -> None:
    """Every live prompt status cycles one to three width-stable visual dots."""
    start = ui_source.index("function promptStatusBase(")
    end = ui_source.index("\nfunction promptStatusLabel", start)
    source = ui_source[start:end]
    script = source + r"""
console.log(JSON.stringify([
  promptStatusBase("Thinking..."),
  promptStatusBase("Thinking… 42 tokens"),
  promptStatusBase("Using shell"),
  promptStatusBase("Starting next queued message…"),
]));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    assert json.loads(proc.stdout) == [
        "Thinking", "Thinking 42 tokens", "Using shell",
        "Starting next queued message",
    ]
    assert 'class="status-text prompt-status-label"' in ui_source
    assert ui_source.count('promptStatusLabel(text, "think-label")') == 2
    assert "this.statusText || thinkingLabel(0), \"think-label\"" in ui_source
    assert ".prompt-status-label::after{" in css_source
    assert '0%,32%{content:"."}' in css_source
    assert '33%,65%{content:".."}' in css_source
    assert '66%,100%{content:"..."}' in css_source
    assert "display:inline-block;width:3ch;text-align:left" in css_source
    assert "prefers-reduced-motion:reduce){.prompt-status-label::after" in css_source


def check_backend_editor(ui_source: str, css_source: str) -> None:
    """The URL stack adds/removes rows and the editor keeps secrets private."""
    def extract(name: str) -> str:
        start = ui_source.index("function {}(".format(name))
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced {}".format(name))

    configured = extract("configuredBackendUrls")
    paired_urls = extract("pairingBackendUrls")
    editor_control = extract("backendUrlEditor").replace(
        "function backendUrlEditor(", "function realBackendUrlEditor(", 1)
    editor = extract("modalEditBackend")
    script = r"""
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.value="";this.isConnected=true;
    this.attributes={};this._innerHTML="";}
  appendChild(child){this.children.push(child);return child;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  focus(){this.focused=true;}
  set innerHTML(value){this._innerHTML=value;this.children=[];}
  get innerHTML(){return this._innerHTML;}
  querySelectorAll(tag){const found=[];const walk=node=>{for(const child of node.children){
    if(child.tagName===tag)found.push(child);walk(child);}};walk(this);return found;}
}
const document={createElement:tag=>new Element(tag)};
const el=(tag,cls,text)=>{const node=new Element(tag);node.className=cls||"";
  if(text!==undefined)node.textContent=text;return node;};
const plusIcon=()=>new Element("svg"),xIcon=()=>new Element("svg");
const requestAnimationFrame=fn=>fn();
__REAL_CONTROL__
const controlRoot=new Element("div");
const realControl=realBackendUrlEditor(controlRoot,["https://home.test"]);
controlRoot.children[0].children[1].onclick();
controlRoot.children[1].children[0].value="https://vpn.test";
const addedValues=realControl.values();
const rowsAfterAdd=controlRoot.children.length;
controlRoot.children[1].children[1].onclick();
const controlResult={rowsAfterAdd,addedValues,rowsAfterRemove:controlRoot.children.length,
  remaining:realControl.values(),plusLabel:controlRoot.children[0].children[1].attributes["aria-label"]};

class Classes {
  constructor(){this.names=new Set(["hidden"]);}
  toggle(name,force){if(force)this.names.add(name);else this.names.delete(name);}
  contains(name){return this.names.has(name);}
}
function control(){return {value:"",disabled:false,isConnected:true,textContent:"",
  classList:new Classes(),setAttribute(){},focus(){},select(){}};}
const modals=[];const calls=[];const saved=[];let toastText="";
function modal(html,className){
  const nodes={"#backend-edit-form":control(),"#backend-edit-name":control(),
    "#backend-edit-urls":control(),"#backend-edit-token":control(),
    "#backend-edit-tls":control(),"#backend-edit-pairing":control(),
    "#backend-edit-cancel":control(),"#backend-edit-save":control(),
    ".backend-edit-error":control()};
  const fields=Object.values(nodes).filter((value,index)=>index>0&&index<8);
  nodes["#backend-edit-form"].querySelectorAll=()=>fields;
  const m={html,className,isConnected:true,querySelector:selector=>nodes[selector]};
  const close=()=>{m.isConnected=false;m.closed=true;};
  modals.push({m,nodes,close});return {m,close};
}
function backendUrlEditor(root,initial){root.urlValues=[...initial];return {
  values:()=>root.urlValues.map(value=>value.trim()).filter(Boolean),
  setValues:values=>{root.urlValues=[...values];},setDisabled(){}};}
const toast=text=>{toastText=text;};
async function api(bid,path,options){calls.push({bid,path,options});return {
  ok:true,backend:{id:7,name:options.body.name,urls:options.body.urls},
  connection_changed:options.body.urls[0]!=="https://old.test"};}
__CONFIGURED__
__PAIRED_URLS__
__EDITOR__
const backend={id:7,name:"Old node",url:"https://old.test",
  urls:["https://old.test","https://vpn.test"],tls_fingerprint:"a".repeat(64)};
const first=modalEditBackend(backend,result=>saved.push(result));
const one=modals[0].nodes;
one["#backend-edit-pairing"].value="{";
await one["#backend-edit-form"].onsubmit({preventDefault(){}});
const invalid={message:one[".backend-edit-error"].textContent,
  visible:!one[".backend-edit-error"].classList.contains("hidden"),calls:calls.length};
one["#backend-edit-cancel"].onclick();
const cancelled=first.m.closed===true;

modalEditBackend(backend,result=>saved.push(result));
const two=modals[1].nodes;
two["#backend-edit-name"].value="Renamed node";
two["#backend-edit-token"].value="";
await two["#backend-edit-form"].onsubmit({preventDefault(){}});
const ordinary=calls[0].options.body;

modalEditBackend(backend,result=>saved.push(result));
const three=modals[2].nodes;
three["#backend-edit-name"].value="Paired node";
three["#backend-edit-pairing"].value=JSON.stringify({url:"https://new.test/",
  token:"rotated-secret",tls_sha256:"b".repeat(64),name:"ignored remote name"});
await three["#backend-edit-form"].onsubmit({preventDefault(){}});
const paired=calls[1].options.body;
modalEditBackend(backend,result=>saved.push(result));
const four=modals[3].nodes;
four["#backend-edit-name"].value="Cleartext node";
four["#backend-edit-pairing"].value=JSON.stringify({url:"http://new.test",
  token:"cleartext-secret"});
await four["#backend-edit-form"].onsubmit({preventDefault(){}});
const cleartext=calls[2].options.body;
console.log(JSON.stringify({invalid,cancelled,ordinary,paired,cleartext,saved:saved.length,
  modalClass:modals[0].m.className,html:modals[0].m.html,toastText,controlResult}));
""".replace("__REAL_CONTROL__", editor_control).replace(
        "__CONFIGURED__", configured).replace("__PAIRED_URLS__", paired_urls).replace(
        "__EDITOR__", editor)
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout.strip())
    assert result["invalid"] == {
        "message": "invalid pairing JSON", "visible": True, "calls": 0}, result
    assert result["cancelled"] is True and result["saved"] == 3, result
    assert result["ordinary"] == {
        "name": "Renamed node", "urls": ["https://old.test", "https://vpn.test"],
        "tls_fingerprint": "a" * 64}, result
    assert result["paired"] == {
        "name": "Paired node",
        "urls": ["https://new.test/", "https://old.test", "https://vpn.test"],
        "token": "rotated-secret", "tls_fingerprint": "b" * 64}, result
    assert result["cleartext"] == {
        "name": "Cleartext node",
        "urls": ["http://new.test", "https://old.test", "https://vpn.test"],
        "token": "cleartext-secret", "tls_fingerprint": ""}, result
    assert result["controlResult"] == {
        "rowsAfterAdd": 2,
        "addedValues": ["https://home.test", "https://vpn.test"],
        "rowsAfterRemove": 1,
        "remaining": ["https://home.test"],
        "plusLabel": "Add another backend URL",
    }, result
    assert result["modalClass"] == "backend-edit-modal", result
    assert "leave blank to keep current" in result["html"]
    assert "tested before they replace" in result["html"]
    assert "working address stays preferred" in result["html"]
    assert 'const edit = el("button", "btn btn-sm", "Edit");' in ui_source
    assert "edit.onclick = () => modalEditBackend(b" in ui_source
    assert "if (result.connection_changed) resetRemoteBackendConnection(b.id);" in ui_source
    assert ".backend-edit-grid{display:grid;grid-template-columns:" in css_source
    assert ".backend-url-row{display:flex;align-items:center;gap:6px;min-width:0}" in css_source
    assert "transition:opacity .25s var(--ease)" in css_source
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in css_source
    assert (".be-actions{grid-column:1;grid-row:3;" +
            "grid-template-columns:repeat(2,minmax(0,1fr))}") in css_source

    active_url = extract("activeBackendUrl")
    sync_location = extract("syncBackendLocation")
    status_script = r"""
const state={remoteOk:{7:false}};
const window={matchMedia:()=>({matches:false})};
let scheduled=null,nextTimer=1;
const setTimeout=fn=>{scheduled=fn;return nextTimer++;};
const clearTimeout=id=>{if(id)scheduled=null;};
class Classes{constructor(){this.names=new Set();}toggle(name,on){
  if(on)this.names.add(name);else this.names.delete(name);}}
const layers=[{textContent:""},{textContent:""}];
const track={dataset:{front:"0"},querySelectorAll:()=>layers};
const version={textContent:""};
const root={dataset:{},classList:new Classes(),attributes:{},isConnected:true,
  querySelector:selector=>selector===".be-url-track"?track:version,
  setAttribute:(name,value)=>{root.attributes[name]=value;}};
__CONFIGURED__
__ACTIVE__
__SYNC__
const backend={id:7,url:"https://home.test",urls:["https://home.test","https://vpn.test"],
  active_url:"https://vpn.test",remote_version:"1.2.3"};
syncBackendLocation(root,backend);
const before={front:track.dataset.front,shown:layers[0].textContent,
  queued:typeof scheduled==="function",label:root.attributes["aria-label"],
  cycling:root.classList.names.has("cycling")};
const tick=scheduled;tick();
const after={front:track.dataset.front,shown:layers[1].textContent,
  current:root.dataset.currentUrl};
state.remoteOk[7]=true;
syncBackendLocation(root,backend);
const connected={front:track.dataset.front,shown:layers[0].textContent,
  cycling:root.classList.names.has("cycling"),version:version.textContent};
console.log(JSON.stringify({before,after,connected}));
""".replace("__CONFIGURED__", configured).replace(
        "__ACTIVE__", active_url).replace("__SYNC__", sync_location)
    status_proc = subprocess.run(
        ["node", "--input-type=module", "-e", status_script], capture_output=True, text=True)
    assert status_proc.returncode == 0, status_proc.stderr[:700]
    status = json.loads(status_proc.stdout)
    assert status["before"] == {
        "front": "0", "shown": "https://home.test", "queued": True,
        "label": "https://home.test or https://vpn.test · v1.2.3", "cycling": True,
    }, status
    assert status["after"] == {
        "front": "1", "shown": "https://vpn.test", "current": "https://vpn.test",
    }, status
    assert status["connected"] == {
        "front": "0", "shown": "https://vpn.test", "cycling": False,
        "version": "v1.2.3",
    }, status
    # Engines and Backends share the same compact active/cycling address node;
    # neither may let its URL track consume spare row width and strand the
    # version. A failed engine node also gets a deliberately aligned callout.
    assert "group.setMeta(backend);" in ui_source
    assert "const group = this.engineGroup(b.name, b, b.id);" in ui_source
    assert "locationEl = backendLocationNode(value);" in ui_source
    assert '(status === "bad" ? " engine-node-unavailable" : "")' in ui_source
    assert 'const loading = status !== "bad";' in ui_source
    assert ('const checkingBackend = loading && status === "pending" && ' +
            'engines === null && !!bid;') in ui_source
    assert 'checkingBackend ? "Checking backend…" : "Checking engines…"' in ui_source
    assert '(loading ? " engine-node-loading" : "")' in ui_source
    assert '"engine-node-message engine-node-empty", "No engines reported"' in ui_source
    assert "icon.appendChild(refreshIcon(10));" in ui_source
    assert (".engine-node-meta .be-url{\n  display:inline-flex;align-items:baseline;" in
            css_source)
    assert (".be-row .be-url{\n  display:flex;align-items:baseline;" in css_source)
    assert (".engine-node-message.engine-node-unavailable," +
            ".engine-node-message.engine-node-loading,") in css_source
    assert ".engine-node-message.engine-node-empty{" in css_source
    assert "display:inline-grid;grid-template-columns:6px auto" in css_source
    assert "margin:5px 0 3px;padding:5px 0;" in css_source
    assert ".engine-node-message,.usage-refresh-note,.eau-note{" in css_source
    assert "font-family:var(--sans);font-size:10.5px;font-weight:400;" in css_source
    assert ".engine-node-message{padding:7px 0 5px 30px;color:var(--txt3)}" in css_source
    assert ".engine-node-loading-icon svg{display:block;flex:0 0 auto;animation:spin" in css_source
    assert (".engine-node-message.engine-node-unavailable::before," +
            "\n.engine-node-message.engine-node-empty::before{justify-self:center}") in css_source
    assert "--alert-triangle:url(" in css_source
    assert ".engine-node-message.engine-node-stale::before{" in css_source
    assert 'content:"";display:block;align-self:center;' in css_source
    assert "background:var(--err);-webkit-mask:var(--alert-triangle)" in css_source
    assert (".engine-node-stale{display:flex;align-items:flex-start;gap:7px;" +
            "padding-left:12px") in css_source
    assert (".be-url-track{\n  position:relative;display:grid;flex:0 1 auto;" in
            css_source)
    assert (".be-url-layer{\n  position:static;grid-area:1/1;" in css_source)
    assert "display:block;flex:1 1 auto" not in css_source
    assert '.be-url-version:not(:empty){margin-left:1ch}' in css_source
    assert '.be-url-version:not(:empty)::before{content:"·";margin-right:1ch}' \
        in css_source
    assert ('if (status === "bad" && refresh && ' +
            'refresh.classList.contains("refreshing")) {') not in ui_source
    assert 'if (refresh) refresh.disabled = !!bid && status !== "ok";' in ui_source

    # A manual retry announces both edges of the attempt, remains gated while
    # in flight, and always restores the button even when the backend is still
    # unreachable. Settings keeps the last values painted and gates the retry
    # again when its post-request availability sync still says offline.
    refresh_source = "async " + extract("refreshEngineVersions")
    refresh_script = r"""
const ENGINE_REFRESH_TIMEOUT=1234;
let shouldFail=true,synced=0,applied=0;const calls=[],errors=[];
const syncRemoteStateViews=()=>{synced++;};
const api=async(bid,path,options)=>{calls.push({bid,path,options});
  if(shouldFail)throw new Error("still unavailable");return {engines:[],usage_refresh:{}};};
const applyEnginesPayload=()=>{applied++;};
const toast=(text,kind)=>{errors.push({text,kind});};
class Classes{constructor(){this.names=new Set();}add(name){this.names.add(name);}
  remove(name){this.names.delete(name);}contains(name){return this.names.has(name);}}
const makeButton=()=>({disabled:false,isConnected:true,classList:new Classes(),attributes:{},
  setAttribute(name,value){this.attributes[name]=String(value);},
  removeAttribute(name){delete this.attributes[name];}});
__REFRESH__
const failedButton=makeButton();
const failedTask=refreshEngineVersions(7,failedButton,"Laptop");
const failedDuring={disabled:failedButton.disabled,
  refreshing:failedButton.classList.contains("refreshing"),
  busy:failedButton.attributes["aria-busy"],synced};
await failedTask;
const failedAfter={disabled:failedButton.disabled,
  refreshing:failedButton.classList.contains("refreshing"),
  busy:failedButton.attributes["aria-busy"]||null,synced,applied,errors:errors.length};
shouldFail=false;
const goodButton=makeButton();
const goodTask=refreshEngineVersions(7,goodButton,"Laptop");
const goodDuring={disabled:goodButton.disabled,
  refreshing:goodButton.classList.contains("refreshing"),synced};
await goodTask;
const goodAfter={disabled:goodButton.disabled,
  refreshing:goodButton.classList.contains("refreshing"),synced,applied};
console.log(JSON.stringify({failedDuring,failedAfter,goodDuring,goodAfter,calls}));
""".replace("__REFRESH__", refresh_source)
    refresh_proc = subprocess.run(
        ["node", "--input-type=module", "-e", refresh_script],
        capture_output=True, text=True)
    assert refresh_proc.returncode == 0, refresh_proc.stderr[:700]
    refreshed = json.loads(refresh_proc.stdout)
    assert refreshed["failedDuring"] == {
        "disabled": True, "refreshing": True, "busy": "true", "synced": 1,
    }, refreshed
    assert refreshed["failedAfter"] == {
        "disabled": False, "refreshing": False, "busy": None,
        "synced": 2, "applied": 0, "errors": 1,
    }, refreshed
    assert refreshed["goodDuring"] == {
        "disabled": True, "refreshing": True, "synced": 3,
    }, refreshed
    assert refreshed["goodAfter"] == {
        "disabled": False, "refreshing": False, "synced": 4, "applied": 1,
    }, refreshed
    assert refreshed["calls"] == [
        {"bid": 7, "path": "engines/refresh",
         "options": {"method": "POST", "timeoutMs": 1234}},
        {"bid": 7, "path": "engines/refresh",
         "options": {"method": "POST", "timeoutMs": 1234}},
    ], refreshed


def check_drawer_drag(ui_source: str) -> None:
    """Run the real mobile drawer gesture against a tiny pointer-event DOM.

    The drawer must occupy an intermediate position for as long as a finger is
    paused, settle by position after a slow drag, accept a short fast flick,
    and leave vertical motion alone for the session scroller.
    """
    marker = ui_source.index("/* Touch-only drawer drag.")
    start = ui_source.index("(() => {", marker)
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = ui_source.index(")();", index) + 4
                break
    assert end is not None, "unbalanced drawer gesture"
    gesture = ui_source[start:end]
    script = r"""
class Classes {
  constructor(...names) { this.names = new Set(names); }
  add(name) { this.names.add(name); }
  remove(name) { this.names.delete(name); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    if (force === undefined) force = !this.names.has(name);
    if (force) this.names.add(name); else this.names.delete(name);
    return force;
  }
}
class Target {
  constructor(width=0) {
    this.width = width; this.listeners = {};
    this.classList = new Classes();
    this.style = {transform:"", opacity:"", removeProperty(name) { this[name] = ""; }};
  }
  addEventListener(kind, fn) { (this.listeners[kind] ||= []).push(fn); }
  emit(kind, values={}) {
    const event = Object.assign({pointerId:1, pointerType:"touch", isPrimary:true,
      clientX:0, clientY:0, timeStamp:0, prevented:false,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() {}}, values);
    for (const fn of this.listeners[kind] || []) fn(event);
    return event;
  }
  setPointerCapture() {} releasePointerCapture() {}
  getBoundingClientRect() { return {width:this.width}; }
  get offsetWidth() { return this.width; }
}
const nodes = {app:new Target(), side:new Target(300),
  "side-backdrop":new Target(), "drawer-edge":new Target()};
const $ = id => nodes[id];
const window = {matchMedia:q => ({matches:q.includes("max-width")})};
let frame = null;
const requestAnimationFrame = fn => { frame = fn; return 1; };
const cancelAnimationFrame = () => { frame = null; };
const runFrame = () => { const fn=frame; frame=null; if (fn) fn(); };
%s
const edge=nodes["drawer-edge"], side=nodes.side, app=nodes.app, shade=nodes["side-backdrop"];
edge.emit("pointerdown", {clientX:10,clientY:100,timeStamp:0});
edge.emit("pointermove", {clientX:90,clientY:100,timeStamp:30});
const anchored={transform:side.style.transform,opacity:shade.style.opacity,
                dragging:app.classList.contains("drawer-dragging")};
const paused={transform:side.style.transform,opacity:shade.style.opacity};
edge.emit("pointerup", {clientX:90,clientY:100,timeStamp:300}); runFrame();
const partialClosed=!app.classList.contains("side-open") && side.style.transform==="";

edge.emit("pointerdown", {clientX:10,clientY:100,timeStamp:400});
edge.emit("pointermove", {clientX:210,clientY:100,timeStamp:650});
edge.emit("pointerup", {clientX:210,clientY:100,timeStamp:800}); runFrame();
const majorityOpen=app.classList.contains("side-open") && side.style.transform==="";

side.emit("pointerdown", {clientX:250,clientY:300,timeStamp:900});
side.emit("pointermove", {clientX:80,clientY:300,timeStamp:950});
const closingHeld=side.style.transform;
side.emit("pointerup", {clientX:80,clientY:300,timeStamp:1200}); runFrame();
const positionClosed=!app.classList.contains("side-open");

edge.emit("pointerdown", {clientX:10,clientY:100,timeStamp:1300});
edge.emit("pointermove", {clientX:14,clientY:190,timeStamp:1350});
edge.emit("pointerup", {clientX:14,clientY:190,timeStamp:1400});
const verticalUntouched=side.style.transform==="" &&
  !app.classList.contains("drawer-dragging") && !app.classList.contains("side-open");

edge.emit("pointerdown", {clientX:10,clientY:100,timeStamp:1500});
edge.emit("pointermove", {clientX:48,clientY:100,timeStamp:1520});
edge.emit("pointerup", {clientX:48,clientY:100,timeStamp:1525}); runFrame();
const flickOpen=app.classList.contains("side-open");
const nextSideTap=side.emit("click").prevented === false;
const gestureClickBlocked=edge.emit("click").prevented === true;
console.log(JSON.stringify({anchored,paused,partialClosed,majorityOpen,
                            closingHeld,positionClosed,verticalUntouched,flickOpen,
                            nextSideTap,gestureClickBlocked}));
""" % gesture
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["anchored"] == {
        "transform": "translate3d(-210px,0,0)", "opacity": "0.3", "dragging": True}, result
    assert result["paused"] == {
        "transform": "translate3d(-210px,0,0)", "opacity": "0.3"}, result
    assert result["partialClosed"] and result["majorityOpen"], result
    assert result["closingHeld"] == "translate3d(-170px,0,0)", result
    assert result["positionClosed"] and result["verticalUntouched"] and result["flickOpen"], result
    assert result["nextSideTap"] and result["gestureClickBlocked"], result


def check_responsive_drawer_chrome(css_source: str) -> None:
    """Narrow touch capability must not hide desktop navigation or cast a shadow."""
    narrow_start = css_source.index("@media (max-width:900px){")
    coarse_start = css_source.index(
        "@media (max-width:900px) and (any-pointer:coarse){", narrow_start)
    narrow = css_source[narrow_start:coarse_start]
    coarse_end = css_source.index(
        "@media (prefers-reduced-motion:reduce){", coarse_start)
    coarse = css_source[coarse_start:coarse_end]

    assert ".burger{display:none}" not in css_source
    assert ".drawer-edge{" in coarse
    assert "box-shadow:20px 0 60px -20px rgba(0,0,0,.8);" not in \
        narrow[narrow.index(".side{"):narrow.index(".side-scroll{")]
    assert ".app.side-open .side,.app.drawer-dragging .side{" in narrow
    assert "box-shadow:20px 0 60px -20px rgba(0,0,0,.8);" in narrow


def check_browser_viewport(ui_source: str) -> None:
    """Run the real BrowserView sizing methods without constructing its DOM."""
    start = ui_source.index("class BrowserView {")
    end = ui_source.index("/* ================= SettingsView", start)
    browser_view = ui_source[start:end]
    script = r"""
const WebSocket={OPEN:1};
let nextTimer=0,cleared=0;
const timers=new Map();
const setTimeout=(fn,delay)=>{const id=++nextTimer;timers.set(id,{fn,delay});return id;};
const clearTimeout=id=>{if(timers.delete(id))cleared++;};
%s
const sent=[];
const view=Object.create(BrowserView.prototype);
view.stage={clientWidth:901.9,clientHeight:543.8};
view.ws={readyState:WebSocket.OPEN,send:value=>sent.push(JSON.parse(value))};
view.lastViewport="";view.viewportTimer=null;
view.sendViewport();
view.sendViewport();
view.stage.clientWidth=7680;view.stage.clientHeight=4320;
view.sendViewport();
view.stage.clientWidth=100;view.stage.clientHeight=100;
view.sendViewport();
view.stage.clientWidth=1000;view.stage.clientHeight=600;
view.queueViewport();
view.stage.clientWidth=1001;view.stage.clientHeight=601;
view.queueViewport();
const [queuedId,queued]=[...timers.entries()][0];
timers.delete(queuedId);
queued.fn();
console.log(JSON.stringify({sent,cleared,timers:timers.size,delay:queued.delay}));
""" % browser_view
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout)
    assert result == {
        "sent": [
            {"type": "viewport", "width": 901, "height": 543},
            {"type": "viewport", "width": 3840, "height": 2160},
            {"type": "viewport", "width": 1001, "height": 601},
        ],
        "cleared": 1, "timers": 0, "delay": 80,
    }, result


def check_browser_transport_controls(ui_source: str) -> None:
    """Exercise wheel collapse, viewer activity, and reconnect backoff."""
    start = ui_source.index("class BrowserView {")
    end = ui_source.index("/* ================= SettingsView", start)
    browser_view = ui_source[start:end]
    script = r"""
const sockets=[];
function WebSocket(url){this.url=url;this.readyState=0;this.bufferedAmount=0;sockets.push(this);}
WebSocket.OPEN=1;
WebSocket.prototype.send=function(){};
WebSocket.prototype.close=function(){};
let visibility="visible";
const document={get visibilityState(){return visibility;}};
let nextRaf=0;
const rafs=new Map();
const requestAnimationFrame=fn=>{const id=++nextRaf;rafs.set(id,fn);return id;};
const cancelAnimationFrame=id=>rafs.delete(id);
const flushRaf=()=>{const item=[...rafs.entries()][0];rafs.delete(item[0]);item[1]();};
let nextTimer=0;
const timers=new Map();
const setTimeout=(fn,delay)=>{const id=++nextTimer;timers.set(id,{fn,delay});return id;};
const clearTimeout=id=>timers.delete(id);
const backendConnectionAllowed=()=>true;
const remoteStoppingMessage=()=>"";
const wsUrl=(_bid,path)=>path;
const noteRemoteSocketReachable=()=>{};
%s

const sent=[];
const wheel=Object.assign(Object.create(BrowserView.prototype),{
  viewerActive:true,closed:false,wheelQueued:null,wheelFrame:null,
  ws:{readyState:WebSocket.OPEN,bufferedAmount:0,
      send:value=>sent.push(JSON.parse(value))},
});
wheel.queueWheel({type:"wheel",nx:.1,ny:.2,dx:2,dy:3,modifiers:0});
wheel.queueWheel({type:"wheel",nx:.4,ny:.5,dx:7,dy:-1,modifiers:8});
const beforeFlush=sent.length;
flushRaf();
wheel.ws.bufferedAmount=300*1024;
wheel.queueWheel({type:"wheel",nx:.6,ny:.7,dx:9,dy:9,modifiers:0});
flushRaf();
wheel.ws.bufferedAmount=0;
wheel.viewerActive=false;
wheel.queueWheel({type:"wheel",nx:.8,ny:.9,dx:10,dy:10,modifiers:0});
flushRaf();

const activitySent=[];
let viewportQueues=0,reconnectRequests=0;
const activity=Object.assign(Object.create(BrowserView.prototype),{
  visible:true,viewerActive:null,closed:false,terminalGone:false,ws:null,
  send:value=>activitySent.push(value),
  queueViewport:()=>{viewportQueues++;},
  scheduleReconnect:delay=>{reconnectRequests++;activity.reconnectRequested=delay;},
});
activity.syncViewerActivity();
activity.syncViewerActivity();
visibility="hidden";
activity.syncViewerActivity();
visibility="visible";
activity.syncViewerActivity();

let connects=0;
const reconnect=Object.assign(Object.create(BrowserView.prototype),{
  closed:false,terminalGone:false,visible:true,ws:null,tab:{bid:0},
  reconnectTimer:null,reconnectDelay:1000,
  connect(){connects++;this.ws={readyState:0};},
});
reconnect.scheduleReconnect();
const firstTimer=[...timers.values()][0];
const firstDelay=firstTimer.delay;
timers.clear();reconnect.reconnectTimer=null;firstTimer.fn();
reconnect.ws=null;reconnect.terminalGone=true;
reconnect.scheduleReconnect();
const terminalTimers=timers.size;
reconnect.terminalGone=false;visibility="hidden";
reconnect.scheduleReconnect();
const hiddenTimers=timers.size;
visibility="visible";reconnect.visible=false;
reconnect.scheduleReconnect();
const invisibleTimers=timers.size;

const closeNotices=[];
let closeRetries=0;
const connection=Object.assign(Object.create(BrowserView.prototype),{
  closed:false,terminalGone:false,visible:true,ws:null,tab:{bid:0,browserId:"A1B2"},
  reconnectTimer:null,reconnectDelay:1000,connectionSequence:0,waitingForBackend:false,
  showDead:(message,ended)=>closeNotices.push({message,ended}),
  scheduleReconnect:()=>{closeRetries++;},syncViewerActivity:()=>{},
  sendColorScheme:()=>{},sendViewport:()=>{},syncRemoteState:()=>{},
});
connection.connect();
const transientSocket=sockets[0];
transientSocket.readyState=WebSocket.OPEN;
transientSocket.onopen();
transientSocket.onclose();
connection.connect();
const terminalSocket=sockets[1];
connection.terminalGone=true;
terminalSocket.onclose();

console.log(JSON.stringify({beforeFlush,sent,activitySent,viewportQueues,
  reconnectRequests,reconnectRequested:activity.reconnectRequested,
  firstDelay,nextDelay:reconnect.reconnectDelay,connects,
  terminalTimers,hiddenTimers,invisibleTimers,closeNotices,closeRetries}));
""" % browser_view
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["beforeFlush"] == 0, result
    assert result["sent"] == [{
        "type": "wheel", "nx": 0.4, "ny": 0.5,
        "dx": 9, "dy": 2, "modifiers": 8,
    }], result
    assert result["activitySent"] == [
        {"type": "viewer_active", "active": True},
        {"type": "viewer_active", "active": False},
        {"type": "viewer_active", "active": True},
    ], result
    assert result["viewportQueues"] == 2 and result["reconnectRequests"] == 2, result
    assert result["reconnectRequested"] == 0, result
    assert result["firstDelay"] == 1000 and result["nextDelay"] == 2000, result
    assert result["connects"] == 1, result
    assert result["terminalTimers"] == 0 and result["hiddenTimers"] == 0 and \
        result["invisibleTimers"] == 0, result
    assert result["closeNotices"] == [{"message": "Connection closed",
                                       "ended": False}], result
    assert result["closeRetries"] == 1, result
    reconnect_button = browser_view[browser_view.index("again.onclick = () => {"):
                                    browser_view.index("const close =", browser_view.index(
                                        "again.onclick = () => {"))]
    assert "this.ws = null;" in reconnect_button and \
        "this.connectionSequence++;" in reconnect_button


def check_browser_theme_fanout(ui_source: str) -> None:
    """A theme toggle sends once per node and prefers a connected view."""
    start = ui_source.index("function applyTheme(")
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    source = ui_source[start:end]
    script = r"""
const calls=[];
const document={documentElement:{classList:{toggle(){}}}};
const lsSet=()=>{};
const themeButton={replaceChildren(){},setAttribute(){}};
const $=()=>themeButton;
const themeIcon=()=>({});
const make=(name,bid,ready)=>({name,tab:{bid},ws:ready===null?null:{readyState:ready},
  sendColorScheme(){calls.push(this.name);}});
const state={views:{
  disconnected:make("disconnected",7,null),
  connected:make("connected",7,1),
  duplicate:make("duplicate",7,1),
  remote:make("remote",8,1),
}};
%s
applyTheme("dark");
console.log(JSON.stringify(calls));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:800]
    assert json.loads(proc.stdout) == ["connected", "remote"], proc.stdout


def check_session_draft_sync(ui_source: str) -> None:
    """Exercise the real composer reconciliation without constructing its DOM."""
    draft_helpers = ui_source[
        ui_source.index("const DRAFT_JOURNAL_VERSION"):
        ui_source.index("function snapshotBrowserState")]
    attachment_helpers = ui_source[
        ui_source.index("const ATTACHMENT_PREVIEW_TYPES"):
        ui_source.index("function dataTransferHasFiles")]
    start = ui_source.index("class SessionView {")
    session_view = ui_source[start:ui_source.index("class TermView", start)]
    script = r"""
const storage=new Map();
const lsGet=key=>storage.has(key)?storage.get(key):null;
const lsSet=(key,value)=>storage.set(key,String(value));
const lsDel=key=>storage.delete(key);
const WebSocket={OPEN:1};
const URL={revoked:[],revokeObjectURL(value){this.revoked.push(value);}};
const fmtBytes=value=>String(value)+" B";
const noteSessionActivity=()=>{};
const scrollCaretIntoView=()=>{};
const toast=()=>{};
%s
%s
%s
function makeView(id="s:0:42") {
  const sent=[];
  const queueClasses=new Set();
  const view=Object.create(SessionView.prototype);
  Object.assign(view, {
    tab:{id,bid:0,sid:42}, closed:false,
    ta:{value:"",selectionStart:0,selectionEnd:0,readOnly:false,focused:false,
      focus(){this.focused=true;},
      setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}},
    queueEl:{
      classList:{toggle(name,on){if(on)queueClasses.add(name);else queueClasses.delete(name);}},
      setAttribute(){},removeAttribute(){},querySelectorAll(){return[];},
    },
    attachments:[],histAttach:null,histIdx:null,histDraft:"",sentThumbs:new Map(),
    draftSupported:true,draftReady:true,draftRevision:0,
    draftMaxChars:100000,status:"idle",_forceScroll:false,
    steering:{supported:true,ready:false,turn_id:""},steerPending:null,
    draftClientId:"device-a",draftClientSeq:0,draftLatestSeq:0,draftAckSeq:0,
    draftInFlightSeq:0,draftPendingText:null,
    draftDeferred:null,draftTouchedBeforeReady:false,draftJournal:null,
    queueEditSeq:0,queueEditPending:null,
    ws:{readyState:WebSocket.OPEN,send:value=>sent.push(JSON.parse(value))},
    renderAttachments(){},resizeComposer(){},releaseHistoryAttachments(){},
    scrollBottom(){},updateRunState(){},setSteeringState(){},setStatus(){},
    discardServerUpload(){},updateSteerControl(){},
  });
  return {view,sent,queueClasses};
}

const first=makeView();
first.view.ta.value="Test";
first.view.ta.selectionStart=first.view.ta.selectionEnd=4;
first.view.saveDraft();
const pendingJournal=JSON.parse(storage.get("puppy.draft.s:0:42"));
first.view.receiveDraft({type:"draft",text:"Test",revision:1,updated_at:1,
  client_id:"device-a",client_seq:1});
const journalCleared=!storage.has("puppy.draft.s:0:42");
first.view.receiveDraft({type:"draft",text:"from device b",revision:2,updated_at:2,
  client_id:"device-b",client_seq:1});
const followed=first.view.ta.value;

first.view.ta.value="local winner";
first.view.saveDraft();
first.view.receiveDraft({type:"draft",text:"peer in flight",revision:3,updated_at:3,
  client_id:"device-b",client_seq:2});
const whilePending={text:first.view.ta.value,deferred:first.view.draftDeferred.text};
first.view.receiveDraft({type:"draft",text:"local winner",revision:4,updated_at:4,
  client_id:"device-a",client_seq:2});
const afterAck={text:first.view.ta.value,deferred:first.view.draftDeferred,
  journal:storage.has("puppy.draft.s:0:42")};
first.view.receiveDraft({type:"draft",text:"latest peer",revision:5,updated_at:5,
  client_id:"device-b",client_seq:3});

const imagePath="/private/uploads/42/1700000000000-abcdef0123/photo.png";
const marker=`${ATTACH_IMAGE_PREFIX}${imagePath}${ATTACH_IMAGE_SUFFIX}`;
first.view.receiveDraft({type:"draft",text:marker,revision:6,updated_at:6,
  client_id:"device-b",client_seq:4});
const sharedAttachment={count:first.view.attachments.length,
  path:first.view.attachments[0].path,prose:first.view.ta.value};
first.view.attachments.push({path:"",url:"blob:upload",ownsUrl:true,uploading:true,
  removed:false,controller:null});
first.view.receiveDraft({type:"draft",text:"peer prose",revision:7,updated_at:7,
  client_id:"device-b",client_seq:5});
const uploadPreserved={count:first.view.attachments.length,
  uploading:first.view.attachments[0].uploading,text:first.view.ta.value};

storage.set("puppy.draft.s:0:43", "local-only text");
const localOnly=makeView("s:0:43");
localOnly.view.draftSupported=false;
localOnly.view.draftReady=false;
localOnly.view.draftJournal=readLocalDraft("s:0:43");
localOnly.view.ta.value=localOnly.view.draftJournal.text;
localOnly.view.initializeDraft(null);
storage.set("puppy.draft.s:0:47", "not a versioned journal");
const invalidJournal=readDraftJournal("s:0:47");

writeDraftJournal("s:0:44", "stale submitted text", 0, true);
const stale=makeView("s:0:44");
stale.view.draftReady=false;
stale.view.draftJournal=readDraftJournal("s:0:44");
stale.view.initializeDraft({text:"newer server text",revision:3,updated_at:8});

writeDraftJournal("s:0:45", "unacknowledged edit", 0);
const unacked=makeView("s:0:45");
unacked.view.draftReady=false;
unacked.view.draftJournal=readDraftJournal("s:0:45");
unacked.view.initializeDraft({text:"accepted prefix",revision:3,updated_at:8});

const offline=makeView("s:0:46");
offline.view.ws=null;
offline.view.ta.value="typed while disconnected";
offline.view.saveDraft();
const offlineBefore={ready:offline.view.draftReady,
  touched:offline.view.draftTouchedBeforeReady};
offline.view.ws={readyState:WebSocket.OPEN,
  send:value=>offline.sent.push(JSON.parse(value))};
offline.view.initializeDraft({text:"server while away",revision:8,updated_at:9});

const burst=makeView("s:0:48");
burst.view.ta.value="a";
burst.view.saveDraft();
burst.view.ta.value="ab";
burst.view.saveDraft();
burst.view.ta.value="latest";
burst.view.saveDraft();
const burstBeforeAck=burst.sent.slice();
burst.view.receiveDraft({type:"draft",text:"a",revision:1,updated_at:10,
  client_id:"device-a",client_seq:1});
const burstAfterFirstAck=burst.sent.slice();
burst.view.receiveDraft({type:"draft",text:"latest",revision:2,updated_at:11,
  client_id:"device-a",client_seq:2});

const submission=makeView("s:0:49");
submission.view.ta.value="first";
submission.view.saveDraft();
submission.view.ta.value="send this exact value";
submission.view.saveDraft();
submission.view.submit();
const submissionSent=submission.sent.slice();
submission.view.receiveDraft({type:"draft",text:"first",revision:1,updated_at:12,
  client_id:"device-a",client_seq:1});
submission.view.receiveDraft({type:"draft",text:"send this exact value",revision:2,
  updated_at:13,client_id:"device-a",client_seq:2});
submission.view.receiveDraft({type:"draft",text:"",revision:3,updated_at:14,
  client_id:"device-a",client_seq:3,consumed:true});

/* Editing wins over a coalesced old composer and over saveDraft calls caused
   by an upload completing behind the click. Its broadcast is held until the
   direct completion can discard the replaced in-flight upload atomically. */
const editing=makeView("s:0:50");
editing.view.ta.value="old composer";
editing.view.saveDraft();
editing.view.ta.value="newest old composer";
editing.view.saveDraft();
let uploadAborted=false;
editing.view.attachments=[{path:"",url:"blob:editing",ownsUrl:true,uploading:true,
  removed:false,uploadId:"",controller:{abort(){uploadAborted=true;}}}];
editing.view.editQueued(1,"queued replacement");
const journalDuringEdit=JSON.parse(storage.get("puppy.draft.s:0:50"));
editing.view.saveDraft();
editing.view.receiveDraft({type:"draft",text:"old composer",revision:1,updated_at:15,
  client_id:"device-a",client_seq:1});
editing.view.receiveDraft({type:"draft",text:"queued replacement",revision:2,
  updated_at:16,client_id:"",client_seq:0});
const beforeEditComplete={text:editing.view.ta.value,sent:editing.sent.slice(),
  readOnly:editing.view.ta.readOnly,deferred:editing.view.draftDeferred.text};
editing.view.queueEditComplete({type:"queue_edit_complete",request_id:
  editing.sent[1].request_id,ok:true,started:false,
  draft:{type:"draft",text:"queued replacement",revision:2,updated_at:16,
    client_id:"",client_seq:0}});

console.log(JSON.stringify({sent:first.sent,pendingJournal,journalCleared,followed,
  whilePending,afterAck,latest:first.view.ta.value,sharedAttachment,uploadPreserved,
  localOnly:{text:localOnly.view.ta.value,sent:localOnly.sent,
             caret:localOnly.view.ta.selectionStart},invalidJournal,
  stale:{text:stale.view.ta.value,sent:stale.sent,
         journal:storage.has("puppy.draft.s:0:44"),
         caret:stale.view.ta.selectionStart},
  unacked:{text:unacked.view.ta.value,sent:unacked.sent,
           caret:unacked.view.ta.selectionStart},
  offline:{before:offlineBefore,text:offline.view.ta.value,sent:offline.sent},
  burst:{beforeAck:burstBeforeAck,afterFirstAck:burstAfterFirstAck,
         journal:storage.has("puppy.draft.s:0:48")},
  submission:{sent:submissionSent,text:submission.view.ta.value,
              journal:storage.has("puppy.draft.s:0:49")},
  editing:{before:beforeEditComplete,journalDuringEdit,
           text:editing.view.ta.value,caret:editing.view.ta.selectionStart,
           readOnly:editing.view.ta.readOnly,focused:editing.view.ta.focused,
           pending:editing.view.queueEditPending,uploadAborted,
           revoked:URL.revoked.includes("blob:editing"),
           attachments:editing.view.attachments.length,
           journal:storage.has("puppy.draft.s:0:50")}}));
""" % (draft_helpers, attachment_helpers, session_view)
    # The embedded SessionView class exceeds the kernel's single-argument cap
    # (MAX_ARG_STRLEN, 128 KiB), so this script rides stdin rather than -e.
    proc = subprocess.run(["node"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["sent"] == [
        {"type": "draft", "text": "Test", "client_id": "device-a", "client_seq": 1},
        {"type": "draft", "text": "local winner", "client_id": "device-a",
         "client_seq": 2},
    ], result
    assert result["pendingJournal"] == {
        "_puppy_draft": 1, "text": "Test", "base_revision": 0,
        "submitted": False}, result
    assert result["journalCleared"] and result["followed"] == "from device b", result
    assert result["whilePending"] == {
        "text": "local winner", "deferred": "peer in flight"}, result
    assert result["afterAck"] == {
        "text": "local winner", "deferred": None, "journal": False}, result
    assert result["latest"] == "peer prose", result
    assert result["sharedAttachment"] == {
        "count": 1, "path": "/private/uploads/42/1700000000000-abcdef0123/photo.png",
        "prose": ""}, result
    assert result["uploadPreserved"] == {
        "count": 1, "uploading": True, "text": "peer prose"}, result
    assert result["localOnly"]["text"] == "local-only text", result
    assert result["localOnly"]["sent"] == [] and result["invalidJournal"] is None, result
    assert result["localOnly"]["caret"] == len("local-only text"), result
    assert result["stale"] == {
        "text": "newer server text", "sent": [], "journal": False,
        "caret": len("newer server text")}, result
    assert result["unacked"]["text"] == "unacknowledged edit", result
    assert result["unacked"]["caret"] == len("unacknowledged edit"), result
    assert result["unacked"]["sent"][0]["text"] == "unacknowledged edit", result
    assert result["offline"]["before"] == {"ready": False, "touched": True}, result
    assert result["offline"]["text"] == "typed while disconnected", result
    assert result["offline"]["sent"][0]["text"] == "typed while disconnected", result
    assert result["burst"] == {
        "beforeAck": [
            {"type": "draft", "text": "a", "client_id": "device-a",
             "client_seq": 1},
        ],
        "afterFirstAck": [
            {"type": "draft", "text": "a", "client_id": "device-a",
             "client_seq": 1},
            {"type": "draft", "text": "latest", "client_id": "device-a",
             "client_seq": 2},
        ],
        "journal": False,
    }, result
    assert result["submission"] == {
        "sent": [
            {"type": "draft", "text": "first", "client_id": "device-a",
             "client_seq": 1},
            {"type": "draft", "text": "send this exact value",
             "client_id": "device-a", "client_seq": 2},
            {"type": "message", "text": "send this exact value",
             "draft": "send this exact value", "draft_client_id": "device-a",
             "draft_client_seq": 3},
        ],
        "text": "",
        "journal": False,
    }, result
    assert result["editing"] == {
        "before": {
            "text": "newest old composer",
            "sent": [
                {"type": "draft", "text": "old composer",
                 "client_id": "device-a", "client_seq": 1},
                {"type": "edit_queue", "request_id": result["editing"]["before"]["sent"][1]["request_id"],
                 "index": 1, "text": "queued replacement"},
            ],
            "readOnly": True,
            "deferred": "queued replacement",
        },
        "journalDuringEdit": {
            "_puppy_draft": 1, "text": "queued replacement",
            "base_revision": 0, "submitted": False,
        },
        "text": "queued replacement", "caret": len("queued replacement"),
        "readOnly": False, "focused": True, "pending": None,
        "uploadAborted": True, "revoked": True, "attachments": 0,
        "journal": False,
    }, result


def check_transcript_batching(ui_source: str) -> None:
    """Stream chunks paint once per frame and snapshots append as one batch."""
    snapshot = ui_source[
        ui_source.index('      case "snapshot":'):
        ui_source.index('      case "draft":')]
    assert "const transcript = document.createDocumentFragment();" in snapshot
    assert "this.inner.appendChild(transcript);" in snapshot
    assert "this.renderEvent(ev, false)" not in snapshot

    def method(name):
        start = ui_source.index("\n  " + name + "(") + 1
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced SessionView." + name)

    script = r"""
let nextFrame=1;
const frames=new Map();
const cancelled=[];
const requestAnimationFrame=fn=>{const id=nextFrame++;frames.set(id,fn);return id;};
const cancelAnimationFrame=id=>{cancelled.push(id);frames.delete(id);};
class FakeNode {
  constructor(className="") { this.className=className;this.children=[];
    this.parentNode=null;this.removed=false; }
  appendChild(node) { node.parentNode=this;this.children.push(node);return node; }
  querySelector(selector) {
    const cls=selector.slice(1);
    for (const child of this.children) {
      if ((child.className || "").split(/\s+/).includes(cls)) return child;
      if (child.querySelector) { const found=child.querySelector(selector);if(found)return found; }
    }
    return null;
  }
  remove() { this.removed=true;if(!this.parentNode)return;
    this.parentNode.children=this.parentNode.children.filter(n=>n!==this);
    this.parentNode=null; }
}
const el=(tag,className)=>new FakeNode(className || "");
const document={createTextNode(value){return {data:value,parentNode:null,writes:0,
  appendData(text){this.data+=text;this.writes++;}};}};
const proto={
%s
};
const inner=new FakeNode("inner");
let scrolls=0;
const view=Object.assign(Object.create(proto),{
  liveEl:null,liveKind:null,liveTextNode:null,livePendingText:"",liveFrame:null,
  inner,statusText:"",syncLiveStatus(){},atBottom(){return true;},
  scrollBottom(){scrolls++;},
});
view.appendLive("text","one");
view.appendLive("text"," two");
view.appendLive("text"," three");
const textNode=view.liveTextNode;
const before={queued:frames.size,text:textNode.data,writes:textNode.writes,
  bubbles:inner.children.length};
const first=[...frames.entries()][0];frames.delete(first[0]);first[1]();
const after={queued:frames.size,text:textNode.data,writes:textNode.writes,scrolls};
view.appendLive("text"," four");
view.appendLive("text"," five");
const second=[...frames.entries()][0];frames.delete(second[0]);second[1]();
const secondPaint={text:textNode.data,writes:textNode.writes,scrolls};
view.appendLive("text"," discarded");
view.clearLive();
const cleared={queued:frames.size,text:textNode.data,writes:textNode.writes,
  bubbles:inner.children.length,cancelled:cancelled.length,live:view.liveEl};
console.log(JSON.stringify({before,after,secondPaint,cleared}));
""" % ",\n".join(method(name) for name in (
        "appendLive", "flushLive", "clearLive"))
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result == {
        "before": {"queued": 1, "text": "", "writes": 0, "bubbles": 1},
        "after": {"queued": 0, "text": "one two three", "writes": 1,
                  "scrolls": 1},
        "secondPaint": {"text": "one two three four five", "writes": 2,
                        "scrolls": 2},
        "cleared": {"queued": 0, "text": "one two three four five",
                    "writes": 2, "bubbles": 0, "cancelled": 1, "live": None},
    }, result


def check_browser_handoff_ui(ui_source: str, css_source: str) -> None:
    """The identified browser owns one session-link pill and its picker menu.

    Linking, moving and unlinking all happen from the browser's own tab: the
    pill names the linked session (dot + name) and opens a session picker, so
    nothing depends on which chat happens to be selected elsewhere."""
    start = ui_source.index("class BrowserView {")
    end = ui_source.index("/* ================= SettingsView", start)
    view = ui_source[start:end]
    assert view.index('class="br-bar"') < view.index('class="br-meta"') < \
        view.index('class="br-stage"')
    assert 'aria-label="Copy Browser ID"' in view
    assert ' title=' not in view and ".title =" not in view and "data-tip" not in view
    # one pill, four state pieces: glyph, session dot, text, picker chevron
    assert 'aria-haspopup="menu"' in view
    assert 'class="sess-dot br-owner-dot hidden"' in view
    assert 'class="br-owner-glyph"' in view and 'class="br-owner-arrow hidden"' in view
    assert '"Link to a session…"' in view
    assert '"Session linking requires an updated backend"' in view
    assert '"Browser closed"' in view and '"Checking session link…"' in view
    # The picker is only for choosing or unlinking a session; selecting a row
    # moves the binding in one call and there is no separate open-session verb.
    assert "showLinkMenu(this.ownerBtn)" in view
    assert 'const scroll = el("div", "br-link-scroll")' in view
    assert "menu.appendChild(scroll)" in view
    assert "scroll.appendChild(row)" in view
    assert 'add(`Open ${owner.name' not in view
    assert "openSessionTab(bid, ownerId" not in view
    assert '"menuitemradio"' in view and "sessDot(s)" in view
    # pick-one rows use the plain choice check, not the settings checkbox
    assert 'choiceSvg("check")' in view and '"br-link-mark"' in view
    assert '"Unlink browser"' in view and '"No sessions on this backend"' in view
    assert "sessionsFor(bid).filter(s => !s.archived || s.id === ownerId)" in view
    assert "positionAnchoredMenu(menu, anchor)" in view
    assert "this.applyBinding(d);" in view
    assert "method: \"POST\", body: { session_id: sessionId }" in view
    assert 'method: "DELETE", timeoutMs: 15000' in view
    # the old selection-dependent handoff is gone everywhere
    assert "Use with current session" not in ui_source
    assert "currentBrowserSession" not in ui_source
    assert ".br-meta{" in css_source and ".br-ident{" in css_source
    # Every inter-row gap is six pixels: four below the address bar plus two on
    # the metadata strip, four below that strip plus two on the typing row, and
    # four below the typing row plus the stage's two-pixel top margin.
    assert "padding:2px 8px 4px;color:var(--txt2)" in css_source
    assert ".br-type{display:flex;align-items:center;gap:6px;flex:0 0 auto;" \
        "padding:2px 8px 4px}" in css_source
    assert ".br-stage{\n  flex:1;min-height:0;margin:2px 8px 8px;" in css_source
    assert ".br-copy-id{width:27px;height:27px;" in css_source
    assert ".br-owner-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" \
        in css_source
    # pill twin of .br-ident; the strip stays one row on every viewport
    assert "height:28px;min-width:0;max-width:440px;" in css_source
    assert '.br-owner[aria-expanded="true"] .br-owner-arrow{transform:rotate(180deg)}' \
        in css_source
    assert ".br-link-menu{" in css_source and "max-height:min(340px," in css_source
    assert "max-width:min(340px," in css_source and ".br-link-mark{" in css_source
    # The menu frame must not also be the scrollport: native scrollbar thumbs
    # otherwise paint over its top/bottom border at their travel limits.
    assert "display:flex;flex-direction:column;overflow:hidden;" in css_source
    assert ".br-link-scroll{min-height:0;overflow-y:auto;overscroll-behavior:contain}" \
        in css_source
    assert ".br-handoff{" not in css_source and ".br-unlink{" not in css_source
    assert ".br-use{" not in css_source
    assert "@media(max-width:560px)" not in css_source
    # The live frame fills the stage, so its permanent outline and stronger
    # focus indication must be an overlay above both image and dead-state UI.
    assert ".br-stage::after{" in css_source
    assert 'content:"";position:absolute;inset:0;z-index:21;pointer-events:none;' \
        in css_source
    assert "border-radius:inherit;box-shadow:inset 0 0 0 1px var(--browser-frame);" \
        in css_source
    assert ".br-stage:focus-visible::after{" in css_source
    assert "box-shadow:inset 0 0 0 2px var(--focus-stage-ring);" in css_source
    assert "--focus-stage-ring:#284669;" in css_source
    assert "--browser-frame:#2a2b2d;" in css_source
    assert "--focus-stage-ring:#a9c7ef;" in css_source
    assert "--browser-frame:#cacdd4;" in css_source
    assert ".br-stage:focus-visible{box-shadow:" not in css_source


def check_desktop_side_drag(ui_source: str) -> None:
    """Run the real desktop sidebar drag against a pointer-event DOM.

    The hidden edge must expose a paused intermediate width, settle slowly by
    position or quickly by velocity, while the visible grip keeps its ordinary
    resize range and changes into a reversible collapse below 200px.
    """
    marker = ui_source.index("/* Desktop sidebar drag.")
    start = ui_source.index("(() => {", marker)
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = ui_source.index(")();", index) + 4
                break
    assert end is not None, "unbalanced desktop sidebar gesture"
    gesture = ui_source[start:end]
    script = r"""
class Classes {
  constructor(...names) { this.names = new Set(names); }
  add(...names) { for (const name of names) this.names.add(name); }
  remove(...names) { for (const name of names) this.names.delete(name); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    if (force === undefined) force = !this.names.has(name);
    if (force) this.names.add(name); else this.names.delete(name);
    return force;
  }
}
class Style {
  constructor() { this.values = {}; this.width = ""; this.opacity = ""; this.visibility = ""; }
  setProperty(name, value) { this.values[name] = value; }
  removeProperty(name) { delete this.values[name]; this[name] = ""; }
  value(name) { return this.values[name] || ""; }
}
class Target {
  constructor(id) { this.id = id; this.listeners = {}; this.classList = new Classes();
    this.style = new Style(); }
  addEventListener(kind, fn) { (this.listeners[kind] ||= []).push(fn); }
  emit(kind, values={}) {
    const event = Object.assign({pointerId:1, pointerType:"mouse", button:0,
      isPrimary:true, clientX:0, clientY:0, timeStamp:0, prevented:false,
      preventDefault() { this.prevented = true; }}, values);
    for (const fn of this.listeners[kind] || []) fn(event);
    return event;
  }
  setPointerCapture() {} releasePointerCapture() {}
  getBoundingClientRect() {
    return {width:parseFloat(document.documentElement.style.value("--side-w")) || 0};
  }
  get offsetWidth() { return this.getBoundingClientRect().width; }
}
const nodes = {app:new Target("app"), side:new Target("side"),
  "side-resize":new Target("side-resize"), "drawer-edge":new Target("drawer-edge")};
const $ = id => nodes[id];
const document = {documentElement:new Target("root")};
const storage = {"puppy.sidecollapsed":"1", "puppy.sidew":"256"};
const lsGet = key => storage[key] || null;
const lsSet = (key, value) => { storage[key] = value; };
const lsDel = key => { delete storage[key]; };
function savedSideWidth() {
  const saved = parseInt(lsGet("puppy.sidew") || "", 10);
  return saved ? Math.min(480, Math.max(200, saved)) : 256;
}
function setSideCollapsed(on, animate=true) {
  const open = savedSideWidth();
  nodes.app.classList.toggle("side-collapsed", !!on);
  if (animate) nodes.app.classList.add("side-animating");
  document.documentElement.style.setProperty("--side-w-open", open + "px");
  document.documentElement.style.setProperty("--side-w", on ? "0px" : open + "px");
  lsSet("puppy.sidecollapsed", on ? "1" : "");
}
const window = {
  matchMedia:query => ({matches:query.includes("min-width")}),
  dispatchEvent() {},
};
class Event { constructor(type) { this.type = type; } }
let frame = null;
const requestAnimationFrame = fn => { frame = fn; return 1; };
const cancelAnimationFrame = () => { frame = null; };
const runFrame = () => {
  const fn = frame; frame = null; if (fn) fn();
  nodes.app.classList.remove("side-animating");
};
%s
const app=nodes.app, side=nodes.side, edge=nodes["drawer-edge"], grip=nodes["side-resize"];
const rootStyle=document.documentElement.style;

edge.emit("pointerdown", {clientX:4,timeStamp:0});
edge.emit("pointermove", {clientX:90,timeStamp:200});
const revealHeld={width:rootStyle.value("--side-w"),opacity:side.style.opacity,
                  dragging:app.classList.contains("side-dragging")};
const revealPaused={width:rootStyle.value("--side-w"),opacity:side.style.opacity};
edge.emit("pointerup", {clientX:90,timeStamp:400}); runFrame();
const minorityClosed=app.classList.contains("side-collapsed") &&
  rootStyle.value("--side-w")==="0px" && side.style.width==="";

edge.emit("pointerdown", {clientX:4,timeStamp:500});
edge.emit("pointermove", {clientX:190,timeStamp:800});
edge.emit("pointerup", {clientX:190,timeStamp:1000}); runFrame();
const majorityOpen=!app.classList.contains("side-collapsed") &&
  rootStyle.value("--side-w")==="256px";

grip.emit("pointerdown", {clientX:256,timeStamp:1100});
grip.emit("pointermove", {clientX:330,timeStamp:1300});
grip.emit("pointerup", {clientX:330,timeStamp:1500});
const resized=rootStyle.value("--side-w")==="330px" && storage["puppy.sidew"]==="330";

grip.emit("pointerdown", {clientX:330,timeStamp:1600});
grip.emit("pointermove", {clientX:80,timeStamp:1900});
const collapseHeld={width:rootStyle.value("--side-w"),opacity:side.style.opacity,
                    clipping:app.classList.contains("side-drag-collapsing")};
grip.emit("pointerup", {clientX:80,timeStamp:2100}); runFrame();
const positionClosed=app.classList.contains("side-collapsed") &&
  rootStyle.value("--side-w")==="0px";

edge.emit("pointerdown", {clientX:4,timeStamp:2200});
edge.emit("pointermove", {clientX:200,timeStamp:2500});
edge.emit("pointerup", {clientX:200,timeStamp:2700}); runFrame();
grip.emit("pointerdown", {clientX:330,timeStamp:2800});
grip.emit("pointermove", {clientX:150,timeStamp:3100});
grip.emit("pointerup", {clientX:150,timeStamp:3300});
const laneRestoresOpen=!app.classList.contains("side-collapsed") &&
  side.style.width==="150px";
runFrame();
const restoredWidth=rootStyle.value("--side-w")==="330px" && side.style.width==="";

grip.emit("pointerdown", {clientX:330,timeStamp:3400});
grip.emit("pointermove", {clientX:50,timeStamp:3700});
grip.emit("pointerup", {clientX:50,timeStamp:3900}); runFrame();
edge.emit("pointerdown", {clientX:4,timeStamp:4000});
edge.emit("pointermove", {clientX:35,timeStamp:4020});
edge.emit("pointerup", {clientX:40,timeStamp:4025}); runFrame();
const flickOpen=!app.classList.contains("side-collapsed") &&
  rootStyle.value("--side-w")==="330px";

grip.emit("pointerdown", {clientX:330,timeStamp:4100});
grip.emit("pointermove", {clientX:70,timeStamp:4200});
grip.emit("pointercancel", {clientX:70,timeStamp:4210}); runFrame();
const cancelRestored=!app.classList.contains("side-collapsed") &&
  rootStyle.value("--side-w")==="330px";
grip.emit("dblclick");
const doubleClickReset=rootStyle.value("--side-w")==="256px" &&
  storage["puppy.sidew"]===undefined;

console.log(JSON.stringify({revealHeld,revealPaused,minorityClosed,majorityOpen,resized,
  collapseHeld,positionClosed,laneRestoresOpen,restoredWidth,flickOpen,cancelRestored,
  doubleClickReset}));
""" % gesture
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout.strip())
    assert result["revealHeld"] == {
        "width": "90px", "opacity": str(90 / 256), "dragging": True}, result
    assert result["revealPaused"] == {
        "width": "90px", "opacity": str(90 / 256)}, result
    assert result["minorityClosed"] and result["majorityOpen"] and result["resized"], result
    assert result["collapseHeld"] == {
        "width": "80px", "opacity": "0.4", "clipping": True}, result
    assert result["positionClosed"] and result["laneRestoresOpen"], result
    assert result["restoredWidth"] and result["flickOpen"] and result["cancelRestored"], result
    assert result["doubleClickReset"], result


def check_user_message_copy(ui_source: str) -> None:
    """Exercise the real user-message copy control and its success reset."""
    start = ui_source.index("\nfunction userMessageCopyButton(") + 1
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None, "unbalanced user-message copy helper"
    helper = ui_source[start:end]
    script = r"""
class Classes {
  constructor() { this.names = new Set(); }
  add(name) { this.names.add(name); }
  remove(name) { this.names.delete(name); }
  contains(name) { return this.names.has(name); }
}
class Button {
  constructor(className) {
    this.className=className; this.classList=new Classes(); this.attributes={};
    this.child=null; this.isConnected=true; this.type="";
  }
  setAttribute(name,value) { this.attributes[name]=value; }
  appendChild(child) { this.child=child; }
  replaceChildren(child) { this.child=child; }
}
const el = (tag,className) => new Button(className);
const copyIcon = (done=false) => ({done});
let copied=null, timer=null, toasts=[];
async function writeClipboardText(text) { copied=text; }
function toast(text,level) { toasts.push({text,level}); }
function setTimeout(fn,delay) { timer={fn,delay}; return 7; }
function clearTimeout() { timer=null; }
%s
(async()=>{
  const exact="first line\nsecond line  ";
  const button=userMessageCopyButton(exact);
  const event={prevented:false,stopped:false,
    preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};
  await button.onclick(event);
  const done={copied,event,type:button.type,className:button.className,
    label:button.attributes["aria-label"],checked:button.child.done,
    active:button.classList.contains("done"),delay:timer&&timer.delay};
  timer.fn();
  const reset={label:button.attributes["aria-label"],checked:button.child.done,
    active:button.classList.contains("done")};
  console.log(JSON.stringify({done,reset,toasts}));
})().catch(error=>{console.error(error);process.exit(1);});
""" % helper
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["done"] == {
        "copied": "first line\nsecond line  ",
        "event": {"prevented": True, "stopped": True},
        "type": "button", "className": "user-copy", "label": "Copied",
        "checked": True, "active": True, "delay": 1400,
    }, result
    assert result["reset"] == {
        "label": "Copy message", "checked": False, "active": False}, result
    assert result["toasts"] == [], result


def check_queue_controls_ui(ui_source: str, css_source: str) -> None:
    """Edit, pause/play, and guarded dragging belong only to queued prompts."""
    start = ui_source.index("\n  renderQueue(q, held, paused, revision)")
    end = ui_source.index("\n  unqueue(index, text)", start)
    render = ui_source[start:end]
    held_start = render.index("held.forEach")
    queued_start = render.index("shown.forEach")
    assert "q-pause" not in render[held_start:queued_start]
    assert "q-edit" not in render[held_start:queued_start]
    assert 'backendSupportsQueueEdit(this.tab.bid)' in render
    assert 'el("button", "q-edit")' in render
    assert 'queueEditIcon(12)' in render
    assert '"Edit this queued message"' in render
    assert 'this.editQueued(i, ident)' in render
    assert 'if (!cfg && backendSupportsQueuePause(this.tab.bid))' in render
    edit_at = render.index('el("button", "q-edit")', queued_start)
    pause_at = render.index('el("button", "q-pause")', queued_start)
    cancel_at = render.index('el("button", "q-x")', queued_start)
    assert edit_at < pause_at < cancel_at
    assert 'queuePauseIcon(isPaused, 12)' in render
    assert 'isPaused ? "Resume this queued message" : "Pause this queued message"' in render
    assert 'this.setQueuePaused(i, ident, !isPaused)' in render
    assert 'type: "set_queue_paused", index, text, paused' in ui_source
    assert 'type: "edit_queue", request_id: requestId, index, text' in ui_source
    assert 'case "queue_edit_complete"' in ui_source
    assert "this.queueEditComplete(d);" in ui_source
    assert 'backend.capabilities.includes("queue-pause")' in ui_source
    assert 'backend.capabilities.includes("queue-edit")' in ui_source
    assert 'backend.capabilities.includes("queue-reorder")' in ui_source
    assert 'type: "begin_queue_reorder", request_id: requestId' in ui_source
    assert 'type: "reorder_queue", request_id: context.requestId' in ui_source
    assert "!context.ready" in ui_source
    assert 'moveDragSlot(container, this.queueDrag.item, ".q-live"' in ui_source
    assert "if (this.queueEditPending) {" in ui_source
    assert "this.applySharedDraft(draft.text, true, true);" in ui_source
    assert 'this.cancelQueueEdit("Connection lost before the queued message could be edited")' \
        in ui_source
    assert '.queue-strip .q-edit{' in css_source
    assert '.queue-strip .q-edit::after{' in css_source
    assert '.queue-strip .q-pause{' in css_source
    assert 'display:inline-grid;place-items:center;' in css_source[
        css_source.index('.queue-strip .q-pause{'):
        css_source.index('.queue-strip .q-pause::after')]
    assert 'width:16px;height:16px;' in css_source
    assert '.queue-strip.editing .q-live button:disabled{' in css_source
    assert '.queue-strip .q-item.q-paused .q-t{opacity:.58}' in css_source
    assert '.queue-strip .q-item.q-sortable{cursor:grab;user-select:none}' in css_source
    assert '.queue-strip .q-live-list.reordering .q-live{will-change:transform}' in css_source


def check_active_turn_steering_ui(ui_source: str, css_source: str) -> None:
    """Running composers expose responsive steer/queue/stop actions."""
    steer_markup = ('<button class="btn-steer hidden" type="button" '
                    'aria-label="Steer the active turn">')
    queue_markup = ('<button class="btn-queue hidden" type="button" '
                    'aria-label="Queue for the next turn">')
    send_markup = '<button class="btn-send" type="button">Send</button>'
    assert steer_markup in ui_source and queue_markup in ui_source and send_markup in ui_source
    assert ui_source.index(steer_markup) < ui_source.index(queue_markup) < \
        ui_source.index(send_markup)
    assert "this.steerBtn.onclick = () => this.steer();" in ui_source
    assert "this.queueBtn.onclick = () => this.submit();" in ui_source
    assert 'if (e.key === "Enter" && !e.shiftKey && !e.isComposing) ' \
        '{ e.preventDefault(); this.submit(); return; }' in ui_source
    assert 'backend.capabilities.includes("active-turn-steering")' in ui_source
    assert 'case "steering_state":' in ui_source
    assert 'case "steer_status":' in ui_source
    assert 'api(this.tab.bid, `sessions/${this.tab.sid}/steer`' in ui_source
    assert "request_id: request.requestId" in ui_source
    assert "expected_turn_id: request.turnId" in ui_source
    assert "this.steerBtn.classList.toggle(\"hidden\", !(running && supported));" \
        in ui_source
    assert "hasAttachments" in ui_source and \
        "Steering accepts text only; use Queue for attachments" in ui_source

    # Stop keeps its accessible name while its visible face is only one drawn
    # square. Width, height, padding, and the icon's zero margin make both
    # centres geometric rather than font-dependent.
    assert "'<span class=\"stop-sq\" aria-hidden=\"true\"></span>'" in ui_source
    assert 'this.sendBtn.setAttribute("aria-label", running ? "Stop" : "Send")' \
        in ui_source
    assert '>Stop</button>' not in ui_source
    assert ".btn-send.stop{" in css_source
    assert "width:32px;min-width:32px;padding:0;gap:0;" in css_source
    assert (".btn-send.stop .stop-sq{display:block;width:10px;height:10px;" +
            "background:currentColor;flex:0 0 auto;margin:0}") in css_source

    # Steer is the green member of the existing composer button family. Wide
    # layouts retain words; narrow layouts retain all three actions as equal
    # square buttons, using the supplied stack-plus and branching-arrow ideas.
    assert ".btn-send,.btn-queue,.btn-steer{" in css_source
    assert ".btn-steer{" in css_source
    assert "background-color:var(--ok-lo);" in css_source
    assert ".btn-send:disabled,.btn-queue:disabled,.btn-steer:disabled{" in css_source
    assert ".btn-send.stop{width:34px;min-width:34px}" in css_source
    assert 'class="composer-action-label">Steer</span>' in ui_source
    assert 'class="composer-action-label">Queue</span>' in ui_source
    assert 'class="composer-action-icon" aria-hidden="true"' in ui_source
    assert "function filledReferenceIcon(size, paths, strokeWidth = 0)" in ui_source
    assert 'svg.setAttribute("viewBox", "0 0 800 800")' in ui_source
    assert 'group.setAttribute("fill", "currentColor")' in ui_source
    assert "function queueActionIcon(size = 18)" in ui_source
    assert "function steerActionIcon(size = 18)" in ui_source
    assert "M2945 7323 c-299 -35" in ui_source
    assert "M2029 6985 c-494 -60" in ui_source
    assert "M3500 2000 l0 -1000" in ui_source
    queue_icon_source = ui_source[ui_source.index("function queueActionIcon("):
                                  ui_source.index("function steerActionIcon(")]
    steer_icon_source = ui_source[ui_source.index("function steerActionIcon("):
                                  ui_source.index("function folderIcon(")]
    assert "], 140);" not in queue_icon_source
    assert "], 140);" in steer_icon_source
    assert ('this.steerBtn.querySelector(".composer-action-icon").' +
            'appendChild(steerActionIcon());') in ui_source
    assert ('this.queueBtn.querySelector(".composer-action-icon").' +
            'appendChild(queueActionIcon());') in ui_source
    assert ".composer-action-icon{display:none;align-items:center;justify-content:center}" \
        in css_source
    assert ".composer-action-icon svg{display:block}" in css_source
    assert ("display:inline-flex;width:34px;min-width:34px;height:34px;" +
            "padding:0;gap:0;") in css_source
    assert (".btn-queue .composer-action-label,.btn-steer " +
            ".composer-action-label{display:none}") in css_source
    assert (".btn-queue .composer-action-icon,.btn-steer " +
            ".composer-action-icon{display:flex}") in css_source

    start = ui_source.index("\n  async steer()") + 1
    brace = ui_source.index("{", start)
    depth = 0
    steer_method = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                steer_method = ui_source[start:index + 1]
                break
    assert steer_method is not None, "unbalanced SessionView.steer"
    script = r"""
let calls=[],toasts=[],draftSaves=0,controlSyncs=0;
const newDraftClientId=()=>"request-1";
const toast=(...args)=>toasts.push(args);
const api=async (bid,path,options)=>{
  calls.push({bid,path,options});
  return {ok:true,status:"sent",request_id:options.body.request_id};
};
const proto={%s};
const view=Object.assign(Object.create(proto),{
  tab:{bid:7,sid:42},ta:{value:"  updated direction  "},attachments:[],
  draftSupported:true,draftReady:true,steering:{supported:true,ready:true,turn_id:"turn-9"},
  steerPending:null,_forceScroll:false,histIdx:3,histDraft:"old",
  updateSteerControl(){controlSyncs++;},resizeComposer(){},
  releaseHistoryAttachments(){},saveDraft(){draftSaves++;},scrollBottom(){},
});
await view.steer();
const sent={calls,toasts,draftSaves,controlSyncs,text:view.ta.value,
  pending:view.steerPending,forceScroll:view._forceScroll};
view.ta.value="later";
view.steering={supported:true,ready:false,turn_id:""};
await view.steer();
console.log(JSON.stringify({sent,afterNotReady:{calls:calls.length,toasts}}));
""" % steer_method
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout)
    assert result["sent"]["calls"] == [{
        "bid": 7,
        "path": "sessions/42/steer",
        "options": {
            "method": "POST",
            "body": {
                "text": "updated direction",
                "request_id": "steer-request-1",
                "expected_turn_id": "turn-9",
            },
            "timeoutMs": 15000,
        },
    }], result
    assert result["sent"]["text"] == "" and result["sent"]["pending"] is None
    assert result["sent"]["draftSaves"] == 1 and result["sent"]["forceScroll"] is True
    assert result["afterNotReady"]["calls"] == 1
    assert result["afterNotReady"]["toasts"][-1][0] == \
        "The active turn is not ready for steering"


def check_system_prompt_settings(ui_source: str, css_source: str) -> None:
    """The prompt editor stays backend-aware and Engine updates shares its card."""
    assert ui_source.count("<h2>Engine updates</h2>") == 1
    section = ui_source.index('const autoSection = el("section", "engine-updates-section")')
    attach = ui_source.index("c2.appendChild(autoSection)", section)
    card = ui_source.index("this.inner.appendChild(c2)", attach)
    assert section < attach < card
    assert 'const autoCard = el("div", "card")' not in ui_source
    assert 'api(0, "system-prompt")' in ui_source
    assert 'backend.capabilities.includes("system-prompt")' in ui_source
    assert '"Remote workspace guidance"' in ui_source
    assert '"Browser guidance"' in ui_source
    assert '"Terminal guidance"' in ui_source
    assert '"Spawned agent guidance"' in ui_source
    assert '"Puppy browser guidance"' not in ui_source
    assert '"Reset to default"' in ui_source
    remote_copy = ("Sent only when this backend runs a model against a project "
                   "stored on another backend.")
    assert remote_copy in ui_source
    guidance_copy = ("Sent to every model turn on backends where Browser is enabled; "
                     "it is not sent on backends where Browser is off.")
    assert guidance_copy in ui_source.replace('" +\n      "', "")
    terminal_copy = ("Sent only when this backend offers shared Terminal tools; "
                     "it is not sent when Terminal is unavailable.")
    assert terminal_copy in ui_source.replace('" +\n      "', "")
    spawn_copy = ("Sent with every model turn on this backend; it governs when "
                  "the agent may delegate one-shot spawned agents to Puppy's nodes.")
    assert spawn_copy in ui_source.replace('" +\n      "', "")
    assert "browserNote.textContent = `Sent only for turns on ${node.name}" not in ui_source
    assert 'body.remote_workspace = record.remoteWorkspaceDraft' in ui_source
    assert 'body.terminal = record.terminalDraft' in ui_source
    assert 'body.spawn = record.spawnDraft' in ui_source
    runner_source = (BASE / "puppy" / "runner.py").read_text()
    assert "system_prompt_text = system_prompts.turn_prompt(" in runner_source
    assert "remote_workspace=descriptor is not None" in runner_source
    assert ".engine-updates-section{" in css_source
    assert ".system-prompt-section+.system-prompt-section{" in css_source
    assert ".system-prompt-section-head{" in css_source
    assert ".system-prompt-textarea{" in css_source
    assert ".config-textarea{display:block;height:72px;min-height:72px;" \
           "max-height:72px;resize:none}" in css_source
    assert "system-prompt-textarea::-webkit-resizer" not in css_source
    assert 'custom.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'remoteText.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'browserText.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'terminalText.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'spawnText.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'class="config-textarea" id="be-pairing" rows="3"' in ui_source
    assert 'class="config-textarea" id="backend-edit-pairing" rows="3"' in ui_source
    assert ui_source.count('class="config-textarea"') == 2
    assert "custom.rows = 3;" in ui_source and "remoteText.rows = 3;" in ui_source and \
        "browserText.rows = 3;" in ui_source and "terminalText.rows = 3;" in ui_source and \
        "spawnText.rows = 3;" in ui_source

    start = ui_source.index("\n  systemPromptCard(") + 1
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    method = ui_source[start:end]
    script = r"""
class Classes {
  constructor(value=""){this.names=new Set(String(value).split(/\s+/).filter(Boolean));}
  add(...names){names.forEach(name=>this.names.add(name));}
  remove(...names){names.forEach(name=>this.names.delete(name));}
  toggle(name,on){if(on===undefined)on=!this.names.has(name);on?this.add(name):this.remove(name);}
  contains(name){return this.names.has(name);}
}
class MockNode {
  constructor(tag,cls="",text="") {
    this.tagName=tag.toUpperCase();this.className=cls;this.classList=new Classes(cls);
    this.textContent=text;this.children=[];this.attributes={};this.value="";
    this.disabled=false;this.isConnected=true;this.maxLength=-1;this.html="";
  }
  appendChild(child){this.children.push(child);child.parentNode=this;return child;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  removeAttribute(name){delete this.attributes[name];if(name==="maxlength")this.maxLength=-1;}
  focus(){document.activeElement=this;}
  set innerHTML(value){this.html=value;}
  get innerHTML(){return this.html;}
}
const document={activeElement:null,createElement:tag=>new MockNode(tag)};
const el=(tag,cls="",text="")=>new MockNode(tag,cls,text);
const supported=new Set([0,1,2]);
const backendSupportsSystemPrompt=bid=>supported.has(bid);
let backendAllowed=true;
const backendConnectionAllowed=bid=>!bid||backendAllowed;
const remoteAvailability=bid=>!bid||backendAllowed?"ok":"bad";
const state={remoteSystemPrompts:{}};
const calls=[],toasts=[];
const payload=(custom,remote,browser,terminal,spawn)=>({custom,remote_workspace:remote,browser,terminal,
  spawn:spawn===undefined?"SPAWN DEFAULT":spawn,
  remote_workspace_default:"REMOTE DEFAULT",browser_default:"DEFAULT",
  terminal_default:"TERMINAL DEFAULT",spawn_default:"SPAWN DEFAULT",max_chars:100});
const legacyPayload=(custom,browser)=>({custom,browser,browser_default:"DEFAULT",max_chars:100});
async function api(bid,path,options={}) {
  calls.push({bid,path,method:options.method||"GET",body:options.body||null});
  if(options.method==="PATCH") {
    if(bid===2) return {system_prompt:legacyPayload(options.body.custom,options.body.browser)};
    return {system_prompt:payload(options.body.custom,options.body.remote_workspace,
      options.body.browser,options.body.terminal,options.body.spawn)};
  }
  if(bid===2) return {system_prompt:legacyPayload("LEGACY","LEGACY BROWSER")};
  return {system_prompt:payload("REMOTE","REMOTE WORKSPACE","REMOTE BROWSER","REMOTE TERMINAL",
    "REMOTE SPAWN")};
}
const toast=(...args)=>toasts.push(args);
class TestView {
  constructor(){this.renderGeneration=1;this.systemPromptBid=0;}
__METHOD__
}
const view=new TestView();
const card=view.systemPromptCard([
  {bid:0,name:"Primary"},{bid:1,name:"Laptop"},{bid:2,name:"Legacy prompts"},
  {bid:3,name:"Old backend"}
],payload("LOCAL","REMOTE DEFAULT","DEFAULT","TERMINAL DEFAULT","SPAWN DEFAULT"),1);
const nodeField=card.children[0],select=nodeField.children[1];
const custom=card.children[1].children[1];
const remoteSection=card.children[2],remoteWorkspace=remoteSection.children[1];
const remoteReset=remoteSection.children[0].children[1];
const browserSection=card.children[3],browser=browserSection.children[1];
const browserReset=browserSection.children[0].children[1];
const terminalSection=card.children[4],terminal=terminalSection.children[1];
const terminalReset=terminalSection.children[0].children[1];
const spawnSection=card.children[5],spawn=spawnSection.children[1];
const spawnReset=spawnSection.children[0].children[1];
const actions=card.children[6],status=actions.children[0],save=actions.children[1];
const before={custom:custom.value,remoteWorkspace:remoteWorkspace.value,
  browser:browser.value,terminal:terminal.value,spawn:spawn.value,
  status:status.textContent,
  remoteNote:remoteSection.children[0].children[0].children[1].textContent,
  browserNote:browserSection.children[0].children[0].children[1].textContent,
  terminalNote:terminalSection.children[0].children[0].children[1].textContent,
  spawnNote:spawnSection.children[0].children[0].children[1].textContent};
custom.value="LOCAL EDIT";custom.oninput();
const dirty=status.textContent;
remoteWorkspace.value="OTHER REMOTE";remoteWorkspace.oninput();remoteReset.onclick();
browser.value="OTHER";browser.oninput();browserReset.onclick();
terminal.value="OTHER TERMINAL";terminal.oninput();terminalReset.onclick();
spawn.value="OTHER SPAWN";spawn.oninput();spawnReset.onclick();
const resetState={remoteWorkspace:remoteWorkspace.value,browser:browser.value,
  terminal:terminal.value,spawn:spawn.value,
  status:status.textContent};
await save.onclick();
const saved={status:status.textContent,toast:toasts[0][0]};
select.value="1";document.activeElement=select;select.onchange();
await new Promise(resolve=>setTimeout(resolve,0));
const remote={custom:custom.value,remoteWorkspace:remoteWorkspace.value,
  browser:browser.value,terminal:terminal.value,spawn:spawn.value,
  status:status.textContent};
backendAllowed=false;view.systemPromptSync();
const offline={custom:custom.value,remoteWorkspace:remoteWorkspace.value,
  browser:browser.value,terminal:terminal.value,spawn:spawn.value,
  status:status.textContent,
  disabled:custom.disabled&&remoteWorkspace.disabled&&browser.disabled&&terminal.disabled&&spawn.disabled&&save.disabled};
backendAllowed=true;view.systemPromptSync();
select.value="2";document.activeElement=select;select.onchange();
await new Promise(resolve=>setTimeout(resolve,0));
const legacy={custom:custom.value,browser:browser.value,
  remoteDisabled:remoteWorkspace.disabled,remotePlaceholder:remoteWorkspace.placeholder,
  terminalDisabled:terminal.disabled,terminalPlaceholder:terminal.placeholder,
  spawnDisabled:spawn.disabled,spawnPlaceholder:spawn.placeholder,
  saveDisabled:save.disabled};
custom.value="LEGACY EDIT";custom.oninput();await save.onclick();
select.value="3";document.activeElement=select;select.onchange();
const unsupported={disabled:custom.disabled&&remoteWorkspace.disabled&&browser.disabled&&terminal.disabled&&spawn.disabled&&save.disabled,
  status:status.textContent};
console.log(JSON.stringify({before,dirty,resetState,saved,remote,offline,legacy,unsupported,calls}));
""".replace("__METHOD__", method)
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["before"] == {
        "custom": "LOCAL", "remoteWorkspace": "REMOTE DEFAULT", "browser": "DEFAULT",
        "terminal": "TERMINAL DEFAULT", "spawn": "SPAWN DEFAULT",
        "status": "Up to 100 characters per field.",
        "remoteNote": remote_copy, "browserNote": guidance_copy,
        "terminalNote": terminal_copy, "spawnNote": spawn_copy,
    }, result
    assert result["dirty"] == "Unsaved changes", result
    assert result["resetState"] == {
        "remoteWorkspace": "REMOTE DEFAULT", "browser": "DEFAULT",
        "terminal": "TERMINAL DEFAULT", "spawn": "SPAWN DEFAULT",
        "status": "Unsaved changes"}, result
    assert result["saved"] == {
        "status": "Saved for new turns.", "toast": "Primary: System prompt saved"}, result
    assert result["remote"]["custom"] == "REMOTE" and \
        result["remote"]["remoteWorkspace"] == "REMOTE WORKSPACE" and \
        result["remote"]["browser"] == "REMOTE BROWSER" and \
        result["remote"]["terminal"] == "REMOTE TERMINAL" and \
        result["remote"]["spawn"] == "REMOTE SPAWN", result
    assert result["offline"] == {
        "custom": "REMOTE", "remoteWorkspace": "REMOTE WORKSPACE",
        "browser": "REMOTE BROWSER", "terminal": "REMOTE TERMINAL",
        "spawn": "REMOTE SPAWN",
        "status": "Backend unavailable · showing last known prompt settings.",
        "disabled": True,
    }, result
    assert result["legacy"] == {
        "custom": "LEGACY", "browser": "LEGACY BROWSER",
        "remoteDisabled": True,
        "remotePlaceholder": "Upgrade this backend to configure remote workspace guidance",
        "terminalDisabled": True,
        "terminalPlaceholder": "Upgrade this backend to configure Terminal guidance",
        "spawnDisabled": True,
        "spawnPlaceholder": "Upgrade this backend to configure spawned agent guidance",
        "saveDisabled": False,
    }, result
    assert result["unsupported"] == {
        "disabled": True,
        "status": "Backend upgrade required for system prompt settings."}, result
    assert [call["method"] for call in result["calls"]] == \
        ["PATCH", "GET", "GET", "PATCH"], result
    assert result["calls"][0]["body"]["remote_workspace"] == "REMOTE DEFAULT", result
    assert result["calls"][0]["body"]["terminal"] == "TERMINAL DEFAULT", result
    assert result["calls"][0]["body"]["spawn"] == "SPAWN DEFAULT", result
    assert "remote_workspace" not in result["calls"][3]["body"], result
    assert "terminal" not in result["calls"][3]["body"], result
    assert "spawn" not in result["calls"][3]["body"], result


def check_remote_workspace_picker(ui_source: str, css_source: str) -> None:
    """Storage choices exclude the executor and explain an empty backend list."""
    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    helpers = function("workspaceBackendChoices") + "\n" + \
        function("renderWorkspaceBackendOptions")
    script = r"""
const provider=new Set([1,2,3]);
const online=new Set([1,2,4]);
const state={backends:[
  {id:1,name:"Executor"},{id:2,name:"Storage"},
  {id:3,name:"Offline"},{id:4,name:"No provider"}
]};
const backendName=bid=>bid===0?"Primary":"Backend "+bid;
const backendSupportsWorkspaceProvider=bid=>provider.has(Number(bid));
const backendConnectionAllowed=bid=>online.has(Number(bid));
let refreshes=0;
const refreshChoiceSelect=()=>{refreshes++;};
const document={createElement:tag=>({tagName:tag.toUpperCase(),value:"",textContent:"",
  disabled:false,selected:false})};
class MockSelect {
  constructor(){this.children=[];this.value="";this.disabled=false;}
  set innerHTML(value){if(value!=="")throw new Error("unexpected markup");
    this.children=[];this.value="";}
  appendChild(option){this.children.push(option);return option;}
  get options(){return this.children;}
}
__HELPERS__
const remoteExecutor=new MockSelect();
let choices=renderWorkspaceBackendOptions(remoteExecutor,1);
const first={ids:choices.map(item=>item.id),values:remoteExecutor.options.map(o=>o.value),
  labels:remoteExecutor.options.map(o=>o.textContent),value:remoteExecutor.value,
  disabled:remoteExecutor.disabled};
remoteExecutor.value="2";
choices=renderWorkspaceBackendOptions(remoteExecutor,1);
const preserved={ids:choices.map(item=>item.id),value:remoteExecutor.value};
const localExecutor=new MockSelect();
choices=renderWorkspaceBackendOptions(localExecutor,0);
const local={ids:choices.map(item=>item.id),values:localExecutor.options.map(o=>o.value)};
state.backends=[{id:3,name:"Offline"},{id:4,name:"No provider"}];
const empty=new MockSelect();
choices=renderWorkspaceBackendOptions(empty,0);
const none={count:choices.length,value:empty.value,disabled:empty.disabled,
  options:empty.options.map(o=>({value:o.value,text:o.textContent,
    disabled:o.disabled,selected:o.selected}))};
console.log(JSON.stringify({first,preserved,local,none,refreshes}));
""".replace("__HELPERS__", helpers)
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["first"] == {
        "ids": [0, 2], "values": ["0", "2"],
        "labels": ["Primary", "Storage"], "value": "0", "disabled": False,
    }, result
    assert result["preserved"] == {"ids": [0, 2], "value": "2"}, result
    assert result["local"] == {"ids": [1, 2], "values": ["1", "2"]}, result
    assert result["none"] == {
        "count": 0, "value": "", "disabled": True,
        "options": [{"value": "", "text": "No other backend available",
                     "disabled": True, "selected": True}],
    }, result
    assert result["refreshes"] == 4, result

    assert "Files on backend" in ui_source
    assert "Files on another backend" in ui_source
    assert "No other backend available" in ui_source
    assert "No other backend is available for this remote workspace" in ui_source
    assert "Files on node" not in ui_source
    assert "Files on another node" not in ui_source
    assert "(same node)" not in ui_source
    assert 'aria-label="Backend the command runs on"' in ui_source
    assert '<label>Backend<select id="nb-be">' in ui_source
    assert '"No backend has its browser enabled' in ui_source
    assert ".workspace-pick .wp{padding-left:8px;padding-right:8px}" in css_source
    assert ".workspace-pick .wp-sub{" in css_source
    assert "min-height:2.5em;white-space:normal;overflow:visible;" in css_source


def check_opencode_chat_models(ui_source: str, css_source: str) -> None:
    """Discovered models stay in controls and OpenCode uses its square OC mark."""
    assert "engineModelPicker(" not in ui_source
    assert "engine-model-selection" not in ui_source
    assert "engines/${encodeURIComponent(engine.key)}/models" not in ui_source
    assert "engine.dynamic_model_options" in ui_source
    assert 'opencode: "opencode"' in ui_source
    assert 'opencode: "OC"' in ui_source
    assert 'opencode: "OpenCode"' in ui_source
    assert '.prov-opencode{' in css_source
    assert 'url("/static/vendor/opencode.svg") center/contain no-repeat' in css_source
    assert "width:17px;height:11px" in css_source
    assert "width:22px;height:18px;color:var(--opencode)" in css_source
    assert ".engine-model-config{" not in css_source

    mark_source = (BASE / "puppy" / "static" / "vendor" / "opencode.svg").read_text()
    assert 'viewBox="0 0 31 20"' in mark_source
    assert 'M0 0h15v20H0zM4 4h7v12H4zM19 0h12v4h-8v12h8v4H19z' in mark_source

    start = ui_source.index("const PROVIDERS =")
    end = ui_source.index("\nfunction provIcon(", start)
    script = ui_source[start:end] + "\nconsole.log(JSON.stringify(provSpec('opencode')));"
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "className": "prov-opencode", "text": "OC", "label": "OpenCode"}


def check_session_provider_marks(css_source: str) -> None:
    """Detailed compact marks grow without changing the session-row layout slot."""
    assert '.si-row.sub .prov-anthropic,.si-row.sub .prov-openai{' in css_source
    assert "width:13px;height:13px;margin:-1px" in css_source
    assert ".si-row.sub .prov-opencode{color:var(--txt3)}" in css_source


def check_sidebar_icon_alignment(css_source: str) -> None:
    """Text-adjacent marks may be optical; button ink stays box-centred."""
    assert ".sess-group-title{" in css_source
    assert ".si-row{display:flex;align-items:center;" in css_source
    assert ".si-row .sess-dot{margin:0 1px;position:relative;top:1px}" in css_source
    assert "font-variant-numeric:tabular-nums;\n  position:relative;top:1px;" in css_source
    assert ".foot-engine-head{display:flex;align-items:center;" in css_source
    assert ".foot-eng{display:flex;align-items:center;" in css_source
    assert ".foot-engine-head>.foot-ico{position:relative;top:-1px}" in css_source
    assert ".foot-engine-head>.disclosure-toggle svg" not in css_source
    disclosure = css_source[css_source.index(".disclosure-toggle{"):
                            css_source.index(".sess-empty{", css_source.index(
                                ".disclosure-toggle{"))]
    assert "display:inline-grid;place-items:center;" in disclosure
    assert "line-height:0" in disclosure and "top:" not in disclosure
    assert ".conn-state{display:flex;align-items:center;" in css_source
    conn_dot = css_source[css_source.index(".conn-dot{"):
                          css_source.index(".conn-dot.ok", css_source.index(".conn-dot{"))]
    assert "margin-left:6px;" in conn_dot
    assert "position:" not in conn_dot and "top:" not in conn_dot


def check_compact_control_alignment(ui_source: str, css_source: str) -> None:
    """Every icon-only hover box owns centred drawn geometry, not font ink."""
    icon_button = css_source[css_source.index(".icon-btn{"):
                             css_source.index("@media (hover:hover){.icon-btn:hover")]
    assert "display:inline-grid;place-items:center;" in icon_button
    assert "padding:0" in icon_button and "line-height:0" in icon_button
    assert ".icon-btn>svg{display:block;margin:0}" in css_source
    assert css_source.count(".burger{display:inline-grid}") == 2

    for selector in (".disclosure-toggle{", ".foot-node-act{", ".tab .t-close{",
                     ".code-copy,.user-copy{", ".queue-strip .q-x{",
                     ".queue-strip .q-edit{", ".queue-strip .q-pause{",
                     ".queue-strip .q-resend{", ".attach-chip .attach-x{",
                     ".engine-node-refresh{"):
        start = css_source.index(selector)
        rule = css_source[start:css_source.index("}", start)]
        assert "place-items:center" in rule, selector

    pause_start = ui_source.index("function queuePauseIcon(")
    pause_end = ui_source.index("\nfunction queueEditIcon", pause_start)
    pause_icon = ui_source[pause_start:pause_end]
    assert "for (const x of [4, 8])" in pause_icon
    assert '`M${x} 3V9`' in pause_icon
    assert '"M4.35 2.7 9.3 6 4.35 9.3Z"' in pause_icon

    choice_start = ui_source.index("function choiceSvg(")
    choice_end = ui_source.index("\n/* Backend sections", choice_start)
    assert '"M2.5 4.25 6 7.75 9.5 4.25"' in ui_source[
        choice_start:choice_end]
    assert "--chevron:url(" in css_source
    assert 'content:"❯"' not in css_source
    assert 'el("span", "t-caret", "❯")' not in ui_source
    assert 'caret.appendChild(choiceSvg("arrow"));' in ui_source

    assert '<button class="icon-btn menu-btn" aria-label="Session menu"></button>' \
        in ui_source
    assert 'root.querySelector(".menu-btn").appendChild(moreIcon());' in ui_source
    assert '>⋮</button>' not in ui_source
    assert "function moreIcon(size = 14)" in ui_source

    # Even SVG/button parity avoids half-pixel placement in the densest boxes.
    for call in ('refreshIcon(12)', 'queueEditIcon(12)',
                 'queuePauseIcon(isPaused, 12)', 'refreshIcon(14)',
                 'plusIcon(14)', 'xIcon(14)'):
        assert call in ui_source
    backspace = css_source[css_source.index(".br-type-bksp{"):
                           css_source.index(".br-type-bksp svg{")]
    assert "padding-top:0!important;padding-bottom:0!important" in backspace


def check_session_activity_clock(ui_source: str, css_source: str) -> None:
    """Running clocks use a compact clock and the shared prompt-status blue."""
    start = ui_source.index("function formatSessionActivity(")
    end = ui_source.index("\nfunction updateSessionActivityLabels", start)
    formatter = ui_source[start:end]
    script = formatter + r"""
const start = 100000;
console.log(JSON.stringify([0, 5, 61, 3599, 3600, 3661, 36000]
  .map(seconds => formatSessionActivity(start, start + seconds * 1000))));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == [
        "0:00", "0:05", "1:01", "59:59", "1:00:00", "1:01:01", "10:00:00",
    ]

    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "activity.style.color" not in sidebar
    assert ".si-be.active-time::before{" in css_source
    assert "display:inline-flex;align-items:center;gap:4px;" in css_source
    assert "position:relative;top:1px;color:var(--acc2);" in css_source
    assert "border-top-color:currentColor;border-radius:50%;animation:spin .8s linear infinite;" \
        in css_source


def check_sidebar_footer_buttons(ui_source: str, css_source: str) -> None:
    """Footer actions stay boxed; the aligned tab add control stays quiet."""
    assert "function themeIcon(size, moon)" in ui_source
    assert 'button.replaceChildren(themeIcon(14, t === "light"));' in ui_source
    assert 'const add = el("button", "icon-btn");' in ui_source
    assert "add.appendChild(plusIcon(14));" in ui_source
    assert ".tab-add-wrap .icon-btn,.foot-row .icon-btn{" in css_source
    assert "display:inline-grid;place-items:center;position:relative;" in css_source
    assert "width:26px;height:26px;margin:0;padding:0;" in css_source
    shared_start = css_source.index(".tab-add-wrap .icon-btn,.foot-row .icon-btn{")
    shared_end = css_source.index("}", shared_start)
    shared_rule = css_source[shared_start:shared_end]
    assert "background:var(--btn-face)" not in shared_rule
    footer_start = css_source.index(".foot-row .icon-btn{", shared_end)
    footer_rule = css_source[footer_start:css_source.index("}", footer_start)]
    assert "border-color:var(--line);" in footer_rule
    assert "background:var(--btn-face);box-shadow:" in footer_rule
    assert ".tab-add-wrap .icon-btn:hover,.foot-row .icon-btn:hover" not in css_source
    assert ".tab-add-wrap .icon-btn>svg,.foot-row .icon-btn>svg{" in css_source
    menu_start = css_source.index(".chat-head .menu-btn{")
    menu_rule = css_source[menu_start:css_source.index("}", menu_start)]
    assert "width:26px;height:26px" in menu_rule and "margin-right:-4px" in menu_rule
    # Desktop: 14px head padding minus the menu's 4px nudge equals the tabbar's
    # 10px edge. Narrow: both use 10px directly after the nudge is cancelled.
    assert ".tabbar{\n  display:flex;align-items:center;gap:4px;padding:8px 10px 0;" \
        in css_source
    assert ".chat-head{\n  display:flex;align-items:center;gap:9px;padding:9px 14px;" \
        in css_source
    assert ".chat-head{padding:8px 10px;gap:6px}" in css_source
    assert ".chat-head .menu-btn{margin-right:0}" in css_source
    assert "transform:translateY(1px)" not in css_source


def check_toast_touch_swipe(ui_source: str, css_source: str) -> None:
    """Only a deliberate rightward finger gesture dismisses an event toast."""
    start = ui_source.index("const TOAST_SWIPE_INTENT_PX = ")
    end = ui_source.index("\nfunction toast(", start)
    swipe_source = ui_source[start:end]
    script = r"""
let nextTimer=0;
const timers=new Map();
const setTimeout=(fn)=>{const id=++nextTimer;timers.set(id,fn);return id;};
const clearTimeout=id=>timers.delete(id);
const flushTimers=()=>{const due=[...timers.values()];timers.clear();due.forEach(fn=>fn());};

class ClassList {
  constructor(){this.values=new Set();}
  add(...names){names.forEach(name=>this.values.add(name));}
  remove(...names){names.forEach(name=>this.values.delete(name));}
  contains(name){return this.values.has(name);}
}
class Target {
  constructor(){
    this.listeners={};this.classList=new ClassList();this.width=300;this.captured=false;
    this.style={transform:"",opacity:"",removeProperty(name){this[name]="";}};
  }
  addEventListener(kind,fn,options={}){
    (this.listeners[kind] ||= []).push({fn,once:!!(options&&options.once)});
  }
  emit(kind,values={}){
    const event=Object.assign({pointerId:1,pointerType:"touch",isPrimary:true,
      clientX:0,clientY:0,timeStamp:0,prevented:false,
      preventDefault(){this.prevented=true;}},values);
    for(const entry of [...(this.listeners[kind]||[])]){
      entry.fn(event);
      if(entry.once){const list=this.listeners[kind];const at=list.indexOf(entry);
        if(at>=0)list.splice(at,1);}
    }
    return event;
  }
  getBoundingClientRect(){return {width:this.width};}
  setPointerCapture(){this.captured=true;}
  releasePointerCapture(){this.captured=false;}
  get offsetWidth(){return this.width;}
}
%s
const make=()=>{
  const target=new Target();const stats={paused:0,resumed:0,dismissed:0};
  wireToastSwipe(target,()=>stats.dismissed++,()=>stats.paused++,()=>stats.resumed++);
  return {target,stats};
};
const fire=(item,kind,x,y,time,extra={})=>item.target.emit(kind,
  Object.assign({clientX:x,clientY:y,timeStamp:time},extra));

const mouse=make();
fire(mouse,"pointerdown",0,0,0,{pointerType:"mouse"});
fire(mouse,"pointermove",150,0,20,{pointerType:"mouse"});
fire(mouse,"pointerup",150,0,30,{pointerType:"mouse"});

const vertical=make();
fire(vertical,"pointerdown",100,100,100);
const verticalMove=fire(vertical,"pointermove",120,145,120);
fire(vertical,"pointerup",120,150,140);

const left=make();
fire(left,"pointerdown",100,20,160);
const leftMove=fire(left,"pointermove",10,20,180);
fire(left,"pointerup",0,20,200);

const short=make();
fire(short,"pointerdown",0,20,220);
const shortMove=fire(short,"pointermove",60,20,340);
const shortLive=short.target.style.transform;
const shortUp=fire(short,"pointerup",60,20,360);
const shortBefore={transform:short.target.style.transform,
  settling:short.target.classList.contains("toast-swipe-settling")};
flushTimers();
const shortAfter={transform:short.target.style.transform,
  settling:short.target.classList.contains("toast-swipe-settling")};

const long=make();
fire(long,"pointerdown",0,20,400);
fire(long,"pointermove",105,20,600);
const longUp=fire(long,"pointerup",105,20,620);
const longAnimating=long.target.classList.contains("toast-swipe-dismissing");
flushTimers();

const fast=make();
fire(fast,"pointerdown",0,20,700);
fire(fast,"pointermove",35,20,720);
fire(fast,"pointerup",45,20,730);
flushTimers();

const cancelled=make();
fire(cancelled,"pointerdown",0,20,800);
fire(cancelled,"pointermove",80,20,820);
fire(cancelled,"pointercancel",80,20,830);
flushTimers();

console.log(JSON.stringify({mouse:mouse.stats,vertical:vertical.stats,left:left.stats,
  short:short.stats,long:long.stats,fast:fast.stats,cancelled:cancelled.stats,
  prevented:{vertical:verticalMove.prevented,left:leftMove.prevented,
    shortMove:shortMove.prevented,shortUp:shortUp.prevented,longUp:longUp.prevented},
  shortLive,shortBefore,shortAfter,longAnimating}));
""" % swipe_source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout.strip())
    assert result["mouse"] == {"paused": 0, "resumed": 0, "dismissed": 0}, result
    assert result["vertical"] == {"paused": 1, "resumed": 1, "dismissed": 0}, result
    assert result["left"] == {"paused": 1, "resumed": 1, "dismissed": 0}, result
    assert result["short"] == {"paused": 1, "resumed": 1, "dismissed": 0}, result
    assert result["long"] == {"paused": 1, "resumed": 0, "dismissed": 1}, result
    assert result["fast"] == {"paused": 1, "resumed": 0, "dismissed": 1}, result
    assert result["cancelled"] == {"paused": 1, "resumed": 1, "dismissed": 0}, result
    assert result["prevented"] == {
        "vertical": False, "left": False, "shortMove": True,
        "shortUp": True, "longUp": True}, result
    assert result["shortLive"] == "translate3d(60px,0,0)", result
    assert result["shortBefore"]["settling"] and \
        result["shortBefore"]["transform"] == "translate3d(0,0,0)", result
    assert not result["shortAfter"]["settling"] and \
        result["shortAfter"]["transform"] == "", result
    assert result["longAnimating"], result

    toast_start = ui_source.index("function toast(")
    toast_end = ui_source.index("\n/* Clipboard", toast_start)
    toast_source = ui_source[toast_start:toast_end]
    assert "wireToastSwipe(t, remove, pauseRemoval, resumeRemoval);" in toast_source
    css_start = css_source.index(".toast{")
    css_end = css_source.index("@keyframes toast-in", css_start)
    toast_css = css_source[css_start:css_end]
    assert "touch-action:pan-y pinch-zoom;" in toast_css
    assert ".toast.toast-swiping{" in toast_css
    assert ".toast.toast-swipe-dismissing{" in toast_css


def check_status_header_activation(ui_source: str, css_source: str) -> None:
    """Status-box node lines are one-click targets; the flat sidebar has none."""
    assert "wireDisclosureSurface(name, disclosure);" not in ui_source
    assert ui_source.count("wireDisclosureSurface(head, disclosure);") == 1
    assert "wireDoubleClickOrTouch" not in ui_source
    assert ("display:flex;align-items:center;gap:6px;min-width:0;color:var(--txt3);" +
            "cursor:pointer;") in css_source
    assert "user-select:none;cursor:pointer;" in css_source


def check_shared_node_order(ui_source: str, css_source: str) -> None:
    """The flat sidebar follows the node order the status panel edits."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "const nodes = sortNodeGroups([{ bid: 0 }]" in sidebar
    assert "wireNodeGroupDrag(" not in sidebar
    assert "disclosureButton(" not in sidebar
    assert ui_source.count("wireNodeGroupDropZone(root);") == 1
    assert 'selector = ".foot-engine-group"' in ui_source
    assert 'restoreDragSlots(context, context.selector);' in ui_source
    assert 'lsSet(NODE_ORDER_KEY, JSON.stringify(keys));' in ui_source
    assert ".foot-engine-group.dragging{opacity:.28}" in css_source
    assert ".foot-engines.reordering .foot-engine-group{will-change:transform}" in css_source
    assert ".sess-group.dragging" not in css_source
    assert "#sess-groups.reordering .sess-item{will-change:transform}" in css_source


def check_flat_session_list(ui_source: str, css_source: str) -> None:
    """One cross-backend list; only remote paths name their filesystem backend."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert 'el("section", "sess-group")' not in sidebar
    assert "sess-group-title" not in sidebar
    assert "item.dataset.bid = String(bid);" in sidebar
    assert "sessionLocationLabel(s, bid));" in sidebar
    assert "sessionLocationTitle(s, bid));" in sidebar
    # the per-node openers moved to the status box beside its disclosures
    foot = ui_source[
        ui_source.index("function renderFootEngines()"):
        ui_source.index("\nfunction findSessionMeta", ui_source.index("function renderFootEngines()"))]
    assert "`Open browser on ${g.name}`" in foot
    assert "`Open terminal on ${g.name}`" in foot
    # reorder drags stay scoped to one backend inside the shared flat surface
    assert 'const rowSelector = () => `.sess-item[data-bid="${dragSess.bid}"]`;' in ui_source
    assert 'reorderChildren(root, `.sess-item[data-bid="${bid}"]`)' in ui_source
    assert "container.insertBefore(dragged, siblings[siblings.length - 1].nextSibling);" \
        in ui_source
    assert ".sess-empty{padding:12px 6px 5px;" in css_source

    start = ui_source.index("function sessionWorkspace(")
    end = ui_source.index("\nfunction sessionDeleteMessage", start)
    helpers = ui_source[start:end]
    script = r"""
const isScratchWorkspace=session=>!!session&&session.workspace_kind==="temporary";
const backendName=bid=>bid===7?"NAS":"local";
const tailPath=(path,n)=>path.length>n?"…"+path.slice(-n):path;
%s
const plain={cwd:"/etc/scripts/puppy"};
const linked={cwd:"/private/mirror/never-show",workspace:{
  root:"/volume1/projects/media-tools",node:"NAS"}};
const scratch={cwd:"/tmp/puppy-scratch/x",workspace_kind:"temporary"};
const expired={cwd:"/tmp/puppy-scratch/x",workspace_kind:"temporary",
  workspace_missing:true};
console.log(JSON.stringify([
  sessionLocationLabel(plain,0),
  sessionLocationTitle(plain,0),
  sessionLocationLabel(linked,0),
  sessionLocationTitle(linked,0),
  sessionLocationLabel(scratch,7),
  sessionLocationLabel(expired,7),
]));
""" % helpers
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    assert json.loads(proc.stdout) == [
        "/etc/scripts/puppy",
        "/etc/scripts/puppy",
        "NAS:…projects/media-tools",
        "NAS:/volume1/projects/media-tools",
        "Scratch workspace",
        "Scratch workspace expired",
    ]


def check_sticky_activity_promotions(ui_source: str, css_source: str) -> None:
    """Each idle-to-busy event takes #1 and keeps that place after completion."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "const list = orderSidebarRows(rows, row =>" in sidebar
    assert "animateSessionRows(root, () => {" in sidebar
    assert '"IDLE"' not in sidebar
    assert "activity.textContent = backendName(bid);" in sidebar
    assert "`Session idle on ${backendName(bid)}`" in sidebar
    # a drop persists the durable order only; other automatic promotions stay separate
    assert ".filter(id => !floated.has(id));" in ui_source
    assert "sessionActivityPromotions.has(sessionActivityKey(bid, s.id))" in ui_source
    assert "if (draggedPromotion) sessionActivityPromotions.delete(promotionKey);" \
        in ui_source
    assert "const visualChanged = visualOrder.length !== context.originalOrder.length" \
        in ui_source
    assert "if (!changed && !draggedPromotion)" in ui_source
    # both FLIP helpers share one motion clock
    assert ui_source.count("{ duration: REORDER_MOTION_MS, easing: REORDER_EASING });") == 2
    assert ".si-be.node{" in css_source
    assert ".si-be.idle" not in css_source

    def function(name):
        start = ui_source.index("function " + name + "(")
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + name)

    source = "\n".join(function(name) for name in (
        "sessionActivityKey", "ingestOneSessionActivity", "orderSidebarRows"))
    script = r"""
const sessionActivityAnchors = new Map();
const sessionActivityPromotions = new Map();
let sessionActivityPromotionSequence = 0;
const reportRemoteCompletion = () => {};
%s
const rows = [
  { bid: 0, s: { id: 1, status: "idle" } },
  { bid: 0, s: { id: 2, status: "running" } },
  { bid: 2, s: { id: 7, status: "running" } },
  { bid: 2, s: { id: 9, status: "idle" } },
];
const order = () => orderSidebarRows(rows, row =>
  sessionActivityPromotions.get(sessionActivityKey(row.bid, row.s.id)))
  .map(row => `${row.bid}:${row.s.id}`);
const snapshots = [order()];
rows[1].s.active_since = 900;
ingestOneSessionActivity(0, rows[1].s, 1000, 2000);
snapshots.push(order());
// A later observed activation takes #1 while the prior session is still busy,
// even when its reported start time is older: promotion is event-ordered.
rows[2].s.active_since = 100;
ingestOneSessionActivity(2, rows[2].s, 1000, 2000);
snapshots.push(order());
rows[1].s.status = "idle";
rows[1].s.completion_status = "interrupted";
ingestOneSessionActivity(0, rows[1].s, null, 3000);
snapshots.push(order());
// A later idle -> busy transition promotes that same session again.
rows[1].s.status = "running";
ingestOneSessionActivity(0, rows[1].s, null, 4000);
snapshots.push(order());
// Ordinary running updates do not compete for #1 again.
ingestOneSessionActivity(2, rows[2].s, 1100, 4100);
snapshots.push(order());
console.log(JSON.stringify({snapshots, promotions: sessionActivityPromotions.size}));
""" % source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout)
    assert result["snapshots"] == [
        ["0:1", "0:2", "2:7", "2:9"],
        ["0:2", "0:1", "2:7", "2:9"],
        ["2:7", "0:2", "0:1", "2:9"],
        ["2:7", "0:2", "0:1", "2:9"],
        ["0:2", "2:7", "0:1", "2:9"],
        ["0:2", "2:7", "0:1", "2:9"],
    ], result
    assert result["promotions"] == 2, result


def check_switch_engine_initial_selection(ui_source: str) -> None:
    """The switch modal initially selects the engine already heading for the
    session - a queued switch target when one is pending, else the current
    engine - and marks both states on the cards."""
    expected = ('let pick = (engines.find(engine => engine.key === '
                '(pendingEngine || s.engine)) ||\n    engines[0] || {}).key || "";')
    assert expected in ui_source
    assert '"Switch queued"' in ui_source and '"Current (reseed)"' in ui_source


def check_engine_picker_alignment(css_source: str) -> None:
    """Provider marks use equal block boxes so every card's text rows align."""
    assert ".engine-pick .ep .ep-ico{display:flex;width:18px;height:18px;" \
        in css_source


def check_browser_chip_order(ui_source: str) -> None:
    """Live browsers follow the directory pill and precede activity text."""
    start = ui_source.index("  syncBrowserChips() {")
    end = ui_source.index("\n  updateHead() {", start)
    method = ui_source[start:end]
    assert 'const anchor = scroll.querySelector(".chat-status");' in method
    assert 'scroll.insertBefore(chip, anchor);' in method
    assert 'scroll.querySelector(".chip.be")' not in method


def check_composer_mentions(ui_source: str, css_source: str) -> None:
    """The chat box's @ shortcut: token detection and filtering run for real,
    the popup owns its keys ahead of the interrupt path, and both MCP agents
    define the inserted mention forms for the engine."""
    start = ui_source.index("\nconst MENTION_QUERY_MAX")
    end = ui_source.index("\n/* ================= SessionView")
    helpers = ui_source[start:end]
    script = r"""
%s
const ctx = (text, caret, endSel) =>
  composerMentionContext(text, caret, endSel === undefined ? caret : endSel);
const items = [
  {label: "Browser AB12"}, {label: "Terminal CD34"},
  {label: "New browser"}, {label: "New terminal"}, {label: "New spawn"},
].map(item => ({label: item.label, search: item.label.toLowerCase()}));
const labels = (query) => filterMentionItems(items, query).map(item => item.label);
const opencodeModel = {value: "anthropic/haiku", effort_options: [
  {value: ""}, {value: "high"}]};
const codex = {key: "codex", effort_options: [
  {value: "", label: "Default"}, {value: "low"}, {value: "max"}]};
console.log(JSON.stringify({
  bare: ctx("@", 1),
  word: ctx("hello @bro", 10),
  email: ctx("mail a@b.c", 10),
  atSign: ctx("meet @ 5", 8),
  prose: ctx("@john about x", 13),
  spaced: ctx("@browser a", 10),
  newline: ctx("line\n@te", 8),
  crossed: ctx("@x\ny", 4),
  paren: ctx("(@ab", 4),
  range: ctx("@ab", 2, 3),
  long: ctx("@" + "x".repeat(30), 31),
  midCaret: ctx("see @term now", 9),
  all: labels(""),
  id: labels("ab"),
  fresh: labels("new"),
  kind: labels("browser"),
  kindId: labels("terminal c"),
  spawnRow: labels("spawn"),
  none: labels("xyz"),
  directiveFull: spawnMentionInsert({node: {bid: 2, name: "NAS.lan"},
    engine: codex, model: {value: "gpt-5.6-sol"}, effort: {value: "max"}}),
  directiveQuoted: spawnMentionInsert({node: {bid: 2, name: "My NAS"},
    engine: codex, model: {value: ""}, effort: {value: "low"}}),
  directiveLocal: spawnMentionInsert({node: null, engine: {key: "claude"},
    model: {value: "haiku"}, effort: {value: ""}}),
  directiveFleet: spawnMentionInsert({count: 10, node: {bid: 2, name: "NAS.lan"},
    engine: codex, model: {value: ""}, effort: {value: ""}}),
  effortsShared: spawnEffortOptionsFor(codex, {value: "gpt-5.6-sol"})
    .map(option => option.value),
  effortsOwn: spawnEffortOptionsFor(codex, opencodeModel)
    .map(option => option.value),
}));
""" % helpers
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["bare"] == {"start": 0, "query": ""}, result
    assert result["word"] == {"start": 6, "query": "bro"}, result
    assert result["email"] is None, result          # user@host stays prose
    assert result["atSign"] is None, result         # "meet @ 5" stays prose
    assert result["prose"] is None, result          # second space ends the token
    assert result["spaced"] == {"start": 0, "query": "browser a"}, result
    assert result["newline"] == {"start": 5, "query": "te"}, result
    assert result["crossed"] is None, result        # tokens never cross lines
    assert result["paren"] == {"start": 1, "query": "ab"}, result
    assert result["range"] is None, result          # only a collapsed caret
    assert result["long"] is None, result
    assert result["midCaret"] == {"start": 4, "query": "term"}, result
    assert result["all"] == ["Browser AB12", "Terminal CD34",
                             "New browser", "New terminal", "New spawn"], result
    assert result["id"] == ["Browser AB12"], result
    assert result["fresh"] == ["New browser", "New terminal", "New spawn"], result
    assert result["kind"] == ["Browser AB12", "New browser"], result
    assert result["kindId"] == ["Terminal CD34"], result
    assert result["spawnRow"] == ["New spawn"], result
    assert result["none"] == [], result
    assert result["directiveFull"] == \
        "@Spawn an agent on NAS.lan using codex gpt-5.6-sol at max effort to", result
    assert result["directiveQuoted"] == \
        '@Spawn an agent on "My NAS" using codex at low effort to', result
    assert result["directiveLocal"] == \
        "@Spawn an agent using claude haiku to", result
    assert result["directiveFleet"] == \
        "@Spawn 10 agents on NAS.lan using codex to", result
    assert result["effortsShared"] == ["", "low", "max"], result
    assert result["effortsOwn"] == ["", "high"], result

    # The popup lives inside the composer box and consumes its keys before the
    # composer's own handlers - most importantly Escape, which must close the
    # list rather than interrupt the running turn.
    assert '<div class="mention-pop hidden" role="listbox"' in ui_source
    keydown = ui_source.index('this.ta.addEventListener("keydown"')
    assert ui_source.index("if (this.mentionKeydown(e)) return;", keydown) < \
        ui_source.index('if (e.key === "Escape" || ctrlC)', keydown)
    assert "this.mentionDismissedAt = m.start;" in ui_source
    # the list follows the caret and leaves with composer focus
    assert 'document.addEventListener("selectionchange", this._onSelectionChange);' \
        in ui_source
    assert 'document.removeEventListener("selectionchange", this._onSelectionChange);' \
        in ui_source
    assert "{ this.ctrlCStreak = 0; this.hideMention(); }" in ui_source
    # completion inserts through the ordinary edit path and keeps focus on rows
    apply_start = ui_source.index("  applyMention(item) {")
    apply_method = ui_source[apply_start:
                             ui_source.index("\n  mentionCandidates() {", apply_start)]
    assert 'ta.dispatchEvent(new Event("input", { bubbles: true }));' in apply_method
    assert 'this.mentionEl.addEventListener("pointerdown", (e) => e.preventDefault());' \
        in ui_source
    # offline nodes are never probed and instance snapshots are briefly cached
    refresh = ui_source[ui_source.index("  refreshMentionInstances() {"):
                        ui_source.index("\n  retireSentAttachment(")]
    assert "if (bid && !backendConnectionAllowed(bid)) return;" in refresh
    assert "browserEnabledFor(bid) && browserInstancesFor(bid)" in refresh
    assert "terminalInstancesFor(bid)" in refresh
    assert "Date.now() - data.at < 10000" in refresh
    # sent bubbles render mention tokens; surrounding prose keeps linkification
    assert "if (text) decorateMentionsInto(n, text);" in ui_source
    assert "Browser [A-Z0-9]{4}|Terminal [A-Z0-9]{4}|New browser|New terminal" \
        in ui_source
    # the sent-message token regex recognises every spawn directive variant
    re_start = ui_source.index("const MENTION_TOKEN_RE")
    re_source = ui_source[re_start:ui_source.index("/g;", re_start) + 3]
    token_script = r"""
%s
const token = text => {
  MENTION_TOKEN_RE.lastIndex = 0;
  const m = MENTION_TOKEN_RE.exec(text);
  return m ? m[2] : null;
};
console.log(JSON.stringify({
  full: token("please @Spawn an agent on NAS.lan using codex gpt-5.6-sol at max effort to review it"),
  quoted: token('@Spawn an agent on "My NAS" using codex at low effort to check'),
  bare: token("@Spawn an agent using claude to summarize"),
  modelOnly: token("@Spawn an agent using claude haiku to summarize"),
  fleet: token("@Spawn 10 agents on NAS.lan using codex to hunt bugs"),
  browser: token("see @Browser AB12 now"),
  prose: token("we will spawn an agent later"),
  incomplete: token("@Spawn an agent using to nothing"),
}));
""" % re_source
    proc = subprocess.run(["node", "-e", token_script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    tokens = json.loads(proc.stdout.strip())
    assert tokens["full"] == \
        "@Spawn an agent on NAS.lan using codex gpt-5.6-sol at max effort to", tokens
    assert tokens["quoted"] == \
        '@Spawn an agent on "My NAS" using codex at low effort to', tokens
    assert tokens["bare"] == "@Spawn an agent using claude to", tokens
    assert tokens["modelOnly"] == "@Spawn an agent using claude haiku to", tokens
    assert tokens["fleet"] == \
        "@Spawn 10 agents on NAS.lan using codex to", tokens
    assert tokens["browser"] == "@Browser AB12", tokens
    assert tokens["prose"] is None, tokens
    assert tokens["incomplete"] is None, tokens

    # The wizard flow itself, driven through the real methods: enter from the
    # flat list, slide through parts, skip single-choice parts, fetch remote
    # engines, go back, and insert only the finished directive.
    methods_start = ui_source.index("  mentionKeydown(e) {")
    methods = ui_source[methods_start:
                        ui_source.index("\n  retireSentAttachment(", methods_start)]
    helpers_again = helpers
    flow_script = r"""
%s
class Classes {
  constructor(){this.names=new Set();}
  add(...n){n.forEach(x=>this.names.add(x));}
  remove(...n){n.forEach(x=>this.names.delete(x));}
  toggle(name,on){if(on===undefined)on=!this.names.has(name);on?this.add(name):this.remove(name);}
  contains(name){return this.names.has(name);}
}
class MockNode {
  constructor(tag,cls="",text=""){this.tag=tag;this.classList=new Classes();
    (cls||"").split(/\s+/).filter(Boolean).forEach(n=>this.classList.add(n));
    this.textContent=text;this.children=[];this.attributes={};}
  appendChild(child){this.children.push(child);return child;}
  setAttribute(k,v){this.attributes[k]=String(v);}
}
const el=(tag,cls,text)=>new MockNode(tag,cls,text);
const svg=()=>new MockNode("svg");
const globeIcon=svg,terminalIcon=svg,plusIcon=svg,refreshIcon=svg,choiceSvg=svg;
const scrollCaretIntoView=()=>{};
const state={instance:"pup",backends:[
  {id:2,name:"NAS.lan",protocol:1,capabilities:["spawn-exec"]},
  {id:3,name:"OLD",protocol:1,capabilities:[]},
  {id:5,name:"LAPTOP",protocol:1,capabilities:["spawn-exec"]},
],engines:[
  {key:"claude",label:"Claude Code",installed:true,auth:"ok",version:"2.1.219",
   model_options:[{value:"",label:"Default"},{value:"haiku",label:"Haiku"}],
   effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]},
  {key:"broken",installed:false},
],engCache:{5:[
  {key:"solo",label:"Solo",installed:true,auth:"ok",
   model_options:[{value:"",label:"Default"}],
   effort_options:[{value:"",label:"Default"}]},
]},tabs:[]};
const backendName=bid=>bid?(state.backends.find(b=>b.id===bid)||{}).name||("backend "+bid):state.instance;
const spawnExecFor=bid=>{if(!bid)return true;
  const backend=state.backends.find(item=>item.id===bid);
  return !!backend&&Number(backend.protocol||0)>0&&
    Array.isArray(backend.capabilities)&&backend.capabilities.includes("spawn-exec");};
const backendConnectionAllowed=()=>true;
const backendHasCapability=()=>false;
const browserEnabledFor=()=>false;
const browserInstancesFor=()=>false;
const terminalInstancesFor=()=>false;
const findSessionMeta=()=>null;
let apiCalls=[],apiResult=null;
const api=(bid,path)=>{apiCalls.push({bid,path});
  return Promise.resolve(apiResult);};
const rememberEnginePayload=(bid,result)=>{
  if(bid)state.engCache[bid]=result.engines;else state.engines=result.engines;};
class MockTa {
  constructor(view){this.view=view;this.value="";this.selectionStart=0;this.selectionEnd=0;}
  setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}
  focus(){}
  dispatchEvent(){this.view.updateMention();}
}
class View {
  constructor(bid){this.tab={bid,sid:9};this.mention=null;this.mentionDismissedAt=-1;
    this.mentionSpawn=null;this.mentionRowEls=[];this.histIdx=null;this.ctrlCStreak=0;
    this.mentionData={at:Date.now(),browsers:null,terminals:null,promise:null};
    this.mentionEl=new MockNode("div");
    Object.defineProperty(this.mentionEl,"textContent",{
      get(){return this._t||"";},set(v){this._t=v;this.children=[];}});
    this.ta=new MockTa(this);}
  type(text){this.ta.value=text;this.ta.setSelectionRange(text.length,text.length);
    this.updateMention();}
  labels(){return this.mention?this.mention.items.map(i=>i.label):null;}
  pick(label){const item=this.mention.items.find(i=>i.label===label);
    if(!item)throw new Error("no row "+label+" in "+JSON.stringify(this.labels()));
    this.applyMention(item);}
%s
}
const out={};
const view=new View(0);
view.type("@");
out.flat=view.labels();
view.pick("New spawn");
out.countStep=[view.labels().length,view.labels()[0],view.labels()[11],
  view.labels()[12],view.mentionSpawn.step];
view.type("@3 ag");
out.countFiltered=view.labels();
view.pick("3 agents");
out.nodeStep=[view.labels(),view.mentionSpawn.step];
view.type("@na");
out.nodeFiltered=view.labels();
const remoteEngines=[{key:"codex",label:"Codex",installed:true,auth:"ok",version:"0.149.0",
  model_options:[{value:"",label:"Default"},{value:"gpt-5.6-sol",label:"GPT-5.6 Sol"}],
  effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]}];
apiResult={engines:remoteEngines,usage_refresh:{}};
view.pick("NAS.lan");
out.remoteLoading=[view.labels(),apiCalls.map(c=>c.bid+":"+c.path)];
async function run(){
  await Promise.resolve();await Promise.resolve();
  out.engineStep=[view.labels(),view.mentionSpawn.step];
  view.pick("Codex");
  out.modelStep=[view.labels(),view.mentionSpawn.step];
  view.pick("GPT-5.6 Sol");
  out.effortStep=[view.labels(),view.mentionSpawn.step];
  view.pick("Max");
  out.inserted=[view.ta.value,view.mention,view.mentionSpawn];
  // back-navigation slides engine -> node -> count -> back to the flat list
  view.type("@");
  view.pick("New spawn");
  view.pick("1 agent");
  view.pick("NAS.lan");
  await Promise.resolve();await Promise.resolve();
  view.mentionKeydown({key:"Escape",preventDefault(){},stopPropagation(){}});
  out.backToNode=view.mentionSpawn.step;
  view.mentionKeydown({key:"Escape",preventDefault(){},stopPropagation(){}});
  out.backToCount=view.mentionSpawn.step;
  view.mentionKeydown({key:"Backspace",preventDefault(){},stopPropagation(){}});
  out.backOut=[view.mentionSpawn,view.labels()];
  // single-model single-effort engines skip straight to insertion,
  // and a backend-hosted session offers no node part at all
  const remote=new View(5);
  remote.type("@");
  out.remoteFlat=remote.labels();
  remote.pick("New spawn");
  out.remoteCount=remote.mentionSpawn.step;
  remote.pick("1 agent");
  out.remoteEngine=[remote.labels(),remote.mentionSpawn.step];
  remote.pick("Solo");
  out.remoteInserted=[remote.ta.value,remote.mentionSpawn];
  console.log(JSON.stringify(out));
}
run().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
""" % (helpers_again, methods)
    proc = subprocess.run(["node", "-e", flow_script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1200]
    flow = json.loads(proc.stdout.strip())
    assert flow["flat"] == ["New terminal", "New spawn"], flow
    assert flow["countStep"] == [13, "1 agent", "12 agents", "Back", "count"], flow
    assert flow["countFiltered"] == ["3 agents", "Back"], flow
    assert flow["nodeStep"] == [["pup", "NAS.lan", "LAPTOP", "Back"], "node"], flow
    assert flow["nodeFiltered"] == ["NAS.lan", "Back"], flow
    assert flow["remoteLoading"] == [["Loading engines…", "Back"],
                                     ["2:engines"]], flow
    assert flow["engineStep"] == [["Codex", "Back"], "engine"], flow
    assert flow["modelStep"] == [["Default", "GPT-5.6 Sol", "Back"], "model"], flow
    assert flow["effortStep"] == [["Default", "Max", "Back"], "effort"], flow
    assert flow["inserted"] == [
        "@Spawn 3 agents on NAS.lan using codex gpt-5.6-sol at max effort to ",
        None, None], flow
    assert flow["backToNode"] == "node", flow
    assert flow["backToCount"] == "count", flow
    assert flow["backOut"] == [None, ["New terminal", "New spawn"]], flow
    assert flow["remoteFlat"] == ["New spawn"], flow
    assert flow["remoteCount"] == "count", flow
    assert flow["remoteEngine"] == [["Solo", "Back"], "engine"], flow
    assert flow["remoteInserted"] == ["@Spawn an agent using solo to ", None], flow

    # The "New spawn" wizard: capability-gated row, parts slide instead of
    # inserting, Escape/Backspace go back, and only the finished directive
    # lands in the composer through the ordinary insert path.
    assert 'if (spawnExecFor(bid))\n      push("new-spawn", "New spawn"' in ui_source
    assert 'backend.capabilities.includes("spawn-exec")' in ui_source
    assert 'if (item.kind === "new-spawn") { this.spawnMentionBegin(); return; }' \
        in ui_source
    assert "if (this.mentionSpawn) { this.spawnStepBack(); return true; }" in ui_source
    assert 'this.mentionSpawn && e.key === "Backspace" && !m.query' in ui_source
    assert "this.applyMention({ insert: spawnMentionInsert(wizard) });" in ui_source
    wizard_hide = ui_source.index("  hideMention() {")
    assert ui_source.index("this.mentionSpawn = null;", wizard_hide) < \
        ui_source.index("if (!this.mention) return;", wizard_hide)
    assert '!this.mentionSpawn && e.key === "Enter"' in ui_source
    assert "if (!this.mentionSpawn) this.refreshMentionInstances();" in ui_source
    # a backend-hosted session can only spawn onto its own node
    spawn_nodes = ui_source[ui_source.index("  spawnNodeChoices() {"):
                            ui_source.index("\n  spawnTargetBid()")]
    assert "if (!bid) {" in spawn_nodes
    assert "!backendConnectionAllowed(backend.id)" in spawn_nodes
    assert ".mention-step-head{" in css_source
    assert ".mention-pop.slide-next{animation:mention-slide-next" in css_source
    assert ".mention-pop.slide-back{animation:mention-slide-back" in css_source
    assert ".mention-pop.slide-next,.mention-pop.slide-back{animation:none}" \
        in css_source
    # styles: anchored above the composer, one .sel highlight, centred icon ink
    assert ".mention-pop{" in css_source
    assert "bottom:calc(100% + 9px)" in css_source
    assert ".mention-item.sel{background:var(--float-menu-hover)}" in css_source
    assert ".mention-ico svg{display:block}" in css_source
    assert ".mention-token{" in css_source

    # Both MCP agents define the inserted mention forms on every channel the
    # WebUI relies on: the server instructions and the tool schemas themselves.
    assert '"@Browser A8AR"' in browser_agent.TOOL_INSTRUCTIONS
    assert '"@New browser"' in browser_agent.TOOL_INSTRUCTIONS
    assert '"@Terminal A8AR"' in terminal_agent.TOOL_INSTRUCTIONS
    assert '"@New terminal"' in terminal_agent.TOOL_INSTRUCTIONS
    browser_tools = {tool["name"]: tool for tool in browser_agent.TOOLS}
    assert '"@Browser A8AR" mention' in browser_tools["navigate"][
        "inputSchema"]["properties"]["browser_id"]["description"]
    assert '"@New browser" mention' in browser_tools["new_browser"]["description"]
    terminal_tools = {tool["name"]: tool for tool in terminal_agent.TOOLS}
    assert '"@Terminal A8AR" mention' in terminal_tools["type"][
        "inputSchema"]["properties"]["terminal_id"]["description"]
    assert '"@New terminal" mention' in terminal_tools["new_terminal"]["description"]
    assert '"@Spawn an agent on NAS.lan using codex gpt-5.6-sol at max effort ' \
        'to <task>"' in spawn_agent.TOOL_INSTRUCTIONS
    assert "passing those values verbatim" in spawn_agent.TOOL_INSTRUCTIONS
    assert '"@Spawn 10 agents ..."' in spawn_agent.TOOL_INSTRUCTIONS
    assert "Wait until every agent has finished" in spawn_agent.TOOL_INSTRUCTIONS
    assert "silent for 600 seconds" in spawn_agent.TOOL_INSTRUCTIONS
    assert "7200-second safety cap" in spawn_agent.TOOL_INSTRUCTIONS
    assert "user sends steering" in spawn_agent.TOOL_INSTRUCTIONS
    spawn_tools = {tool["name"]: tool for tool in spawn_agent.TOOLS}
    assert '"@Spawn an agent ... to ..." mention' in \
        spawn_tools["spawn"]["description"]
    count_schema = spawn_tools["spawn"]["inputSchema"]["properties"]["count"]
    assert count_schema["minimum"] == 1 and count_schema["maximum"] == 12
    spawn_limits = spawn_tools["spawn"]["inputSchema"]["properties"]
    assert spawn_limits["idle_timeout_s"]["default"] == 600
    assert spawn_limits["max_runtime_s"]["default"] == 7200
    assert "timeout_s" not in spawn_limits
    wait_schema = spawn_tools["wait"]["inputSchema"]
    assert wait_schema["properties"]["jobs"]["type"] == "array"
    assert wait_schema["required"] == ["jobs"]
    update_schema = spawn_tools["update_limits"]["inputSchema"]
    assert update_schema["required"] == ["jobs"]
    assert update_schema["anyOf"] == [
        {"required": ["idle_timeout_s"]},
        {"required": ["max_runtime_s"]},
    ]
    assert spawn_tools["cancel"]["inputSchema"]["required"] == ["jobs"]


def check_chat_status_bar_layout(ui_source: str, css_source: str) -> None:
    """Identity is one pill; location names the backend that owns the files."""
    start = ui_source.index("function workspaceLocationNode(")
    end = ui_source.index("\nfunction sessionDeleteMessage", start)
    helpers = ui_source[start:end]
    script = r"""
const sessionWorkspace=session=>session&&session.workspace&&session.workspace.root?
  session.workspace:null;
const backendName=bid=>bid===7?"Executor":"Primary";
const tailPath=(path,n)=>path.length>n?"…"+path.slice(-n):path;
%s
const direct={cwd:"/etc/scripts/puppy"};
const remote={cwd:"/private/mirror/never-show",workspace:{
  root:"/volume/projects/puppy",node:"NAS"}};
const missing={cwd:"/stale/scratch/path",workspace_missing:true};
const long={workspace:{root:"/one/two/three/four/five",node:"NAS"}};
console.log(JSON.stringify({
  direct:[workspaceLocationLabel(direct,0),workspaceLocationTitle(direct,0)],
  remote:[workspaceLocationLabel(remote,7),workspaceLocationTitle(remote,7)],
  missing:workspaceLocationLabel(missing,0),
  longFull:workspaceLocationLabel(long,7),
  long:workspaceLocationLabel(long,7,20),
}));
""" % helpers
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout)
    assert result == {
        "direct": ["Primary:/etc/scripts/puppy", "Primary:/etc/scripts/puppy"],
        "remote": ["NAS:/volume/projects/puppy", "NAS:/volume/projects/puppy"],
        "missing": "Primary:Scratch workspace expired",
        "longFull": "NAS:/one/two/three/four/five",
        "long": "NAS:…/three/four/five",
    }, result

    build_start = ui_source.index("  buildDom() {")
    build_end = ui_source.index("\n  connect()", build_start)
    build = ui_source[build_start:build_end]
    assert '<span class="chip be">' not in build
    assert '<span class="chip eng">' in build and '<span class="chip cwd">' in build
    head_start = ui_source.index("  updateHead() {")
    head_end = ui_source.index("\n  updateRunState()", head_start)
    head = ui_source[head_start:head_end]
    assert "const identityText = `${backendName(this.tab.bid)} · ${engineText} · ${modelText}`;" \
        in head
    assert "cwd.textContent = workspaceLocationLabel(s, this.tab.bid);" in head
    assert "workspaceLocationTitle(s, this.tab.bid)" in head
    assert 'querySelector(".chip.be")' not in head
    assert "Every pill is durable session metadata" in css_source
    assert ".chat-meta-scroll .chip{max-width:none}" in css_source


def check_backend_name_single_activation(ui_source: str) -> None:
    """One surface click toggles; its button and later double clicks do not."""
    def extract_function(marker: str) -> str:
        start = ui_source.index(marker)
        brace = ui_source.index("{", start)
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced " + marker)

    wire = extract_function("function wireDisclosureSurface(")
    script = r"""
class Target {
  constructor() { this.listeners={}; }
  addEventListener(kind,fn) { (this.listeners[kind] ||= []).push(fn); }
  emit(kind,values={}) {
    const event=Object.assign({detail:0,prevented:false,stopped:false,target:this,
      preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}},values);
    for(const fn of this.listeners[kind] || []) fn(event);
    return event;
  }
}
%s
const target=new Target();
const disclosure={clicks:0,child:{},click(){this.clicks++;},
  contains(node){return node===this.child;}};
wireDisclosureSurface(target,disclosure);
const events=[1,2,3,0].map(detail=>target.emit("click",{detail}));
const buttonEvent=target.emit("click",{detail:1,target:disclosure});
const buttonChildEvent=target.emit("click",{detail:1,target:disclosure.child});
console.log(JSON.stringify({clicks:disclosure.clicks,events,buttonEvent,buttonChildEvent}));
""" % wire
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout.strip())
    assert result["clicks"] == 2, result  # one physical click + programmatic activation
    assert all(event["prevented"] and event["stopped"]
               for event in result["events"]), result
    assert not result["buttonEvent"]["prevented"] and \
        not result["buttonEvent"]["stopped"], result
    assert not result["buttonChildEvent"]["prevented"] and \
        not result["buttonChildEvent"]["stopped"], result


def check_browser_disable_closes_scoped_tabs(ui_source: str) -> None:
    """Disabling one node retires its browser tabs and no other tab."""
    start = ui_source.index("function closeBrowserTabsForBackend(")
    brace = ui_source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(ui_source)):
        if ui_source[index] == "{":
            depth += 1
        elif ui_source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None, "unbalanced scoped browser-tab cleanup"
    helper = ui_source[start:end]
    script = r"""
const state={tabs:[
  {id:"local-browser",type:"browser",bid:0,browserId:"L1CL"},
  {id:"remote-7-a",type:"browser",bid:7,browserId:"R7A1"},
  {id:"remote-8",type:"browser",bid:8,browserId:"R8B1"},
  {id:"remote-7-b",type:"browser",bid:7,browserId:"R7A2"},
  {id:"session-7",type:"session",bid:7,sid:4},
  {id:"settings",type:"settings"},
]};
const closed=[];
function closeTab(id){closed.push(id);state.tabs.splice(state.tabs.findIndex(tab=>tab.id===id),1);}
%s
const remoteCount=closeBrowserTabsForBackend(7);
const afterRemote=state.tabs.map(tab=>tab.id);
const localCount=closeBrowserTabsForBackend(0);
console.log(JSON.stringify({remoteCount,afterRemote,localCount,closed,
  remaining:state.tabs.map(tab=>tab.id)}));
""" % helper
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["remoteCount"] == 2 and result["localCount"] == 1, result
    assert result["closed"] == ["remote-7-a", "remote-7-b", "local-browser"], result
    assert result["afterRemote"] == [
        "local-browser", "remote-8", "session-7", "settings"], result
    assert result["remaining"] == ["remote-8", "session-7", "settings"], result


def check_quota_math(ui_source: str) -> None:
    """The footer's weekly figure, run through node against every payload shape.
    claude's `utilization` is a 0..1 fraction and codex's `used_percent` is
    0..100 - the scale must follow the field name, never the magnitude, which
    once rendered an 89%-consumed week as "99% wk"."""
    def extract(start):
        i = ui_source.index(start)
        b = ui_source.index("{", i)
        depth = 0
        for j in range(b, len(ui_source)):
            if ui_source[j] == "{":
                depth += 1
            elif ui_source[j] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[i:j + 1]
        raise AssertionError("unbalanced " + start)
    script = (extract("\nfunction weeklyUsedPercent(") +
              extract("\nfunction weeklyQuotaLeft(") + """
const claude = {rateLimitType: "seven_day_overage_included", utilization: 0.89,
  unifiedWindows: {five_hour: {utilization: 0.29}, seven_day: {utilization: 0.68},
                   seven_day_overage_included: {utilization: 0.89}}};
const out = [
  weeklyQuotaLeft({rate_limit: claude}),                                  // 11
  weeklyQuotaLeft({rate_limit: {rateLimitType: "five_hour", utilization: 0.3,
    unifiedWindows: {seven_day: {utilization: 0.68}}}}),                  // 32
  weeklyQuotaLeft({quota: {weekly_used_percent: 19}}),                    // 81
  weeklyQuotaLeft({rate_limit: {primary: {window_minutes: 10080,
                                          used_percent: 40}}}),           // 60
  weeklyQuotaLeft({rate_limit: null}),                                    // null
  weeklyQuotaLeft({rate_limit: {rateLimitType: "seven_day",
                                utilization: 1.15}}),                     // 0
];
console.log(JSON.stringify(out));
""")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:400]
    values = json.loads(proc.stdout.strip())
    rounded = [None if v is None else round(v) for v in values]
    assert rounded == [11, 32, 81, 60, None, 0], values


def check_browser_loading_ui(ui_source: str, css_source: str) -> None:
    """The bar acknowledges a navigation instantly, node statuses own the
    loading flag, and a transient error is a toast, not the dead overlay."""
    start = ui_source.index("class BrowserView {")
    end = ui_source.index("/* ================= SettingsView", start)
    view_source = ui_source[start:end]
    assert "this.setLoading(d.loading === true);" in view_source
    assert 'this.send({ type: "navigate", url: target });' in view_source
    # optimistic acknowledgment precedes the send
    assert view_source.index("this.setLoading(true);") < \
        view_source.index('this.send({ type: "navigate", url: target });')
    assert "if (d.terminal === true) {" in view_source
    assert 'toast(d.text || "Browser error", "error", 5000);' in view_source
    script = r"""
const timers=new Map();let nextTimer=0;
const setTimeout=(fn,delay)=>{const id=++nextTimer;timers.set(id,{fn,delay});return id;};
const clearTimeout=id=>{timers.delete(id);};
%s
const classes=new Set();
const view=Object.create(BrowserView.prototype);
view.loadingTimer=null;
view.root={classList:{
  toggle(name,on){if(on)classes.add(name);else classes.delete(name);},
  remove(name){classes.delete(name);}}};
view.setLoading(true);
const armed=classes.has("loading")&&timers.size===1;
const delay=[...timers.values()][0].delay;
view.setLoading(false);
const clearedOnStatus=!classes.has("loading")&&timers.size===0;
view.setLoading(true);
[...timers.values()][0].fn();
const selfExpired=!classes.has("loading")&&view.loadingTimer===null;
console.log(JSON.stringify({armed,delay,clearedOnStatus,selfExpired}));
""" % view_source
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout)
    assert result == {"armed": True, "delay": 20000, "clearedOnStatus": True,
                      "selfExpired": True}, result
    assert ".view.browser.loading .br-bar::after" in css_source
    assert "br-loading-sweep" in css_source


def check_shared_storage_settings_ui(ui_source: str, css_source: str) -> None:
    """The shared sign-in toggle rides the Browser switch's status fetch,
    appears only for capable nodes, and downgrades with them offline."""
    assert 'api(bid, "browser/shared-storage", {' in ui_source
    assert 'id="set-browser-share"' in ui_source
    assert 'backendHasCapability(b, "browser-shared-storage")' in ui_source
    assert "st.shared_storage === true" in ui_source
    assert 'typeof st.shared_storage === "boolean"' in ui_source
    assert "record.shared.input.disabled = true;" in ui_source
    assert "be-browser-share" in ui_source
    assert ".browser-share-toggle" in css_source


async def main() -> None:
    stub = TEST_ROOT / "stub-chromium"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o700)
    os.environ["PUPPY_BROWSER_BIN"] = str(stub)

    config.load()
    db.connect()
    catalog_path = Path(browser._catalog_path())
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_catalog = '{"version":0,"ids":{},"bindings":{}}'
    catalog_path.write_text(invalid_catalog, encoding="utf-8")
    try:
        browser._load_catalog()
    except browser.BrowserError as exc:
        assert "not current" in str(exc), str(exc)
    else:
        raise AssertionError("noncurrent browser catalog was accepted")
    assert catalog_path.read_text(encoding="utf-8") == invalid_catalog
    catalog_path.unlink()
    # Every handoff keeps the current one-browser/one-session catalog bijective
    # without mutating its input.
    current_records = {
        "A1B2": {"created_at": 1.0, "closed_at": None,
                 "origin": "agent", "owner_session": 11},
        "C3D4": {"created_at": 2.0, "closed_at": None,
                 "origin": "user", "owner_session": 13},
    }
    current_bindings = {11: "A1B2", 13: "C3D4"}
    moved_records, moved_bindings, moved_ids = browser._rebind_catalog(
        current_records, current_bindings, "C3D4", 11)
    assert moved_bindings == {11: "C3D4"} and moved_ids == {"A1B2", "C3D4"}
    assert moved_records["A1B2"]["owner_session"] is None
    assert current_bindings == {11: "A1B2", 13: "C3D4"}
    assert browser._redact_diagnostic_url(
        "https://user:pass@example.test/path?token=secret#fragment") == \
        "https://example.test/path"
    assert browser._redact_diagnostic_url("data:text/plain,private") == \
        "data:[redacted]"
    assert browser._normalize_url("example.test/path") == "https://example.test/path"
    assert browser._normalize_url("about:blank") == "about:blank"
    assert browser._normalize_url("data:text/plain,hello") == "data:text/plain,hello"
    assert browser._normalize_url("file:///etc/passwd") == ""
    assert browser._normalize_url("chrome://version") == ""
    await check_cdp_message_isolation()
    await check_bounded_ax_source()
    await check_blocking_cleanup_offload()
    check_fragmented_bridge_response()
    app = build_app()
    web_runner = web.AppRunner(app)
    await web_runner.setup()
    site = web.TCPSite(web_runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    assert browser_agent.turn_mcp(999, "disabled-turn") is None
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
                assert "browser-instances" in ping["capabilities"], ping
                assert "browser-handoff" in ping["capabilities"], ping
                assert "browser-file-workflows" in ping["capabilities"], ping
                assert "terminal-instances" in ping["capabilities"], ping
                assert "terminal-handoff" in ping["capabilities"], ping
                assert "system-prompt" in ping["capabilities"], ping
                assert ping["browser"] == {"enabled": False}, ping
            async with http.get(url + "/api/system-prompt", headers=headers) as r:
                prompt_settings = await read_json(r)
                assert r.status == 200, prompt_settings
            default_remote_prompt = \
                prompt_settings["system_prompt"]["remote_workspace_default"]
            default_browser_prompt = prompt_settings["system_prompt"]["browser_default"]
            default_terminal_prompt = prompt_settings["system_prompt"]["terminal_default"]
            assert prompt_settings["system_prompt"]["remote_workspace"] == \
                default_remote_prompt
            assert prompt_settings["system_prompt"]["terminal"] == \
                default_terminal_prompt
            assert system_prompts.turn_prompt(remote_workspace=False) == ""
            assert system_prompts.turn_prompt(remote_workspace=True) == \
                default_remote_prompt
            async with http.patch(url + "/api/system-prompt", headers=headers,
                                  json={"custom": "first\r\nsecond"}) as r:
                prompt_settings = await read_json(r)
                assert r.status == 200, prompt_settings
                assert prompt_settings["system_prompt"]["custom"] == "first\nsecond"
                assert prompt_settings["system_prompt"]["remote_workspace"] == \
                    default_remote_prompt
            assert system_prompts.turn_prompt(remote_workspace=False) == "first\nsecond"
            assert system_prompts.turn_prompt(remote_workspace=True) == \
                "first\nsecond\n\n" + default_remote_prompt
            async with http.patch(url + "/api/system-prompt", headers=headers,
                                  json={"custom": "",
                                        "remote_workspace": default_remote_prompt,
                                        "browser": default_browser_prompt,
                                        "terminal": default_terminal_prompt}) as r:
                assert r.status == 200, await r.text()

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

            # Every create gets a distinct mixed A-Z/0-9 ID and isolated
            # profile. Two instances can run concurrently on the same node.
            created_ids = []
            for _ in range(2):
                async with http.post(url + "/api/browser/instances", headers=headers,
                                     json={}) as r:
                    created = await read_json(r)
                    assert r.status == 201, created
                    browser_id = created["browser"]["id"]
                    assert len(browser_id) == 4 and browser_id.isalnum() and \
                        browser_id == browser_id.upper(), browser_id
                    assert any(char.isalpha() for char in browser_id) and \
                        any(char.isdigit() for char in browser_id), browser_id
                    created_ids.append(browser_id)
            assert len(set(created_ids)) == 2, created_ids
            first_id, closed_id = created_ids

            launches = await wait_for(
                lambda: read_lines("argv.jsonl")
                if len(read_lines("argv.jsonl")) >= 2 else None,
                message="two browser launches")
            profiles = []
            for launch in launches[:2]:
                argv = launch["argv"]
                assert "--remote-debugging-pipe" in argv and "--headless=new" in argv, argv
                assert ("--no-sandbox" in argv) is (os.geteuid() == 0), argv
                profiles.extend(value.split("=", 1)[1] for value in argv
                                if value.startswith("--user-data-dir="))
            assert len(set(profiles)) == 2, profiles
            assert all(any(browser_id in profile for browser_id in created_ids)
                       for profile in profiles), profiles
            # every fresh instance identifies itself on its launch tab instead
            # of leaving the user staring at about:blank
            painted = await wait_for(
                lambda: read_lines("document.jsonl")
                if len(read_lines("document.jsonl")) >= 2 else None,
                message="start pages")
            assert {launch["frameId"] for launch in painted[:2]} == {"f1"}, painted
            for browser_id in created_ids:
                assert any(browser_id in launch["html"] and
                           "is ready" in launch["html"] for launch in painted), browser_id
            # painting it must not add a history entry or a visible address
            assert not any(nav["url"] != "about:blank"
                           for nav in read_lines("navigations.jsonl")), \
                read_lines("navigations.jsonl")

            # and the non-root argv never carries the flag
            assert "--no-sandbox" not in browser.launch_argv("/x", "/p", as_root=False)
            assert "--no-sandbox" in browser.launch_argv("/x", "/p", as_root=True)
            assert "--window-size=1280,800" in \
                browser.launch_argv("/x", "/p", as_root=False)
            assert browser._normalize_viewport(900, 540) == {"width": 900, "height": 540}
            assert browser._normalize_viewport(159, 540) is None
            assert browser._normalize_viewport(float("nan"), 540) is None
            assert browser._normalize_viewport(7680, 4320) == \
                {"width": 3840, "height": 2160}

            catalog = json.loads(Path(browser._catalog_path()).read_text())
            assert set(created_ids) <= set(catalog["ids"]), catalog

            # Closed IDs stay retired in the history, and their route no longer
            # resolves. A synthetic record older than 30 days is pruned on the
            # next allocation together with its private directory.
            async with http.delete(
                    url + "/api/browser/instances/" + closed_id, headers=headers) as r:
                assert r.status == 200, await read_json(r)
            catalog = json.loads(Path(browser._catalog_path()).read_text())
            assert catalog["ids"][closed_id]["closed_at"] is not None, catalog
            old_id = "Z9Z9" if "Z9Z9" not in created_ids else "Y8Y8"
            registry = browser.manager()
            registry.records[old_id] = {
                "created_at": 1.0,
                "closed_at": 1.0,
                "origin": "user",
                "owner_session": None,
            }
            old_root = Path(browser._instance_root(old_id))
            old_root.mkdir(parents=True, mode=0o700)
            (old_root / "retired-marker").write_text("retired")
            browser._write_catalog(registry.records, registry.bindings)
            async with http.post(url + "/api/browser/instances", headers=headers,
                                 json={}) as r:
                third = await read_json(r)
                assert r.status == 201, third
            third_id = third["browser"]["id"]
            assert third_id not in created_ids
            await wait_for(lambda: not (old_root / "retired-marker").exists(),
                           message="retired browser storage cleanup")
            if third_id == old_id:
                assert registry.records[old_id]["closed_at"] is None
            else:
                assert old_id not in registry.records and not old_root.exists()

            # A browser/chat handoff is explicit, authenticated, and
            # one-to-one in both directions. Moving a chat releases its old
            # browser, and releasing a browser leaves no stale catalog owner.
            handoff_sid = db.create_session(
                "handoff chat", "codex", str(BASE), "", "", "#7aa2f7",
                "danger-full-access")
            other_sid = db.create_session(
                "other chat", "codex", str(BASE), "", "", "#7aa2f7",
                "danger-full-access")
            binding_path = url + "/api/browser/instances/{}/binding"
            async with http.get(binding_path.format(first_id), headers=headers) as r:
                binding = await read_json(r)
                assert r.status == 200 and binding["session_id"] is None, binding
            async with http.post(binding_path.format(first_id), headers=headers,
                                 json={"session_id": handoff_sid}) as r:
                binding = await read_json(r)
                assert r.status == 200 and binding["session_id"] == handoff_sid, binding
                assert binding["session_name"] == "handoff chat", binding
            async with http.post(binding_path.format(third_id), headers=headers,
                                 json={"session_id": handoff_sid}) as r:
                moved = await read_json(r)
                assert r.status == 200 and moved["session_id"] == handoff_sid, moved
            assert browser.manager().get(first_id).owner_session is None
            assert browser.manager().bindings == {handoff_sid: third_id}
            async with http.post(binding_path.format(first_id), headers=headers,
                                 json={"session_id": other_sid}) as r:
                assert r.status == 200, await read_json(r)
            async with http.delete(binding_path.format(first_id), headers=headers) as r:
                released = await read_json(r)
                assert r.status == 200 and released["session_id"] is None, released
            async with http.post(binding_path.format(first_id), headers=headers,
                                 json={"session_id": handoff_sid}) as r:
                assert r.status == 200, await read_json(r)
            assert browser.manager().get(third_id).owner_session is None
            assert browser.manager().bindings == {handoff_sid: first_id}
            async with http.post(binding_path.format(third_id), headers=headers,
                                 json={"session_id": other_sid}) as r:
                assert r.status == 200, await read_json(r)
            async with http.delete(url + "/api/sessions/{}".format(other_sid),
                                   headers=headers) as r:
                assert r.status == 200, await read_json(r)
            assert browser.manager().get(third_id).owner_session is None
            assert browser.manager().bindings == {handoff_sid: first_id}
            async with http.post(binding_path.format(first_id), headers=headers,
                                 json={"session_id": True}) as r:
                assert r.status == 400, await read_json(r)

            # ID-scoped viewer websocket: status text, frames, input forwarding.
            texts, frames = [], []
            ws = await http.ws_connect(
                url + "/api/ws/browser/" + first_id, headers=headers)
            reader = asyncio.ensure_future(collect_ws(ws, texts, frames))
            await wait_for(lambda: frames, message="first frame")
            await wait_for(lambda: any(t.get("type") == "status" for t in texts),
                           message="status message")
            initial_binding = await wait_for(
                lambda: next((item for item in texts if item.get("type") == "binding"), None),
                message="browser binding message")
            assert initial_binding["session_id"] == handoff_sid, initial_binding
            assert initial_binding["session_name"] == "handoff chat", initial_binding
            assert frames[0] == b"stub-jpeg-frame-bytes", frames[0][:40]
            meta = next(t for t in texts if t.get("type") == "frame_meta")
            assert meta["width"] == 1280 and meta["height"] == 800, meta

            # The real Chromium viewport follows the viewer pane, its stream
            # ceiling permits that negotiated size, and normalized input uses
            # the new CSS-pixel dimensions rather than the launch default.
            await ws.send_json({"type": "viewport", "width": 900, "height": 540})
            viewports = await wait_for(
                lambda: read_lines("viewport.jsonl")
                if read_lines("viewport.jsonl") and
                read_lines("viewport.jsonl")[-1].get("width") == 900 else None,
                message="dynamic browser viewport")
            assert viewports[-1]["height"] == 540, viewports[-1]
            assert viewports[-1]["deviceScaleFactor"] == 1
            assert viewports[-1]["mobile"] is False
            await wait_for(
                lambda: next((t for t in texts if t.get("type") == "frame_meta" and
                              t.get("width") == 900 and t.get("height") == 540), None),
                message="resized frame metadata")
            casts = read_lines("screencast.jsonl")
            assert casts and casts[0]["maxWidth"] == 3840 and \
                casts[0]["maxHeight"] == 2160, casts

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
            assert press["params"]["x"] == 450 and press["params"]["y"] == 135, press
            key = next(i for i in inputs if i["method"] == "Input.dispatchKeyEvent")
            assert key["params"]["text"] == "a", key
            insert = next(i for i in inputs if i["method"] == "Input.insertText")
            assert insert["params"]["text"] == "hello", insert

            # pages render with the WebUI's theme: dark until a viewer says
            # otherwise, and every live browser follows a toggle at once
            def schemes():
                return [feature["value"] for line in read_lines("media.jsonl")
                        for feature in (line["features"] or [])
                        if feature["name"] == "prefers-color-scheme"]

            assert schemes() and set(schemes()) == {"dark"}, schemes()
            assert browser.color_scheme() == "dark"
            media_count = len(read_lines("media.jsonl"))
            await ws.send_json({"type": "color_scheme", "value": "light"})
            await wait_for(lambda: len(read_lines("media.jsonl")) >= media_count + 2,
                           message="light emulation on every live browser")
            assert schemes()[-2:] == ["light", "light"], schemes()
            assert config.get("browser.color_scheme") == "light"
            media_count = len(read_lines("media.jsonl"))
            await ws.send_json({"type": "color_scheme", "value": "light"})
            await asyncio.sleep(0.2)
            assert len(read_lines("media.jsonl")) == media_count, schemes()
            # a stale or hostile value is a rendering hint, not a launch failure
            assert browser.normalize_color_scheme("neon") == "dark"
            assert browser.normalize_color_scheme(None) == "dark"
            media_count = len(read_lines("media.jsonl"))
            await ws.send_json({"type": "color_scheme", "value": "sepia"})
            await wait_for(lambda: len(read_lines("media.jsonl")) >= media_count + 2,
                           message="fallback emulation")
            assert schemes()[-2:] == ["dark", "dark"], schemes()
            media_count = len(read_lines("media.jsonl"))
            await ws.send_json({"type": "color_scheme", "value": "light"})
            await wait_for(lambda: len(read_lines("media.jsonl")) >= media_count + 2,
                           message="light again")
            assert schemes()[-2:] == ["light", "light"], schemes()

            # A hidden pane keeps its Browser process and socket but pauses the
            # expensive screencast. Reactivation starts it and delivers a fresh
            # frame without making the user reconnect manually.
            instance = browser.manager().get(first_id)
            stops = len(read_lines("screencast-stop.jsonl"))
            await ws.send_json({"type": "viewer_active", "active": False})
            await wait_for(
                lambda: not instance.screencasting and
                len(read_lines("screencast-stop.jsonl")) > stops,
                message="inactive viewer screencast pause")
            assert instance.running and instance.viewer_count() == 1
            hidden_frames = len(frames)
            hidden_shots = len(read_lines("screenshots.jsonl"))
            await ws.send_json({"type": "reload"})
            await asyncio.sleep(0.5)
            assert len(frames) == hidden_frames
            assert len(read_lines("screenshots.jsonl")) == hidden_shots
            starts = len(read_lines("screencast.jsonl"))
            await ws.send_json({"type": "viewer_active", "active": True})
            await wait_for(lambda: instance.screencasting and
                           len(read_lines("screencast.jsonl")) > starts,
                           message="active viewer screencast resume")
            await wait_for(lambda: len(frames) > hidden_frames,
                           message="active viewer fresh frame")

            # bare hostnames gain a scheme; LAN-ish suffixes stay cleartext
            await ws.send_json({"type": "navigate", "url": "openhab.lan/start"})
            navs = await wait_for(lambda: read_lines("navigations.jsonl"),
                                  message="navigation")
            assert navs[0]["url"] == "http://openhab.lan/start", navs

            # Chromium sometimes keeps the requested DOM viewport but resets
            # its screencast surface on the first navigation. A mismatched
            # frame is never painted; the node reapplies the same metrics and
            # restarts the stream without waiting for another user resize.
            repair_text_start = len(texts)
            repair_frame_start = len(frames)
            repair_viewports = len(read_lines("viewport.jsonl"))
            repair_casts = len(read_lines("screencast.jsonl"))
            await ws.send_json({"type": "navigate",
                                "url": "http://stub.invalid/surface-reset"})
            await wait_for(
                lambda: len(read_lines("viewport.jsonl")) > repair_viewports,
                message="stale screencast viewport repair")
            await wait_for(
                lambda: len(read_lines("screencast.jsonl")) > repair_casts,
                message="screencast restart after navigation")
            await wait_for(lambda: len(frames) > repair_frame_start,
                           message="fresh frame after viewport repair")
            assert b"stale-screencast-surface" not in frames[repair_frame_start:]
            repair_meta = [item for item in texts[repair_text_start:]
                           if item.get("type") == "frame_meta"]
            assert not any(item.get("width") == 1280 and
                           item.get("height") == 657 for item in repair_meta), repair_meta
            assert read_lines("viewport.jsonl")[-1]["width"] == 900
            assert read_lines("viewport.jsonl")[-1]["height"] == 540

            # A renderer can reject the viewport override while navigation is
            # in flight. Repair must still restart the stream and send a fresh
            # frame instead of leaving only status/URL messages alive.
            failed_repair_frames = len(frames)
            failed_repair_casts = len(read_lines("screencast.jsonl"))
            failed_repair_calls = len(read_lines("viewport-fail.jsonl"))
            await ws.send_json({"type": "navigate",
                                "url": "http://stub.invalid/surface-reset-error"})
            await wait_for(
                lambda: len(read_lines("viewport-fail.jsonl")) > failed_repair_calls,
                message="rejected viewport repair")
            await wait_for(
                lambda: len(read_lines("screencast.jsonl")) > failed_repair_casts,
                message="stream restart after rejected viewport repair")
            await wait_for(lambda: len(frames) > failed_repair_frames,
                           message="fresh frame after rejected viewport repair")
            assert browser.manager().get(first_id).screencasting is True

            # If a known visual change produces no damage frame at all, the
            # watchdog captures one first. A healthy silent stream must not pay
            # for a stop/start cycle merely because the page had no damage.
            silent_frames = len(frames)
            silent_casts = len(read_lines("screencast.jsonl"))
            silent_shots = len(read_lines("screenshots.jsonl"))
            await ws.send_json({"type": "navigate",
                                "url": "http://stub.invalid/no-frame"})
            await wait_for(
                lambda: len(read_lines("screenshots.jsonl")) > silent_shots,
                timeout=3, message="silent screencast recovery")
            await wait_for(lambda: len(frames) > silent_frames,
                           message="watchdog recovery frame")
            assert len(read_lines("screencast.jsonl")) == silent_casts

            # Title churn updates the toolbar but does not imply visual damage.
            # Several rapid target events collapse into one history read and
            # never arm the screenshot watchdog used for actual URL changes.
            instance = browser.manager().get(first_id)
            history_reads = len(read_lines("history.jsonl"))
            title_shots = len(read_lines("screenshots.jsonl"))
            for index in range(6):
                info = dict(instance.targets[instance.page_target])
                info["title"] = "dynamic title {}".format(index)
                instance._on_message({
                    "method": "Target.targetInfoChanged",
                    "params": {"targetInfo": info},
                })
            await wait_for(lambda: len(read_lines("history.jsonl")) > history_reads,
                           message="coalesced title navigation refresh")
            await asyncio.sleep(0.9)
            assert len(read_lines("history.jsonl")) == history_reads + 1
            assert len(read_lines("screenshots.jsonl")) == title_shots

            # A last-viewer detach schedules screencast shutdown. If a new
            # WebSocket attaches before that task runs, the stale shutdown
            # must not stop the replacement after its explicit first frame.
            await ws.close()
            await wait_for(
                lambda: browser.manager().get(first_id).viewer_count() == 0,
                message="browser viewer detach")
            await asyncio.wait_for(reader, timeout=2)
            texts, frames = [], []
            ws = await http.ws_connect(
                url + "/api/ws/browser/" + first_id, headers=headers)
            reader = asyncio.ensure_future(collect_ws(ws, texts, frames))
            await wait_for(lambda: frames, message="reconnected first frame")
            instance = browser.manager().get(first_id)
            stops_before = len(read_lines("screencast-stop.jsonl"))
            await instance._stop_screencast_if_idle()
            assert len(read_lines("screencast-stop.jsonl")) == stops_before
            live_frame_start = len(frames)
            await ws.send_json({"type": "reload"})
            await wait_for(lambda: len(frames) > live_frame_start,
                           message="live frame after stale viewer stop")

            async with http.get(url + "/api/browser/status", headers=headers) as r:
                running = await read_json(r)
                assert running["running"] is True and running["viewers"] == 1, running
                assert {item["id"] for item in running["instances"]} == \
                    {first_id, third_id}, running
                flow = next(item["frame_flow"] for item in running["instances"]
                            if item["id"] == first_id)
                assert flow["received"] > 0 and flow["captured"] > 0 and \
                    flow["forwarded"] > 0, flow
                assert flow["dropped_surface"] >= 2, flow
                assert flow["dropped"] == sum(
                    flow[key] for key in ("dropped_surface", "dropped_decode",
                                          "dropped_inactive", "dropped_backpressure")), flow
                assert flow["receive_hz"] >= 0 and flow["forward_hz"] >= 0, flow

            # Disabling stops every process, preserves the logical IDs, and
            # informs all attached viewers.
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": False}) as r:
                disabled = await read_json(r)
                assert r.status == 200 and disabled["enabled"] is False, disabled
                assert {item["id"] for item in disabled["instances"]} == \
                    {first_id, third_id}, disabled
                assert all(item["running"] is False for item in disabled["instances"]), \
                    disabled
            await wait_for(lambda: any(t.get("type") == "gone" for t in texts),
                           message="gone notice after disable")
            await ws.close()
            reader.cancel()
            async with http.get(url + "/api/browser/status", headers=headers) as r:
                stopped = await read_json(r)
                assert stopped["running"] is False, stopped

            # a disabled node refuses viewers outright
            ws2 = await http.ws_connect(
                url + "/api/ws/browser/" + first_id, headers=headers)
            refused = await ws2.receive()
            refused_payload = json.loads(refused.data)
            assert refused.type == aiohttp.WSMsgType.TEXT and \
                "disabled" in refused_payload["text"] and \
                refused_payload["terminal"] is True
            await ws2.close()

            # Re-enabling does not eagerly launch any instance.
            async with http.post(url + "/api/browser/enabled", headers=headers,
                                 json={"enabled": True}) as r:
                assert r.status == 200

            # Concurrent unqualified requests from one session converge on one
            # default rather than racing two browser processes into existence.
            race_sid = db.create_session(
                "browser race", "codex", str(BASE), "", "", "#7aa2f7",
                "danger-full-access")
            raced = await asyncio.gather(
                browser.manager().agent_browser(race_sid),
                browser.manager().agent_browser(race_sid))
            assert raced[0] is raced[1], [item.browser_id for item in raced]
            await browser.manager().close(raced[0].browser_id, "race test complete")

            # Every enabled turn gets a private stdio MCP descriptor. Both
            # engine drivers place it in their native configuration, including
            # resume commands (the path used after an engine handoff).
            agent_sid = db.create_session(
                "browser agent", "codex", str(BASE), "", "", "#7aa2f7",
                "danger-full-access")
            turn_id = "browser-turn-1"
            agent_hub = session_runner.hub(agent_sid)
            agent_hub.status = "running"
            agent_hub._active_turn_id = turn_id
            session_capture = CaptureSocket()
            updates_capture = CaptureSocket()
            agent_hub.attach(session_capture)
            session_runner.updates_attach(updates_capture)
            custom_prompt = "Configured system guidance for every turn."
            policy = browser_agent.AGENT_SELECTION_POLICY + \
                " Keep the shared browser visible while interacting."
            config.set_system_prompts(
                custom_prompt, config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT,
                policy, config.DEFAULT_TERMINAL_SYSTEM_PROMPT,
                config.DEFAULT_SPAWN_SYSTEM_PROMPT)
            descriptor = browser_agent.turn_mcp(agent_sid, turn_id)
            assert descriptor and descriptor["name"] == "puppy_browser", descriptor
            assert descriptor["engine_guidance"] == policy
            assert "default for interactive web navigation" in policy
            assert "repository's own browser test suite" in policy
            assert "briefly state the concrete reason" in policy
            assert descriptor["env"]["PUPPY_BROWSER_SESSION_ID"] == str(agent_sid)
            assert descriptor["env"]["PUPPY_BROWSER_TURN_ID"] == turn_id
            expected_instructions = policy + " " + browser_agent.TOOL_INSTRUCTIONS
            assert Path(descriptor["env"]["PUPPY_BROWSER_SOCKET"]).stat().st_mode & 0o777 \
                == 0o600

            agent_session = db.get_session(agent_sid)
            claude_argv = ClaudeDriver().build_cmd(
                agent_session, True, "hello", "native-1", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            config_index = claude_argv.index("--mcp-config")
            claude_mcp = json.loads(claude_argv[config_index + 1])
            assert claude_mcp["mcpServers"]["puppy_browser"]["command"] == \
                descriptor["command"], claude_mcp
            guidance_index = claude_argv.index("--append-system-prompt")
            assert claude_argv[guidance_index + 1] == custom_prompt + "\n\n" + policy
            resumed = dict(agent_session, native_session_id="existing-native")
            claude_resume = ClaudeDriver().build_cmd(
                resumed, False, "again", "unused", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            assert "--resume" in claude_resume and "--mcp-config" in claude_resume
            resume_guidance = claude_resume.index("--append-system-prompt")
            assert claude_resume[resume_guidance + 1] == custom_prompt + "\n\n" + policy
            claude_without_browser = ClaudeDriver().build_cmd(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt=custom_prompt)
            assert "--mcp-config" not in claude_without_browser
            plain_guidance = claude_without_browser.index("--append-system-prompt")
            assert claude_without_browser[plain_guidance + 1] == custom_prompt
            claude_without_guidance = ClaudeDriver().build_cmd(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt="")
            assert "--append-system-prompt" not in claude_without_guidance

            codex_first = CodexDriver().build_cmd(
                agent_session, True, "hello", "native-2", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            assert codex_first[:3] == ["codex", "app-server", "--stdio"]
            assert "hello" not in codex_first
            codex_first_ctx = CodexDriver().turn_context(
                agent_session, True, "hello", "native-2", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            assert codex_first_ctx["prompt"].startswith(
                "<puppy_system_prompt>\n" + custom_prompt +
                "\n</puppy_system_prompt>\n\n<puppy_browser_policy>\n" + policy +
                "\n</puppy_browser_policy>\n\n")
            assert codex_first_ctx["prompt"].endswith("\n\nhello")
            codex_argv = CodexDriver().build_cmd(
                resumed, False, "again", "unused", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            mcp_options = [value for value in codex_argv
                           if "mcp_servers.puppy_browser" in value]
            assert any(".command=" in value for value in mcp_options), codex_argv
            assert any("PUPPY_BROWSER_TURN_ID" in value for value in mcp_options), codex_argv
            codex_ctx = CodexDriver().turn_context(
                resumed, False, "again", "unused", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            assert codex_ctx["native_session_id"] == "existing-native"
            assert codex_ctx["prompt"].startswith(
                "<puppy_system_prompt>\n" + custom_prompt + "\n</puppy_system_prompt>\n\n" +
                "<puppy_browser_policy>\n" + policy)
            assert codex_ctx["prompt"].endswith("\n\nagain")
            assert codex_ctx["prompt"].count(policy) == 1
            codex_without_browser = CodexDriver().build_cmd(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt=custom_prompt)
            codex_plain_ctx = CodexDriver().turn_context(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt=custom_prompt)
            assert codex_plain_ctx["prompt"] == (
                "<puppy_system_prompt>\n" + custom_prompt +
                "\n</puppy_system_prompt>\n\nplain")
            assert not any("mcp_servers.puppy_browser" in value
                           for value in codex_without_browser)
            codex_without_guidance = CodexDriver().turn_context(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt="")
            assert codex_without_guidance["prompt"] == "plain"

            opencode = OpenCodeDriver()
            opencode_env = opencode.build_env(
                agent_session, True, "hello", "native-3", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            inline = json.loads(opencode_env["OPENCODE_CONFIG_CONTENT"])
            opencode_agent = inline["agent"]["puppy_console"]
            assert inline["default_agent"] == "puppy_console"
            assert opencode_agent["prompt"] == custom_prompt + "\n\n" + policy
            opencode_ctx = opencode.turn_context(
                agent_session, True, "hello", "native-3", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            assert opencode_ctx["mcp_servers"][0]["command"] == descriptor["command"]
            assert any(item["name"] == "PUPPY_BROWSER_TURN_ID" and
                       item["value"] == turn_id
                       for item in opencode_ctx["mcp_servers"][0]["env"])
            opencode_plain = json.loads(opencode.build_env(
                agent_session, True, "plain", "native-4", browser_mcp=None,
                system_prompt=custom_prompt)["OPENCODE_CONFIG_CONTENT"])
            assert opencode_plain["agent"]["puppy_console"]["prompt"] == custom_prompt
            opencode_without_guidance = json.loads(opencode.build_env(
                agent_session, True, "plain", "native-5", browser_mcp=None,
                system_prompt="")["OPENCODE_CONFIG_CONTENT"])
            assert "prompt" not in opencode_without_guidance["agent"]["puppy_console"]

            mcp_env = dict(os.environ)
            mcp_env.update(descriptor["env"])
            base64_stub = base64.b64encode(b"stub-jpeg-frame-bytes").decode()
            mcp = await asyncio.create_subprocess_exec(
                descriptor["command"], *descriptor["args"], env=mcp_env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                initialized = await mcp_request(mcp, 1, "initialize", {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "browser-test", "version": "1"},
                })
                assert initialized["result"]["serverInfo"]["version"] == "4"
                instructions = initialized["result"].get("instructions", "")
                assert instructions == expected_instructions
                assert policy in instructions and \
                    "default for interactive web navigation" in instructions and \
                    "Do not launch or install Chrome" in instructions and \
                    "fresh, isolated" in instructions and \
                    "without taking focus" in instructions and \
                    "explicitly asks" in instructions and \
                    "wait_for" in instructions and \
                    "temporary" in instructions
                listed = await mcp_request(mcp, 2, "tools/list")
                listed_tools = listed["result"]["tools"]
                tool_names = [tool["name"] for tool in listed_tools]
                assert tool_names[:3] == ["snapshot", "navigate", "screenshot"]
                assert tool_names[-1] == "new_browser"
                tools = {tool["name"]: tool for tool in listed_tools}
                assert {"new_browser", "snapshot", "screenshot", "navigate",
                        "inspect_element", "click", "type", "press", "hover",
                        "select", "check", "scroll", "wait_for", "back",
                        "forward", "reload", "pages", "switch_page",
                        "console_messages", "network_failures", "upload_file",
                        "downloads", "read_download"} <= set(tools)
                assert "evaluate" not in tools and "cdp" not in tools
                assert "shared, user-visible" in tools["snapshot"]["description"]
                assert "shared, user-visible" in tools["navigate"]["description"]
                assert "additional isolated" in tools["new_browser"]["description"]
                assert "reported viewport size" in tools["click"]["description"]
                assert "browser_id" in tools["snapshot"]["inputSchema"]["properties"]
                assert "browser_id" not in tools["new_browser"]["inputSchema"]["properties"]
                assert "file_path" not in tools["upload_file"]["inputSchema"]["properties"]
                assert tools["upload_file"]["inputSchema"]["required"] == ["upload_id"]
                assert tools["upload_file"]["inputSchema"]["properties"]["upload_id"][
                    "pattern"] == "^[0-9]{13}-[0-9a-f]{10}$"

                snapshot = await mcp_request(mcp, 3, "tools/call", {
                    "name": "snapshot", "arguments": {}})
                assert snapshot["result"]["isError"] is False, snapshot
                snapshot_text = next(item["text"] for item in
                                     snapshot["result"]["content"]
                                     if item["type"] == "text")
                assert "[b1] button" in snapshot_text and \
                    "[b2] textbox" in snapshot_text, snapshot_text
                session_activity = await wait_for(
                    lambda: [item for item in session_capture.messages
                             if item.get("type") == "browser_activity"],
                    message="session browser activity event")
                update_activity = await wait_for(
                    lambda: [item for item in updates_capture.messages
                             if item.get("type") == "browser_activity"],
                    message="global browser activity event")
                agent_id = session_activity[0]["browser_id"]
                assert agent_id not in {first_id, closed_id, third_id}
                assert snapshot_text.startswith("Browser {}\n".format(agent_id)), snapshot_text
                assert update_activity[0]["browser_id"] == agent_id

                # Large pages can be searched or scoped without exposing raw
                # CDP/JavaScript. Refs intentionally belong to the latest tree.
                searched = await mcp_request(mcp, 101, "tools/call", {
                    "name": "snapshot", "arguments": {"query": "Email"}})
                searched_text = searched["result"]["content"][0]["text"]
                assert "textbox \"Email\"" in searched_text and \
                    "button \"Continue\"" not in searched_text, searched_text
                scoped = await mcp_request(mcp, 102, "tools/call", {
                    "name": "snapshot", "arguments": {"scope_ref": "b1"}})
                scoped_text = scoped["result"]["content"][0]["text"]
                assert "scoped to b1" in scoped_text and \
                    "textbox \"Email\"" in scoped_text, scoped_text
                fresh = await mcp_request(mcp, 103, "tools/call", {
                    "name": "snapshot", "arguments": {}})
                fresh_text = fresh["result"]["content"][0]["text"]
                assert "[b3] combobox" in fresh_text and \
                    "[b4] checkbox" in fresh_text and \
                    "[b5] button \"Upload file\"" in fresh_text, fresh_text
                ax_methods = [item["method"] for item in read_lines("ax.jsonl")]
                assert "Accessibility.getRootAXNode" in ax_methods, ax_methods
                assert "Accessibility.getChildAXNodes" in ax_methods, ax_methods
                assert "Accessibility.getPartialAXTree" in ax_methods, ax_methods
                assert "Accessibility.getFullAXTree" not in ax_methods, ax_methods
                chooser_intercepts = await wait_for(
                    lambda: read_lines("file-chooser-intercept.jsonl"),
                    message="file chooser interception")
                assert chooser_intercepts[-1] == {"enabled": True}, chooser_intercepts

                # A browser file input accepts only an upload already owned by
                # this chat. The tool never receives or accepts an arbitrary
                # filesystem path, and a different chat's upload is invisible.
                upload_bytes = b"user supplied browser upload\n"
                async with http.post(
                        url + "/api/sessions/{}/upload".format(agent_sid),
                        headers={**headers, "Content-Type": "text/plain",
                                 "X-Puppy-Filename": "agent-document.txt",
                                 "X-Puppy-Size": str(len(upload_bytes))},
                        data=upload_bytes) as r:
                    uploaded = await read_json(r)
                    assert r.status == 200, uploaded
                uploaded_to_page = await mcp_request(mcp, 127, "tools/call", {
                    "name": "upload_file", "arguments": {
                        "ref": "b5", "upload_id": uploaded["upload_id"]}})
                upload_text = uploaded_to_page["result"]["content"][0]["text"]
                assert uploaded_to_page["result"]["isError"] is False, uploaded_to_page
                assert "user-provided session upload" in upload_text and \
                    "agent-document.txt" in upload_text and \
                    "no page-level outcome wait was needed" in upload_text, upload_text
                file_inputs = read_lines("file-inputs.jsonl")
                assert file_inputs and file_inputs[-1]["backendNodeId"] == 14, file_inputs
                assert file_inputs[-1]["files"] == [uploaded["path"]], file_inputs[-1]
                agent_instance = browser.manager().get(agent_id)
                agent_instance._on_message({
                    "method": "Page.fileChooserOpened",
                    "sessionId": agent_instance.page_session,
                    "params": {"backendNodeId": 14, "mode": "selectSingle"},
                })
                chooser_upload = await mcp_request(mcp, 133, "tools/call", {
                    "name": "upload_file", "arguments": {
                        "upload_id": uploaded["upload_id"]}})
                assert chooser_upload["result"]["isError"] is False, chooser_upload
                assert "open file chooser" in \
                    chooser_upload["result"]["content"][0]["text"]
                assert agent_instance.agent_file_chooser is None
                stale_chooser = await mcp_request(mcp, 134, "tools/call", {
                    "name": "upload_file", "arguments": {
                        "upload_id": uploaded["upload_id"]}})
                assert stale_chooser["result"]["isError"] is True, stale_chooser
                assert "click the page's upload control" in \
                    stale_chooser["result"]["content"][0]["text"]

                async with http.post(
                        url + "/api/sessions/{}/upload".format(handoff_sid),
                        headers={**headers, "Content-Type": "text/plain",
                                 "X-Puppy-Filename": "other-chat.txt",
                                 "X-Puppy-Size": "5"}, data=b"other") as r:
                    other_upload = await read_json(r)
                    assert r.status == 200, other_upload
                cross_chat_upload = await mcp_request(mcp, 128, "tools/call", {
                    "name": "upload_file", "arguments": {
                        "ref": "b5", "upload_id": other_upload["upload_id"]}})
                assert cross_chat_upload["result"]["isError"] is True, cross_chat_upload
                assert "unavailable in this chat" in \
                    cross_chat_upload["result"]["content"][0]["text"]

                # Downloads are scoped to this logical Browser. Direct regular
                # files receive temporary refs; partials remain visible but
                # unreadable, and symlinks never enter the catalog.
                download_root = Path(browser._instance_root(agent_id)) / "downloads"
                note_path = download_root / "agent-note.txt"
                note_path.write_text("downloaded result\n", encoding="utf-8")
                image_path = download_root / "pixel.png"
                image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"stub-png")
                (download_root / "pending.bin.crdownload").write_bytes(b"partial")
                try:
                    os.symlink("/etc/passwd", str(download_root / "escape.txt"))
                except (OSError, NotImplementedError):
                    pass
                downloads = await mcp_request(mcp, 129, "tools/call", {
                    "name": "downloads", "arguments": {}})
                downloads_text = downloads["result"]["content"][0]["text"]
                assert "UNTRUSTED BROWSER DOWNLOADS" in downloads_text
                assert "agent-note.txt" in downloads_text and "pixel.png" in downloads_text
                assert "[in progress]" in downloads_text and \
                    "pending.bin.crdownload" in downloads_text
                assert "escape.txt" not in downloads_text
                note_line = next(line for line in downloads_text.splitlines()
                                 if "agent-note.txt" in line)
                note_ref = note_line.split("[", 1)[1].split("]", 1)[0]
                read_note = await mcp_request(mcp, 130, "tools/call", {
                    "name": "read_download", "arguments": {"download_ref": note_ref}})
                read_note_text = read_note["result"]["content"][0]["text"]
                assert "UNTRUSTED BROWSER DOWNLOAD" in read_note_text and \
                    "downloaded result" in read_note_text and \
                    str(note_path) in read_note_text, read_note_text
                image_line = next(line for line in downloads_text.splitlines()
                                  if "pixel.png" in line)
                image_ref = image_line.split("[", 1)[1].split("]", 1)[0]
                read_image = await mcp_request(mcp, 131, "tools/call", {
                    "name": "read_download", "arguments": {"download_ref": image_ref}})
                assert any(item.get("type") == "image" and
                           item.get("mimeType") == "image/png" for item in
                           read_image["result"]["content"]), read_image
                note_path.write_text("changed after listing\n", encoding="utf-8")
                stale_download = await mcp_request(mcp, 132, "tools/call", {
                    "name": "read_download", "arguments": {"download_ref": note_ref}})
                assert stale_download["result"]["isError"] is True, stale_download
                assert "changed" in stale_download["result"]["content"][0]["text"]

                inspected = await mcp_request(mcp, 104, "tools/call", {
                    "name": "inspect_element", "arguments": {"ref": "b1"}})
                inspected_text = inspected["result"]["content"][0]["text"]
                assert "Box: x=100.0 y=40.0 width=200.0 height=40.0" in inspected_text
                assert "font-family: \"Stub Sans\"" in inspected_text
                assert "https://example.test/path" in inspected_text
                assert "user:pass" not in inspected_text and \
                    "token=secret" not in inspected_text

                shot = await mcp_request(mcp, 4, "tools/call", {
                    "name": "screenshot", "arguments": {}})
                assert any(item.get("type") == "image" and
                           item.get("data") == base64_stub for item in
                           shot["result"]["content"]), shot
                full_shot = await mcp_request(mcp, 105, "tools/call", {
                    "name": "screenshot", "arguments": {
                        "format": "png", "full_page": True}})
                assert any(item.get("type") == "image" and
                           item.get("mimeType") == "image/png"
                           for item in full_shot["result"]["content"]), full_shot
                element_shot = await mcp_request(mcp, 106, "tools/call", {
                    "name": "screenshot", "arguments": {"ref": "b2"}})
                assert element_shot["result"]["isError"] is False, element_shot
                captures = read_lines("screenshots.jsonl")
                assert any(item.get("format") == "png" and
                           item.get("clip", {}).get("height") == 1800
                           for item in captures), captures
                assert any(item.get("clip", {}).get("width") == 204 and
                           item.get("clip", {}).get("height") == 44
                           for item in captures), captures

                waited = await mcp_request(mcp, 107, "tools/call", {
                    "name": "wait_for", "arguments": {
                        "text": "Ready", "text_absent": "Missing",
                        "url_contains": "about:blank", "timeout_ms": 100}})
                assert waited["result"]["isError"] is False, waited
                assert "Observed text 'Ready'" in waited["result"]["content"][0]["text"]
                timed_out = await mcp_request(mcp, 122, "tools/call", {
                    "name": "wait_for", "arguments": {
                        "text": "Never present", "timeout_ms": 0}})
                assert timed_out["result"]["isError"] is True, timed_out
                assert "timed out after 0 ms" in timed_out["result"]["content"][0]["text"]
                conflicting_shot = await mcp_request(mcp, 123, "tools/call", {
                    "name": "screenshot", "arguments": {
                        "full_page": True, "ref": "b1"}})
                assert conflicting_shot["result"]["isError"] is True, conflicting_shot
                invalid_select = await mcp_request(mcp, 124, "tools/call", {
                    "name": "select", "arguments": {
                        "ref": "b3", "value": "pl", "label": "Poland"}})
                assert invalid_select["result"]["isError"] is True, invalid_select
                invalid_check = await mcp_request(mcp, 125, "tools/call", {
                    "name": "check", "arguments": {
                        "ref": "b4", "checked": "yes"}})
                assert invalid_check["result"]["isError"] is True, invalid_check
                excessive_wait = await mcp_request(mcp, 126, "tools/call", {
                    "name": "navigate", "arguments": {
                        "url": "example.test", "timeout_ms": 30000,
                        "wait_ms": 10000,
                        "wait_for": {"text": "Ready", "timeout_ms": 30000}}})
                assert excessive_wait["result"]["isError"] is True, excessive_wait
                assert "combined navigation waits" in \
                    excessive_wait["result"]["content"][0]["text"]

                clicked = await mcp_request(mcp, 5, "tools/call", {
                    "name": "click", "arguments": {
                        "ref": "b1", "wait_for": {"text": "Ready", "timeout_ms": 100},
                        "include_snapshot": True}})
                clicked_text = clicked["result"]["content"][0]["text"]
                assert "Dispatched a left click" in clicked_text and \
                    "Outcome: Observed text 'Ready'" in clicked_text and \
                    "Accessibility snapshot" in clicked_text, clicked_text
                typed = await mcp_request(mcp, 6, "tools/call", {
                    "name": "type", "arguments": {
                        "ref": "b2", "text": "agent text", "clear": True,
                        "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                typed_text = typed["result"]["content"][0]["text"]
                assert "Verified: b2 reports a value length of 10" in typed_text, typed_text
                all_evaluations = [item["expression"] for item in
                                   read_lines("evaluate.jsonl")]
                assert any("innerText" in expression for expression in all_evaluations), \
                    "text waits must inspect page text"
                evaluation_start = len(all_evaluations)
                fast_started = asyncio.get_event_loop().time()
                typed_fast = await mcp_request(mcp, 135, "tools/call", {
                    "name": "type", "arguments": {
                        "ref": "b2", "text": "fast path", "clear": True}})
                fast_elapsed = asyncio.get_event_loop().time() - fast_started
                typed_fast_text = typed_fast["result"]["content"][0]["text"]
                assert "no page-level outcome wait was needed" in typed_fast_text, \
                    typed_fast_text
                assert fast_elapsed < 0.55, fast_elapsed
                fast_evaluations = [item["expression"] for item in
                                    read_lines("evaluate.jsonl")[evaluation_start:]]
                assert fast_evaluations and \
                    all("innerText" not in expression for expression in fast_evaluations), \
                    fast_evaluations
                hovered = await mcp_request(mcp, 108, "tools/call", {
                    "name": "hover", "arguments": {
                        "ref": "b1", "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "Moved the pointer over b1" in hovered["result"]["content"][0]["text"]
                selected_option = await mcp_request(mcp, 109, "tools/call", {
                    "name": "select", "arguments": {
                        "ref": "b3", "label": "Poland",
                        "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "selected \"Poland\"" in \
                    selected_option["result"]["content"][0]["text"]
                checked = await mcp_request(mcp, 110, "tools/call", {
                    "name": "check", "arguments": {
                        "ref": "b4", "checked": False,
                        "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "Verified: b4 is unchecked" in \
                    checked["result"]["content"][0]["text"]
                pressed = await mcp_request(mcp, 111, "tools/call", {
                    "name": "press", "arguments": {
                        "key": "Enter", "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "Dispatched key Enter" in pressed["result"]["content"][0]["text"]
                scrolled = await mcp_request(mcp, 112, "tools/call", {
                    "name": "scroll", "arguments": {
                        "delta_y": 320, "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "Dispatched a scroll" in scrolled["result"]["content"][0]["text"] and \
                    "Verified: the document scroll position changed" in \
                    scrolled["result"]["content"][0]["text"]
                fast_scroll = await mcp_request(mcp, 136, "tools/call", {
                    "name": "scroll", "arguments": {"delta_y": 40}})
                assert "no page-level outcome wait was needed" in \
                    fast_scroll["result"]["content"][0]["text"] and \
                    "Verified: the document scroll position changed" in \
                    fast_scroll["result"]["content"][0]["text"]

                navigated = await mcp_request(mcp, 7, "tools/call", {
                    "name": "navigate", "arguments": {
                        "url": "router.lan/status", "wait_ms": 0,
                        "wait_for": {"url_contains": "router.lan", "timeout_ms": 1000}}})
                navigated_text = navigated["result"]["content"][0]["text"]
                assert "Observed document domcontentloaded" in navigated_text and \
                    "Observed URL containing 'router.lan'" in navigated_text, navigated_text
                assert len([item for item in session_capture.messages
                            if item.get("type") == "browser_activity"]) == 1
                assert len([item for item in updates_capture.messages
                            if item.get("type") == "browser_activity"]) == 1
                assert not db.get_events(agent_sid), "browser activity must stay ephemeral"

                agent_inputs = read_lines("input.jsonl")
                assert any(item["params"].get("type") == "mousePressed" and
                           item["params"].get("x") == 200 and
                           item["params"].get("y") == 60 for item in agent_inputs), agent_inputs
                assert any(item["method"] == "Input.insertText" and
                           item["params"].get("text") == "agent text"
                           for item in agent_inputs), agent_inputs
                assert any(item["method"] == "DOM.focus" and
                           item["params"].get("backendNodeId") == 11
                           for item in read_lines("dom.jsonl"))
                assert read_lines("navigations.jsonl")[-1]["url"] == \
                    "http://router.lan/status"
                functions = read_lines("agent-functions.jsonl")
                assert any(item["backend"] == 12 and item["arguments"] == [None, "Poland"]
                           for item in functions), functions
                assert any(item["backend"] == 13 and item["arguments"] == [False]
                           for item in functions), functions

                # Back/forward/reload wait for observable document lifecycle
                # events instead of reporting success from dispatch alone.
                went_forward = await mcp_request(mcp, 113, "tools/call", {
                    "name": "forward", "arguments": {}})
                assert "new document became interactive" in \
                    went_forward["result"]["content"][0]["text"]
                went_back = await mcp_request(mcp, 114, "tools/call", {
                    "name": "back", "arguments": {}})
                assert "new document became interactive" in \
                    went_back["result"]["content"][0]["text"]
                reloaded = await mcp_request(mcp, 115, "tools/call", {
                    "name": "reload", "arguments": {}})
                assert "Observed document domcontentloaded" in \
                    reloaded["result"]["content"][0]["text"]

                # Diagnostics are captured per attached page, bounded, clearly
                # untrusted, and strip credentials/query/fragment from URLs.
                diagnostic_session = agent_instance.page_session
                agent_instance._on_message({
                    "method": "Runtime.exceptionThrown", "sessionId": diagnostic_session,
                    "params": {"exceptionDetails": {
                        "text": "Uncaught", "lineNumber": 4,
                        "url": "https://user:pass@example.test/app?token=secret#x",
                        "exception": {"description": "TypeError: stub failure"}}}})
                agent_instance._on_message({
                    "method": "Network.requestWillBeSent", "sessionId": diagnostic_session,
                    "params": {"requestId": "r1", "request": {
                        "method": "POST",
                        "url": "https://user:pass@example.test/api?token=secret#x"}}})
                agent_instance._on_message({
                    "method": "Network.responseReceived", "sessionId": diagnostic_session,
                    "params": {"requestId": "r1", "type": "Fetch",
                               "response": {"status": 503}}})
                console = await mcp_request(mcp, 116, "tools/call", {
                    "name": "console_messages", "arguments": {
                        "level": "error", "clear": True}})
                console_text = console["result"]["content"][0]["text"]
                assert "UNTRUSTED PAGE CONSOLE" in console_text and \
                    "TypeError: stub failure" in console_text and \
                    "https://example.test/app:5" in console_text
                assert "user:pass" not in console_text and "token=secret" not in console_text
                network = await mcp_request(mcp, 117, "tools/call", {
                    "name": "network_failures", "arguments": {"clear": True}})
                network_text = network["result"]["content"][0]["text"]
                assert "UNTRUSTED PAGE NETWORK" in network_text and \
                    "[HTTP 503] POST https://example.test/api" in network_text
                assert "user:pass" not in network_text and "token=secret" not in network_text
                cleared = await mcp_request(mcp, 118, "tools/call", {
                    "name": "network_failures", "arguments": {}})
                assert "No failed requests" in cleared["result"]["content"][0]["text"]

                # Pages are limited to this logical Browser and use temporary
                # refs; switching clears element refs and attaches the viewer.
                agent_instance.targets["stub-popup-2"] = {
                    "targetId": "stub-popup-2", "type": "page", "title": "popup",
                    "url": "https://popup.example.test/",
                    "openerId": "stub-page-1"}
                pages = await mcp_request(mcp, 119, "tools/call", {
                    "name": "pages", "arguments": {}})
                pages_text = pages["result"]["content"][0]["text"]
                assert "[p1] navigated (current)" in pages_text and \
                    "[p2] popup" in pages_text, pages_text
                switched = await mcp_request(mcp, 120, "tools/call", {
                    "name": "switch_page", "arguments": {"page_ref": "p2"}})
                switched_text = switched["result"]["content"][0]["text"]
                assert "Switched to p2" in switched_text and \
                    "https://popup.example.test/" in switched_text, switched_text
                stale_ref = await mcp_request(mcp, 121, "tools/call", {
                    "name": "click", "arguments": {"ref": "b1"}})
                assert stale_ref["result"]["isError"] is True, stale_ref
                assert "stale element ref" in stale_ref["result"]["content"][0]["text"]

                # A new instance is created only through the explicit tool;
                # subsequent unqualified calls bind to it across the session.
                opened = await mcp_request(mcp, 8, "tools/call", {
                    "name": "new_browser", "arguments": {}})
                opened_text = opened["result"]["content"][0]["text"]
                activities = await wait_for(
                    lambda: [item for item in session_capture.messages
                             if item.get("type") == "browser_activity"]
                    if len([item for item in session_capture.messages
                            if item.get("type") == "browser_activity"]) >= 2 else None,
                    message="second identified browser event")
                second_agent_id = activities[-1]["browser_id"]
                assert second_agent_id != agent_id and second_agent_id in opened_text
                continued = await mcp_request(mcp, 9, "tools/call", {
                    "name": "screenshot", "arguments": {}})
                continued_text = next(item["text"] for item in
                                      continued["result"]["content"]
                                      if item["type"] == "text")
                assert continued_text.startswith(
                    "Browser {}\n".format(second_agent_id)), continued_text

                # Closing the session's current browser retires the ID. The
                # next unqualified call detects that and creates a fresh one.
                async with http.delete(
                        url + "/api/browser/instances/" + second_agent_id,
                        headers=headers) as r:
                    assert r.status == 200, await read_json(r)
                replacement = await mcp_request(mcp, 10, "tools/call", {
                    "name": "snapshot", "arguments": {}})
                replacement_text = replacement["result"]["content"][0]["text"]
                activities = await wait_for(
                    lambda: [item for item in session_capture.messages
                             if item.get("type") == "browser_activity"]
                    if len([item for item in session_capture.messages
                            if item.get("type") == "browser_activity"]) >= 3 else None,
                    message="replacement browser event")
                replacement_id = activities[-1]["browser_id"]
                assert replacement_id not in {agent_id, second_agent_id}
                assert replacement_text.startswith(
                    "Browser {}\n".format(replacement_id)), replacement_text

                # A user-named ID selects that existing browser, then remains
                # the session default. Closed/unknown named IDs fail clearly.
                selected = await mcp_request(mcp, 11, "tools/call", {
                    "name": "snapshot", "arguments": {"browser_id": first_id}})
                selected_text = selected["result"]["content"][0]["text"]
                assert selected_text.startswith("Browser {}\n".format(first_id)), selected_text
                selected_again = await mcp_request(mcp, 12, "tools/call", {
                    "name": "screenshot", "arguments": {}})
                assert selected_again["result"]["content"][0]["text"].startswith(
                    "Browser {}\n".format(first_id)), selected_again
                missing = await mcp_request(mcp, 13, "tools/call", {
                    "name": "snapshot", "arguments": {"browser_id": closed_id}})
                assert missing["result"]["isError"] is True, missing
                assert "closed or unknown" in missing["result"]["content"][0]["text"]

                # A subprocess from a completed/replaced turn cannot keep
                # driving any browser even if the next turn is running.
                agent_hub._active_turn_id = "browser-turn-2"
                stale = await mcp_request(mcp, 14, "tools/call", {
                    "name": "reload", "arguments": {}})
                assert stale["result"]["isError"] is True, stale
                assert "no longer running" in stale["result"]["content"][0]["text"]
            finally:
                if mcp.stdin:
                    mcp.stdin.close()
                try:
                    await asyncio.wait_for(mcp.wait(), timeout=5)
                except asyncio.TimeoutError:
                    mcp.kill()
                    await mcp.wait()
                agent_hub.detach(session_capture)
                session_runner.updates_detach(updates_capture)
                agent_hub.status = "idle"

            ui_source = (BASE / "puppy" / "static" / "app.js").read_text()
            css_source = (BASE / "puppy" / "static" / "app.css").read_text()
            check_free_identifiers(ui_source)
            check_static_template_styles(ui_source)
            check_reconnect_status(ui_source, css_source)
            check_backend_shutdown_notice(ui_source)
            check_offline_sidebar_sessions(ui_source, css_source)
            check_controller_backend_pooling(ui_source)
            check_backend_last_known_settings(ui_source, css_source)
            check_interrupted_completion(ui_source)
            check_thinking_icons(ui_source)
            check_prompt_status_animation(ui_source, css_source)
            check_backend_editor(ui_source, css_source)
            check_drawer_drag(ui_source)
            check_responsive_drawer_chrome(css_source)
            check_browser_viewport(ui_source)
            check_browser_transport_controls(ui_source)
            check_browser_theme_fanout(ui_source)
            check_session_draft_sync(ui_source)
            check_transcript_batching(ui_source)
            check_browser_handoff_ui(ui_source, css_source)
            check_desktop_side_drag(ui_source)
            check_user_message_copy(ui_source)
            check_queue_controls_ui(ui_source, css_source)
            check_active_turn_steering_ui(ui_source, css_source)
            check_system_prompt_settings(ui_source, css_source)
            check_remote_workspace_picker(ui_source, css_source)
            check_opencode_chat_models(ui_source, css_source)
            check_session_provider_marks(css_source)
            check_sidebar_icon_alignment(css_source)
            check_compact_control_alignment(ui_source, css_source)
            check_session_activity_clock(ui_source, css_source)
            check_sidebar_footer_buttons(ui_source, css_source)
            check_toast_touch_swipe(ui_source, css_source)
            check_status_header_activation(ui_source, css_source)
            check_shared_node_order(ui_source, css_source)
            check_flat_session_list(ui_source, css_source)
            check_sticky_activity_promotions(ui_source, css_source)
            check_switch_engine_initial_selection(ui_source)
            check_engine_picker_alignment(css_source)
            check_browser_chip_order(ui_source)
            check_composer_mentions(ui_source, css_source)
            check_chat_status_bar_layout(ui_source, css_source)
            check_backend_name_single_activation(ui_source)
            check_browser_disable_closes_scoped_tabs(ui_source)
            check_browser_loading_ui(ui_source, css_source)
            check_shared_storage_settings_ui(ui_source, css_source)
            check_quota_math(ui_source)
            # one checkbox face app-wide: a native checkbox is painted by the
            # browser, ignores the theme and differs per platform, so the form
            # input and the drawn menu mark share one rule and one tick path
            css_source = (BASE / "puppy" / "static" / "app.css").read_text()
            assert ".check input[type=checkbox],\n.menu-check-mark{" in css_source
            assert "-webkit-appearance:none;appearance:none" in css_source
            assert css_source.count("--check-tick:url(") == 1
            # Engine-status disclosures use the sidebar's 240ms slide/fade
            # helper. They are settled (and truly hidden) at rest, with
            # transitions attached only for a user-triggered toggle so a
            # restored collapse cannot animate during the first paint.
            assert "setDisclosureCollapsed(body, isCollapsed, animate);" in ui_source
            assert "sync(true);" in ui_source
            assert ui_source.count("SLIDE_MOTION_MS + 40") == 2
            assert ".foot-engine-body[hidden]{display:none}" in css_source
            assert ".foot-engine-body.disclosure-animating{" in css_source
            assert "--slide-time:.24s;--slide-fade-time:.2s;" in css_source
            assert ("transition:height var(--slide-time) var(--ease)," +
                    "opacity var(--slide-fade-time) var(--ease),") in css_source
            assert ("transition:width var(--slide-time) var(--ease)," +
                    "opacity var(--slide-fade-time) var(--ease);") in css_source
            assert "@media (prefers-reduced-motion:reduce){" in css_source
            # A resumed/online phone wakes any session socket whose timer was
            # frozen in the background. Successful transport evidence clears
            # only the overlay; it never overwrites model activity again.
            assert ui_source.count("wakeSessionConnections();") == 2
            assert ui_source.count("wakeRemoteUpdateConnections();") == 2
            assert ui_source.count("this.setReconnecting(false);") == 2
            assert "this.setReconnecting(true);" in ui_source
            assert 'this.setStatus("Connection lost' not in ui_source
            # The metadata strip already scrolls horizontally. Every pill must
            # keep its full intrinsic width rather than inheriting the generic
            # 160px phone cap and clipping its value.
            assert ".chat-meta-scroll .chip{max-width:none}" in css_source
            # Compatible headless nodes get one lightweight list watcher. Its
            # explicit lifecycle event, not an ordinary socket close, is what
            # retires cached running state immediately.
            assert 'new WebSocket(wsUrl(bid, "ws/updates"))' in ui_source
            assert 'message.type !== "node_stopping"' in ui_source
            assert 'backend.capabilities.includes("shutdown-notice")' in ui_source
            assert "if (state.remoteStopping[bid] && !recoveredFromStopping) return;" in ui_source
            # The scroll container is the touch-action boundary on Chromium;
            # without its own pan-y rule a close drag is cancelled before the
            # pointer stream reaches the drawer, while vertical scroll remains native.
            assert ".side-scroll{touch-action:pan-y pinch-zoom}" in css_source
            assert ".app.drawer-dragging .side{transition:none;will-change:transform}" in css_source
            # A hidden desktop sidebar leaves no visual strip, but the first
            # eight pixels expose a directional cursor and a captured drag.
            # During that drag both the column and workspace follow --side-w.
            assert ".app.side-collapsed .drawer-edge{" in css_source
            assert "width:8px;z-index:41;\n    cursor:e-resize;touch-action:none;" in css_source
            assert (".app.side-dragging.side-collapsed .side{" +
                    "width:var(--side-w);visibility:visible}") in css_source
            assert ".app.side-dragging .side{transition:none;will-change:width,opacity}" in css_source
            # Sent user prose has an overlaid square copy control. Its absolute
            # positioning cannot reflow the bubble, and attachment markers are
            # stripped before both rendering and copying.
            assert "if (text) n.appendChild(userMessageCopyButton(text));" in ui_source
            assert ".code-copy,.user-copy{" in css_source
            assert "position:absolute;z-index:1;top:6px;right:6px;width:27px;height:27px;" in css_source
            assert ".user-copy{opacity:.3}" in css_source
            assert ".code-copy:hover,.user-copy:hover{" in css_source
            # Complete status lines use one click; later desktop double-click
            # events cannot undo the first activation.
            assert "wireDisclosureSurface(name, disclosure);" not in ui_source
            assert ui_source.count("wireDisclosureSurface(head, disclosure);") == 1
            assert "if (event.detail > 1) return;" in ui_source
            assert ui_source.count("event.detail > 0 && event.detail % 2 === 0") == 1
            # A node disable already stops its processes. The settings response
            # and asynchronous state paths also retire only that node's tabs.
            assert ui_source.count("closeBrowserTabsForBackend(") == 4
            assert "if (result.enabled === false) closeBrowserTabsForBackend(bid);" in ui_source
            assert "if (node.browser && node.browser.enabled === false)" in ui_source
            # the head strip hides per session, and the toggle sits in BOTH the
            # head's own menu and the sidebar menu - hiding the head takes its
            # own opener with it, so the sidebar copy is the way back
            assert ui_source.count('menuCheckRow("Show status bar"') == 2
            assert "classList.toggle(\"meta-hidden\", !sessionShowsMeta(s))" in ui_source
            # decided before the view is attached, so a session that hides the
            # strip never paints it and then drops it on the first frame
            assert ("if (!sessionShowsMeta(findSessionMeta(this.tab.bid, this.tab.sid)))"
                    in ui_source)
            assert "syncSessionMetaVisibility();" in ui_source
            # The directory list sits in normal flow, so closing it on focus
            # loss reflows the page. Held until the press that took the focus
            # has landed, a click on OK is not swallowed by the close.
            assert "afterPointerRelease(() => {" in ui_source
            assert ui_source.count("let pointerPressed = false;") == 1
            assert "close();" in ui_source
            assert 'case "browser_activity"' in ui_source
            assert "`Browser ${id} @ ${backendName(bid)}`" in ui_source
            assert "{ activate: false, afterTabId: sessionTabId, sid }" in ui_source
            assert "browser/instances/${encodeURIComponent(closing.browserId)}" in ui_source
            # the owning chat advertises its live browsers as clickable bubbles
            assert "syncBrowserChips()" in ui_source
            assert 'type: "color_scheme", value: currentTheme()' in ui_source
            # The managed page's CSS viewport follows the visible browser
            # stage. Hidden/transient sizes are ignored, resize bursts are
            # collapsed, and teardown disconnects the observer.
            assert "this.stage.clientWidth" in ui_source
            assert "this.stage.clientHeight" in ui_source
            assert 'this.resizeObs = new ResizeObserver(() => this.queueViewport());' \
                in ui_source
            assert 'this.ws.send(JSON.stringify({ type: "viewport", ...size }));' \
                in ui_source
            assert "this.sendViewport(true);" in ui_source
            assert "if (this.resizeObs) { this.resizeObs.disconnect();" in ui_source
            # the settings switch is seeded before the availability probe, so it
            # cannot render off and then visibly flip on
            assert "input.checked = browserEnabledFor(bid);" in ui_source
            # session menus float on <body>: inside .chat-head's z-index:2
            # stacking context a split's divider and terminal painted over them
            assert "anchor.parentElement.appendChild(menu)" not in ui_source
            assert ui_source.count("document.body.appendChild(menu)") >= 5
            # one float at a time: every opener funnels through closeAllMenus,
            # so the static + menu cannot sit open beside a .dyn menu. The two
            # permitted closeMenusToggling mentions are its own definition and
            # the single call inside closeAllMenus.
            assert "function closeAllMenus(anchor)" in ui_source
            assert "if (closeAllMenus(button)) return;" in ui_source
            assert ui_source.count("closeMenusToggling(") == 2, \
                "menu openers must close through closeAllMenus"
            # The transcript decides whether to follow the tail BEFORE it
            # mutates: re-measuring after a streaming block becomes rendered
            # markdown reads that growth as the user having scrolled away.
            assert "atBottom()" in ui_source
            assert "scrollBottom(false)" not in ui_source, \
                "transcript scrolls must be forced or gated on a pre-sampled follow"
            assert ui_source.index("input.checked = browserEnabledFor(bid);") < \
                ui_source.index('status = await api(bid, "browser/status"')
            assert 'el("button", "chip browser")' in ui_source
            assert "t.browserGone !== true" in ui_source

            # ---- the address bar never blocks the viewer input loop ----
            async with http.post(url + "/api/browser/instances", headers=headers,
                                 json={}) as r:
                nav_id = (await read_json(r))["browser"]["id"]
            nav_manager = browser.manager().get(nav_id)
            nav_pid = nav_manager.pid
            nav_texts, nav_frames = [], []
            nav_ws = await http.ws_connect(
                url + "/api/ws/browser/" + nav_id, headers=headers)
            nav_reader = asyncio.ensure_future(collect_ws(nav_ws, nav_texts, nav_frames))
            await wait_for(lambda: any(t.get("type") == "status" for t in nav_texts),
                           message="nav-check status")
            hang_started = time.monotonic()
            await nav_ws.send_json({"type": "navigate",
                                    "url": "http://stub.invalid/slow-nav"})
            await nav_ws.send_json({"type": "insert_text", "text": "not-blocked"})
            await wait_for(
                lambda: any(line["method"] == "Input.insertText" and
                            line["params"].get("text") == "not-blocked"
                            for line in read_lines("input.jsonl")),
                timeout=3.0, message="input processed during a hung navigation")
            assert time.monotonic() - hang_started < 3.0
            # the request is acknowledged immediately: optimistic URL + loading
            await wait_for(
                lambda: next((t for t in nav_texts if t.get("type") == "status" and
                              t.get("loading") is True and
                              t.get("url") == "http://stub.invalid/slow-nav"), None),
                message="optimistic loading status")
            # a real navigation settles the flag through main-frame lifecycle
            await nav_ws.send_json({"type": "navigate",
                                    "url": "http://ok.example.test/done"})
            await wait_for(
                lambda: next((t for t in nav_texts if t.get("type") == "status" and
                              t.get("url") == "http://ok.example.test/done" and
                              t.get("loading") is False), None),
                message="loading cleared after commit")
            # a refused scheme answers this viewer without killing the view
            await nav_ws.send_json({"type": "navigate", "url": "file:///etc/passwd"})
            refusal = await wait_for(
                lambda: next((t for t in nav_texts if t.get("type") == "error"), None),
                message="refused navigation notice")
            assert refusal.get("terminal") is not True, refusal
            assert "http(s)" in refusal.get("text", ""), refusal
            assert all(line["url"] != "file:///etc/passwd"
                       for line in read_lines("navigations.jsonl")
                       if line.get("pid") == nav_pid)
            await nav_ws.close()
            nav_reader.cancel()
            async with http.delete(url + "/api/browser/instances/" + nav_id,
                                   headers=headers) as r:
                assert r.status == 200, await read_json(r)

            # ---- shared persistent sign-in storage across browsers ----
            store_file = Path(browser_store.store_path())
            assert not store_file.exists()
            async with http.get(url + "/api/ping", headers=headers) as r:
                ping_caps = await read_json(r)
                assert "browser-shared-storage" in ping_caps["capabilities"], ping_caps
            async with http.get(url + "/api/browser/status", headers=headers) as r:
                shared_status = await read_json(r)
                assert shared_status["shared_storage"] is False, shared_status
            async with http.post(url + "/api/browser/shared-storage",
                                 headers=headers, json={"enabled": "yes"}) as r:
                assert r.status == 400
            async with http.post(url + "/api/browser/shared-storage",
                                 headers=headers, json={"enabled": True}) as r:
                shared_status = await read_json(r)
                assert r.status == 200 and shared_status["shared_storage"] is True
            assert config.get("browser.shared_storage") is True

            async with http.post(url + "/api/browser/instances", headers=headers,
                                 json={}) as r:
                share_a = (await read_json(r))["browser"]["id"]
            share_a_manager = browser.manager().get(share_a)
            share_a_pid = share_a_manager.pid
            await wait_for(lambda: any(line["pid"] == share_a_pid for line in
                                       read_lines("cookies-read.jsonl")),
                           message="launch-time cookie import read")

            def store_state():
                if not store_file.exists():
                    return None
                return json.loads(store_file.read_text())

            def store_cookie_names(data):
                return {entry["cookie"]["name"] for entry in (data or {}).get(
                    "cookies", [])}

            # signing in on one browser lands the cookie in the shared store
            await share_a_manager.handle_client(
                {"type": "navigate", "url": "http://cookie.example.test/login"})
            await wait_for(lambda: "auth" in store_cookie_names(store_state()),
                           timeout=15.0, message="auth cookie reached the store")
            assert (os.stat(store_file).st_mode & 0o777) == 0o600
            assert (os.stat(store_file.parent).st_mode & 0o777) == 0o700
            stored = store_state()
            auth_entry = next(entry["cookie"] for entry in stored["cookies"]
                              if entry["cookie"]["name"] == "auth")
            assert auth_entry["value"] == "cookie-secret", auth_entry
            assert auth_entry["httpOnly"] is True, auth_entry

            # a second browser starts already signed in
            async with http.post(url + "/api/browser/instances", headers=headers,
                                 json={}) as r:
                share_b = (await read_json(r))["browser"]["id"]
            share_b_manager = browser.manager().get(share_b)
            share_b_pid = share_b_manager.pid
            assert share_b_pid != share_a_pid
            await wait_for(lambda: any(
                line["pid"] == share_b_pid and any(
                    cookie.get("name") == "auth" for cookie in line["cookies"])
                for line in read_lines("cookies-set.jsonl")),
                timeout=15.0, message="second browser imported the shared cookie")

            # localStorage flows capture -> store -> seed script on every peer
            await share_a_manager.handle_client(
                {"type": "navigate", "url": "http://ls.example.test/app"})
            await wait_for(lambda: "http://ls.example.test" in
                           (store_state() or {}).get("storage", {}),
                           timeout=15.0, message="localStorage reached the store")

            def seeded_pids():
                return {line["pid"] for line in read_lines("seed-scripts.jsonl")
                        if "local-secret" in line["source"]}

            await wait_for(lambda: {share_a_pid, share_b_pid} <= seeded_pids(),
                           timeout=15.0, message="seed script on both browsers")
            seeded = next(line["source"] for line in read_lines("seed-scripts.jsonl")
                          if "local-secret" in line["source"])
            assert "localStorage.getItem(k)===null" in seeded, seeded

            # signing out on one browser removes the cookie everywhere
            await share_a_manager.handle_client(
                {"type": "navigate", "url": "http://cookie.example.test/logout"})
            await wait_for(lambda: store_state() is not None and
                           "auth" not in store_cookie_names(store_state()),
                           timeout=15.0, message="store dropped the auth cookie")
            await wait_for(lambda: any(
                line["pid"] == share_b_pid and line["params"].get("name") == "auth"
                for line in read_lines("cookies-delete.jsonl")),
                timeout=15.0, message="peer browser dropped the auth cookie")

            # turning the toggle off stops syncing but keeps the store for later
            async with http.post(url + "/api/browser/shared-storage",
                                 headers=headers, json={"enabled": False}) as r:
                shared_status = await read_json(r)
                assert shared_status["shared_storage"] is False, shared_status
            assert store_file.exists()
            await asyncio.sleep(0.3)   # let poked timers observe the toggle
            reads_when_off = len(read_lines("cookies-read.jsonl"))
            await share_a_manager.handle_client(
                {"type": "navigate", "url": "http://plain.example.test/after"})
            await wait_for(lambda: any(
                line["url"] == "http://plain.example.test/after"
                for line in read_lines("navigations.jsonl")),
                message="post-toggle navigation")
            await asyncio.sleep(2.6)   # past STORE_SYNC_DELAY
            assert len(read_lines("cookies-read.jsonl")) == reads_when_off
            for share_id in (share_a, share_b):
                async with http.delete(url + "/api/browser/instances/" + share_id,
                                       headers=headers) as r:
                    assert r.status == 200, await read_json(r)

            # A crash affects only the addressed instance; other browser IDs
            # remain available and the crashed logical browser can be restarted.
            texts3, frames3 = [], []
            ws3 = await http.ws_connect(
                url + "/api/ws/browser/" + first_id, headers=headers)
            reader3 = asyncio.ensure_future(collect_ws(ws3, texts3, frames3))
            await wait_for(lambda: frames3, message="frame before crash")
            await ws3.send_json({"type": "navigate", "url": "http://stub.invalid/die"})
            await wait_for(lambda: any(t.get("type") == "gone" for t in texts3),
                           message="gone notice after crash")
            await ws3.close()
            reader3.cancel()
            registry = browser.manager()
            first_instance = registry.get(first_id)
            await wait_for(lambda: not first_instance.running,
                           message="addressed manager stopped")
            assert registry.running, "other identified browsers should survive one crash"

            # The retired ID log and session binding are private node-local
            # browser state, deliberately outside config backup/restore.
            catalog = json.loads(Path(browser._catalog_path()).read_text())
            assert catalog["bindings"][str(agent_sid)] == first_id, catalog

            # the whole config, browser toggle included, round-trips an export
            exported = config.export_data()
            assert exported["browser"]["enabled"] is True, exported
            assert config.normalize_import(exported)["browser"]["enabled"] is True
    finally:
        await browser.shutdown()
        await web_runner.cleanup()
        assert not Path(browser_agent.socket_path()).exists()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    print("browser tests passed")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
