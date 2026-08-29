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

from puppy import browser, browser_agent, config, db, runner as session_runner  # noqa: E402
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
active_target = PAGE["targetId"]


def send(message):
    os.write(4, json.dumps(message).encode() + b"\0")


def record(name, payload):
    with open(os.path.join(LOG, name), "a") as f:
        f.write(json.dumps(payload) + "\n")


buffer = b""
casting = False
viewport_width, viewport_height = 1280, 800
frame_session = 10
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
        elif method == "Accessibility.getFullAXTree":
            result = {"nodes": [
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
            ]}
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
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            if expression.startswith("JSON.stringify({url:location.href,title:document.title})"):
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
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            result = {"currentIndex": 1, "entries": [
                {"id": 1, "url": "about:blank"}, {"id": 2, "url": current["url"]},
                {"id": 3, "url": "https://forward.example.test/"}]}
        elif method == "Page.startScreencast":
            record("screencast.jsonl", params)
            casting = True
            emit_frame = (viewport_width, viewport_height, FRAME)
        elif method == "Page.stopScreencast":
            casting = False
        elif method == "Page.navigate":
            url = params.get("url", "")
            record("navigations.jsonl", {"url": url})
            if url == "http://stub.invalid/die":
                send({"id": msg.get("id"), "result": {}})
                raise SystemExit(4)
            current = POPUP if active_target == POPUP["targetId"] else PAGE
            current["url"] = url
            current["title"] = "navigated"
            send({"method": "Target.targetInfoChanged",
                  "params": {"targetInfo": dict(current)}})
            result = {"frameId": "f1"}
            emit_lifecycle = True
            if url == "http://stub.invalid/surface-reset":
                emit_frame = (1280, 657, STALE_FRAME)
        elif method in ("Page.reload", "Page.navigateToHistoryEntry"):
            emit_lifecycle = True
        elif method.startswith("Input."):
            record("input.jsonl", {"method": method, "params": params})
            if method == "Input.insertText":
                TYPED_VALUE = str(params.get("text") or "")
        elif method == "Browser.close":
            send({"id": msg.get("id"), "result": {}})
            raise SystemExit(0)
        if msg.get("id") is not None:
            send({"id": msg.get("id"), "result": result})
        if emit_lifecycle:
            session_id = "stub-sess-2" if active_target == POPUP["targetId"] \
                else "stub-sess-1"
            send({"method": "Page.domContentEventFired", "sessionId": session_id,
                  "params": {"timestamp": 1}})
            send({"method": "Page.loadEventFired", "sessionId": session_id,
                  "params": {"timestamp": 2}})
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


def check_reconnect_status(ui_source: str) -> None:
    """A transport outage must overlay, not destroy, model activity text.

    The old close handler called setStatus("Connection lost ..."). A reconnect
    snapshot restores running/idle but carries no ephemeral status text, so the
    warning survived while fresh model output streamed underneath it. Exercise
    the real SessionView status methods in node to keep those states separate.
    """
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
const proto = {
%s
};
const view = Object.assign(Object.create(proto), {
  tab: {bid: 7},
  reconnecting: false,
  statusText: "thinking 42 tokens",
  statusEl: {innerHTML: ""},
  liveText: "",
  syncLiveStatus() { this.liveText = this.visibleStatusText(); },
  syncHeadOverflow() {},
});
const take = () => ({header: view.statusEl.innerHTML, live: view.liveText,
                     activity: view.statusText, reconnecting: view.reconnecting});
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
""" % ",\n".join(methods)
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:400]
    result = json.loads(proc.stdout.strip())
    assert "thinking 42 tokens" in result["before"]["header"], result
    assert "Connection lost" in result["lost"]["header"], result
    assert result["lost"]["activity"] == "thinking 42 tokens", result
    assert "Connection lost" in result["changedWhileLost"]["header"], result
    assert result["changedWhileLost"]["activity"] == "using shell", result
    assert "Backend shutting down" in result["gracefulStop"]["header"], result
    assert "Connection lost" not in result["gracefulStop"]["header"], result
    assert "using shell" in result["recovered"]["header"], result
    assert "Connection lost" not in result["recovered"]["header"], result
    assert result["recovered"]["live"] == "using shell", result


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
        "remoteStoppingMessage", "handleRemoteNodeStopping", "clearRemoteNodeStopping"))
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
  }
  appendChild(child){child.parent=this;this.children.push(child);return child;}
  replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];
    children.forEach(child=>this.appendChild(child));}
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
""" % (function("thinkingIconNode"), function("isThinkingStatus"), method)
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:500]
    result = json.loads(proc.stdout.strip())
    assert result["codex"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["claude"] == {"cls": "think-brain", "text": "🧠"}, result
    assert result["tool"] == {"cls": "spinner", "text": ""}, result
    assert result["idle"] is None, result
    assert result["classified"] == [True, True, False, False], result
    assert ui_source.count("sum.appendChild(thinkingIconNode())") == 2


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
            'refresh.classList.contains("refreshing")) {') in ui_source
    assert 'refresh.disabled = status === "bad"' not in ui_source

    # A manual retry announces both edges of the attempt, remains gated while
    # in flight, and always restores the button even when the backend is still
    # unreachable. Settings uses those announcements to paint its checking row.
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


