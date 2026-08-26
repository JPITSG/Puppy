#!/usr/bin/env python3
"""No-quota tests for the managed headless-browser surface.

A stub "chromium" speaks just enough of the --remote-debugging-pipe CDP
contract (fds 3/4, NUL-framed JSON) to exercise the probe, the node-owned
enable gate, isolated named instances, ID retention, sandbox flag selection,
the screencast/input websocket, viewer input scaling, URL normalization, the
private per-turn MCP bridge, hidden model guidance, background first-use tab
events, close-and-replace behavior, and crash reporting. No real browser is
installed or launched and nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import base64
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

from puppy import browser, browser_agent, config, db, runner as session_runner  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import CodexDriver  # noqa: E402
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

with open(os.path.join(LOG, "argv.jsonl"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "argv": sys.argv}) + "\n")

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
        elif method == "Accessibility.getFullAXTree":
            result = {"nodes": [
                {"nodeId": "root", "role": {"value": "RootWebArea"},
                 "name": {"value": "Stub page"}, "childIds": ["button", "input"]},
                {"nodeId": "button", "role": {"value": "button"},
                 "name": {"value": "Continue"}, "backendDOMNodeId": 10,
                 "properties": [{"name": "focusable", "value": {"value": True}}]},
                {"nodeId": "input", "role": {"value": "textbox"},
                 "name": {"value": "Email"}, "backendDOMNodeId": 11,
                 "properties": [{"name": "focusable", "value": {"value": True}}]},
            ]}
        elif method == "DOM.getBoxModel":
            result = {"model": {"content": [100, 40, 300, 40, 300, 80, 100, 80]}}
        elif method == "DOM.focus":
            record("dom.jsonl", {"method": method, "params": params})
        elif method == "Emulation.setEmulatedMedia":
            record("media.jsonl", {"features": params.get("features")})
        elif method == "Page.getFrameTree":
            result = {"frameTree": {"frame": {"id": "f1", "url": PAGE["url"]}}}
        elif method == "Page.setDocumentContent":
            record("document.jsonl", {"frameId": params.get("frameId"),
                                      "html": params.get("html", "")})
            PAGE["title"] = "start page"
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


async def main() -> None:
    stub = TEST_ROOT / "stub-chromium"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o700)
    os.environ["PUPPY_BROWSER_BIN"] = str(stub)

    config.load()
    db.connect()
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

            # ID-scoped viewer websocket: status text, frames, input forwarding.
            texts, frames = [], []
            ws = await http.ws_connect(
                url + "/api/ws/browser/" + first_id, headers=headers)
            reader = asyncio.ensure_future(collect_ws(ws, texts, frames))
            await wait_for(lambda: frames, message="first frame")
            await wait_for(lambda: any(t.get("type") == "status" for t in texts),
                           message="status message")
            assert frames[0] == b"stub-jpeg-frame-bytes", frames[0][:40]
            meta = next(t for t in texts if t.get("type") == "frame_meta")
            assert meta["width"] == 1280 and meta["height"] == 800, meta

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
            descriptor = browser_agent.turn_mcp(agent_sid, turn_id)
            assert descriptor and descriptor["name"] == "puppy_browser", descriptor
            assert descriptor["env"]["PUPPY_BROWSER_SESSION_ID"] == str(agent_sid)
            assert descriptor["env"]["PUPPY_BROWSER_TURN_ID"] == turn_id
            assert Path(descriptor["env"]["PUPPY_BROWSER_SOCKET"]).stat().st_mode & 0o777 \
                == 0o600

            agent_session = db.get_session(agent_sid)
            claude_argv = ClaudeDriver().build_cmd(
                agent_session, True, "hello", "native-1", browser_mcp=descriptor)
            config_index = claude_argv.index("--mcp-config")
            claude_mcp = json.loads(claude_argv[config_index + 1])
            assert claude_mcp["mcpServers"]["puppy_browser"]["command"] == \
                descriptor["command"], claude_mcp
            resumed = dict(agent_session, native_session_id="existing-native")
            claude_resume = ClaudeDriver().build_cmd(
                resumed, False, "again", "unused", browser_mcp=descriptor)
            assert "--resume" in claude_resume and "--mcp-config" in claude_resume

            codex_argv = CodexDriver().build_cmd(
                resumed, False, "again", "unused", browser_mcp=descriptor)
            resume_index = codex_argv.index("resume")
            mcp_options = [value for value in codex_argv[:resume_index]
                           if "mcp_servers.puppy_browser" in value]
            assert any(".command=" in value for value in mcp_options), codex_argv
            assert any("PUPPY_BROWSER_TURN_ID" in value for value in mcp_options), codex_argv

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
                assert initialized["result"]["serverInfo"]["version"] == "2"
                instructions = initialized["result"].get("instructions", "")
                assert "fresh, isolated" in instructions and \
                    "without taking focus" in instructions and \
                    "explicitly asks" in instructions
                listed = await mcp_request(mcp, 2, "tools/list")
                tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
                assert {"new_browser", "snapshot", "screenshot", "navigate",
                        "click", "type"} <= set(tools)
                assert "browser_id" in tools["snapshot"]["inputSchema"]["properties"]
                assert "browser_id" not in tools["new_browser"]["inputSchema"]["properties"]

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

                shot = await mcp_request(mcp, 4, "tools/call", {
                    "name": "screenshot", "arguments": {}})
                assert any(item.get("type") == "image" and
                           item.get("data") == base64_stub for item in
                           shot["result"]["content"]), shot
                await mcp_request(mcp, 5, "tools/call", {
                    "name": "click", "arguments": {"ref": "b1"}})
                await mcp_request(mcp, 6, "tools/call", {
                    "name": "type", "arguments": {
                        "ref": "b2", "text": "agent text", "clear": True}})
                await mcp_request(mcp, 7, "tools/call", {
                    "name": "navigate", "arguments": {
                        "url": "router.lan/status", "wait_ms": 0}})
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
                assert read_lines("dom.jsonl")[-1]["params"]["backendNodeId"] == 11
                assert read_lines("navigations.jsonl")[-1]["url"] == \
                    "http://router.lan/status"

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
            assert 'case "browser_activity"' in ui_source
            assert "`Browser ${id} @ ${backendName(bid)}`" in ui_source
            assert "{ activate: false, afterTabId: sessionTabId, sid }" in ui_source
            assert "browser/instances/${encodeURIComponent(closing.browserId)}" in ui_source
            # the owning chat advertises its live browsers as clickable bubbles
            assert "syncBrowserChips()" in ui_source
            assert 'type: "color_scheme", value: currentTheme()' in ui_source
            # the settings switch is seeded before the availability probe, so it
            # cannot render off and then visibly flip on
            assert "input.checked = browserEnabledFor(bid);" in ui_source
            # session menus float on <body>: inside .chat-head's z-index:2
            # stacking context a split's divider and terminal painted over them
            assert "anchor.parentElement.appendChild(menu)" not in ui_source
            assert ui_source.count("document.body.appendChild(menu)") >= 5
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
            await ws3.send_json({"type": "navigate", "url": "stub://die"})
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
