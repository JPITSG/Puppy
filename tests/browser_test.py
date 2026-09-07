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
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("browser-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
STUB_LOG = TEST_ROOT / "stub-log"
STUB_LOG.mkdir(mode=0o700)
os.environ["PUPPY_BROWSER_STUB_LOG"] = str(STUB_LOG)

from puppy import (browser, browser_agent, browser_store, config, db,
                   localization,
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
                requested = arguments[0] if arguments[0] is not None else "test-value"
                label = arguments[1] if arguments[1] is not None else "Testland"
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


def with_live_views(script):
    """Isolated UI snippets share the production task-view traversal helper."""
    if "liveViews()" in script and "function liveViews()" not in script:
        source = (BASE / "puppy" / "static" / "app.js").read_text()
        start = source.index("function liveViews()")
        script = source[start:source.index("function sessionViewFor(", start)] + "\n" + script
    return script


def check_session_task_capability(ui_source):
    start = ui_source.index("function backendSupportsSessionTasks(")
    helper = ui_source[start:ui_source.index("function backendSupportsSessionTools(", start)]
    script = """
const state = {backends:[{id:1,protocol:0},{id:2,capabilities:[]},{id:3,capabilities:['session-tasks']}]};
%s
const oldLocal = backendSupportsSessionTasks(0);
state.nodeCapabilities = ['session-tasks'];
console.log(JSON.stringify([oldLocal,backendSupportsSessionTasks(0),backendSupportsSessionTasks(1),backendSupportsSessionTasks(2),backendSupportsSessionTasks(3),backendSupportsSessionTasks(99)]));
""" % helper
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [False, True, False, False, True, False]


def check_task_config_ui() -> None:
    for name in ("composer_ui_test.js", "task_config_ui_test.js",
                 "task_menu_ui_test.js", "task_review_ui_test.js",
                 "task_fold_ui_test.js", "live_controls_ui_test.js", "tab_drag_ui_test.js"):
        proc = subprocess.run(["node", str(BASE / "tests" / name)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def check_session_task_helpers(ui_source: str) -> None:
    """The task strip, the Tasks sheet and the sidebar's activity slot all take
    their words and colours from one state table, and the review sheet colours
    a unified diff by line role."""
    start = ui_source.index("const TASK_STATES =")
    end = ui_source.index("function tasksIcon(", start)
    script = """
%s
console.log(JSON.stringify({
  labels: ["running","queued","pending","held","ready","applied","stopped","failed","odd"]
    .map(state => [taskStateLabel({state}), taskStateClass({state})]),
  approval: [taskStateLabel({state:"running", needs_approval:true}),
             taskStateClass({state:"running", needs_approval:true})],
  reviewable: ["running","queued","held","ready","applied","stopped","failed"]
    .map(state => taskReviewable({state})),
  activity: [null, {total:0}, {total:2,running:0,approval:0,ready:2},
             {total:2,running:1,approval:0,ready:0},
             {total:3,running:2,approval:1,ready:0}].map(taskActivityLabel),
  title: taskActivityTitle({total:3,running:2,approval:1,ready:0}),
  diff: ["diff --git a/x b/x","index 1..2 100644","--- a/x","+++ b/x","@@ -1 +1 @@",
         "-old","+new"," same","Binary files differ"].map(diffLineClass),
}));
""" % ui_source[start:end]
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout)
    assert result["labels"] == [
        ["Running", "busy"], ["Queued", "busy"], ["Starting", "busy"], ["Held", "warn"],
        ["Review", "ok"], ["Applied", ""], ["Stopped", "warn"], ["Failed", "bad"],
        ["odd", ""]], result
    assert result["approval"] == ["Needs approval", "warn"], result
    assert result["reviewable"] == [False, False, False, True, True, True, True], result
    assert result["activity"] == [
        None, None, None, {"cls": "active-time", "text": "1 task"},
        {"cls": "attention", "text": "1 needs input"}], result
    assert result["title"] == "3 tasks: 2 running, 1 needs input", result
    assert result["diff"] == [
        "meta", "meta", "meta", "meta", "hunk", "del", "add", "", "meta"], result


def check_session_task_visibility(ui_source: str) -> None:
    """A per-session Tasks toggle must land on an open workspace without
    rebuilding its strip or touching the chat underneath, and a node whose
    bootstrap has not arrived is never read as disabled."""
    start = ui_source.index("function liveViews()")
    end = ui_source.index("async function modalNewTask(", start)
    script = """
const assert = require("assert");
let session = { id: 1, tasks_enabled: true, status: "idle", color: "#abc", engine: "codex" };
let building = true;
const dragTab = null;
const classes = () => {
  const values = new Set();
  return { contains: v => values.has(v), add: v => values.add(v), remove: v => values.delete(v),
           toggle: (v, on) => on ? values.add(v) : values.delete(v) };
};
const node = () => ({ classList: classes(), style: {}, dataset: {}, children: [],
  appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
  setAttribute() {}, querySelector() { return null; }, querySelectorAll() { return []; } });
const state = { views: {} };
const sessionsFor = () => session ? [session] : [];
const findSessionMeta = () => session;
const el = () => {
  if (!building) throw new Error("An unchanged task list should retain its rendered controls");
  return node();
};
const syncPromptSpinnerPhase = () => {};
const syncHorizontalOverflow = () => {};
const xIcon = () => node();
const localStorage = { getItem() { return null; }, setItem() {} };
%s
const view = Object.create(SessionWorkspaceView.prototype);
const main = { root: node(), draft: "Keep this draft" };
Object.assign(view, { tab: { bid: 0, sid: 1 }, root: node(), strip: node(), overviewButton: node(),
  taskViews: new Map([[1, main]]), opened: [], hidden: new Set(), selected: 1, seen: {}, overview: null, rendered: "" });
view.refreshTasks();
const painted = view.strip.children.length;
building = false;
for (const enabled of [true, false, true, false]) {
  session.tasks_enabled = enabled;
  view.refreshTasks();
  assert.strictEqual(view.root.classList.contains("tasks-disabled"), !enabled);
  assert.strictEqual(view.activeView(), main);
  assert.strictEqual(main.draft, "Keep this draft");
}
building = true;
session = null;
view.refreshTasks();
console.log(JSON.stringify({ painted, disabledWithoutBootstrap: view.root.classList.contains("tasks-disabled"),
  mainShown: main.root.classList.contains("on") }));
""" % ui_source[start:end]
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:800]
    assert json.loads(proc.stdout) == {
        "painted": 1, "disabledWithoutBootstrap": False, "mainShown": True}, proc.stdout


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
class Node {
  constructor(tag="", cls="", text="") {
    this.tag=tag;this.className=cls;this.textContent=text;this.children=[];
    this.attributes={};this.style={values:{},setProperty:(key,value)=>{
      this.style.values[key]=String(value);
    }};
  }
  setAttribute(name,value){this.attributes[name]=String(value);}
  appendChild(child){this.children.push(child);return child;}
  replaceChildren(...children){this.children=[];children.forEach(child=>this.appendChild(child));}
  querySelector(selector){const cls=selector.slice(1);for(const child of this.children){
    if(child.className.split(" ").includes(cls))return child;
    const nested=child.querySelector(selector);if(nested)return nested;}return null;}
}
const el=(tag,cls="",text="")=>new Node(tag,cls,text);
const PROMPT_SPIN_MS=800;
%s
%s
%s
%s
%s
let stopping = "";
const remoteStoppingMessage = () => stopping;
const proto = {
%s
};
const classes = new Set();
const view = Object.assign(Object.create(proto), {
  tab: {bid: 7},
  reconnecting: false,
  statusText: "thinking 42 tokens",
  statusEl: new Node("span", "chat-status"),
  root: {classList: {toggle(name, on) {
    if (on) classes.add(name); else classes.delete(name);
  }}},
  liveText: "",
  syncLiveStatus() { this.liveText = this.visibleStatusText(); },
  syncHeadOverflow() {},
  updateSteerControl() {},
  updateApprovalControl() {},
});
const headerText=()=>view.statusEl.children.map(child=>child.textContent).join(" ");
const take = () => ({header: headerText(), live: view.liveText,
                     activity: view.statusText, reconnecting: view.reconnecting,
                     metadataHidden: classes.has("transport-lost")});
view.renderStatus();
const firstSpinner=view.statusEl.querySelector(".spinner");
const before = take();
view.setReconnecting(true);
const lost = take();
const lostSpinner=view.statusEl.querySelector(".spinner");
view.setStatus("using shell");
const changedWhileLost = take();
stopping = "Backend shutting down…";
view.renderStatus();
const gracefulStop = take();
stopping = "";
view.setReconnecting(false);
const recovered = take();
const spinnerStayed=firstSpinner===lostSpinner&&firstSpinner===view.statusEl.querySelector(".spinner");
view.setStatus("");
const cleared=view.statusEl.children.length===0;
view.setStatus("Starting…");
const newRoundSpinner=view.statusEl.querySelector(".spinner")!==firstSpinner;
console.log(JSON.stringify({before, lost, changedWhileLost, gracefulStop, recovered,
  spinnerStayed,cleared,newRoundSpinner}));
""" % (function("promptStatusBase"), function("syncPromptSpinnerPhase"),
         function("promptSpinnerNode"), function("promptStatusLabel"),
         function("updatePromptStatusLabel"), ",\n".join(methods))
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert result["spinnerStayed"] and result["cleared"] and \
        result["newRoundSpinner"], result
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
        "liveViews", "retireRemoteSessionActivity", "handleRemoteNodeStopping",
        "clearRemoteNodeStopping"))
    script = r"""
const state = {
  backends: [{id: 7, name: "worker"}, {id: 8, name: "other"}],
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
  nested: {taskViews: new Map([[3, {tab: {bid: 7}, handleNodeStopping(message) { if (message.includes("restarting")) stopped++; }, clearNodeStopping() { cleared++; }}]])},
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert stopped["stopped"] == 2 and stopped["otherStopped"] == 0, stopped
    assert result["clearedState"] == {"notice": None, "error": None, "cleared": 2}, result


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
        "retireRemoteSessionActivity", "backendSupportsStateStream",
        "reconcileRemoteState"))
    script = r"""
const cached = {id: 44, name: "cached", status: "running", active_since: 10};
const state = {
  backends: [{id: 7, availability: {state: "offline", reason: "powered off"},
    last_known: {version: 1, sessions: [cached]}}],
  remoteSessions: {}, remoteOk: {}, remoteErrors: {}, remoteStopping: {},
  engCache: {}, remoteEngineErrors: {}, remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {}, remoteUsageRefresh: {}, remoteAutoUpgrade: {},
  remoteUploadSettings: {}, remoteSystemPrompts: {}, remoteBrowser: {},
  remoteBrowserStatus: {}, terminalInstances: {}, stateStreamReady: {},
  stateStreamRuntime: {}, stateStreamRevisions: {}, remoteNodeUpgrade: {},
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert ".sess-item.backend-unavailable{opacity:.56;filter:grayscale(1)}" in css_source
    assert ".sess-item.archived.backend-unavailable{opacity:.4}" in css_source


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
        "retireRemoteSessionActivity", "backendSupportsStateStream",
        "nodeStateStreamActive", "reconcileRemoteState",
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
  remoteBrowserStatus: {}, terminalInstances: {}, stateStreamReady: {},
  stateStreamRuntime: {}, stateStreamRevisions: {}, remoteNodeUpgrade: {},
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
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
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


def check_node_state_stream_ui(ui_source: str) -> None:
    """Push state is capability-gated, revisioned, and keeps HTTP fallback."""
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
        "clearNodeStateRevisions", "acceptStateSnapshot",
        "backendSupportsStateStream", "nodeStateStreamActive",
        "statePollingNeeded"))
    script = r"""
let reloads=0;
const location={reload(){reloads++;}};
const state={
  runtimeId:"controller-runtime", stateStreamRevisions:{},
  stateStreamRuntime:{7:"remote-a"}, stateStreamReady:{0:true,7:true},
  backends:[
    {id:7,protocol:2,capabilities:["node-state-stream-v1"]},
    {id:8,protocol:2,capabilities:[]},
  ],
};
const backendConnectionAllowed=()=>true;
%s
const localFirst=acceptStateSnapshot(0,{type:"sessions",state_topic:"sessions",
  state_revision:1,runtime_id:"controller-runtime"});
const localDuplicate=acceptStateSnapshot(0,{type:"sessions",state_topic:"sessions",
  state_revision:1,runtime_id:"controller-runtime"});
const booleanRevision=acceptStateSnapshot(0,{type:"sessions",state_topic:"sessions",
  state_revision:true,runtime_id:"controller-runtime"});
const numericRuntime=acceptStateSnapshot(0,{type:"sessions",state_topic:"sessions",
  state_revision:2,runtime_id:123});
const wrongLocal=acceptStateSnapshot(0,{type:"sessions",state_topic:"sessions",
  state_revision:2,runtime_id:"other-controller"});
state.stateStreamRevisions["7:sessions"]=5;
state.stateStreamRevisions["7:engines"]=9;
const remoteNext=acceptStateSnapshot(7,{type:"sessions",state_topic:"sessions",
  state_revision:6,runtime_id:"remote-a"});
const remoteRestart=acceptStateSnapshot(7,{type:"sessions",state_topic:"sessions",
  state_revision:1,runtime_id:"remote-b"});
const withLegacy=statePollingNeeded();
state.backends=state.backends.slice(0,1);
const allLive=statePollingNeeded();
state.stateStreamReady[7]=false;
const remoteDown=statePollingNeeded();
state.stateStreamReady[7]=true;state.stateStreamReady[0]=false;
const localDown=statePollingNeeded();
const remoteViaDeadController=nodeStateStreamActive(7);
console.log(JSON.stringify({
  localFirst,localDuplicate,booleanRevision,numericRuntime,wrongLocal,reloads,
  remoteNext,remoteRestart,
  remoteRuntime:state.stateStreamRuntime[7],remoteSessions:state.stateStreamRevisions["7:sessions"],
  oldEngineRevision:Object.prototype.hasOwnProperty.call(state.stateStreamRevisions,"7:engines"),
  withLegacy,allLive,remoteDown,localDown,remoteViaDeadController,
  exactCap:backendSupportsStateStream({protocol:2,capabilities:["node-state-stream-v1"]}),
  protocolZero:backendSupportsStateStream({protocol:0,capabilities:["node-state-stream-v1"]}),
  missingCap:backendSupportsStateStream({protocol:2,capabilities:[]}),
}));
""" % source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:900]
    result = json.loads(proc.stdout.strip())
    assert result == {
        "localFirst": True, "localDuplicate": False, "wrongLocal": False,
        "booleanRevision": False, "numericRuntime": False,
        "reloads": 1, "remoteNext": True, "remoteRestart": True,
        "remoteRuntime": "remote-b", "remoteSessions": 1,
        "oldEngineRevision": False, "withLegacy": True, "allLive": False,
        "remoteDown": True, "localDown": True, "remoteViaDeadController": False,
        "exactCap": True,
        "protocolZero": False, "missingCap": False,
    }, result

    ingest = function("ingestSessionActivity")
    reconcile = function("reconcileRemoteState")
    assert "live.has" not in ingest
    assert "state.stateStreamRevisions" in reconcile
    assert "if (!message || message.state_topic !== message.type) return;" in ui_source
    assert "if (nodeStateStreamActive(0)) return;" in ui_source
    assert "backendSupportsStateStream(backend) && nodeStateStreamActive(bid)" in ui_source
    assert "!backendSupportsStateStream(backend)" in ui_source
    assert "if (nodeStateStreamActive(bid)) {" in ui_source
    assert "!nodeStateStreamActive(record.backend.id)" in ui_source
    assert "d.stream_version !== 1" in ui_source

    topic_source = function("noteLocalStateStreamTopic")
    script = r"""
const state={stateStreamReady:{0:false},stateStreamRuntime:{0:"local-run"}};
let localStateStreamTopics=new Set();
let updatesReconnectRefreshTimer=null;
let pollingStarts=0;
const startRemotePolling=()=>{pollingStarts++;};
%s
for(const type of ["sessions","engines","node","browser_status"])
  noteLocalStateStreamTopic({type,state_topic:type,state_revision:1,runtime_id:"local-run"});
const partialReady=state.stateStreamReady[0];
noteLocalStateStreamTopic({type:"terminal_instances",state_topic:"terminal_instances",
  state_revision:true,runtime_id:"local-run"});
const malformedReady=state.stateStreamReady[0];
noteLocalStateStreamTopic({type:"terminal_instances",state_topic:"terminal_instances",
  state_revision:1,runtime_id:"local-run"});
console.log(JSON.stringify({partialReady,malformedReady,
  completeReady:state.stateStreamReady[0],pollingStarts}));
""" % topic_source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:900]
    assert json.loads(proc.stdout.strip()) == {
        "partialReady": False, "malformedReady": False,
        "completeReady": True, "pollingStarts": 1,
    }

    remote_stream_source = function("applyRemoteStreamState")
    script = r"""
let syncs=0,polls=0;
const state={
  stateStreamReady:{7:false},stateStreamRuntime:{7:"remote-a"},
  stateStreamRevisions:{"7:sessions":4},
  backends:[{id:7,availability:{state:"offline",reason:"restarting"}}],
};
const controllerBackendHealth=backend=>backend.availability;
const syncRemoteStateViews=()=>{syncs++;};
const startRemotePolling=()=>{polls++;};
const clearNodeStateRevisions=bid=>{
  for(const key of Object.keys(state.stateStreamRevisions))
    if(key.startsWith(`${bid}:`)) delete state.stateStreamRevisions[key];
};
%s
applyRemoteStreamState({backend_id:7,connected:true,node_runtime_id:"remote-b"});
const staleConnectRejected=state.stateStreamReady[7]===false &&
  state.stateStreamRuntime[7]==="remote-a";
state.backends[0].availability={state:"online",reason:""};
applyRemoteStreamState({backend_id:7,connected:true,node_runtime_id:"remote-b"});
const onlineAccepted=state.stateStreamReady[7]===true &&
  state.stateStreamRuntime[7]==="remote-b" &&
  !Object.prototype.hasOwnProperty.call(state.stateStreamRevisions,"7:sessions");
applyRemoteStreamState({backend_id:7,connected:false,node_runtime_id:"remote-b"});
console.log(JSON.stringify({staleConnectRejected,onlineAccepted,
  disconnected:state.stateStreamReady[7]===false,syncs,polls}));
""" % remote_stream_source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:900]
    result = json.loads(proc.stdout.strip())
    assert result == {
        "staleConnectRejected": True, "onlineAccepted": True,
        "disconnected": True, "syncs": 2, "polls": 3,
    }, result


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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert "Backend unavailable · showing last known ${noun}" in function("backendStateNote")
    assert 'backendStateNote(availability, !!current)' in ui_source
    assert 'backendStateNote(backendStatus, true, "prompt settings")' in ui_source
    assert ".engine-node-offline-values .engine-row{opacity:.6}" in css_source
    assert ".usage-refresh-controls input:disabled{opacity:.5;cursor:not-allowed}" \
        in css_source
    backend_actions = ui_source[
        ui_source.index("    /* backends */"):
        ui_source.index("    /* security */", ui_source.index("    /* backends */"))]
    assert "installBackendRecord(" in backend_actions
    assert "discardBackendRecord(" in backend_actions
    assert "refreshState()" not in backend_actions
    assert "pollRemotes(" not in backend_actions


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
        "sessionActivityKey", "ingestOneSessionActivity"))
    script = r"""
const state = {notify: {configured: true, enabled: true}, backends: [
  {id: 8, capabilities: ["completion-events"]},
]};
const sessionActivityAnchors = new Map([
  ["7:1", 1000], ["7:2", 1000], ["7:3", 1000], ["8:4", 1000],
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
// Even an incomplete observation cannot trigger browser-owned notifications.
ingestOneSessionActivity(7, {id: 3, status: "idle"}, 20, 5000);
// A current backend is consumed by the controller and must never double-fire.
ingestOneSessionActivity(8,
  {id: 4, status: "idle", completion_status: "ok"}, 20, 5000);
console.log(JSON.stringify({posts, remaining: sessionActivityAnchors.size}));
""" % source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["remaining"] == 0, result
    assert result["posts"] == [], result
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
    this.attributes={};this.style={values:{},setProperty:(key,value)=>{
      this.style.values[key]=String(value);
    }};
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
const PROMPT_SPIN_MS=800;
%s
%s
%s
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
const toolMarker=marker();
view.statusText="writing file";view.syncLiveStatus();
const toolStayed=marker()===toolMarker;
view.statusText="thinking… 42 tokens";view.syncLiveStatus();
const claude={cls:marker().className,text:marker().textContent};
view.status="idle";view.syncLiveStatus();
console.log(JSON.stringify({codex,tool,toolStayed,claude,idle:view.statusRow,
  classified:[isThinkingStatus("thinking"),isThinkingStatus("Thinking 9 tokens"),
    isThinkingStatus("rethinking"),isThinkingStatus("writing...")]}));
""" % (function("promptStatusBase"), function("syncPromptSpinnerPhase"),
         function("promptSpinnerNode"), function("promptStatusLabel"),
         function("updatePromptStatusLabel"), function("thinkingIconNode"),
         function("isThinkingStatus"), method)
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["codex"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["claude"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["tool"] == {"cls": "spinner", "text": ""}, result
    assert result["toolStayed"] is True, result
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    assert json.loads(proc.stdout) == [
        "Thinking", "Thinking 42 tokens", "Using shell",
        "Starting next queued message",
    ]
    assert 'label = promptStatusLabel(text, "status-text");' in ui_source
    assert "updatePromptStatusLabel(label, text);" in ui_source
    assert ui_source.count('promptStatusLabel(text, "think-label")') == 2
    assert "this.statusText || thinkingLabel(0), \"think-label\"" in ui_source
    assert ".prompt-status-label::after{" in css_source
    assert '0%,32%{clip-path:inset(0 66.6667% 0 0)}' in css_source
    assert '33%,65%{clip-path:inset(0 33.3333% 0 0)}' in css_source
    assert '66%,100%{clip-path:inset(0 0 0 0)}' in css_source
    assert 'content:"...";display:inline-block;text-align:left' in css_source
    assert 'prefers-reduced-motion:reduce){.prompt-status-label::after{content:"...";animation:none}' in css_source

    # Rebuilt prompt rings start on one document-wide phase. At 950ms and
    # 1750ms (one full cycle later), replacement nodes therefore receive the
    # same negative delay instead of restarting from zero.
    spinner_start = ui_source.index("const PROMPT_SPIN_MS = 800;")
    spinner_end = ui_source.index("\n/* the header's thinking status", spinner_start)
    spinner_source = ui_source[spinner_start:spinner_end]
    script = r"""
const el=(tag,cls)=>({tag,className:cls,style:{values:{},setProperty(key,value){
  this.values[key]=value;
}}});
%s
const one=promptSpinnerNode(950);
const two=promptSpinnerNode(1750);
const parent=syncPromptSpinnerPhase(el("span","active-time"),450);
console.log(JSON.stringify([one.style.values["--prompt-spin-delay"],
  two.style.values["--prompt-spin-delay"],parent.style.values["--prompt-spin-delay"]]));
""" % spinner_source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    assert json.loads(proc.stdout) == ["-150ms", "-150ms", "-450ms"]
    for selector in (".si-be.active-time::before{",
                     ".sess-dot.running,.engine-dot.running,.tab.running .t-dot{",
                     ".chat-status .spinner{", ".live-status .spinner{"):
        start = css_source.index(selector)
        rule = css_source[start:css_source.index("}", start)]
        assert "animation-delay:var(--prompt-spin-delay,0ms)" in rule


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
    ".form-error":control()};
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
const invalid={message:one[".form-error"].textContent,
  visible:!one[".form-error"].classList.contains("hidden"),calls:calls.length};
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
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
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
    assert "installBackendRecord(result.backend, !!result.connection_changed);" in ui_source
    assert "function installBackendRecord(record, resetConnection = false)" in ui_source
    assert ".backend-edit-grid{display:grid;grid-template-columns:" in css_source
    assert ".backend-url-row{display:flex;align-items:center;gap:6px;min-width:0}" in css_source
    assert "transition:opacity var(--t-slow) var(--ease)" in css_source
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
    assert '"engine-node-message engine-node-empty", "No engines installed"' in ui_source
    assert "icon.appendChild(refreshIcon(10));" in ui_source
    assert (".engine-node-meta .be-url{\n  display:inline-flex;align-items:baseline;" in
            css_source)
    assert (".be-row .be-url{\n  display:flex;align-items:baseline;" in css_source)
    assert (".engine-node-message.engine-node-unavailable," +
            ".engine-node-message.engine-node-loading,") in css_source
    assert ".engine-node-message.engine-node-empty{" in css_source
    assert "display:inline-grid;grid-template-columns:6px auto" in css_source
    assert "margin:5px 0 3px;padding:5px 0;" in css_source
    assert ".hint,.help,.engine-node-message,.usage-refresh-note,.eau-note," in css_source
    assert "font-family:var(--sans);font-size:var(--fs-xs);font-weight:400;" in css_source
    assert ".engine-node-message{padding:7px 0 5px 30px;color:var(--txt3)}" in css_source
    assert ".engine-node-loading-icon svg{display:block;flex:0 0 auto;animation:spin" in css_source
    assert (".engine-node-message.engine-node-unavailable::before," +
            "\n.engine-node-message.engine-node-empty::before{justify-self:center}") in css_source
    assert "--alert-triangle:url(" in css_source
    assert ".engine-node-message.engine-node-stale::before{" in css_source
    assert 'content:"";display:block;align-self:center;' in css_source
    assert "background:var(--err);-webkit-mask:var(--alert-triangle)" in css_source
    assert (".engine-node-stale{display:flex;align-items:flex-start;gap:8px;" +
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
    assert ('if (refresh) refresh.disabled = refresh.classList.contains("refreshing") ||' +
            '\n        (!!bid && status !== "ok");') in ui_source
    assert (".engine-node-refresh.refresh-success{color:var(--ok);" +
            "background:var(--ok-dim);opacity:1}") in css_source
    assert (".engine-node-refresh.refresh-failure{color:var(--err);" +
            "background:var(--err-dim);opacity:1}") in css_source

    # A manual retry announces both edges of the attempt, remains gated while
    # in flight, and then paints a centred check/X before returning to its
    # refresh glyph. Toasts name successful and failed components, including a
    # partial response rather than flattening it into a generic verdict.
    feedback_source = extract("engineRefreshFeedback")
    reset_source = extract("resetEngineRefreshButton")
    result_source = extract("showEngineRefreshResult")
    refresh_source = "async " + extract("refreshEngineVersions")
    refresh_script = r"""
const ENGINE_REFRESH_TIMEOUT=1234;
const ENGINE_REFRESH_RESULT_MS=3200;
const TOAST_LONG=7000;
let shouldFail=true,synced=0,applied=0;const calls=[],toasts=[];
const syncRemoteStateViews=()=>{synced++;};
const api=async(bid,path,options)=>{calls.push({bid,path,options});
  if(shouldFail)throw new Error("still unavailable");return {engines:[
    {label:"First",installed:true,version:"1.2.3",auth:"ok",latest_check_error:"",
      dynamic_model_options:true,model_catalog_loaded:true,model_catalog_error:"",
      model_catalog_note:""}],usage_refresh:{}};};
const applyEnginesPayload=()=>{applied++;};
const toast=(text,kind,ms)=>{toasts.push({text,kind,ms});};
const refreshIcon=size=>({kind:"refresh",size});
const checkIcon=size=>({kind:"check",size});
const xIcon=size=>({kind:"x",size});
let nextTimer=0;const timers=new Map();
const setTimeout=(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id;};
const clearTimeout=id=>{if(id)timers.delete(id);};
class Classes{constructor(){this.names=new Set();}add(name){this.names.add(name);}
  remove(...names){names.forEach(name=>this.names.delete(name));}
  contains(name){return this.names.has(name);}}
const makeButton=()=>({disabled:false,isConnected:true,classList:new Classes(),attributes:{},icon:null,
  setAttribute(name,value){this.attributes[name]=String(value);},
  removeAttribute(name){delete this.attributes[name];},
  replaceChildren(icon){this.icon=icon;}});
__FEEDBACK__
__RESET__
__RESULT__
__REFRESH__
const failedButton=makeButton();
const failedTask=refreshEngineVersions(7,failedButton,"Worker node");
const failedDuring={disabled:failedButton.disabled,
  refreshing:failedButton.classList.contains("refreshing"),
  busy:failedButton.attributes["aria-busy"],synced};
await failedTask;
const failedAfter={disabled:failedButton.disabled,
  refreshing:failedButton.classList.contains("refreshing"),
  failed:failedButton.classList.contains("refresh-failure"),icon:failedButton.icon.kind,
  label:failedButton.attributes["aria-label"],busy:failedButton.attributes["aria-busy"]||null,
  synced,applied,toasts:toasts.length};
const failedReset=timers.get(failedButton._engineRefreshResultTimer);
failedReset.fn();
const failedRestored={failed:failedButton.classList.contains("refresh-failure"),
  icon:failedButton.icon.kind,label:failedButton.attributes["aria-label"]};
shouldFail=false;
const goodButton=makeButton();
const goodTask=refreshEngineVersions(7,goodButton,"Worker node");
const goodDuring={disabled:goodButton.disabled,
  refreshing:goodButton.classList.contains("refreshing"),synced};
await goodTask;
const goodAfter={disabled:goodButton.disabled,
  refreshing:goodButton.classList.contains("refreshing"),
  succeeded:goodButton.classList.contains("refresh-success"),icon:goodButton.icon.kind,
  label:goodButton.attributes["aria-label"],synced,applied,toasts:toasts.length};
const partial=engineRefreshFeedback("Worker node",{engines:[
  {label:"First",installed:true,version:"1.2.3",auth:"ok",
    latest_check_error:"registry timed out",dynamic_model_options:true,
    model_catalog_loaded:true,model_catalog_error:"catalog timed out",model_catalog_note:""}
]});
console.log(JSON.stringify({failedDuring,failedAfter,failedRestored,goodDuring,goodAfter,
  calls,toasts,partial,failedDelay:failedReset.ms}));
""".replace("__FEEDBACK__", feedback_source).replace("__RESET__", reset_source).replace(
        "__RESULT__", result_source).replace("__REFRESH__", refresh_source)
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
        "failed": True, "icon": "x",
        "label": "Engine refresh failed on Worker node",
        "synced": 2, "applied": 0, "toasts": 1,
    }, refreshed
    assert refreshed["failedRestored"] == {
        "failed": False, "icon": "refresh",
        "label": "Re-check engine versions, sign-in and model lists on Worker node",
    }, refreshed
    assert refreshed["failedDelay"] == 3200, refreshed
    assert refreshed["goodDuring"] == {
        "disabled": True, "refreshing": True, "synced": 3,
    }, refreshed
    assert refreshed["goodAfter"] == {
        "disabled": False, "refreshing": False, "succeeded": True, "icon": "check",
        "label": "Engine refresh succeeded on Worker node",
        "synced": 4, "applied": 1, "toasts": 2,
    }, refreshed
    assert refreshed["calls"] == [
        {"bid": 7, "path": "engines/refresh",
         "options": {"method": "POST", "timeoutMs": 1234}},
        {"bid": 7, "path": "engines/refresh",
         "options": {"method": "POST", "timeoutMs": 1234}},
    ], refreshed
    assert refreshed["toasts"][0]["kind"] == "error", refreshed
    assert "Failed: version checks, sign-in checks, latest-release checks, model-list refresh" \
        in refreshed["toasts"][0]["text"], refreshed
    assert refreshed["toasts"][1]["kind"] == "ok", refreshed
    assert ("Succeeded: version checks, sign-in checks, latest-release checks, " +
            "model-list refresh") in refreshed["toasts"][1]["text"], refreshed
    assert refreshed["partial"]["ok"] is False, refreshed
    assert "Succeeded: version checks, sign-in checks" in refreshed["partial"]["text"], \
        refreshed
    assert "Failed: latest-release checks (First: registry timed out)" in \
        refreshed["partial"]["text"], refreshed
    assert "model-list refresh (First: catalog timed out)" in \
        refreshed["partial"]["text"], refreshed


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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
  resetFps(){},
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["beforeFlush"] == 1, result
    assert result["sent"] == [{
        "type": "wheel", "nx": 0.1, "ny": 0.2,
        "dx": 2, "dy": 3, "modifiers": 0,
    }, {
        "type": "wheel", "nx": 0.4, "ny": 0.5,
        "dx": 7, "dy": -1, "modifiers": 8,
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    composer = ui_source[ui_source.index("class Composer {"):
                         ui_source.index("/* ================= SessionView =================")]
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
%s
function makeView(id="s:0:42") {
  const sent=[];
  const queueClasses=new Set();
  const view=Object.create(SessionView.prototype);
  Object.assign(view, {
    tab:{id,bid:0,sid:42}, closed:false,
    queueEl:{
      classList:{toggle(name,on){if(on)queueClasses.add(name);else queueClasses.delete(name);}},
      setAttribute(){},removeAttribute(){},querySelectorAll(){return[];},
    },
    draftReady:true,draftRevision:0,
    draftMaxChars:100000,status:"idle",_forceScroll:false,
    steering:{supported:true,ready:false,turn_id:""},steerPending:null,
    sideQuestion:{supported:true,ready:false,turn_id:""},askPending:null,
    draftClientId:"device-a",draftClientSeq:0,draftLatestSeq:0,draftAckSeq:0,
    draftInFlightSeq:0,draftPendingText:null,
    draftDeferred:null,draftTouchedBeforeReady:false,draftJournal:null,
    queueEditSeq:0,queueEditPending:null,
    ws:{readyState:WebSocket.OPEN,send:value=>sent.push(JSON.parse(value))},
    scrollBottom(){},updateRunState(){},setSteeringState(){},
    setSideQuestionState(){},setStatus(){},
    updateSteerControl(){},
  });
  view.composer=Object.assign(Object.create(Composer.prototype), {
    host:{bid:0,sid:42,privateUploads:()=>false}, closed:false,busy:false,
    ta:{value:"",selectionStart:0,selectionEnd:0,readOnly:false,focused:false,
      focus(){this.focused=true;},removeAttribute(){},
      setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}},
    attachments:[],histAttach:null,histDraft:"",sentThumbs:new Map(),
    history:null,attachStrip:{querySelectorAll(){return[];}},
    renderAttachments(){},resize(){},hideMention(){},
    discardServerUpload(){},syncUploadButton(){},
  });
  return {view,sent,queueClasses};
}

const first=makeView();
first.view.composer.ta.value="Test";
first.view.composer.ta.selectionStart=first.view.composer.ta.selectionEnd=4;
first.view.saveDraft();
const pendingJournal=JSON.parse(storage.get("puppy.draft.s:0:42"));
first.view.receiveDraft({type:"draft",text:"Test",revision:1,updated_at:1,
  client_id:"device-a",client_seq:1});
const journalCleared=!storage.has("puppy.draft.s:0:42");
first.view.receiveDraft({type:"draft",text:"from device b",revision:2,updated_at:2,
  client_id:"device-b",client_seq:1});
const followed=first.view.composer.ta.value;

first.view.composer.ta.value="local winner";
first.view.saveDraft();
first.view.receiveDraft({type:"draft",text:"peer in flight",revision:3,updated_at:3,
  client_id:"device-b",client_seq:2});
const whilePending={text:first.view.composer.ta.value,deferred:first.view.draftDeferred.text};
first.view.receiveDraft({type:"draft",text:"local winner",revision:4,updated_at:4,
  client_id:"device-a",client_seq:2});
const afterAck={text:first.view.composer.ta.value,deferred:first.view.draftDeferred,
  journal:storage.has("puppy.draft.s:0:42")};
first.view.receiveDraft({type:"draft",text:"latest peer",revision:5,updated_at:5,
  client_id:"device-b",client_seq:3});

const imagePath="/private/uploads/42/1700000000000-abcdef0123/photo.png";
const marker=`${ATTACH_IMAGE_PREFIX}${imagePath}${ATTACH_IMAGE_SUFFIX}`;
first.view.receiveDraft({type:"draft",text:marker,revision:6,updated_at:6,
  client_id:"device-b",client_seq:4});
const sharedAttachment={count:first.view.composer.attachments.length,
  path:first.view.composer.attachments[0].path,prose:first.view.composer.ta.value};
first.view.composer.attachments.push({path:"",url:"blob:upload",ownsUrl:true,uploading:true,
  removed:false,controller:null});
first.view.receiveDraft({type:"draft",text:"peer prose",revision:7,updated_at:7,
  client_id:"device-b",client_seq:5});
const uploadPreserved={count:first.view.composer.attachments.length,
  uploading:first.view.composer.attachments[0].uploading,text:first.view.composer.ta.value};

writeDraftJournal("s:0:43", "pending text", 2);
const invalidDraft=makeView("s:0:43");
const beforeInvalidDraft=storage.get("puppy.draft.s:0:43");
let rejectedDraft=false;
try { invalidDraft.view.initializeDraft(null); }
catch(error) { rejectedDraft=/invalid shared draft/.test(error.message); }
const malformedDraft={rejected:rejectedDraft,ready:invalidDraft.view.draftReady,
  sent:invalidDraft.sent,unchanged:storage.get("puppy.draft.s:0:43")===beforeInvalidDraft};
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
offline.view.composer.ta.value="typed while disconnected";
offline.view.saveDraft();
const offlineBefore={ready:offline.view.draftReady,
  touched:offline.view.draftTouchedBeforeReady};
offline.view.ws={readyState:WebSocket.OPEN,
  send:value=>offline.sent.push(JSON.parse(value))};
offline.view.initializeDraft({text:"server while away",revision:8,updated_at:9});

const burst=makeView("s:0:48");
burst.view.composer.ta.value="a";
burst.view.saveDraft();
burst.view.composer.ta.value="ab";
burst.view.saveDraft();
burst.view.composer.ta.value="latest";
burst.view.saveDraft();
const burstBeforeAck=burst.sent.slice();
burst.view.receiveDraft({type:"draft",text:"a",revision:1,updated_at:10,
  client_id:"device-a",client_seq:1});
const burstAfterFirstAck=burst.sent.slice();
burst.view.receiveDraft({type:"draft",text:"latest",revision:2,updated_at:11,
  client_id:"device-a",client_seq:2});

const submission=makeView("s:0:49");
submission.view.composer.ta.value="first";
submission.view.saveDraft();
submission.view.composer.ta.value="send this exact value";
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
editing.view.composer.ta.value="old composer";
editing.view.saveDraft();
editing.view.composer.ta.value="newest old composer";
editing.view.saveDraft();
let uploadAborted=false;
editing.view.composer.attachments=[{path:"",url:"blob:editing",ownsUrl:true,uploading:true,
  removed:false,uploadId:"",controller:{abort(){uploadAborted=true;}}}];
editing.view.editQueued(1,"queued replacement");
const journalDuringEdit=JSON.parse(storage.get("puppy.draft.s:0:50"));
editing.view.saveDraft();
editing.view.receiveDraft({type:"draft",text:"old composer",revision:1,updated_at:15,
  client_id:"device-a",client_seq:1});
editing.view.receiveDraft({type:"draft",text:"queued replacement",revision:2,
  updated_at:16,client_id:"",client_seq:0});
const beforeEditComplete={text:editing.view.composer.ta.value,sent:editing.sent.slice(),
  readOnly:editing.view.composer.ta.readOnly,deferred:editing.view.draftDeferred.text};
editing.view.queueEditComplete({type:"queue_edit_complete",request_id:
  editing.sent[1].request_id,ok:true,started:false,
  draft:{type:"draft",text:"queued replacement",revision:2,updated_at:16,
    client_id:"",client_seq:0}});

console.log(JSON.stringify({sent:first.sent,pendingJournal,journalCleared,followed,
  whilePending,afterAck,latest:first.view.composer.ta.value,sharedAttachment,uploadPreserved,
  malformedDraft,invalidJournal,
  stale:{text:stale.view.composer.ta.value,sent:stale.sent,
         journal:storage.has("puppy.draft.s:0:44"),
         caret:stale.view.composer.ta.selectionStart},
  unacked:{text:unacked.view.composer.ta.value,sent:unacked.sent,
           caret:unacked.view.composer.ta.selectionStart},
  offline:{before:offlineBefore,text:offline.view.composer.ta.value,sent:offline.sent},
  burst:{beforeAck:burstBeforeAck,afterFirstAck:burstAfterFirstAck,
         journal:storage.has("puppy.draft.s:0:48")},
  submission:{sent:submissionSent,text:submission.view.composer.ta.value,
              journal:storage.has("puppy.draft.s:0:49")},
  editing:{before:beforeEditComplete,journalDuringEdit,
           text:editing.view.composer.ta.value,caret:editing.view.composer.ta.selectionStart,
           readOnly:editing.view.composer.ta.readOnly,focused:editing.view.composer.ta.focused,
           pending:editing.view.queueEditPending,uploadAborted,
           revoked:URL.revoked.includes("blob:editing"),
           attachments:editing.view.composer.attachments.length,
           journal:storage.has("puppy.draft.s:0:50")}}));
""" % (draft_helpers, attachment_helpers, composer, session_view)
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
    assert result["malformedDraft"] == {
        "rejected": True, "ready": False, "sent": [], "unchanged": True}, result
    assert result["invalidJournal"] is None, result
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
    assert "this.rebuildTranscript(d.events" in snapshot
    assert "this.renderEvent(ev, false)" not in snapshot

    def method(name):
        start = ui_source.index("\n  " + name + "(") + 1
        brace = ui_source.index(") {", start) + 2
        depth = 0
        for index in range(brace, len(ui_source)):
            if ui_source[index] == "{":
                depth += 1
            elif ui_source[index] == "}":
                depth -= 1
                if depth == 0:
                    return ui_source[start:index + 1]
        raise AssertionError("unbalanced SessionView." + name)

    rebuilt = method("rebuildTranscript")
    assert "const fragment = document.createDocumentFragment();" in rebuilt
    assert "this.inner.appendChild(fragment);" in rebuilt
    assert "this.buildEventNode(ev)" in rebuilt

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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert view.index('class="br-bar"') < view.index('class="br-meta edge-scroll-viewport"') < \
        view.index('class="br-stage"')
    assert 'aria-label="Copy Browser ID"' in view
    # The stream statistics have help; the session-link pill is self-labelled.
    owner_start = view.index('class="br-owner"')
    owner = view[owner_start:view.index('</button>', owner_start)]
    assert ' title=' not in owner and ".title =" not in view and "data-tip" not in view
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
    assert ".br-ident .br-copy-id{width:27px;height:100%;" in css_source
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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

    # the sent message and the side-question answer share one implementation,
    # so exercising the message control exercises both
    helper = "\n".join(extract(name) for name in (
        "wireCopyButton", "hoverCopyButton", "userMessageCopyButton"))
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert "this.composer.replace(draft.text, true, true);" in ui_source
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
    assert '.queue-strip .q-item.q-paused .q-t{opacity:.72}' in css_source
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
    assert 'if (e.isComposing) return;' in ui_source
    assert 'if (e.key === "Enter" && !e.shiftKey) ' \
        '{ e.preventDefault(); this.host.submit(); return; }' in ui_source
    assert 'submit: () => this.submit(),' in ui_source
    assert 'backend.capabilities.includes("active-turn-steering")' in ui_source
    assert 'backend.capabilities.includes("session-control-ws-v1")' in ui_source
    assert 'await this.sendActiveTurnControl("steer", body)' in ui_source

    # Copy shortcuts inside the textarea stay entirely browser-native. Escape
    # and the visible Stop button are the explicit ways to interrupt a turn.
    keydown_start = ui_source.index('  keydown(e) {')
    keydown_end = ui_source.index('  newline() {', keydown_start)
    keydown = ui_source[keydown_start:keydown_end]
    assert 'if (e.key === "Escape") {' in keydown
    assert "this.host.escape(e);" in keydown
    host_start = ui_source.index('this.composer = new Composer(')
    host_end = ui_source.index('    // Paint the crash journal', host_start)
    assert "this.interrupt();" in ui_source[host_start:host_end]
    assert 'e.key === "c"' not in keydown and 'e.key === "C"' not in keydown
    assert "ctrlCStreak" not in ui_source
    assert ('this.sendBtn.onclick = () => this.status === "running" ? '
            'this.interrupt() : this.submit();') in ui_source
    assert 'interrupt() {' in ui_source
    assert 'type: "interrupt", clear_queue: false' in ui_source
    assert 'case "steering_state":' in ui_source
    assert 'case "steer_status":' in ui_source
    assert 'api(this.tab.bid, `sessions/${this.tab.sid}/steer`' in ui_source
    assert "request_id: request.requestId" in ui_source
    assert "expected_turn_id: request.turnId" in ui_source
    assert "this.steerBtn.classList.toggle(\"hidden\", !(running && supported));" \
        in ui_source
    assert "hasAttachments" in ui_source and \
        "Steering accepts text only · queue the message to attach files" in ui_source

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
    assert ".btn-send,.btn-queue,.btn-steer,.btn-ask{" in css_source
    assert ".btn-steer{" in css_source
    assert "background-color:var(--ok-lo);" in css_source
    assert ".btn-send:disabled,.btn-queue:disabled,.btn-steer:disabled,\n.btn-ask:disabled{" \
        in css_source
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
    assert ".composer-action-icon{display:var(--live-action-icon,none);align-items:center;justify-content:center}" in css_source
    assert ".composer-action-icon svg{display:block}" in css_source
    assert ".composer-action-label{display:var(--live-action-label,inline)}" in css_source
    assert "@container composer (max-width:480px)" in css_source
    assert "--live-action-width:32px;--live-action-padding:0;" in css_source
    assert "--live-action-width:34px;--live-action-padding:0;" in css_source
    assert "--live-action-label:none;--live-action-icon:flex;" in css_source
    assert ".btn-queue,.btn-steer,.btn-ask{height:34px;gap:0}" in css_source

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
    composer = ui_source[ui_source.index("class Composer {"):
                         ui_source.index("/* ================= SessionView =================")]
    script = r"""
let calls=[],toasts=[],draftSaves=0,controlSyncs=0;
const newDraftClientId=()=>"request-1";
const toast=(...args)=>toasts.push(args);
const backendSupportsSessionControlSocket=()=>false;
const api=async (bid,path,options)=>{
  calls.push({bid,path,options});
  return {ok:true,status:"sent",request_id:options.body.request_id};
};
const attachmentMarkerLine=()=>{throw new Error("unexpected attachment in steering fixture");};
%s
const proto={%s};
const view=Object.assign(Object.create(proto),{
  tab:{bid:7,sid:42},
  draftReady:true,steering:{supported:true,ready:true,turn_id:"turn-9"},
  steerPending:null,_forceScroll:false,
  updateSteerControl(){controlSyncs++;},resizeComposer(){},
  releaseHistoryAttachments(){},saveDraft(){draftSaves++;},scrollBottom(){},
});
view.composer=Object.assign(Object.create(Composer.prototype), {
  host:{bid:7,sid:42},ta:{value:"  updated direction  ",removeAttribute(){}},attachments:[],
  histAttach:null,resize(){},renderAttachments(){},hideMention(){},
});
await view.steer();
const sent={calls,toasts,draftSaves,controlSyncs,text:view.composer.ta.value,
  pending:view.steerPending,forceScroll:view._forceScroll};
view.composer.ta.value="later";
view.steering={supported:true,ready:false,turn_id:""};
await view.steer();
console.log(JSON.stringify({sent,afterNotReady:{calls:calls.length,toasts}}));
""" % (composer, steer_method)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", with_live_views(script)],
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

    def class_method(name):
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

    control_script = r"""
const WebSocket={OPEN:1};
const frames=[];
const proto={%s,%s};
const view=Object.assign(Object.create(proto),{
  ws:{readyState:1,send:value=>frames.push(JSON.parse(value))},
  controlRequests:new Map(),
});
const accepted=view.sendActiveTurnControl("steer",{
  text:"new direction",request_id:"steer-1",expected_turn_id:"turn-1"});
const waiting=view.controlRequests.size;
view.completeActiveTurnControl("steer",{
  type:"steer_complete",request_id:"steer-1",ok:true,status:"sent"});
const acceptedResult=await accepted;
const rejected=view.sendActiveTurnControl("ask",{
  question:"why?",request_id:"ask-1",expected_turn_id:"turn-1"});
view.completeActiveTurnControl("ask",{
  type:"ask_complete",request_id:"ask-1",error:"turn ended"});
let rejectedMessage="";
try{await rejected;}catch(error){rejectedMessage=error.message;}
console.log(JSON.stringify({frames,waiting,remaining:view.controlRequests.size,
  accepted:acceptedResult.status,rejectedMessage}));
""" % (class_method("sendActiveTurnControl"),
         class_method("completeActiveTurnControl"))
    proc = subprocess.run(["node", "--input-type=module", "-e", control_script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    control = json.loads(proc.stdout)
    assert control == {
        "frames": [
            {"type": "steer", "text": "new direction", "request_id": "steer-1",
             "expected_turn_id": "turn-1"},
            {"type": "ask", "question": "why?", "request_id": "ask-1",
             "expected_turn_id": "turn-1"},
        ],
        "waiting": 1, "remaining": 0, "accepted": "sent",
        "rejectedMessage": "turn ended",
    }, control


def check_side_question_ui(ui_source: str, css_source: str) -> None:
    """Ask sits in the composer's running-action family and folds its answer.

    The control is the quiet member of that family: a question changes nothing
    about the turn, so it wears the neutral control face rather than Send's
    accent or Steer's go-colour, and it collapses to the drawn question mark
    on the same square grid as the other two when labels are dropped.
    """
    ask_markup = ('<button class="btn-ask hidden" type="button" '
                  'aria-label="Ask a side question">')
    steer_markup = ('<button class="btn-steer hidden" type="button" '
                    'aria-label="Steer the active turn">')
    assert ask_markup in ui_source
    assert ui_source.index(ask_markup) < ui_source.index(steer_markup)
    assert 'class="composer-action-label">Ask</span>' in ui_source
    assert "function askActionIcon(size = 18)" in ui_source
    assert ('this.askBtn.querySelector(".composer-action-icon").' +
            'appendChild(askActionIcon());') in ui_source
    assert "this.askBtn.onclick = () => this.ask();" in ui_source
    assert ".btn-ask{" in css_source
    assert ".btn-queue,.btn-steer,.btn-ask{" in css_source
    assert "--live-action-label:none;--live-action-icon:flex;" in css_source

    # one sync point: the two live turn-input controls answer to the same
    # conditions, so steering's updater drives this one too
    assert "this.updateAskControl();" in ui_source
    steer_control = ui_source[ui_source.index("  updateSteerControl() {"):
                              ui_source.index("  visibleStatusText() {")]
    assert "this.updateAskControl();" in steer_control

    assert 'backend.capabilities.includes("active-turn-side-question")' in ui_source
    assert 'api(this.tab.bid, `sessions/${this.tab.sid}/ask`' in ui_source
    assert 'await this.sendActiveTurnControl("ask", body)' in ui_source
    assert 'case "side_question_state":' in ui_source
    assert 'case "side_question_progress":' in ui_source

    # the answer is its own event folded into the question's card, the way a
    # tool result folds into its tool call
    assert 'case "side_question": {' in ui_source
    assert 'case "side_question_result": {' in ui_source
    assert "this.asideCards[d.request_id] = n;" in ui_source
    assert "delete this.asideCards[d.request_id];" in ui_source
    assert ".aside-card{" in css_source and ".aside-a{" in css_source

    # The answer half carries its own hover-revealed copy control, built from
    # the very helper a sent message uses, and anchored below the divider so
    # it can never read as belonging to the question above it.
    assert 'return hoverCopyButton(text, "aside-copy", "Copy answer");' in ui_source
    assert 'return hoverCopyButton(text, "user-copy", "Copy message");' in ui_source
    assert "if (d.text) body.appendChild(asideAnswerCopyButton(d.text));" in ui_source
    assert ".code-copy,.user-copy,.aside-copy{" in css_source
    assert ".user-copy,.aside-copy{opacity:0}" in css_source
    assert ".aside-copy{top:5px;right:0}" in css_source
    aside_start = css_source.index("\n.aside-a{") + 1
    assert "position:relative" in css_source[
        aside_start:css_source.index("}", aside_start)]

    # no accent rail on the card: the border is uniform on all four sides,
    # in the base rule and in every state variant of it
    card_start = css_source.index(".aside-card{")
    aside_block = css_source[card_start:css_source.index(".result-line{", card_start)]
    assert "border-left" not in aside_block, aside_block

    # The answer goes through the same md() as an assistant message, so the
    # markdown rules belong to the container rather than to whichever surface
    # holds it. Scoped to .msg-assistant they left the aside card with
    # unindented lists whose markers hung outside its left edge, and no code,
    # table, quote or paragraph rhythm at all.
    for rule in (".md>*+*{", ".md pre{", ".md code{", ".md ul,.md ol{",
                 ".md blockquote{", ".md table{", ".md h1,.md h2,.md h3{"):
        assert rule in css_source, rule
    assert ".md code{font-family:var(--mono);font-size:.92em;background:var(--hov);" in css_source
    light_theme = css_source[css_source.index("html.light{"):]
    assert "--hov:" in light_theme
    assert ".msg-assistant ul" not in css_source
    assert ".msg-assistant code{" not in css_source
    assert ".msg-assistant pre{" not in css_source


def check_modal_surface(ui_source: str, css_source: str) -> None:
    """Every modal wears the New session modal's surface and title.

    That modal is the reference the others are matched to, so the panel
    material and the title voice are declared once for cards and modals
    together. The trap this guards is source order: `.modal` is defined far
    below the shared rule, so a background restated there would silently win
    and take every modal back to its own look.
    """
    assert ".card,.modal{" in css_source
    assert ".card h2,.modal h2{" in css_source
    # no modal may opt out of the shared surface with its own variant rule
    assert ".modal.new-session-modal" not in css_source
    assert ".modal.agent-notes-modal" not in css_source

    start = css_source.index("\n.modal{") + 1
    block = re.sub(r"/\*.*?\*/", "", css_source[start:css_source.index("}", start)],
                   flags=re.S)
    assert "background" not in block, block

    # one body-copy voice: no modal restates it under a private name
    assert ".modal-copy{" in css_source
    # copy runs to paragraphs inside that voice, and a confirm's subject (a
    # task or backend name) is a line of its own rather than part of a sentence
    assert ".modal-copy+.modal-copy{margin-top:-4px}" in css_source
    assert ".modal-subject{color:var(--txt);font-weight:600;overflow-wrap:anywhere}" in css_source
    assert ".backend-edit-intro{" not in css_source
    assert ".listener-handoff-status{" not in css_source
    for markup in ('<p class="modal-copy">Update its display name',
                   '<p class="modal-copy">The session keeps its transcript'):
        assert markup in ui_source, markup
    assert 'el("p", "modal-copy listener-handoff-status"' in ui_source

    # and one footer behaviour on a narrow touch viewport, for every modal
    assert ".modal .m-btns .btn{flex:1 1 0;min-width:0}" in css_source
    assert ".backend-edit-modal .m-btns" not in css_source


def check_system_prompt_settings(ui_source: str, css_source: str) -> None:
    """The prompt editor stays backend-aware and Engine updates shares its card."""
    assert ui_source.count("<h2>Engine updates</h2>") == 1
    assert "New npm releases wait at least 10 minutes" in ui_source
    assert 'el("button", "seg-btn", "When ready")' in ui_source
    assert 'return "Installs after the 10-minute release wait";' in ui_source
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
    guidance_copy = ("Sent to every model turn on backends where the browser is enabled; "
                     "it is not sent on backends where it is off.")
    assert guidance_copy in ui_source.replace('" +\n      "', "")
    terminal_copy = ("Sent only when this backend offers shared terminal tools; "
                     "it is not sent when the terminal is unavailable.")
    assert terminal_copy in ui_source.replace('" +\n      "', "")
    spawn_copy = ("Sent with every model turn on this backend; it governs when "
                  "the agent may delegate one-shot spawned agents to Puppy's backends.")
    assert spawn_copy in ui_source.replace('" +\n      "', "")
    assert "browserNote.textContent = `Sent only for turns on ${node.name}" not in ui_source
    assert 'body.remote_workspace = record.remoteWorkspaceDraft' in ui_source
    assert 'body.terminal = record.terminalDraft' in ui_source
    assert 'body.spawn = record.spawnDraft' in ui_source
    runner_source = (BASE / "puppy" / "runner.py").read_text()
    assert 'system_prompt_text = "" if tool else system_prompts.turn_prompt(' \
        in runner_source
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
  addEventListener(name,listener){this["on"+name]=listener;}
  click(){return this.onclick();}
  setAttribute(name,value){this.attributes[name]=String(value);}
  removeAttribute(name){delete this.attributes[name];if(name==="maxlength")this.maxLength=-1;}
  focus(){document.activeElement=this;}
  set innerHTML(value){this.html=value;}
  get innerHTML(){return this.html;}
}
const document={activeElement:null,createElement:tag=>new MockNode(tag)};
const el=(tag,cls="",text="")=>new MockNode(tag,cls,text);
const enhanceChoiceSelect=()=>{},refreshChoiceSelect=()=>{};
const TOAST_LONG=7000;
__BACKEND_NOTE__
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
const invalidPayload=(custom,browser)=>({custom,browser,browser_default:"DEFAULT",max_chars:100});
async function api(bid,path,options={}) {
  calls.push({bid,path,method:options.method||"GET",body:options.body||null});
  if(options.method==="PATCH") {
    if(bid===2) return {system_prompt:invalidPayload(options.body.custom,options.body.browser)};
    return {system_prompt:payload(options.body.custom,options.body.remote_workspace,
      options.body.browser,options.body.terminal,options.body.spawn)};
  }
  if(bid===2) return {system_prompt:invalidPayload("INCOMPLETE","INCOMPLETE BROWSER")};
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
  {bid:0,name:"Primary"},{bid:1,name:"Worker node"},{bid:2,name:"Malformed prompts"},
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
const invalid={disabled:custom.disabled&&remoteWorkspace.disabled&&browser.disabled&&terminal.disabled&&spawn.disabled,
  status:status.textContent,saveLabel:save.textContent,saveDisabled:save.disabled};
await save.onclick();
select.value="3";document.activeElement=select;select.onchange();
const unsupported={disabled:custom.disabled&&remoteWorkspace.disabled&&browser.disabled&&terminal.disabled&&spawn.disabled&&save.disabled,
  status:status.textContent};
console.log(JSON.stringify({before,dirty,resetState,saved,remote,offline,invalid,unsupported,calls}));
""".replace("__METHOD__", method).replace("__BACKEND_NOTE__", ui_source[
        ui_source.index("function backendStateNote("):
        ui_source.index("function engineStatusText(")])
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["before"] == {
        "custom": "LOCAL", "remoteWorkspace": "REMOTE DEFAULT", "browser": "DEFAULT",
        "terminal": "TERMINAL DEFAULT", "spawn": "SPAWN DEFAULT",
        "status": "Up to 100 characters per field",
        "remoteNote": remote_copy, "browserNote": guidance_copy,
        "terminalNote": terminal_copy, "spawnNote": spawn_copy,
    }, result
    assert result["dirty"] == "Unsaved changes", result
    assert result["resetState"] == {
        "remoteWorkspace": "REMOTE DEFAULT", "browser": "DEFAULT",
        "terminal": "TERMINAL DEFAULT", "spawn": "SPAWN DEFAULT",
        "status": "Unsaved changes"}, result
    assert result["saved"] == {
        "status": "Saved for new turns", "toast": "Primary: System prompt saved"}, result
    assert result["remote"]["custom"] == "REMOTE" and \
        result["remote"]["remoteWorkspace"] == "REMOTE WORKSPACE" and \
        result["remote"]["browser"] == "REMOTE BROWSER" and \
        result["remote"]["terminal"] == "REMOTE TERMINAL" and \
        result["remote"]["spawn"] == "REMOTE SPAWN", result
    assert result["offline"] == {
        "custom": "REMOTE", "remoteWorkspace": "REMOTE WORKSPACE",
        "browser": "REMOTE BROWSER", "terminal": "REMOTE TERMINAL",
        "spawn": "REMOTE SPAWN",
        "status": "Backend unavailable · showing last known prompt settings",
        "disabled": True,
    }, result
    assert result["invalid"] == {
        "disabled": True, "status": "backend returned invalid system prompt settings",
        "saveLabel": "Retry", "saveDisabled": False,
    }, result
    assert result["unsupported"] == {
        "disabled": True,
        "status": "Backend upgrade required for system prompt settings"}, result
    assert [call["method"] for call in result["calls"]] == \
        ["PATCH", "GET", "GET", "GET"], result
    assert result["calls"][0]["body"]["remote_workspace"] == "REMOTE DEFAULT", result
    assert result["calls"][0]["body"]["terminal"] == "TERMINAL DEFAULT", result
    assert result["calls"][0]["body"]["spawn"] == "SPAWN DEFAULT", result
    assert all(call["method"] == "GET" for call in result["calls"] if call["bid"] == 2), result


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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert 'aria-label="Backend the commands run on"' in ui_source
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
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "className": "prov-opencode", "text": "OC", "label": "OpenCode"}


def check_dynamic_model_catalog_ui(ui_source: str) -> None:
    """Every engine payload reaches live pickers without inventing efforts."""
    assert "const enginePayloadListeners = new Set();" in ui_source
    assert "auto_upgrade: s.auto_upgrade || state.autoUpgrade" in ui_source
    assert "rememberEnginePayload(0, payload);" in ui_source
    assert "enginePayloadListeners.add(enginePayloadListener);" in ui_source
    assert "onClose(() => enginePayloadListeners.delete(enginePayloadListener));" \
        in ui_source
    assert "renderEngines(loaded);\n    try {" in ui_source
    assert "renderEngines(loaded, true);" in ui_source
    assert "select.dataset.engineChoicesDirty = \"true\";" in ui_source
    assert "select.dataset.engineChoiceBusy = \"true\";" in ui_source
    assert ("this.syncNativeComposerChoices();\n"
            "    this.syncToolsButton();\n"
            "    this.syncFastIndicator();\n"
            "    this.syncComposerMeta();") in ui_source
    assert "Re-check engine versions, sign-in and model lists" in ui_source
    assert "Model-list refresh warning" in ui_source

    start = ui_source.index("function effortOptionsForModel(")
    end = ui_source.index("\n\nconst headWord", start)
    script = ui_source[start:end] + r'''
const exact={allow_custom_model:false,model_options:[
  {value:"known",effort_options:[{value:"",label:"Default"},
                                 {value:"high",label:"High"}]}],
  effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]};
const custom={allow_custom_model:true,model_options:[],
  effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]};
const saved={permission_mode:"read-only",model:"known",effort:"high"};
const current=initialEngineConfig({...exact,session_defaults:saved});
current.model="changed";
let missingDefaultsRejected=false;
try { initialEngineConfig({default_permission:"auto",model_options:[{value:""}]}); }
catch(error) { missingDefaultsRejected=/engine defaults/.test(error.message); }
const select={children:[],appendChild(row){this.children.push(row)}};
globalThis.document={createElement(){return {}}};
globalThis.refreshChoiceSelect=()=>{};
fillEngineChoice(select, [{value:"",label:"Engine default"}], "retired");
console.log(JSON.stringify({
  known:effortOptionsForModel(exact,"known").map(item=>item.value),
  retired:effortOptionsForModel(exact,"retired").map(item=>item.value),
  custom:effortOptionsForModel(custom,"custom-id").map(item=>item.value),
  saved:initialEngineConfig({...exact,session_defaults:saved}),
  missingDefaultsRejected,
  nativeDefault:effortOptionsForModel(exact,"known")[0].label,
  unavailable:select.children[0].disabled && select.value === "retired",
}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "known": ["", "high"], "retired": [""], "custom": ["", "max"],
        "saved": {"permission_mode": "read-only", "model": "known", "effort": "high"},
        "missingDefaultsRejected": True,
        "nativeDefault": "Engine default", "unavailable": True}


def check_timer_settings_ui(ui_source: str, css_source: str) -> None:
    """The six timer controls retain polling for disconnected streams."""
    for label in (
            "Published CLI releases", "Model catalogs",
            "Installed CLI versions and sign-in status",
            "Remote session fallback polling",
            "Remote metadata fallback polling",
            "Remote completion notification synchronization"):
        assert label in ui_source
    assert 'backend.capabilities.includes("timer-settings")' in ui_source
    assert 'method: "PATCH", body: { [spec.key]: value }' in ui_source
    assert 'api(bid, "timers", { timeoutMs: 10000 })' in ui_source
    assert '"Apply to other online backends?"' in ui_source
    assert '{ confirmLabel: "Apply to all", destructive: false }' in ui_source
    assert '"Reset all to defaults"' in ui_source
    assert '"Reset timers everywhere?"' in ui_source
    assert '{ confirmLabel: "Reset all", destructive: false }' in ui_source
    assert "body: { ...current.defaults }" in ui_source
    assert "if (localUpdated) startRemotePolling();" in ui_source
    assert "Promise.allSettled(targets.map" in ui_source
    assert 'failed: ${failed.join("; ")}' in ui_source
    assert 'Math.min(timerMilliseconds("remote_session_seconds"),' in ui_source
    assert 'timerMilliseconds("remote_engine_seconds")' in ui_source
    assert "state.remoteSessionCheckedAt" in ui_source
    assert "remotePollingTickMilliseconds()" in ui_source
    assert ".timer-row{" in css_source
    assert ".timer-section{" in css_source
    assert ".timer-node{" in css_source
    assert ".timer-actions{" in css_source

    constants_start = ui_source.index("const TIMER_DEFAULT_VALUES")
    constants_end = ui_source.index("/* Browser-clock anchors", constants_start)
    normalize_start = ui_source.index("function normalizeTimerSettings(")
    normalize_end = ui_source.index("\n\n/* Every node's engine payload", normalize_start)
    script = r'''const state={timers:null,remoteTimers:{}};
''' + ui_source[constants_start:constants_end] + "\n" + \
        ui_source[normalize_start:normalize_end] + r'''
const payload={values:{...TIMER_DEFAULT_VALUES},defaults:{...TIMER_DEFAULT_VALUES},limits:{}};
for(const name of Object.keys(TIMER_DEFAULT_VALUES)) payload.limits[name]={
  min:name==="remote_session_seconds"?2:1,
  max:10080,
  unit:name.endsWith("_minutes")?"minutes":"seconds",
};
state.timers=normalizeTimerSettings(payload);
const before=remotePollingTickMilliseconds();
payload.values.remote_session_seconds=120;
payload.values.remote_engine_seconds=7;
state.timers=normalizeTimerSettings(payload);
console.log(JSON.stringify({before,after:remotePollingTickMilliseconds(),valid:!!state.timers}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {"before": 12000, "after": 7000, "valid": True}

    targets_start = ui_source.index("function onlineTimerTargets(")
    targets_end = ui_source.index("\n\nfunction backendSupportsSystemPrompt", targets_start)
    script = r'''const statuses={1:"ok",2:"ok",3:"bad",4:"ok",5:"ok"};
const supported=new Set([0,1,2,3,5]);
const reachable=new Set([0,1,2,3,4]);
function backendSupportsTimerSettings(bid){return supported.has(bid);}
function remoteAvailability(bid){return statuses[bid] || "pending";}
function backendConnectionAllowed(bid){return reachable.has(bid);}
''' + ui_source[targets_start:targets_end] + r'''
const nodes=[
  {bid:0,name:"Local"},{bid:1,name:"Source"},{bid:2,name:"Online"},
  {bid:3,name:"Offline"},{bid:4,name:"Legacy"},{bid:5,name:"Blocked"},
];
console.log(JSON.stringify({
  all:onlineTimerTargets(nodes).map(node=>node.name),
  remote:onlineTimerPropagationTargets(nodes,1).map(node=>node.name),
  local:onlineTimerPropagationTargets(nodes,0).map(node=>node.name),
}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "all": ["Local", "Source", "Online"],
        "remote": ["Local", "Online"], "local": ["Source", "Online"]}

    propagate_start = ui_source.index("async function propagateTimerSetting(")
    propagate_end = ui_source.index("\n\nfunction backendSupportsSystemPrompt", propagate_start)
    script = r'''const calls=[];
async function api(bid,path,options){
  calls.push({bid,path,options});
  if(bid===3) throw new Error("offline during save");
  return {timers:{bid}};
}
function rememberTimerSettings(bid,payload){return bid!==2 && payload.bid===bid;}
''' + ui_source[propagate_start:propagate_end] + r'''
const result=await propagateTimerSetting([
  {bid:1,name:"Good"},{bid:2,name:"Malformed"},{bid:3,name:"Gone"},
],"model_catalog_minutes",9);
console.log(JSON.stringify({result,calls}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout)
    assert result["result"] == {
        "updated": ["Good"],
        "failed": ["Malformed: backend returned invalid timer settings",
                   "Gone: offline during save"],
    }
    assert [call["bid"] for call in result["calls"]] == [1, 2, 3]
    assert all(call["path"] == "timers" and
               call["options"]["body"] == {"model_catalog_minutes": 9}
               for call in result["calls"])

    reset_start = ui_source.index("async function resetTimerSettings(")
    reset_end = ui_source.index("\n\nfunction backendSupportsSystemPrompt", reset_start)
    script = r'''const calls=[];
async function api(bid,path,options={}){
  calls.push({bid,path,options});
  if(!options.method){
    if(bid===4)return {timers:null};
    return {timers:{defaults:{node_default:bid}}};
  }
  if(bid===3)throw new Error("offline during reset");
  return {timers:{reset_bid:bid}};
}
function normalizeTimerSettings(payload){return payload&&payload.defaults?payload:null;}
function rememberTimerSettings(bid,payload){return payload&&payload.reset_bid===bid;}
''' + ui_source[reset_start:reset_end] + r'''
const result=await resetTimerSettings([
  {bid:0,name:"Local"},{bid:2,name:"Different"},
  {bid:3,name:"Gone"},{bid:4,name:"Malformed"},
]);
console.log(JSON.stringify({result,calls}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    result = json.loads(proc.stdout)
    assert result["result"] == {
        "updated": ["Local", "Different"],
        "failed": ["Gone: offline during reset",
                   "Malformed: backend returned invalid timer settings"],
        "localUpdated": True,
    }
    patch_calls = [call for call in result["calls"]
                   if call["options"].get("method") == "PATCH"]
    assert [(call["bid"], call["options"]["body"])
            for call in patch_calls] == [
                (0, {"node_default": 0}), (2, {"node_default": 2}),
                (3, {"node_default": 3})]

    # Dismissing either promise-backed dialog via Escape/backdrop must settle it;
    # otherwise the caller can remain disabled forever after a close without a button.
    confirm_start = ui_source.index("function modalSubjectHtml(")
    confirm_end = ui_source.index("\n\nfunction modalNotice", confirm_start)
    prompt_start = ui_source.index("function modalPrompt(")
    prompt_end = ui_source.index("\n\n/* Edit a paired backend", prompt_start)
    assert "onClose(() =>" in ui_source[confirm_start:confirm_end]
    assert "resolve(false)" in ui_source[confirm_start:confirm_end]
    assert "onClose(() =>" in ui_source[prompt_start:prompt_end]
    assert "resolve(null)" in ui_source[prompt_start:prompt_end]
    script = r'''let current=null;
function esc(value){return String(value);}
function modal(html){
  const controls={"#mc-no":{focus(){}},"#mc-yes":{focus(){}}};
  let listener=()=>{};
  const close=()=>listener();
  current={html,controls,dismiss:()=>listener()};
  return {m:{querySelector:key=>controls[key]},close,onClose:fn=>{listener=fn;}};
}
''' + ui_source[confirm_start:confirm_end] + r'''
const dismissedPromise=modalConfirm("Title","Copy");
current.dismiss();
const dismissed=await dismissedPromise;
const acceptedPromise=modalConfirm("Title","Copy",{
  confirmLabel:"Apply fleet",destructive:false,
});
const styled=current.html.includes("btn btn-pri") && current.html.includes("Apply fleet");
current.controls["#mc-yes"].onclick();
const accepted=await acceptedPromise;
modalConfirm("Title","Removed for good.\n\nNot touched:\nthe project",{subject:"  Task name  "});
const paragraphs=current.html.split('<p class="modal-copy">').length-1;
const subject=current.html.includes('<p class="modal-copy modal-subject">Task name</p><p class="modal-copy">Removed for good.</p>');
const lineBreak=current.html.includes("Not touched:<br>the project</p>");
current.dismiss();
modalConfirm("Title","");
const emptyCopy=current.html.includes('<p class="modal-copy"></p>') && !current.html.includes("modal-subject");
current.dismiss();
console.log(JSON.stringify({dismissed,accepted,styled,paragraphs,subject,lineBreak,emptyCopy}));
'''
    proc = subprocess.run(["node", "--input-type=module", "-e", with_live_views(script)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "dismissed": False, "accepted": True, "styled": True, "paragraphs": 2,
        "subject": True, "lineBreak": True, "emptyCopy": True}


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


def check_ui_contrast_palette(ui_source: str, css_source: str) -> None:
    """Small readable copy keeps AA contrast on every common app surface."""
    def palette(marker: str) -> dict:
        colors = {}
        # Shared forms and the console contribute to the same theme.
        for block in re.findall(re.escape(marker) + r"(.*?)\n\}", css_source, re.S):
            colors.update(re.findall(
                r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", block))
        return colors

    def luminance(value: str) -> float:
        channels = [int(value[index:index + 2], 16) / 255
                    for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= .04045 else
                  ((channel + .055) / 1.055) ** 2.4
                  for channel in channels]
        return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]

    def contrast(first: str, second: str) -> float:
        high, low = sorted((luminance(first), luminance(second)), reverse=True)
        return (high + .05) / (low + .05)

    dark = palette(":root{")
    light = palette("html.light{")
    for name, colors in (("dark", dark), ("light", light)):
        for ink in ("txt", "txt2", "txt3"):
            for surface in ("bg", "panel", "panel2"):
                ratio = contrast(colors[ink], colors[surface])
                assert ratio >= 4.5, (name, ink, surface, ratio)
        # These tokens label compact status and control surfaces, not merely
        # decorative artwork, so panel2 is the conservative common backdrop.
        for ink in ("acc", "ok", "warn", "err"):
            ratio = contrast(colors[ink], colors["panel2"])
            assert ratio >= 4.5, (name, ink, "panel2", ratio)

    assert "color:var(--terminal-overlay-ink);" in css_source
    assert contrast(dark["terminal-overlay-ink"], dark["bg"]) >= 7
    assert "@media (hover:none){\n  .sess-item .si-pin,.sess-item .si-notes{opacity:.55}" \
        in css_source

    # A fresh managed browser paints its own standalone document, so it must
    # carry the same readable light ink instead of silently retaining the old
    # palette outside app.css.
    for token in ("acc", "ok", "txt3"):
        assert "--{}:{};".format(token, light[token]) in browser.START_PAGE_HTML

    # Terminal programs own their ANSI choices. Puppy's neutral foreground is
    # already very high contrast and should not rewrite application palettes.
    assert 'foreground: "#e8e8ec"' in ui_source


def check_compact_control_alignment(ui_source: str, css_source: str) -> None:
    """Every icon-only hover box owns centred drawn geometry, not font ink."""
    icon_button = css_source[css_source.index(".icon-btn{"):
                             css_source.index("@media (hover:hover){.icon-btn:hover")]
    assert "display:inline-grid;place-items:center;" in icon_button
    assert "padding:0" in icon_button and "line-height:0" in icon_button
    assert ".icon-btn>svg{display:block;margin:0}" in css_source
    assert css_source.count(".burger{display:inline-grid}") == 2

    for selector in (".disclosure-toggle{", ".foot-node-act{", ".tab .t-close{",
                     ".code-copy,.user-copy,.aside-copy{", ".queue-strip .q-x{",
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert "syncPromptSpinnerPhase(activity); // inherited by the ::before ring" \
        in sidebar
    dot_start = ui_source.index("function sessDot(")
    dot_end = ui_source.index("\nconst PROVIDERS", dot_start)
    assert 'if (running) syncPromptSpinnerPhase(dot);' in \
        ui_source[dot_start:dot_end]
    tab_start = ui_source.index("function wireTabDrag(")
    tab_end = ui_source.index("\nfunction syncHorizontalOverflow", tab_start)
    assert 'if (tab.classList.contains("running")) syncPromptSpinnerPhase(tdot);' \
        in ui_source[tab_start:tab_end]
    script = r"""
const assert = require('node:assert/strict');
const {FakeDocument} = require(%s);
const document = new FakeDocument();
const el = (tag, cls, text = '') => {
  const node = document.createElement(tag);
  node.className = cls; node.textContent = text; return node;
};
const syncPromptSpinnerPhase = node => { node.synced = true; };
const guardNativeTouchDrag = () => () => false;
const suppressContextGestureActivation = () => {};
const xIcon = () => el('span', '');
let meta;
const findSessionMeta = () => meta;
%s
%s
for (const [session, running] of [
  [{status:'idle'}, false],
  [{status:'running'}, true],
  [{status:'idle', task_activity:{total:2, running:1}}, true],
  [{status:'idle', task_activity:{total:2, running:2, approval:1}}, true],
  [{status:'running', task_activity:{total:2, running:0}}, true],
  [{status:'idle', task_activity:{total:2, running:0, ready:2}}, false],
  [{status:'idle', task_activity:{total:0, running:0}}, false],
]) {
  meta = {...session, color:'#abc'};
  const dot = sessDot(meta);
  const tab = renderTabNode({id:'session', type:'session', bid:0, sid:1}, {}, null);
  assert.equal(dot.classList.contains('running'), running);
  assert.equal(tab.classList.contains('running'), running);
  assert.equal(!!dot.synced, running);
  assert.equal(!!tab.querySelector('.t-dot').synced, running);
  assert.equal(dot.style.color, '#abc');
  assert.equal(tab.querySelector('.t-dot').style.color, '#abc');
}
meta = null;
assert.equal(renderTabNode({type:'session', sid:1}, {}, null).classList.contains('running'), false);
""" % (json.dumps(str(BASE / "tests" / "fake_dom.js")),
       ui_source[dot_start:dot_end], ui_source[tab_start:tab_end])
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


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
    assert ".chat-head{\n  display:flex;align-items:center;gap:8px;padding:9px 14px;" \
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    """Saved node order only breaks ties in the merged sidebar."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "const nodes = sortNodeGroups([{ bid: 0 }]" in sidebar
    assert "const rows = mergeSidebarRows(nodes.map(node => ({" in sidebar
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
    assert "sessionRowRows(bid, s," in sidebar
    shared_rows = ui_source[ui_source.index("function sessionRowRows("):
                            ui_source.index("function renderSidebar()")]
    assert "sessionLocationLabel(s, bid));" in shared_rows
    assert "sessionLocationTitle(s, bid));" in shared_rows
    # the per-node openers moved to the status box beside its disclosures
    foot = ui_source[
        ui_source.index("function renderFootEngines()"):
        ui_source.index("\nfunction findSessionMeta", ui_source.index("function renderFootEngines()"))]
    assert "`Open browser on ${g.name}`" in foot
    assert "`Open terminal on ${g.name}`" in foot
    # reorder drags stay scoped to one backend inside the shared flat surface
    assert 'const rowSelector = () => `.sess-item[data-bid="${dragSess.bid}"]` +' \
        in ui_source
    assert '(dragSess.pinning ? `[data-pinned="${dragSess.pinned}"]` : "");' \
        in ui_source
    assert 'reorderChildren(root, `.sess-item[data-bid="${bid}"]`)' in ui_source
    assert "container.insertBefore(dragged, siblings[siblings.length - 1].nextSibling);" \
        in ui_source
    assert ".sess-empty{padding:12px 6px 5px;" in css_source

    start = ui_source.index("function sessionWorkspace(")
    end = ui_source.index("\nfunction sessionDeleteMessage", start)
    helpers = ui_source[start:end]
    script = r"""
const isScratchWorkspace=session=>!!session&&session.workspace_kind==="temporary";
const backendName=bid=>bid===7?"Builder":"local";
const tailPath=(path,n)=>path.length>n?"…"+path.slice(-n):path;
%s
const plain={cwd:"/srv/apps/puppy"};
const linked={cwd:"/private/mirror/never-show",workspace:{
  root:"/srv/projects/sample-media",node:"Builder"}};
const scratch={cwd:"/srv/puppy/data/workspaces/session-test",workspace_kind:"temporary"};
const missing={cwd:"/srv/puppy/data/workspaces/session-test",workspace_kind:"temporary",
  workspace_missing:true};
console.log(JSON.stringify([
  sessionLocationLabel(plain,0),
  sessionLocationTitle(plain,0),
  sessionLocationLabel(linked,0),
  sessionLocationTitle(linked,0),
  sessionLocationLabel(scratch,7),
  sessionLocationLabel(missing,7),
  workspaceTitle(missing),
  workspaceTitle({...missing,task:{parent:1}}),
]));
""" % helpers
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    assert json.loads(proc.stdout) == [
        "/srv/apps/puppy",
        "/srv/apps/puppy",
        "Builder:…cts/sample-media",
        "Builder:/srv/projects/sample-media",
        "Scratch workspace",
        "Scratch workspace missing",
        "This scratch workspace is missing. It will be recreated before the next turn.",
        "This task's working copy is missing. Its conversation is kept; create a new task to resume the work.",
    ]


def check_node_owned_session_order(ui_source: str, css_source: str) -> None:
    """The sidebar merges durable node orders without a local activity overlay."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert "const list = rows" in sidebar
    assert "animateSessionRows(root, () => {" in sidebar
    assert '"IDLE"' not in sidebar
    assert "activity.textContent = backendName(bid);" in sidebar
    assert "`Session idle on ${backendName(bid)}`" in sidebar
    # The node owns one durable ordering; the browser has no promotion overlay.
    assert "orderSidebarRows" not in ui_source
    assert "sessionActivityPromotions" not in ui_source
    assert ".filter(id => !floated.has(id));" not in ui_source
    assert "const visualChanged = visualOrder.length !== context.originalOrder.length" \
        in ui_source
    assert "if (!changed) {" in ui_source
    # both FLIP helpers share one motion clock
    assert ui_source.count("{ duration: REORDER_MOTION_MS, easing: REORDER_EASING });") == 2
    assert ".si-be.node{" in css_source
    assert ".si-be.idle" not in css_source


def check_session_pins(ui_source: str, css_source: str) -> None:
    """Pins are capable-node actions and the node remains order-authoritative."""
    sidebar = ui_source[
        ui_source.index("function renderSidebar()"):
        ui_source.index("\nfunction sessDot", ui_source.index("function renderSidebar()"))]
    assert 'item.dataset.pinned = s.pinned === true ? "1" : "0";' in sidebar
    pin_append = "actions.appendChild(sessionPinMark(bid, s))"
    notes_append = "actions.appendChild(agentNotesMark(bid, s))"
    assert sidebar.index(pin_append) < sidebar.index(notes_append)
    assert 'const actions = el("span", "si-actions")' in sidebar
    assert "if (actions.childElementCount) r2.appendChild(actions);" in sidebar
    # Never locally sort on the flag: the array order is the node's contract.
    assert ".sort((" not in sidebar

    support = ui_source[
        ui_source.index("function backendSupportsSessionPinning("):
        ui_source.index("\nfunction backendSupportsAgentNotes(")]
    assert 'backend.capabilities.includes("session-pinning")' in support
    assert "backendHasCapability" not in support

    mark = ui_source[
        ui_source.index("function sessionPinMark("):
        ui_source.index("\n/* Bottom-right", ui_source.index("function sessionPinMark("))]
    for needle in ('mark.setAttribute("role", "button")',
                   'mark.setAttribute("aria-pressed"',
                   'mark.setAttribute("aria-label"',
                   'mark.addEventListener("pointerdown"',
                   'mark.addEventListener("contextmenu"',
                   'mark.addEventListener("dragstart"'):
        assert needle in mark
    assert '"Pin session to top"' in mark and '"Unpin session"' in mark

    context = ui_source[
        ui_source.index("function sessionContextMenu("):
        ui_source.index("\n/* Live sortable layouts", ui_source.index(
            "function sessionContextMenu("))]
    assert "backendSupportsSessionPinning(bid)" in context
    assert '"Pin session to top"' in context and '"Unpin session"' in context

    drag = ui_source[
        ui_source.index("let dragSess = null;"):
        ui_source.index("\n// below this much", ui_source.index("let dragSess = null;"))]
    assert 'e.target.closest(".si-pin,.si-notes")' in drag
    assert "sessionOrderPending.has(nodeKey)" in drag
    assert "orderSnapshot: sessionOrderSnapshot(bid)" in drag
    assert "sameSessionOrderSnapshot(" in drag
    assert "expected_order: previousIds" in drag
    assert "expected_pinned: previousPinned" in drag
    # Slot motion is cohort-scoped, but reconstruction deliberately includes
    # both cohorts so hidden archived/filter rows retain their slots.
    assert '`[data-pinned="${dragSess.pinned}"]`' in drag
    assert 'reorderChildren(root, `.sess-item[data-bid="${bid}"]`)' in drag

    payload_helper = ui_source[
        ui_source.index("function acceptSessionListPayload("):
        ui_source.index("\nasync function refreshSessionList(")]
    assert "remotePollSequence[node] = (remotePollSequence[node] || 0) + 1" \
        in payload_helper

    assert ".sess-item .si-pin," in css_source
    assert ".sess-item .si-actions{" in css_source
    assert "align-items:center;gap:0;" in css_source
    assert "width:22px;height:22px;margin:0;border-radius:var(--btn-r);" in css_source
    assert ".sess-item .si-pin.on{opacity:1;color:var(--acc2)}" in css_source
    assert ".sess-item .si-pin:focus-visible," in css_source

    # The row's two marks are one pair, not two icons that happen to sit side by
    # side: one drawing size in CSS, and one grid and stroke in the glyphs, so
    # neither can drift into looking heavier than the other.
    assert ".sess-item .si-pin svg,.sess-item .si-notes svg{width:14px;height:14px}" \
        in css_source
    assert "mark.appendChild(sessionPinIcon(14));" in ui_source
    assert "mark.appendChild(agentNotesIcon(14));" in ui_source
    for glyph in ("sessionPinIcon", "agentNotesIcon"):
        body = ui_source[ui_source.index("function %s(size = 14) {" % glyph):]
        body = body[:body.index("\n}\n")]
        assert 'setAttribute("viewBox", "0 0 18 18")' in body, glyph
        assert 'setAttribute("stroke-width", "1.25")' in body, glyph


def check_queued_permission_choices(ui_source: str) -> None:
    """Permission options and value follow the queued engine/config tail."""
    assert 'backend.capabilities.includes("session-fast-mode")' in ui_source
    assert 'eng.supports_fast_mode === true' in ui_source
    assert 'label: "Fast mode"' in ui_source
    assert 'modelOption.fast_mode_available === true' in ui_source
    assert 'const row = menuCheckRow(item.label, !!item.on' in ui_source
    choice_start = ui_source.index("  composerChoiceSpec(kind, native = false) {")
    choice_end = ui_source.index("\n  syncNativeComposerChoices(", choice_start)
    choice = ui_source[choice_start:choice_end]
    assert "options: [...((eng && eng.permission_options) || [])]" in choice
    assert "selected: eff.permission_mode || \"\"" in choice
    assert "backendSupportsQueuedPermission(this.tab.bid)" in choice
    assert 'bind("perm", (value) => this.applyPermissionChoice(value));' in ui_source
    assert 'setMini("perm", "Permission mode", eff.permission_mode || "auto",' \
        in ui_source
    assert 'backend.capabilities.includes("queued-permission-config")' in ui_source

    start = ui_source.index("function effectiveQueuedConfig(")
    end = ui_source.index("\n\nclass SessionView", start)
    script = r"""
%s
const engines = {
  claude: {default_permission:"auto"},
  codex: {default_permission:"workspace-write"},
};
const session = {engine:"claude", model:"sonnet", effort:"high",
  permission_mode:"auto", fast_mode:false};
const initial = effectiveQueuedConfig(session, []);
const switchRow = {kind:"engine", engine:"codex", model:"gpt-5.6-sol",
  effort:"max",permission_mode:"workspace-write",fast_mode:"off"};
const current = effectiveQueuedConfig(session, [switchRow]);
const switched = effectiveQueuedConfig(session, [{...switchRow,
  permission_mode:"workspace-write", fast_mode:"on"}]);
const picked = effectiveQueuedConfig(session, [{...switchRow,
  permission_mode:"workspace-write", fast_mode:"on"}, {kind:"config", engine:"codex",
  permission_mode:"danger-full-access"}]);
const stale = effectiveQueuedConfig(session, [{...switchRow,
  permission_mode:"workspace-write", fast_mode:"on"}, {kind:"config", engine:"codex",
  permission_mode:"danger-full-access"}, {kind:"config", engine:"claude",
  permission_mode:"plan"}]);
const slim = value => ({engine:value.engine, permission:value.permission_mode,
  fast:value.fast_mode, queuedEngine:value.queuedEngine,
  queuedPermission:value.queuedPermission, queuedFast:value.queuedFast});
console.log(JSON.stringify([initial, current, switched, picked, stale].map(slim)));
""" % ui_source[start:end]
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == [
        {"engine": "claude", "permission": "auto",
         "fast": False, "queuedEngine": False, "queuedPermission": False,
         "queuedFast": False},
        # A queued switch carries its captured permission and Fast settings.
        {"engine": "codex", "permission": "workspace-write",
         "fast": False, "queuedEngine": True, "queuedPermission": True,
         "queuedFast": True},
        {"engine": "codex", "permission": "workspace-write",
         "fast": True, "queuedEngine": True, "queuedPermission": True,
         "queuedFast": True},
        {"engine": "codex", "permission": "danger-full-access",
         "fast": True, "queuedEngine": True, "queuedPermission": True,
         "queuedFast": True},
        # A stale row validated for the old engine is ignored.
        {"engine": "codex", "permission": "danger-full-access",
         "fast": True, "queuedEngine": True, "queuedPermission": True,
         "queuedFast": True},
    ]


def check_fast_mode_indicator(ui_source: str, css_source: str) -> None:
    """Fast gets one feature-gated square and no off-state placeholder."""
    template_start = ui_source.index('<div class="composer-meta-scroll">')
    template_end = ui_source.index('${composerChoice("model"', template_start)
    template = ui_source[template_start:template_end]
    assert template.index('class="mini attach-add"') < \
        template.index('class="mini tools-open hidden"') < \
        template.index('class="mini fast-indicator hidden"') < \
        template.index('${composerChoice("perm"')
    assert 'class="mini fast-indicator hidden" role="img"' in template
    assert 'aria-label="Fast mode is on" title="Fast mode is on"' in template
    assert 'this.fastIndicator.firstElementChild.appendChild(fastModeIcon(12));' \
        in ui_source

    icon_start = ui_source.index("function fastModeIcon(")
    icon_end = ui_source.index("\n\n/* A font's vertical-ellipsis", icon_start)
    icon = ui_source[icon_start:icon_end]
    assert 'svg.setAttribute("viewBox", "0 0 16 16")' in icon
    assert 'p.setAttribute("fill", "currentColor")' in icon

    method_start = ui_source.index("  syncFastIndicator() {")
    method_end = ui_source.index("\n\n  showToolsMenu(", method_start)
    method = ui_source[method_start:method_end]
    assert "backendSupportsFastMode(this.tab.bid)" in method
    assert "eng.supports_fast_mode === true" in method
    assert "eff.fast_mode === true" in method
    assert 'classList.toggle("hidden", !visible)' in method
    assert '"Fast mode is on for the next turn"' in method
    # Engine feature metadata is the boundary; names and tier ids would make
    # this presentation brittle across catalog and CLI changes.
    assert "codex" not in method and "service_tier" not in method

    script = r"""
let capability = true;
const engines = {
  tiered: {supports_fast_mode:true},
  ordinary: {supports_fast_mode:false},
};
const engineInfo = (_bid, key) => engines[key] || null;
const backendSupportsFastMode = () => capability;
class Harness {
  constructor(config) {
    this.config = config;
    this.session = {engine:config.engine};
    this.tab = {bid:0};
    const classes = new Set(["hidden"]);
    const attrs = {};
    this.fastIndicator = {
      _classes:classes, _attrs:attrs, title:"",
      classList:{toggle:(name, on) => on ? classes.add(name) : classes.delete(name)},
      setAttribute:(name, value) => { attrs[name] = value; },
      removeAttribute:(name) => { delete attrs[name]; },
    };
  }
  effectiveConfig() { return this.config; }
%s
}
const read = config => {
  const h = new Harness(config); h.syncFastIndicator();
  return {hidden:h.fastIndicator._classes.has("hidden"),
    label:h.fastIndicator._attrs["aria-label"] || ""};
};
const results = [];
results.push(read({engine:"tiered", fast_mode:true, queuedFast:false}));
results.push(read({engine:"tiered", fast_mode:false, queuedFast:false}));
results.push(read({engine:"ordinary", fast_mode:true, queuedFast:false}));
results.push(read({engine:"tiered", fast_mode:true, queuedFast:true}));
capability = false;
results.push(read({engine:"tiered", fast_mode:true, queuedFast:false}));
capability = true;
const changed = new Harness({engine:"tiered", fast_mode:true, queuedFast:false});
changed.syncFastIndicator();
changed.config = {engine:"tiered", fast_mode:false, queuedFast:true};
changed.syncFastIndicator();
results.push({hidden:changed.fastIndicator._classes.has("hidden")});
console.log(JSON.stringify(results));
""" % method
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == [
        {"hidden": False, "label": "Fast mode is on"},
        {"hidden": True, "label": ""},
        {"hidden": True, "label": ""},
        {"hidden": False, "label": "Fast mode is on for the next turn"},
        {"hidden": True, "label": ""},
        {"hidden": True},
    ]

    assert (".composer-row .mini.attach-add,.composer-row .mini.tools-open,\n"
            ".composer-row .mini.fast-indicator{width:24px;padding:0}") \
        in css_source
    assert ".composer-row .mini.fast-indicator{color:var(--fast-mode);cursor:default}" \
        in css_source
    assert css_source.count("--fast-mode:") == 2


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
    the popup owns Escape ahead of the interrupt path, and both MCP agents
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
  directiveFull: spawnMentionInsert({node: {bid: 2, name: "build-node.lan"},
    engine: codex, model: {value: "gpt-5.6-sol"}, effort: {value: "max"}}),
  directiveQuoted: spawnMentionInsert({node: {bid: 2, name: "Build node west"},
    engine: codex, model: {value: ""}, effort: {value: "low"}}),
  directiveLocal: spawnMentionInsert({node: null, engine: {key: "claude"},
    model: {value: "haiku"}, effort: {value: ""}}),
  directiveFleet: spawnMentionInsert({count: 10, node: {bid: 2, name: "build-node.lan"},
    engine: codex, model: {value: ""}, effort: {value: ""}}),
  effortsShared: spawnEffortOptionsFor(codex, {value: "gpt-5.6-sol"})
    .map(option => option.value),
  effortsOwn: spawnEffortOptionsFor(codex, opencodeModel)
    .map(option => option.value),
}));
""" % helpers
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
        "@Spawn an agent on build-node.lan using codex gpt-5.6-sol at max effort", result
    assert result["directiveQuoted"] == \
        '@Spawn an agent on "Build node west" using codex at low effort', result
    assert result["directiveLocal"] == \
        "@Spawn an agent using claude haiku", result
    assert result["directiveFleet"] == \
        "@Spawn 10 agents on build-node.lan using codex", result
    assert result["effortsShared"] == ["", "low", "max"], result
    assert result["effortsOwn"] == ["", "high"], result

    # The popup lives inside the composer box and consumes its keys before the
    # composer's own handlers - most importantly Escape, which must close the
    # list rather than interrupt the running turn.
    assert '<div class="mention-pop hidden" role="listbox"' in ui_source
    keydown = ui_source.index('this.ta.addEventListener("keydown"')
    assert ui_source.index("if (this.mentionKeydown(e)) return;", keydown) < \
        ui_source.index('if (e.key === "Escape")', keydown)
    assert "this.mentionDismissedAt = m.start;" in ui_source
    # the list follows the caret and leaves with composer focus
    assert 'document.addEventListener("selectionchange", this._onSelectionChange);' \
        in ui_source
    assert 'document.removeEventListener("selectionchange", this._onSelectionChange);' \
        in ui_source
    assert 'this.ta.addEventListener("blur", () => { this.hideMention(); this.stopTyping(); });' in ui_source
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
    # The sent-message token regex recognises every spawn directive variant,
    # but leaves its final " to" task separator as ordinary prose.
    re_start = ui_source.index("const MENTION_TOKEN_RE")
    re_source = ui_source[re_start:ui_source.index("\nfunction decorateMentionsInto", re_start)]
    token_script = r"""
%s
const token = text => {
  MENTION_TOKEN_RE.lastIndex = 0;
  const m = MENTION_TOKEN_RE.exec(text);
  return m ? m[2] : null;
};
console.log(JSON.stringify({
  full: token("please @Spawn an agent on build-node.lan using codex gpt-5.6-sol at max effort to review it"),
  quoted: token('@Spawn an agent on "Build node west" using codex at low effort to check'),
  bare: token("@Spawn an agent using claude to summarize"),
  modelOnly: token("@Spawn an agent using claude haiku to summarize"),
  fleet: token("@Spawn 10 agents on build-node.lan using codex to hunt bugs"),
  browser: token("see @Browser AB12 now"),
  session: token("see @Session-Project-plan-A7K2 now"),
  unicodeSession: token("@Session-Żółć-東京-A7K2"),
  longCode: token("@Session-Project-ABCDE"),
  partialSession: token("@Session-P"),
  prose: token("we will spawn an agent later"),
  incomplete: token("@Spawn an agent using to nothing"),
}));
""" % re_source
    proc = subprocess.run(["node", "-e", token_script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    tokens = json.loads(proc.stdout.strip())
    assert tokens["full"] == \
        "@Spawn an agent on build-node.lan using codex gpt-5.6-sol at max effort", tokens
    assert tokens["quoted"] == \
        '@Spawn an agent on "Build node west" using codex at low effort', tokens
    assert tokens["bare"] == "@Spawn an agent using claude", tokens
    assert tokens["modelOnly"] == "@Spawn an agent using claude haiku", tokens
    assert tokens["fleet"] == \
        "@Spawn 10 agents on build-node.lan using codex", tokens
    assert tokens["browser"] == "@Browser AB12", tokens
    assert tokens["session"] == "@Session-Project-plan-A7K2", tokens
    assert tokens["unicodeSession"] == "@Session-Żółć-東京-A7K2", tokens
    assert tokens["longCode"] is None and tokens["partialSession"] is None, tokens
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
const state={instance:"controller",backends:[
  {id:2,name:"build-node.lan",protocol:2,capabilities:["spawn-exec"]},
  {id:3,name:"OLD",protocol:2,capabilities:[]},
  {id:5,name:"worker-two",protocol:2,capabilities:["spawn-exec"]},
],engines:[
  {key:"claude",label:"Claude Code",installed:true,auth:"ok",version:"2.1.219",
   model_options:[{value:"",label:"Default"},{value:"haiku",label:"Haiku"}],
   effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]},
  {key:"broken",installed:false},
],engCache:{5:[
  {key:"solo",label:"Solo",installed:true,auth:"ok",
   model_options:[{value:"",label:"Default"}],
   effort_options:[{value:"",label:"Default"}]},
]},tabs:[],browserStatus:{enabled:false,instances:[]},
  remoteBrowserStatus:{},terminalInstances:{0:[]}};
const backendName=bid=>bid?(state.backends.find(b=>b.id===bid)||{}).name||("backend "+bid):state.instance;
const spawnExecFor=bid=>{if(!bid)return true;
  const backend=state.backends.find(item=>item.id===bid);
  return !!backend&&Number(backend.protocol||0)>0&&
    Array.isArray(backend.capabilities)&&backend.capabilities.includes("spawn-exec");};
const backendConnectionAllowed=()=>true;
const backendHasCapability=()=>false;
let liveStateStream=false;
const nodeStateStreamActive=()=>liveStateStream;
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
  constructor(bid){this.host={bid,sid:9,selfHint:"this session"};this.mention=null;this.mentionDismissedAt=-1;
    this.mentionSpawn=null;this.mentionRowEls=[];this.history=null;
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
view.type("@build");
out.nodeFiltered=view.labels();
const remoteEngines=[{key:"codex",label:"Codex",installed:true,auth:"ok",version:"0.149.0",
  model_options:[{value:"",label:"Default"},{value:"gpt-5.6-sol",label:"GPT-5.6 Sol"}],
  effort_options:[{value:"",label:"Default"},{value:"max",label:"Max"}]}];
apiResult={engines:remoteEngines,usage_refresh:{}};
view.pick("build-node.lan");
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
  view.pick("build-node.lan");
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
  // A live state stream already seeds mentionData and must not recursively
  // re-enter updateMention from refreshMentionInstances. Exercise both the
  // initial @ and the common query -> Backspace -> @ sequence.
  const streamed=new View(0);
  liveStateStream=true;
  streamed.type("@");
  out.streamedOpen=streamed.labels();
  streamed.type("@a");
  streamed.type("@");
  out.streamedBackspace=streamed.labels();
  const controller="b".repeat(32), peer="a".repeat(32);
  apiResult={controller,all_mention:"@Session-All-ALL1",unavailable:[],sessions:[
    {bid:0,id:9,ref:controller+"/9",title:"Origin",short_id:"ORI1",mention:"@Session-Origin-ORI1",node_name:"local",cwd:"/project",status:"idle"},
    {bid:0,id:10,ref:controller+"/10",title:"API decisions",short_id:"API1",mention:"@Session-API-decisions-API1",node_name:"local",cwd:"/project",status:"idle"},
    {bid:2,id:11,ref:peer+"/11",title:"Frontend layout notes",short_id:"FRN1",mention:"@Session-Frontend-layout-notes-FRN1",node_name:"NAS",cwd:"/ui",archived:true}
  ]};
  const selected=new View(0);
  selected.type("@Session-");
  await Promise.resolve();await Promise.resolve();
  out.sessionChoices=selected.labels();
  selected.type("@Session-api"); out.sessionFiltered=selected.labels();
  selected.pick("API decisions");
  selected.type("@Session-Frontend-layout-notes"); selected.pick("Frontend layout notes");
  selected.pick("Insert 2 selected");
  out.sessionsInserted=selected.ta.value;
  selected.type("@Session-");
  await Promise.resolve();await Promise.resolve();
  selected.pick("All sessions");out.allSessionsInserted=selected.ta.value;
  console.log(JSON.stringify(out));
}
run().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
""" % (helpers_again, methods)
    proc = subprocess.run(["node", "-e", flow_script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1200]
    flow = json.loads(proc.stdout.strip())
    assert flow["flat"] == ["Session", "New terminal", "New spawn"], flow
    assert flow["countStep"] == [13, "1 agent", "12 agents", "Back", "count"], flow
    assert flow["countFiltered"] == ["3 agents", "Back"], flow
    assert flow["nodeStep"] == [
        ["controller", "build-node.lan", "worker-two", "Back"], "node"], flow
    assert flow["nodeFiltered"] == ["build-node.lan", "Back"], flow
    assert flow["remoteLoading"] == [["Loading engines…", "Back"],
                                     ["2:engines"]], flow
    assert flow["engineStep"] == [["Codex", "Back"], "engine"], flow
    assert flow["modelStep"] == [["Default", "GPT-5.6 Sol", "Back"], "model"], flow
    assert flow["effortStep"] == [["Default", "Max", "Back"], "effort"], flow
    assert flow["inserted"] == [
        "@Spawn 3 agents on build-node.lan using codex gpt-5.6-sol at max effort ",
        None, None], flow
    assert flow["backToNode"] == "node", flow
    assert flow["backToCount"] == "count", flow
    assert flow["backOut"] == [None, ["Session", "New terminal", "New spawn"]], flow
    assert flow["remoteFlat"] == ["New spawn"], flow
    assert flow["remoteCount"] == "count", flow
    assert flow["remoteEngine"] == [["Solo", "Back"], "engine"], flow
    assert flow["remoteInserted"] == ["@Spawn an agent using solo ", None], flow
    assert flow["streamedOpen"] == ["Session", "New terminal", "New spawn"], flow
    assert flow["streamedBackspace"] == ["Session", "New terminal", "New spawn"], flow

    assert flow["sessionChoices"] == ["All sessions", "API decisions", "Frontend layout notes", "Back"], flow
    assert flow["sessionFiltered"] == ["API decisions", "Back"], flow
    assert flow["sessionsInserted"] == "@Session-API-decisions-API1 @Session-Frontend-layout-notes-FRN1 ", flow
    assert flow["allSessionsInserted"] == "@Session-All-ALL1 ", flow

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
    assert '!this.mentionSpawn && !this.mentionSession && e.key === "Enter"' in ui_source
    assert "if (!this.mentionSpawn && !this.mentionSession) this.refreshMentionInstances();" in ui_source
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
    assert '"@Spawn an agent on build-node.lan using codex at max effort ' \
        'to <task>"' in spawn_agent.TOOL_INSTRUCTIONS
    assert "passing those values verbatim" in spawn_agent.TOOL_INSTRUCTIONS
    assert '"@Spawn 10 agents ..."' in spawn_agent.TOOL_INSTRUCTIONS
    assert "Wait until every agent has finished" in spawn_agent.TOOL_INSTRUCTIONS
    assert "Settings > Timeouts" in spawn_agent.TOOL_INSTRUCTIONS
    assert "Zero means unlimited" in spawn_agent.TOOL_INSTRUCTIONS
    assert "user sends steering" in spawn_agent.TOOL_INSTRUCTIONS
    spawn_tools = {tool["name"]: tool for tool in spawn_agent.TOOLS}
    assert '"@Spawn an agent ... to ..." mention' in \
        spawn_tools["spawn"]["description"]
    count_schema = spawn_tools["spawn"]["inputSchema"]["properties"]["count"]
    assert count_schema["minimum"] == 1 and count_schema["maximum"] == 12
    spawn_limits = spawn_tools["spawn"]["inputSchema"]["properties"]
    assert spawn_limits["idle_timeout_s"]["minimum"] == 0
    assert "default" not in spawn_limits["idle_timeout_s"]
    assert spawn_limits["max_runtime_s"]["minimum"] == 0
    assert "default" not in spawn_limits["max_runtime_s"]
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


def check_smooth_wheel_swipe(ui_source: str, css_source: str) -> None:
    """Wheel motion and its edge cues settle on the same exact boundary."""
    fade_start = ui_source.index("function syncHorizontalOverflow")
    fade_end = ui_source.index("\n\n/* The horizontal strips", fade_start)
    fade_source = ui_source[fade_start:fade_end]
    fade_script = r"""
const values = {};
const viewport = {style:{setProperty(name,value){values[name]=value;}}};
const scroller = {scrollLeft:0,scrollWidth:500,clientWidth:100,
                  parentElement:viewport};
const getComputedStyle = () => ({getPropertyValue:() => "34px"});
%s
function sample(left, width=500) {
  scroller.scrollLeft=left; scroller.scrollWidth=width;
  syncHorizontalOverflow(scroller);
  return [parseFloat(values["--edge-scroll-left-fade-size"]),
          parseFloat(values["--edge-scroll-right-fade-size"])];
}
console.log(JSON.stringify([
  sample(0), sample(12.5), sample(34), sample(390), sample(400),
  sample(450), sample(0,100)
]));
""" % fade_source
    fade_proc = subprocess.run(["node", "-e", fade_script],
                               capture_output=True, text=True)
    assert fade_proc.returncode == 0, fade_proc.stderr[:1000]
    assert json.loads(fade_proc.stdout) == [
        [0, 34], [12.5, 34], [34, 34], [34, 10], [34, 0],
        [34, 0], [0, 0],
    ]
    edge_start = css_source.index("/* Shared overflow cues")
    edge_end = css_source.index("/* Give the strip", edge_start)
    edge_css = css_source[edge_start:edge_end]
    assert "--edge-scroll-left-fade-size:0px" in edge_css
    assert "--edge-scroll-right-fade-size:0px" in edge_css
    assert "transition:opacity" not in edge_css
    assert ".edge-scroll-viewport.more-left" not in edge_css

    start = ui_source.index("const WHEEL_SWIPE_STRIPS")
    end = ui_source.index("\nfunction syncAllTabOverflow", start)
    source = ui_source[start:end]
    script = r"""
let reduced = false, quantized = false;
const listeners = {};
class Element {
  constructor() {
    this.scrollLeft = 0; this.scrollWidth = 500; this.clientWidth = 100;
    this.isConnected = true;
  }
  get scrollLeft() { return this._scrollLeft; }
  set scrollLeft(value) { this._scrollLeft = quantized ? Math.round(value) : value; }
  closest(selector) { return selector.includes(".tabs") ? this : null; }
}
const document = {addEventListener(kind, fn, options) {
  listeners[kind] = {fn, options};
}};
const window = {matchMedia:() => ({matches:reduced})};
let frameSeq = 0, clock = 0;
const frames = new Map();
const requestAnimationFrame = fn => { const id = ++frameSeq; frames.set(id, fn); return id; };
const cancelAnimationFrame = id => frames.delete(id);
%s
const strip = new Element();
function wheel(values={}) {
  const event = Object.assign({target:strip, ctrlKey:false, defaultPrevented:false,
    deltaX:0, deltaY:0, deltaMode:0, prevented:false,
    preventDefault() { this.prevented = true; }}, values);
  listeners.wheel.fn(event);
  return event;
}
function runFrame() {
  const row = frames.entries().next().value;
  if (!row) return false;
  frames.delete(row[0]); clock += 16; row[1](clock); return true;
}
function settle() {
  let count = 0;
  while (runFrame() && ++count < 200) {}
  if (frames.size) throw new Error("wheel animation did not settle");
  return count;
}

const first = wheel({deltaY:120});
const queued = {left:strip.scrollLeft, frames:frames.size};
runFrame();
const eased = strip.scrollLeft;
const second = wheel({deltaY:80});
const frameCount = settle();
const accumulated = strip.scrollLeft;

strip.scrollLeft = 200;
const pending = wheel({deltaY:80});
const horizontal = wheel({deltaX:90, deltaY:4});
const horizontalResult = {pending:pending.prevented, native:horizontal.prevented,
  frames:frames.size, left:strip.scrollLeft};

strip.scrollLeft = 400;
const atRight = wheel({deltaY:100});
strip.scrollLeft = 0;
const atLeft = wheel({deltaY:-100});

const line = wheel({deltaY:2, deltaMode:1}); settle();
const lineLeft = strip.scrollLeft;
strip.scrollLeft = 0;
const page = wheel({deltaY:1, deltaMode:2}); settle();
const pageLeft = strip.scrollLeft;

strip.scrollLeft = 0;
const pointerStart = wheel({deltaY:100});
listeners.pointerdown.fn({target:strip});
const pointerResult = {prevented:pointerStart.prevented, frames:frames.size,
  left:strip.scrollLeft};

strip.scrollLeft = 0; strip.isConnected = false;
const detached = wheel({deltaY:100}); runFrame();
const detachedResult = {prevented:detached.prevented, frames:frames.size,
  left:strip.scrollLeft};
strip.isConnected = true;

reduced = true; strip.scrollLeft = 0;
const reducedEvent = wheel({deltaY:100});
const reducedResult = {prevented:reducedEvent.prevented, frames:frames.size,
  left:strip.scrollLeft};

reduced = false; quantized = true; strip.scrollLeft = 0;
wheel({deltaY:10000}); settle();
const quantizedRight = strip.scrollLeft;
wheel({deltaY:-10000}); settle();
const quantizedLeft = strip.scrollLeft;

console.log(JSON.stringify({quantizedRight, quantizedLeft, selectors:WHEEL_SWIPE_STRIPS, passive:listeners.wheel.options.passive,
  pointerPassive:listeners.pointerdown.options.passive,
  first:first.prevented, second:second.prevented, queued, eased, frameCount, accumulated,
  horizontalResult, atRight:atRight.prevented, atLeft:atLeft.prevented,
  line:line.prevented, lineLeft, page:page.prevented, pageLeft,
  pointerResult, detachedResult, reducedResult}));
""" % source
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout.strip())
    assert result["quantizedRight"] == 400 and result["quantizedLeft"] == 0, result
    assert result["selectors"] == [
        ".tabs", ".chat-meta-scroll", ".composer-meta-scroll", ".br-meta-scroll"], result
    assert result["passive"] is False and result["pointerPassive"] is True, result
    assert result["first"] and result["second"], result
    assert result["queued"] == {"left": 0, "frames": 1}, result
    assert 0 < result["eased"] < 120, result
    assert 1 < result["frameCount"] < 200, result
    assert result["accumulated"] == 200, result
    assert result["horizontalResult"] == {
        "pending": True, "native": False, "frames": 0, "left": 200}, result
    assert not result["atRight"] and not result["atLeft"], result
    assert result["line"] and result["lineLeft"] == 32, result
    assert result["page"] and result["pageLeft"] == 100, result
    assert result["pointerResult"] == {
        "prevented": True, "frames": 0, "left": 0}, result
    assert result["detachedResult"] == {
        "prevented": True, "frames": 0, "left": 0}, result
    assert result["reducedResult"] == {
        "prevented": True, "frames": 0, "left": 100}, result


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
const direct={cwd:"/srv/apps/puppy"};
const remote={cwd:"/private/mirror/never-show",workspace:{
  root:"/srv/projects/sample-app",node:"Builder"}};
const missing={cwd:"/stale/scratch/path",workspace_missing:true};
const long={workspace:{root:"/one/two/three/four/five",node:"Builder"}};
console.log(JSON.stringify({
  direct:[workspaceLocationLabel(direct,0),workspaceLocationTitle(direct,0)],
  remote:[workspaceLocationLabel(remote,7),workspaceLocationTitle(remote,7)],
  missing:workspaceLocationLabel(missing,0),
  longFull:workspaceLocationLabel(long,7),
  long:workspaceLocationLabel(long,7,20),
}));
""" % helpers
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout)
    assert result == {
        "direct": ["Primary:/srv/apps/puppy", "Primary:/srv/apps/puppy"],
        "remote": ["Builder:/srv/projects/sample-app", "Builder:/srv/projects/sample-app"],
        "missing": "Primary:Scratch workspace missing",
        "longFull": "Builder:/one/two/three/four/five",
        "long": "Builder:…ee/four/five",
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["remoteCount"] == 2 and result["localCount"] == 1, result
    assert result["closed"] == ["remote-7-a", "remote-7-b", "local-browser"], result
    assert result["afterRemote"] == [
        "local-browser", "remote-8", "session-7", "settings"], result
    assert result["remaining"] == ["remote-8", "session-7", "settings"], result


def check_quota_math(ui_source: str) -> None:
    """The footer selects the all-model week without guessing from its value
    or allowing a currently-binding model-specific window to take its place."""
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
    script = (extract("\nfunction weeklyQuotaSample(") +
              extract("\nfunction weeklyQuotaLeft(") +
              extract("\nfunction quotaTitle(") + """
const fmtDateTime = epoch => String(epoch);
const claude = {rateLimitType: "seven_day_overage_included", utilization: 0.94,
  unifiedWindows: {five_hour: {utilization: 0.26}, seven_day: {utilization: 0.52},
                   seven_day_overage_included: {utilization: 0.94}},
  captured_at: 222};
const left = (value, now = 100) =>
  weeklyQuotaLeft(weeklyQuotaSample(value, now));
const values = [
  left({rate_limit: claude}),                                             // 48
  left({rate_limit: {rateLimitType: "five_hour", utilization: 0.3,
    unifiedWindows: {seven_day: {utilization: 0.68}}}}),                  // 32
  left({quota: {weekly_used_percent: 19}}),                               // 81
  left({rate_limit: {primary: {window_minutes: 10080,
                               used_percent: 40}}}),                      // 60
  left({rate_limit: {secondary: {windowDurationMins: 10080,
                                 usedPercent: 41}}}),                     // 59
  left({rate_limit: null}),                                               // null
  left({rate_limit: {rateLimitType: "seven_day",
                     utilization: 1.15}}),                                // 0
  left({rate_limit: {rateLimitType: "seven_day_opus",
                     utilization: 0.9}}),                                 // null
  left({rate_limit: {rateLimitType: "seven_day_overage_included",
                     utilization: 0.9}}),                                 // null
  left({rate_limit: {rateLimitType: "future_weekly_model",
                     utilization: 0.9}}),                                 // null
  left({rate_limit: {unifiedWindows: {
    seven_day: {utilization: Number.NaN}}}}),                             // null
  left({rate_limit: {unifiedWindows: {
    seven_day: {utilization: Number.POSITIVE_INFINITY}}}}),               // null
  left({rate_limit: {unifiedWindows: {
    seven_day: {utilization: 0.4, resetsAt: 99}}}}, 100),                 // null
];
const quotaEngine = {
  quota: {weekly_used_percent: 19, resets_at: 333, as_of: 222},
  rate_limit: {captured_at: 111, unifiedWindows: {
    five_hour: {utilization: 0.9}, seven_day: {utilization: 0.8}}},
};
const provenance = quotaTitle(quotaEngine, weeklyQuotaSample(quotaEngine, 100));
const claudeEngine = {rate_limit: claude};
const claudeProvenance = quotaTitle(
  claudeEngine, weeklyQuotaSample(claudeEngine, 100));
console.log(JSON.stringify({values, provenance, claudeProvenance}));
""")
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:400]
    result = json.loads(proc.stdout.strip())
    rounded = [None if v is None else round(v) for v in result["values"]]
    assert rounded == [48, 32, 81, 60, 59, None, 0, None, None, None,
                       None, None, None], result
    assert "week 19% used" in result["provenance"], result
    assert "reported 222" in result["provenance"], result
    assert "111" not in result["provenance"] and \
        "5h" not in result["provenance"], result
    assert "all-model week 52% used" in result["claudeProvenance"], result
    assert "model-specific week 94% used" in result["claudeProvenance"], result
    assert "5h 26% used" in result["claudeProvenance"], result
    assert "overage" not in result["claudeProvenance"], result
    assert "reported 222" in result["claudeProvenance"], result


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
    assert 'toast(d.text || "Browser error", "error", TOAST_LONG);' in view_source
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
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
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
    assert "st.shared_storage_health" in ui_source
    assert "persistence failed" in ui_source
    assert "non-partitioned cookies" in ui_source
    assert "IndexedDB, sessionStorage, or service workers" in ui_source
    assert "backend-local store is excluded from backups" in ui_source
    assert "record.shared.input.disabled = true;" in ui_source
    assert "be-browser-share" in ui_source
    assert ".browser-share-toggle" in css_source


def check_audit_truthfulness_ui(ui_source: str) -> None:
    """History, search, reconnect, undo and usage copy retain exact provenance."""
    # Divider endpoints prefer the engine-confirmed model, including the
    # newest config-change line (not only engine-switch lines).
    switch_start = ui_source.index("  syncSwitchLines() {")
    switch_end = ui_source.index("\n  switchLineNode(", switch_start)
    switch_source = ui_source[switch_start:switch_end]
    assert "s.last_model" in switch_source
    assert "if (!engines) return" not in switch_source

    # Titles are a real selectable kind, and selected kinds are sent exactly.
    assert '{ key: "title", label: "Titles" }' in ui_source
    assert '[...this.kinds, "title"]' not in ui_source
    assert 'v: 2, kinds: [...this.kinds]' in ui_source

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

    # Raw BM25 deliberately disagrees with node rank here. Federated ordering
    # must interleave the per-node ordinal, using freshness only as its tie.
    script = (extract("\nfunction searchGroupNewest(") +
              extract("\nfunction searchRelevanceCompare(") + r'''
const rows = [
  {id:"raw-winner",nodeRank:1,group:{matches:[{rank:-999,ts:300}]}},
  {id:"rank-first-old",nodeRank:0,group:{matches:[{rank:-1,ts:100}]}},
  {id:"rank-first-new",nodeRank:0,group:{matches:[{rank:-2,ts:200}]}},
];
rows.sort(searchRelevanceCompare);
console.log(JSON.stringify(rows.map(row => row.id)));
''')
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    assert json.loads(proc.stdout) == [
        "rank-first-new", "rank-first-old", "raw-winner"]

    # A reconnect always refreshes state, while a new process identity reloads
    # cache-busted assets and therefore uptime/version/clock rendering too.
    assert "refreshAfterUpdatesReconnect(sequence, ws)" in ui_source
    assert "await refreshState();" in ui_source
    assert "previousRuntime !== state.runtimeId" in ui_source
    assert "location.reload();" in ui_source

    assert "That prompt and reply stay visible in " in ui_source
    assert "Removes your last prompt and its reply" not in ui_source
    assert "installed Codex CLI" in ui_source
    assert "Other engines do not provide an account-refresh API" in ui_source


async def check_shared_store_persistence_health() -> None:
    """A failed durable write is visible and retried without another change."""
    path = TEST_ROOT / "store-health" / "state.json"
    original_path = browser_store.store_path
    original_save = browser_store._save_blocking
    shared = browser_store.SharedStore()
    cookie = {
        "name": "auth", "value": "secret", "domain": "health.test",
        "path": "/", "secure": True, "httpOnly": True,
    }

    def fail_save(_path, _payload):
        raise OSError("simulated durable write failure")

    try:
        browser_store.store_path = lambda: str(path)
        browser_store._save_blocking = fail_save
        first = await shared.sync(
            {browser_store.cookie_key(cookie): cookie}, {}, {}, {})
        failed = await shared.persistence_health()
        assert failed["ok"] is False and failed["dirty"] is True, failed
        assert "simulated durable write failure" in failed["error"], failed
        assert not path.exists()

        browser_store._save_blocking = original_save
        # No new browser mutation: dirty state alone must retry persistence.
        await shared.sync(first["cookie_state"], first["cookie_state"], {}, {})
        recovered = await shared.persistence_health()
        assert recovered["ok"] is True and recovered["dirty"] is False, recovered
        assert recovered["last_saved_at"] is not None and path.is_file(), recovered

        current = json.loads(path.read_text())
        invalid_records = [
            "not json", {"v": 0}, dict(current, serial="1"),
            dict(current, extra=True),
            {key: value for key, value in current.items() if key != "storage"},
            dict(current, cookies=[{"cookie": cookie}]),
            dict(current, cookies=[{"cookie": dict(cookie, secure="true"), "seen": 1.0}]),
            dict(current, storage={"https://health.test": {"items": {}, "ts": None}}),
        ]
        for invalid in invalid_records:
            original = invalid if isinstance(invalid, str) else json.dumps(invalid)
            path.write_text(original)
            unreadable = browser_store.SharedStore()
            health = await unreadable.persistence_health()
            assert health["ok"] is False and health["dirty"] is False, health
            for call in (lambda: unreadable.sync({}, {}, {}, {}), unreadable.seed_snapshot):
                try:
                    await call()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("invalid sign-in storage became an empty store")
            assert path.read_text() == original, "a rejected store must retain its exact bytes"
            path.write_text(json.dumps(current))  # deliberate operator repair
            health = await unreadable.persistence_health()
            assert health["ok"] is True and health["dirty"] is False, health
            assert unreadable._cookies == shared._cookies
    finally:
        browser_store.store_path = original_path
        browser_store._save_blocking = original_save


def check_server_clock_format(ui_source: str) -> None:
    """Every WebUI clock follows the primary process's LC_TIME hour cycle."""
    assert localization.clock_format_from_pattern("%I:%M:%S %p") == "12h"
    assert localization.clock_format_from_pattern("%OI:%M:%S %p") == "12h"
    assert localization.clock_format_from_pattern("%H:%M:%S") == "24h"
    assert localization.clock_format_from_pattern("%R") == "24h"
    assert localization.clock_format_from_pattern("%%H %X") is None
    assert localization.clock_format() in ("12h", "24h")

    def extract(name: str) -> str:
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
        raise AssertionError("unterminated {}".format(name))

    helpers = "\n".join(extract(name) for name in (
        "serverClockOptions", "fmtClockSetting", "parseClockSetting",
        "clockSettingExample", "fmtTime", "fmtDateTime", "fmtStamp"))
    script = r'''
const state={clockFormat:"24h"};
%s
const result={};
result.cycle24=serverClockOptions({weekday:"short"});
result.show24=fmtClockSetting("15:05");
result.parse24=parseClockSetting("3:05");
result.reject24=parseClockSetting("24:00");
result.example24=clockSettingExample();
state.clockFormat="12h";
result.cycle12=serverClockOptions({weekday:"short"});
result.midnight=fmtClockSetting("00:05");
result.morning=fmtClockSetting("03:30");
result.afternoon=fmtClockSetting("15:30");
result.am=parseClockSetting("12:05 AM");
result.pm=parseClockSetting("12:05 PM");
result.dotted=parseClockSetting("3:30 p.m.");
result.reject12=parseClockSetting("15:30");
result.example12=clockSettingExample();
Date.prototype.toLocaleString=Date.prototype.toLocaleTimeString=function(locales,options){return options;};
const today=new Date(),year=today.getFullYear();
const otherDay=new Date(year,today.getMonth()===0?11:0,1);
const samples=[today,otherDay,new Date(year-1,0,1)];
for(const cycle of ["24h","12h"]){
  state.clockFormat=cycle;
  result["stamps"+cycle]=samples.map(date=>{
    const options=fmtStamp(date.getTime()/1000);
    return [options.hourCycle,"month" in options,"year" in options];
  });
}
result.invalidStamp=fmtStamp("invalid");
console.log(JSON.stringify(result));
''' % helpers
    proc = subprocess.run(["node", "-e", with_live_views(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:700]
    assert json.loads(proc.stdout) == {
        "cycle24": {"weekday": "short", "hourCycle": "h23"},
        "show24": "15:05", "parse24": "03:05", "reject24": "",
        "example24": "03:30",
        "cycle12": {"weekday": "short", "hourCycle": "h12"},
        "midnight": "12:05 AM", "morning": "3:30 AM",
        "afternoon": "3:30 PM", "am": "00:05", "pm": "12:05",
        "dotted": "15:30", "reject12": "", "example12": "3:30 AM",
        "stamps24h": [["h23", False, False], ["h23", True, False], ["h23", True, True]],
        "stamps12h": [["h12", False, False], ["h12", True, False], ["h12", True, True]],
        "invalidStamp": "",
    }
    assert 'time.type = "time"' not in ui_source
    assert 'time.type = "text"' in ui_source
    assert ui_source.count(".toLocaleString([],") == 1
    assert ui_source.count(".toLocaleTimeString(") == 1


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
    # Reject the retired singleton origin even when its retention has expired.
    for closed_at in (None, 2.0):
        invalid_catalog = json.dumps({"version": 1, "ids": {"A1B2": {
            "created_at": 1.0, "closed_at": closed_at, "origin": "legacy",
            "owner_session": None}}, "bindings": {}})
        catalog_path.write_text(invalid_catalog, encoding="utf-8")
        try:
            browser._load_catalog()
        except browser.BrowserError as exc:
            assert "invalid origin" in str(exc), str(exc)
        else:
            raise AssertionError("retired singleton origin was accepted")
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
    await check_shared_store_persistence_health()
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
                assert status["shared_storage_health"]["ok"] is True, status
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
            async with http.get(url + "/api/state", headers=headers) as r:
                primary_state = await read_json(r)
                assert r.status == 200, primary_state
                assert primary_state["clock_format"] == localization.clock_format()
                assert isinstance(primary_state["runtime_id"], str) and \
                    primary_state["runtime_id"], primary_state
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
            # Closing discards the profile as soon as its process is down: the
            # retention window reserves the four-character ID, not tens of
            # megabytes of Chromium cache and site data.
            closed_root = Path(browser._instance_root(closed_id))
            await wait_for(lambda: not closed_root.exists(),
                           message="closed browser storage discarded")
            assert closed_id in catalog["ids"], catalog

            old_id = "Z9Z9" if "Z9Z9" not in created_ids else "Y8Y8"
            registry = browser.manager()

            # A crash between writing the catalog and finishing that removal -
            # or a cleanup interrupted after closing the browser but before deleting its
            # whole window - is swept at startup, ID still reserved.
            stranded_id = "X4X4" if "X4X4" not in created_ids else "W3W3"
            registry.records[stranded_id] = {
                "created_at": 2.0, "closed_at": time.time(),
                "origin": "user", "owner_session": None,
            }
            stranded_root = Path(browser._instance_root(stranded_id))
            stranded_root.mkdir(parents=True, mode=0o700)
            (stranded_root / "stranded-marker").write_text("stranded")
            registry._discard_closed_storage()
            await wait_for(lambda: not stranded_root.exists(),
                           message="stranded closed-browser storage swept")
            assert stranded_id in registry.records, registry.records
            del registry.records[stranded_id]

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
            await ws.send_json({"type": "navigate", "url": "device.lan/start"})
            navs = await wait_for(lambda: read_lines("navigations.jsonl"),
                                  message="navigation")
            assert navs[0]["url"] == "http://device.lan/start", navs

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
            policy = config.DEFAULT_BROWSER_SYSTEM_PROMPT + \
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
                        "ref": "b3", "value": "test-value", "label": "Testland"}})
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
                        "ref": "b3", "label": "Testland",
                        "wait_for": {"text": "Ready", "timeout_ms": 100}}})
                assert "selected \"Testland\"" in \
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
                        "url": "gateway.lan/status", "wait_ms": 0,
                        "wait_for": {"url_contains": "gateway.lan", "timeout_ms": 1000}}})
                navigated_text = navigated["result"]["content"][0]["text"]
                assert "Observed document domcontentloaded" in navigated_text and \
                    "Observed URL containing 'gateway.lan'" in navigated_text, navigated_text
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
                    "http://gateway.lan/status"
                functions = read_lines("agent-functions.jsonl")
                assert any(item["backend"] == 12 and item["arguments"] == [None, "Testland"]
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
            css_source = "\n".join((BASE / "puppy" / "static" / name).read_text()
                                   for name in ("auth.css", "app.css"))
            index_source = (BASE / "puppy" / "static" / "index.html").read_text()
            auth_html = (BASE / "puppy" / "static" / "auth.html").read_text()
            auth_js = (BASE / "puppy" / "static" / "auth.js").read_text()
            assert 'id="auth-setup-code-wrap" class="hidden"' in auth_html
            assert 'status.setup_code_required' in auth_js
            assert 'body.setup_code = field("auth-setup-code").value' in auth_js
            assert 'id="auth-form"' not in index_source
            assert '/static/auth.css?v=__V__' in index_source
            check_free_identifiers(ui_source)
            check_static_template_styles(ui_source)
            check_server_clock_format(ui_source)
            check_reconnect_status(ui_source, css_source)
            check_session_task_capability(ui_source)
            check_task_config_ui()
            check_session_task_helpers(ui_source)
            check_session_task_visibility(ui_source)
            check_backend_shutdown_notice(ui_source)
            check_offline_sidebar_sessions(ui_source, css_source)
            check_controller_backend_pooling(ui_source)
            check_node_state_stream_ui(ui_source)
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
            check_side_question_ui(ui_source, css_source)
            check_modal_surface(ui_source, css_source)
            check_system_prompt_settings(ui_source, css_source)
            check_remote_workspace_picker(ui_source, css_source)
            check_opencode_chat_models(ui_source, css_source)
            check_dynamic_model_catalog_ui(ui_source)
            check_timer_settings_ui(ui_source, css_source)
            check_session_provider_marks(css_source)
            check_sidebar_icon_alignment(css_source)
            check_ui_contrast_palette(ui_source, css_source)
            check_compact_control_alignment(ui_source, css_source)
            check_session_activity_clock(ui_source, css_source)
            check_sidebar_footer_buttons(ui_source, css_source)
            check_toast_touch_swipe(ui_source, css_source)
            check_status_header_activation(ui_source, css_source)
            check_shared_node_order(ui_source, css_source)
            check_flat_session_list(ui_source, css_source)
            check_node_owned_session_order(ui_source, css_source)
            check_session_pins(ui_source, css_source)
            check_queued_permission_choices(ui_source)
            check_fast_mode_indicator(ui_source, css_source)
            check_switch_engine_initial_selection(ui_source)
            check_engine_picker_alignment(css_source)
            check_browser_chip_order(ui_source)
            check_composer_mentions(ui_source, css_source)
            check_smooth_wheel_swipe(ui_source, css_source)
            check_chat_status_bar_layout(ui_source, css_source)
            check_backend_name_single_activation(ui_source)
            check_browser_disable_closes_scoped_tabs(ui_source)
            check_browser_loading_ui(ui_source, css_source)
            check_shared_storage_settings_ui(ui_source, css_source)
            check_audit_truthfulness_ui(ui_source)
            check_quota_math(ui_source)
            # one checkbox face app-wide: a native checkbox is painted by the
            # browser, ignores the theme and differs per platform, so the form
            # input and the drawn menu mark share one rule and one tick path
            css_source = "\n".join((BASE / "puppy" / "static" / name).read_text()
                                   for name in ("auth.css", "app.css"))
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
            assert ".code-copy,.user-copy,.aside-copy{" in css_source
            assert "position:absolute;z-index:1;top:6px;right:6px;width:27px;height:27px;" in css_source
            assert ".user-copy,.aside-copy{opacity:0}" in css_source
            assert ".code-copy:hover,.user-copy:hover,.aside-copy:hover{" in css_source
            # Complete status lines use one click; later desktop double-click
            # events cannot undo the first activation.
            assert "wireDisclosureSurface(name, disclosure);" not in ui_source
            assert ui_source.count("wireDisclosureSurface(head, disclosure);") == 1
            assert "if (event.detail > 1) return;" in ui_source
            assert ui_source.count("event.detail > 0 && event.detail % 2 === 0") == 1
            # A node disable already stops its processes. The settings response
            # and asynchronous state paths also retire only that node's tabs.
            assert ui_source.count("closeBrowserTabsForBackend(") == 5
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