def check_browser_handoff_ui(ui_source: str, css_source: str) -> None:
    """The identified browser owns a compact, responsive chat-link strip."""
    start = ui_source.index("class BrowserView {")
    end = ui_source.index("/* ================= SettingsView", start)
    view = ui_source[start:end]
    assert view.index('class="br-bar"') < view.index('class="br-meta"') < \
        view.index('class="br-stage"')
    assert 'aria-label="Copy Browser ID"' in view
    assert 'Use with current session' in view and 'Move to current session' in view
    assert 'Linked to ${ownerName}' in view and 'Not linked to a session' in view
    assert "this.applyBinding(d);" in view
    assert "method: \"POST\", body: { session_id: sessionId }" in view
    assert 'method: "DELETE", timeoutMs: 15000' in view
    assert "currentBrowserSession(this.tab.bid)" in view
    assert ".br-meta{" in css_source and ".br-ident{" in css_source
    assert ".br-copy-id{width:27px;height:27px;" in css_source
    assert ".br-owner-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" \
        in css_source
    assert ".br-handoff{display:flex;align-items:center;gap:6px;" in css_source
    assert ".br-unlink{width:30px;height:28px;" in css_source
    assert "@media(max-width:560px){" in css_source
    assert ".br-meta{display:grid;grid-template-columns:auto minmax(0,1fr);" \
        in css_source


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


def check_queue_pause_ui(ui_source: str, css_source: str) -> None:
    """Pause/play belongs only to ordinary queued prompts and stays compact."""
    start = ui_source.index("\n  renderQueue(q, held, paused)")
    end = ui_source.index("\n  unqueue(index, text)", start)
    render = ui_source[start:end]
    held_start = render.index("held.forEach")
    queued_start = render.index("shown.forEach")
    assert "q-pause" not in render[held_start:queued_start]
    assert 'if (!cfg && backendSupportsQueuePause(this.tab.bid))' in render
    assert render.index('el("button", "q-pause")') < render.index('el("button", "q-x")', queued_start)
    assert 'queuePauseIcon(isPaused, 11)' in render
    assert 'isPaused ? "Resume this queued message" : "Pause this queued message"' in render
    assert 'this.setQueuePaused(i, ident, !isPaused)' in render
    assert 'type: "set_queue_paused", index, text, paused' in ui_source
    assert 'backend.capabilities.includes("queue-pause")' in ui_source
    assert '.queue-strip .q-pause{' in css_source
    assert 'width:16px;height:16px;' in css_source
    assert '.queue-strip .q-item.q-paused .q-t{opacity:.58}' in css_source


def check_system_prompt_settings(ui_source: str, css_source: str) -> None:
    """The prompt editor stays node-aware and Engine updates shares its card."""
    assert ui_source.count("<h2>Engine updates</h2>") == 1
    section = ui_source.index('const autoSection = el("section", "engine-updates-section")')
    attach = ui_source.index("c2.appendChild(autoSection)", section)
    card = ui_source.index("this.inner.appendChild(c2)", attach)
    assert section < attach < card
    assert 'const autoCard = el("div", "card")' not in ui_source
    assert 'api(0, "system-prompt")' in ui_source
    assert 'backend.capabilities.includes("system-prompt")' in ui_source
    assert '"Puppy browser guidance"' in ui_source
    assert '"Reset to default"' in ui_source
    guidance_copy = ("Sent to every model turn on nodes where Browser is enabled; "
                     "it is not sent on nodes where Browser is off.")
    assert guidance_copy in ui_source.replace('" +\n      "', "")
    assert "browserNote.textContent = `Sent only for turns on ${node.name}" not in ui_source
    assert 'body: { custom: record.customDraft, browser: record.browserDraft }' in ui_source
    runner_source = (BASE / "puppy" / "runner.py").read_text()
    assert "system_prompt=system_prompts.custom_prompt()" in runner_source
    assert ".engine-updates-section{" in css_source
    assert ".system-prompt-section+.system-prompt-section{" in css_source
    assert ".system-prompt-section-head{" in css_source
    assert ".system-prompt-textarea{" in css_source
    assert ".config-textarea{display:block;height:72px;min-height:72px;" \
           "max-height:72px;resize:none}" in css_source
    assert "system-prompt-textarea::-webkit-resizer" not in css_source
    assert 'custom.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'browserText.className = "system-prompt-textarea config-textarea";' in ui_source
    assert 'class="config-textarea" id="be-pairing" rows="3"' in ui_source
    assert 'class="config-textarea" id="backend-edit-pairing" rows="3"' in ui_source
    assert ui_source.count('class="config-textarea"') == 2
    assert "custom.rows = 3;" in ui_source and "browserText.rows = 3;" in ui_source

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
const supported=new Set([0,1]);
const backendSupportsSystemPrompt=bid=>supported.has(bid);
const calls=[],toasts=[];
const payload=(custom,browser)=>({custom,browser,browser_default:"DEFAULT",max_chars:100});
async function api(bid,path,options={}) {
  calls.push({bid,path,method:options.method||"GET",body:options.body||null});
  if(options.method==="PATCH")
    return {system_prompt:payload(options.body.custom,options.body.browser)};
  return {system_prompt:payload("REMOTE","REMOTE BROWSER")};
}
const toast=(...args)=>toasts.push(args);
class TestView {
  constructor(){this.renderGeneration=1;this.systemPromptBid=0;}
__METHOD__
}
const view=new TestView();
const card=view.systemPromptCard([
  {bid:0,name:"Primary"},{bid:1,name:"Laptop"},{bid:2,name:"Old backend"}
],payload("LOCAL","DEFAULT"),1);
const nodeField=card.children[0],select=nodeField.children[1];
const custom=card.children[1].children[1];
const browserSection=card.children[2],browser=browserSection.children[1];
const reset=browserSection.children[0].children[1];
const actions=card.children[3],status=actions.children[0],save=actions.children[1];
const before={custom:custom.value,browser:browser.value,status:status.textContent,
  note:browserSection.children[0].children[0].children[1].textContent};
custom.value="LOCAL EDIT";custom.oninput();
const dirty=status.textContent;
browser.value="OTHER";browser.oninput();reset.onclick();
const resetState={browser:browser.value,status:status.textContent};
await save.onclick();
const saved={status:status.textContent,toast:toasts[0][0]};
select.value="1";document.activeElement=select;select.onchange();
await new Promise(resolve=>setTimeout(resolve,0));
const remote={custom:custom.value,browser:browser.value,status:status.textContent,
  note:browserSection.children[0].children[0].children[1].textContent};
select.value="2";document.activeElement=select;select.onchange();
const unsupported={disabled:custom.disabled&&browser.disabled&&save.disabled,
  status:status.textContent};
console.log(JSON.stringify({before,dirty,resetState,saved,remote,unsupported,calls}));
""".replace("__METHOD__", method)
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:1000]
    result = json.loads(proc.stdout)
    assert result["before"] == {
        "custom": "LOCAL", "browser": "DEFAULT",
        "status": "Up to 100 characters per field.",
        "note": guidance_copy,
    }, result
    assert result["dirty"] == "Unsaved changes", result
    assert result["resetState"] == {
        "browser": "DEFAULT", "status": "Unsaved changes"}, result
    assert result["saved"] == {
        "status": "Saved for new turns.", "toast": "Primary: System prompt saved"}, result
    assert result["remote"]["custom"] == "REMOTE" and \
        result["remote"]["browser"] == "REMOTE BROWSER", result
    assert result["remote"]["note"] == guidance_copy, result
    assert result["unsupported"] == {
        "disabled": True,
        "status": "Backend upgrade required for system prompt settings."}, result
    assert [call["method"] for call in result["calls"]] == ["PATCH", "GET"], result


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


def check_switch_engine_initial_selection(ui_source: str) -> None:
    """The switch modal initially selects the session's current engine."""
    expected = ('let pick = (engines.find(engine => engine.key === s.engine) || '
                'engines[0] || {}).key || "";')
    assert expected in ui_source


def check_double_activation_survives_rerender(ui_source: str) -> None:
    """A backend name rebuilt between clicks must still complete the gesture."""
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

    activation_pointer = extract_function("function activationPointer(")
    fallback_start = ui_source.index("const DOUBLE_ACTIVATION_MS = ")
    wire_start = ui_source.index("function wireDoubleClickOrTouch(", fallback_start)
    wire = extract_function("function wireDoubleClickOrTouch(")
    fallback = ui_source[fallback_start:wire_start] + wire
    script = r"""
class Target {
  constructor() { this.listeners={}; }
  addEventListener(kind,fn) { (this.listeners[kind] ||= []).push(fn); }
  emit(kind,values={}) {
    const event=Object.assign({isPrimary:true,pointerType:"",detail:0,timeStamp:0,
      clientX:0,clientY:0,prevented:false,stopped:false,
      preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}},values);
    for(const fn of this.listeners[kind] || []) fn(event);
    return event;
  }
}
%s
%s
const activations=[];
const make=(key,label)=>{const target=new Target();
  wireDoubleClickOrTouch(target,()=>activations.push(label),key);return target;};
const click=(target,detail,time,x=20,pointerType="mouse")=>{
  target.emit("pointerdown",{pointerType,timeStamp:time-20,clientX:x,clientY:10});
  return target.emit("click",{pointerType,detail,timeStamp:time,clientX:x,clientY:10});
};

const oldName=make("sessions:remote:7","old");
const first=click(oldName,1,100,20);
const replacementName=make("sessions:remote:7","replacement");
const replacement=click(replacementName,1,330,23);

pendingDoubleActivation=null;
click(make("sessions:remote:7","wrong sessions"),1,500,20);
click(make("status:remote:7","wrong status"),1,650,20);

pendingDoubleActivation=null;
click(make("sessions:remote:8","too late first"),1,1000,20);
click(make("sessions:remote:8","too late second"),1,1801,20);

pendingDoubleActivation=null;
click(make("sessions:remote:9","too far first"),1,2000,10);
click(make("sessions:remote:9","too far second"),1,2200,40);

pendingDoubleActivation=null;
const native=click(make("sessions:local","native"),2,2500,20);
pendingDoubleActivation=null;
const rapidTarget=make("sessions:remote:11","rapid");
const rapid=[1,2,3,4,5,6].map((detail,index)=>
  click(rapidTarget,detail,2600 + index * 70,20));
const touch=click(make("sessions:remote:10","touch"),1,2800,20,"touch");
console.log(JSON.stringify({activations,first,replacement,native,rapid,touch}));
""" % (activation_pointer, fallback)
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[:600]
    result = json.loads(proc.stdout.strip())
    assert result["activations"] == [
        "replacement", "native", "rapid", "rapid", "rapid", "touch"], result
    assert not result["first"]["prevented"] and not result["first"]["stopped"], result
    for key in ("replacement", "native", "touch"):
        assert result[key]["prevented"] and result[key]["stopped"], result
    assert [event["prevented"] for event in result["rapid"]] == [
        False, True, False, True, False, True], result


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


async def main() -> None:
    stub = TEST_ROOT / "stub-chromium"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o700)
    os.environ["PUPPY_BROWSER_BIN"] = str(stub)

    config.load()
    db.connect()
    # Phase-2 catalogs could map several sessions to one Browser. The visible
    # owner migration preserves a recorded owner, drops ambiguous duplicates,
    # and every subsequent handoff stays bijective without mutating its input.
    old_records = {
        "A1B2": {"created_at": 1.0, "closed_at": None,
                 "origin": "agent", "owner_session": 11},
        "C3D4": {"created_at": 2.0, "closed_at": None,
                 "origin": "user", "owner_session": None},
    }
    old_bindings = {11: "A1B2", 12: "A1B2", 13: "C3D4"}
    normalized_records, normalized_bindings = browser._normalize_catalog_ownership(
        old_records, old_bindings)
    assert normalized_bindings == {11: "A1B2", 13: "C3D4"}
    assert normalized_records["A1B2"]["owner_session"] == 11
    moved_records, moved_bindings, moved_ids = browser._rebind_catalog(
        normalized_records, normalized_bindings, "C3D4", 11)
    assert moved_bindings == {11: "C3D4"} and moved_ids == {"A1B2", "C3D4"}
    assert moved_records["A1B2"]["owner_session"] is None
    assert normalized_bindings == {11: "A1B2", 13: "C3D4"}
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
                assert "system-prompt" in ping["capabilities"], ping
                assert ping["browser"] == {"enabled": False}, ping
            async with http.get(url + "/api/system-prompt", headers=headers) as r:
                prompt_settings = await read_json(r)
                assert r.status == 200, prompt_settings
            default_browser_prompt = prompt_settings["system_prompt"]["browser_default"]
            async with http.patch(url + "/api/system-prompt", headers=headers,
                                  json={"custom": "first\r\nsecond"}) as r:
                prompt_settings = await read_json(r)
                assert r.status == 200, prompt_settings
                assert prompt_settings["system_prompt"]["custom"] == "first\nsecond"
            async with http.patch(url + "/api/system-prompt", headers=headers,
                                  json={"custom": "", "browser": default_browser_prompt}) as r:
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
            assert not (old_root / "retired-marker").exists()
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
            await ws.send_json({"type": "color_scheme", "value": "light"})
            await wait_for(lambda: "light" in schemes(), message="light emulation")
            assert config.get("browser.color_scheme") == "light"
            # a stale or hostile value is a rendering hint, not a launch failure
            assert browser.normalize_color_scheme("neon") == "dark"
            assert browser.normalize_color_scheme(None) == "dark"
            await ws.send_json({"type": "color_scheme", "value": "sepia"})
            await wait_for(lambda: schemes()[-1] == "dark", message="fallback emulation")
            await ws.send_json({"type": "color_scheme", "value": "light"})
            await wait_for(lambda: schemes()[-1] == "light", message="light again")

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

            async with http.get(url + "/api/browser/status", headers=headers) as r:
                running = await read_json(r)
                assert running["running"] is True and running["viewers"] == 1, running
                assert {item["id"] for item in running["instances"]} == \
                    {first_id, third_id}, running

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
            assert refused.type == aiohttp.WSMsgType.TEXT and \
                "disabled" in json.loads(refused.data)["text"]
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
            config.set_system_prompts(custom_prompt, policy)
            descriptor = browser_agent.turn_mcp(agent_sid, turn_id)
            assert descriptor and descriptor["name"] == "puppy_browser", descriptor
            assert descriptor["engine_guidance"] == policy
            assert "default for interactive web navigation" in policy
            assert "repository's own browser test suite" in policy
            assert "briefly state the concrete reason" in policy
            assert descriptor["env"]["PUPPY_BROWSER_SESSION_ID"] == str(agent_sid)
            assert descriptor["env"]["PUPPY_BROWSER_TURN_ID"] == turn_id
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
            assert codex_first[-1].startswith(
                "<puppy_system_prompt>\n" + custom_prompt +
                "\n</puppy_system_prompt>\n\n<puppy_browser_policy>\n" + policy +
                "\n</puppy_browser_policy>\n\n")
            assert codex_first[-1].endswith("\n\nhello")
            codex_argv = CodexDriver().build_cmd(
                resumed, False, "again", "unused", browser_mcp=descriptor,
                system_prompt=custom_prompt)
            resume_index = codex_argv.index("resume")
            mcp_options = [value for value in codex_argv[:resume_index]
                           if "mcp_servers.puppy_browser" in value]
            assert any(".command=" in value for value in mcp_options), codex_argv
            assert any("PUPPY_BROWSER_TURN_ID" in value for value in mcp_options), codex_argv
            assert codex_argv[-1].startswith(
                "<puppy_system_prompt>\n" + custom_prompt + "\n</puppy_system_prompt>\n\n" +
                "<puppy_browser_policy>\n" + policy)
            assert codex_argv[-1].endswith("\n\nagain")
            assert codex_argv[-1].count(policy) == 1
            codex_without_browser = CodexDriver().build_cmd(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt=custom_prompt)
            assert codex_without_browser[-1] == (
                "<puppy_system_prompt>\n" + custom_prompt +
                "\n</puppy_system_prompt>\n\nplain")
            assert not any("mcp_servers.puppy_browser" in value
                           for value in codex_without_browser)
            codex_without_guidance = CodexDriver().build_cmd(
                resumed, False, "plain", "unused", browser_mcp=None,
                system_prompt="")
            assert codex_without_guidance[-1] == "plain"

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
                    "agent-document.txt" in upload_text, upload_text
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
                assert "Dispatched a scroll" in scrolled["result"]["content"][0]["text"]

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
            check_reconnect_status(ui_source)
            check_backend_shutdown_notice(ui_source)
            check_interrupted_completion(ui_source)
            check_thinking_icons(ui_source)
            check_backend_editor(ui_source, css_source)
            check_drawer_drag(ui_source)
            check_browser_viewport(ui_source)
            check_browser_handoff_ui(ui_source, css_source)
            check_desktop_side_drag(ui_source)
            check_user_message_copy(ui_source)
            check_queue_pause_ui(ui_source, css_source)
            check_system_prompt_settings(ui_source, css_source)
            check_opencode_chat_models(ui_source, css_source)
            check_session_provider_marks(css_source)
            check_switch_engine_initial_selection(ui_source)
            check_double_activation_survives_rerender(ui_source)
            check_browser_disable_closes_scoped_tabs(ui_source)
            check_quota_math(ui_source)
            # one checkbox face app-wide: a native checkbox is painted by the
            # browser, ignores the theme and differs per platform, so the form
            # input and the drawn menu mark share one rule and one tick path
            css_source = (BASE / "puppy" / "static" / "app.css").read_text()
            assert ".check input[type=checkbox],\n.menu-check-mark{" in css_source
            assert "-webkit-appearance:none;appearance:none" in css_source
            assert css_source.count("--check-tick:url(") == 1
            # Session and engine-status disclosures share the sidebar's 240ms
            # slide/fade helper. They are settled (and truly hidden) at rest,
            # with transitions attached only for a user-triggered toggle so a
            # restored collapse cannot animate during the first paint.
            assert "setDisclosureCollapsed(body, isCollapsed, animate);" in ui_source
            assert "sync(true);" in ui_source
            assert ui_source.count("SLIDE_MOTION_MS + 40") == 2
            assert ".sess-group-body[hidden],.foot-engine-body[hidden]{display:none}" in css_source
            assert (".sess-group-body.disclosure-animating," +
                    ".foot-engine-body.disclosure-animating{") in css_source
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
            # The metadata strip already scrolls horizontally. Its engine/model
            # identity must use its full intrinsic width rather than inheriting
            # the generic 160px phone cap and clipping the selected model ID.
            assert ".chat-meta-scroll .chip.eng{max-width:none}" in css_source
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
            # Polls rebuild backend headings. The second click is keyed to the
            # logical section so replacing its span cannot reset a double-click.
            assert "previous.key === activationKey" in ui_source
            assert "`sessions:${key}`" in ui_source
            assert "`status:${key}`" in ui_source
            assert ui_source.count("event.detail > 0 && event.detail % 2 === 0") == 2
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
