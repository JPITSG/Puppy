#!/usr/bin/env python3
"""No-quota integration test for the separately deployable headless backend."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from puppy import __version__, protocol, upgrade_contract  # noqa: E402
from puppy.drivers.codex import CodexDriver  # noqa: E402


def exercise_driver_normalization() -> None:
    driver = CodexDriver()
    context = {}
    search = driver.parse_line(json.dumps({
        "type": "item.completed",
        "item": {
            "type": "web_search", "id": "search-1", "status": "completed",
            "query": "python TLS support ...",
            "action": {"type": "search", "query": None,
                       "queries": ["python 3.9 TLS", "aiohttp certificate pin"]},
            "results": [
                {"title": "ssl documentation", "url": "https://docs.python.org/3/library/ssl.html",
                 "snippet": "TLS support in the standard library."},
                {"title": "aiohttp documentation", "url": "https://docs.aiohttp.org/",
                 "snippet": "Fingerprint verification."},
            ],
        },
    }), context)
    assert [action.get("kind") for action in search] == ["tool_use", "tool_result"]
    assert search[0]["data"]["tool"] == "web_search"
    assert search[0]["data"]["input"]["queries"] == \
        ["python 3.9 TLS", "aiohttp certificate pin"]
    assert "https://docs.python.org/" in search[1]["data"]["content"]
    assert search[1]["data"]["is_error"] is False

    structured_search = driver.parse_line(json.dumps({
        "type": "item.completed",
        "item": {"type": "web_search", "action": "open_page",
                 "results": {"url": "https://example.test/", "status": 200}},
    }), context)
    assert structured_search[0]["data"]["input"] == {"action": "open_page"}
    assert structured_search[0]["data"]["tool_use_id"] == "codex-item-1"
    assert '"status": 200' in structured_search[1]["data"]["content"]

    image = driver.parse_line(json.dumps({
        "type": "item.completed",
        "item": {"type": "image_view", "id": "image-1", "path": "/data/example.png"},
    }), context)
    assert [action.get("kind") for action in image] == ["tool_use", "tool_result"]
    assert image[0]["data"]["input"] == {"path": "/data/example.png"}

    future_call = driver.parse_line(json.dumps({
        "type": "item.completed",
        "item": {"type": "collab_agent_tool_call", "id": "call-1", "name": "delegate",
                 "arguments": {"task": "inspect"}, "result": {"status": "done"}},
    }), context)
    assert [action.get("kind") for action in future_call] == ["tool_use", "tool_result"]
    assert future_call[0]["data"]["tool"] == "delegate"
    assert '"status": "done"' in future_call[1]["data"]["content"]

    lifecycle = driver.parse_line(json.dumps({
        "type": "item.completed", "item": {"type": "context_compaction", "id": "compact-1"},
    }), context)
    assert lifecycle == []


def exercise_opencode_driver() -> None:
    """Pin catalog parsing and the full no-model ACP handshake/state machine."""
    from puppy import runner
    from puppy.drivers.opencode import OpenCodeDriver, _display_provider, parse_model_catalog
    catalog = parse_model_catalog("""provider/model-a
{
  "id": "model-a",
  "providerID": "provider",
  "name": "Model A",
  "limit": {"context": 128000, "output": 8192},
  "capabilities": {"input": {"text": true, "image": true}},
  "variants": {"high": {}, "max": {}}
}
second/model-b
{
  "id": "model-b",
  "providerID": "second",
  "name": "Model B",
  "variants": {}
}
""")
    assert [model["value"] for model in catalog] == \
        ["provider/model-a", "second/model-b"]
    assert catalog[0]["label"] == "Model A"
    assert "128k context" in catalog[0]["hint"] and "image input" in catalog[0]["hint"]
    assert [item["value"] for item in catalog[0]["effort_options"]] == \
        ["", "high", "max"]

    driver = OpenCodeDriver()
    driver._catalog = catalog
    exposed = driver.model_options()
    assert [model["value"] for model in exposed] == \
        ["", "provider/model-a", "second/model-b"]
    assert exposed[0]["label"] == "Default"
    assert exposed[1]["label"] == "Provider · Model A"
    assert _display_provider("opencode") == "OpenCode"
    assert driver.default_model() == ""
    assert runner._starts_fresh_native_session(
        {"native_session_id": ""}, False, driver) is True
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, False, driver) is False
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, True, driver) is True
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, True, CodexDriver()) is False
    session = {
        "cwd": "/tmp", "native_session_id": "", "model": "provider/model-a",
        "effort": "high", "permission_mode": "manual",
    }
    browser = {
        "name": "puppy_browser", "command": "/tmp/browser-agent",
        "args": ["--session", "9"], "env": {"PUPPY_SOCKET": "/tmp/socket"},
        "engine_guidance": "Use the shared browser.",
    }
    terminal_mcp = {
        "name": "puppy_terminal", "command": "/tmp/terminal-agent",
        "args": [], "env": {"PUPPY_TERMINAL_SOCKET": "/tmp/terminal-socket"},
        "engine_guidance": "Use the shared terminal only when requested.",
    }
    inline = json.loads(driver.build_env(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=terminal_mcp,
        system_prompt="Be concise.")["OPENCODE_CONFIG_CONTENT"])
    agent = inline["agent"]["puppy_console"]
    assert inline["default_agent"] == "puppy_console"
    assert "Be concise." in agent["prompt"] and "Use the shared browser." in agent["prompt"]
    assert "Use the shared terminal only when requested." in agent["prompt"]
    assert agent["permission"]["*"] == "ask" and agent["permission"]["read"] == "allow"

    ctx = driver.turn_context(session, True, "hello", "pin", browser_mcp=browser,
                              terminal_mcp=terminal_mcp)
    assert ctx["mcp_servers"] == [{
        "name": "puppy_browser", "command": "/tmp/browser-agent",
        "args": ["--session", "9"],
        "env": [{"name": "PUPPY_SOCKET", "value": "/tmp/socket"}],
    }, {
        "name": "puppy_terminal", "command": "/tmp/terminal-agent",
        "args": [],
        "env": [{"name": "PUPPY_TERMINAL_SOCKET", "value": "/tmp/terminal-socket"}],
    }]
    initial = driver.initial_stdin(session, "hello")[0]
    assert initial["method"] == "initialize" and initial["params"]["protocolVersion"] == 1
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:initialize", "result": {
            "agentCapabilities": {"loadSession": True}}}), ctx)
    assert actions[0]["data"]["method"] == "session/new"

    options = [
        {"id": "model", "currentValue": "provider/default",
         "options": [{"value": "provider/default"}, {"value": "provider/model-a"}]},
        {"id": "mode", "currentValue": "build",
         "options": [{"value": "build"}, {"value": "puppy_console"}]},
    ]
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:session",
        "result": {"sessionId": "ses_test", "configOptions": options}}), ctx)
    assert actions[0] == {"a": "native_id", "id": "ses_test"}
    assert actions[1]["data"]["params"]["configId"] == "mode"

    options[1]["currentValue"] = "puppy_console"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config", "result": {"configOptions": options}}), ctx)
    assert actions[-1]["data"]["params"] == {
        "sessionId": "ses_test", "configId": "model", "value": "provider/model-a"}

    model_options = [
        {"id": "model", "currentValue": "provider/model-a"},
        {"id": "effort", "currentValue": "none",
         "options": [{"value": "none"}, {"value": "high"}]},
        {"id": "mode", "currentValue": "puppy_console"},
    ]
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config",
        "result": {"configOptions": model_options}}), ctx)
    assert actions[0] == {"a": "model", "model": "provider/model-a"}
    assert actions[-1]["data"]["params"]["configId"] == "effort"
    model_options[1]["currentValue"] = "high"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config",
        "result": {"configOptions": model_options}}), ctx)
    assert actions[-1]["data"]["method"] == "session/prompt"
    assert actions[-1]["data"]["params"]["prompt"] == [{"type": "text", "text": "hello"}]

    update = lambda value: json.dumps({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": "ses_test", "update": value}})
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "considering"}}), ctx)
    assert actions[-1]["msg"] == {"type": "delta", "block": "thinking",
                                   "text": "considering"}
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Before tool."}}), ctx)
    assert actions[0]["kind"] == "thinking"
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": " More."}}), ctx)
    assert actions[-1]["msg"]["text"] == " More."
    actions = driver.parse_line(update({
        "sessionUpdate": "tool_call", "toolCallId": "call-1", "kind": "execute",
        "title": "Run check", "status": "in_progress", "rawInput": {"command": "true"}}), ctx)
    assert [action.get("kind") for action in actions if action.get("a") == "event"] == \
        ["assistant", "tool_use"]
    assert actions[0]["data"]["text"] == "Before tool. More."
    actions = driver.parse_line(update({
        "sessionUpdate": "tool_call_update", "toolCallId": "call-1", "kind": "execute",
        "title": "Run check", "status": "completed", "rawOutput": {"output": "ok"}}), ctx)
    assert actions[-1]["kind"] == "tool_result" and actions[-1]["data"]["is_error"] is False

    approval = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
        "params": {"sessionId": "ses_test", "toolCall": {
            "toolCallId": "call-2", "kind": "execute", "title": "Run command",
            "rawInput": {"command": "make test"}}, "options": [
                {"optionId": "once", "kind": "allow_once"},
                {"optionId": "always", "kind": "allow_always"},
                {"optionId": "reject", "kind": "reject_once"},
            ]}}), ctx)[0]["req"]
    assert approval["request_id"] == "7" and approval["suggestions"][0]["type"] == "allowAlways"
    reply = driver.approval_payload(
        "7", "allow", approval["input"], updated_permissions=[{"type": "allowAlways"}],
        request=approval)
    assert reply == {"jsonrpc": "2.0", "id": 7,
                     "result": {"outcome": {"outcome": "selected", "optionId": "always"}}}

    driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Done."}}), ctx)
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:prompt", "result": {
            "stopReason": "end_turn", "usage": {
                "inputTokens": 10, "outputTokens": 4, "totalTokens": 14}}}), ctx)
    assert actions[0] == {"a": "event", "kind": "assistant", "data": {"text": "Done."}}
    assert actions[-1]["data"]["usage"] == {
        "input_tokens": 10, "output_tokens": 4, "total_tokens": 14}

    resumed = dict(session, native_session_id="ses_existing", effort="")
    resume_ctx = driver.turn_context(resumed, False, "again", "pin")
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:initialize", "result": {}}), resume_ctx)
    assert actions[0]["data"]["method"] == "session/resume"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:session",
        "error": {"code": -32601, "message": "Method not found"}}), resume_ctx)
    assert actions[0]["data"]["method"] == "session/load"


async def exercise_opencode_binary_fallback(root) -> None:
    """The official per-user install must work outside a login-shell PATH."""
    from puppy import cli_upgrade
    from puppy.drivers import base as driver_base
    from puppy.drivers.opencode import OpenCodeDriver

    root = Path(root)
    home = root / "home"
    binary = home / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    binary.write_text("""#!{python}
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("1.2.3")
elif sys.argv[1:] == ["models", "--verbose"]:
    print("fallback/model-a")
    print(json.dumps({{
        "id": "model-a", "providerID": "fallback", "name": "Model A",
        "variants": {{"high": {{}}}},
    }}))
else:
    raise SystemExit(2)
""".format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)

    saved_home = os.environ.get("HOME")
    saved_path = os.environ.get("PATH")
    try:
        os.environ["HOME"] = str(home)
        # Deliberately exclude the fake install: this is the systemd case.
        os.environ["PATH"] = "/usr/bin:/bin"
        driver = OpenCodeDriver()
        assert driver.resolved_binary() == str(binary)

        driver_base.invalidate_status("opencode")
        status = await driver.status()
        assert status["installed"] is True
        assert status["version"] == "1.2.3"
        assert status["auth"] == "ok" and status["detail"] == "binary available"

        await driver.refresh_model_options(force=True)
        assert [item["value"] for item in driver.model_options()] == \
            ["", "fallback/model-a"]
        session = {"cwd": "/tmp"}
        assert driver.build_cmd(session, True, "hello", "pin")[:2] == \
            [str(binary), "acp"]
        assert cli_upgrade._argv(driver) == [str(binary), "upgrade"]
    finally:
        driver_base.invalidate_status("opencode")
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path


def exercise_activity_blocks(session_hub_cls) -> None:
    """Queued turns retain one start time and become idle only after the tail."""
    hub = session_hub_cls(-1)
    started = time.time() - 42
    hub.status = "running"
    hub.active_since = started
    hub.queue = ["next queued turn"]
    assert hub._take_next_turn() == "next queued turn"
    assert hub.status == "running" and hub.active_since == started
    assert hub._take_next_turn() is None
    assert hub.status == "idle" and hub.active_since is None


async def exercise_shutdown_broadcast(runner_module) -> None:
    """Both node and open-session watchers receive the same bounded notice."""
    class Capture:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    hub_id = -9002
    hub = runner_module.SessionHub(hub_id)
    updates = Capture()
    session = Capture()
    runner_module._hubs[hub_id] = hub
    runner_module.updates_attach(updates)
    hub.attach(session)
    try:
        await runner_module.announce_node_stopping("restart")
        for capture in (updates, session):
            assert len(capture.messages) == 1
            notice = capture.messages[0]
            assert notice["type"] == "node_stopping"
            assert notice["reason"] == "restart"
            assert isinstance(notice["server_time"], (int, float))
    finally:
        hub.detach(session)
        runner_module.updates_detach(updates)
        runner_module._hubs.pop(hub_id, None)


def exercise_host_cpu_math(host_metrics_module) -> None:
    previous = host_metrics_module._parse_cpu_stat(
        "intr 1\ncpu 100 10 20 400 50 5 6 9 1000 1000\n")
    current = host_metrics_module._parse_cpu_stat(
        "cpu 130 10 30 440 50 5 10 15 5000 5000\n")
    assert previous is not None and current is not None
    # guest counters are deliberately excluded because Linux already includes
    # them in user/nice. Of 90 elapsed ticks, 40 were idle.
    assert round(host_metrics_module._cpu_percent(previous, current), 1) == 55.6
    assert host_metrics_module._parse_cpu_stat("cpu invalid counters\n") is None
    assert host_metrics_module._cpu_percent(current, previous) is None


async def exercise_upgrade_readiness(upgrade_module, runner_module,
                                     terminal_module, temporary: Path) -> None:
    """Readiness and the POST gate must agree on workload and runtime blockers."""
    temporary.mkdir(parents=True, exist_ok=True)
    marker = temporary / "readiness-pending.json"
    runtime = {"enabled": True, "reason": "", "marker": marker}
    app = {"puppy_upgrade_draining": False}
    hub_id = -9001
    hub = runner_module.SessionHub(hub_id)
    old_terminal_count = terminal_module._active_terminals
    old_upload_count = upgrade_module.uploads._active_uploads
    old_runtime = upgrade_module._runtime
    runner_module._hubs[hub_id] = hub
    try:
        ready = upgrade_module._readiness(runtime, app)
        assert ready["ready"] is True and ready["state"] == "ready"
        assert ready["sessions"] == [] and ready["active_terminals"] == 0

        hub.status = "running"
        hub.active_since = time.time()
        hub.queue = ["queued behind the active turn"]
        terminal_module._active_terminals = 2
        busy = upgrade_module._readiness(runtime, app)
        assert busy["ready"] is False and busy["state"] == "busy"
        assert busy["sessions"][0]["id"] == hub_id
        assert busy["sessions"][0]["queued"] == 1
        assert busy["active_terminals"] == 2
        assert "running turn" in busy["reason"] and "queued message" in busy["reason"]
        assert "active terminals" in busy["reason"]

        upgrade_module._runtime = lambda tls_enabled=None: runtime
        rejected = await upgrade_module.h_upgrade(type("Request", (), {"app": app})())
        rejected_body = json.loads(rejected.text)
        assert rejected.status == 409
        assert rejected_body["readiness"]["state"] == "busy"

        app["puppy_upgrade_draining"] = True
        upgrading = upgrade_module._readiness(runtime, app)
        assert upgrading["ready"] is False and upgrading["state"] == "upgrading"
        app["puppy_upgrade_draining"] = False

        hub.status = "idle"
        hub.active_since = None
        hub.queue = []
        terminal_module._active_terminals = 0
        upgrade_module.uploads._active_uploads = 1
        uploading = upgrade_module._workload_readiness()
        assert uploading["ready"] is False and uploading["state"] == "busy"
        assert uploading["active_uploads"] == 1
        assert "active file upload" in uploading["reason"]
        upgrade_module.uploads._active_uploads = 0
        marker.touch()
        blocked = upgrade_module._readiness(runtime, app)
        assert blocked["ready"] is False and blocked["state"] == "blocked"

        unsupported = upgrade_module._readiness({
            "enabled": False, "reason": "launcher unavailable", "marker": None,
        }, app)
        assert unsupported["state"] == "unsupported"
        assert unsupported["reason"] == "launcher unavailable"
    finally:
        upgrade_module._runtime = old_runtime
        runner_module._hubs.pop(hub_id, None)
        terminal_module._active_terminals = old_terminal_count
        upgrade_module.uploads._active_uploads = old_upload_count


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def previous_patch_version() -> str:
    major, minor, patch = upgrade_contract.version_key(__version__)
    assert patch > 0
    return f"{major}.{minor}.{patch - 1}"


def copy_with_version(source: Path, target: Path, version: str) -> None:
    with zipfile.ZipFile(source) as current, \
            zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as older:
        for info in current.infolist():
            payload = current.read(info.filename)
            if info.filename == "puppy/__init__.py":
                text = payload.decode("utf-8")
                text = text.replace(f'__version__ = "{__version__}"',
                                    f'__version__ = "{version}"')
                payload = text.encode("utf-8")
            older.writestr(info, payload)
    target.chmod(0o755)


def stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def stop_process_with_notice(process: subprocess.Popen, url: str, token: str,
                                   fingerprint: str = "") -> None:
    """SIGTERM must announce the node lifecycle before its sockets disappear."""
    headers = {"X-Puppy-Token": token}
    async with aiohttp.ClientSession() as http:
        updates = await http.ws_connect(
            url + "/api/ws/updates", headers=headers, ssl=ssl_pin(fingerprint))
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "sessions"
        process.terminate()
        notice = await updates.receive_json(timeout=3)
        assert notice["type"] == "node_stopping", notice
        assert notice["reason"] == "shutdown", notice
        assert isinstance(notice["server_time"], (int, float)), notice
        await updates.close()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError("backend did not finish its graceful shutdown")


def ssl_pin(fingerprint: str):
    return aiohttp.Fingerprint(bytes.fromhex(fingerprint)) if fingerprint else True


async def wait_for_backend(url: str, process: subprocess.Popen,
                           fingerprint: str = "") -> None:
    pinned = ssl_pin(fingerprint)
    async with aiohttp.ClientSession() as http:
        for _ in range(100):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"backend exited during startup:\n{output[-3000:]}")
            try:
                async with http.get(url + "/api/ping", ssl=pinned) as response:
                    if response.status == 401:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.05)
    raise AssertionError("backend did not start")


async def exercise_node(url: str, token: str, expected_version: str,
                        upgrade_enabled: bool, fingerprint: str = "") -> None:
    good = {"X-Puppy-Token": token}
    pinned = ssl_pin(fingerprint)
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", ssl=pinned) as response:
            assert response.status == 401
        async with http.get(url + "/api/ping",
                            headers={"X-Puppy-Token": "wrong-token-value"},
                            ssl=pinned) as response:
            assert response.status == 401
        async with http.get(url + "/api/ping", headers=good, ssl=pinned) as response:
            assert response.status == 200
            ping = await response.json()
        assert ping["role"] == "backend"
        assert ping["protocol"] == 1
        assert ping["version"] == expected_version
        assert "sessions" in ping["capabilities"]
        assert "temporary-workspaces" in ping["capabilities"]
        assert "engine-usage-refresh" in ping["capabilities"]
        assert "engine-usage-refresh-manual" in ping["capabilities"]
        assert "engine-upgrade" in ping["capabilities"]
        assert "engine-model-selection" not in ping["capabilities"]
        assert "file-uploads" in ping["capabilities"]
        assert "queue-pause" in ping["capabilities"]
        assert "queue-edit" in ping["capabilities"]
        assert "queue-reorder" in ping["capabilities"]
        assert "queued-engine-switch" in ping["capabilities"]
        assert "session-drafts" in ping["capabilities"]
        assert "system-prompt" in ping["capabilities"]
        assert "shutdown-notice" in ping["capabilities"]
        assert ping["shutting_down"] is False
        # browser surface: capability is static, enablement is node config
        # (off in this deployment), availability is probed on demand
        assert "browser" in ping["capabilities"]
        assert "browser-instances" in ping["capabilities"]
        assert "browser-handoff" in ping["capabilities"]
        assert "browser-file-workflows" in ping["capabilities"]
        assert ping["browser"] == {"enabled": False}
        assert ping["uploads"]["enabled"] is \
            (ping["uploads"]["max_file_size_mb"] > 0)
        async with http.get(url + "/api/system-prompt", headers=good,
                            ssl=pinned) as response:
            prompt_payload = await response.json()
            assert response.status == 200, prompt_payload
        prompt_defaults = prompt_payload["system_prompt"]
        assert prompt_defaults["custom"] == ""
        assert "coding engine runs on this backend" in \
            prompt_defaults["remote_workspace"]
        assert prompt_defaults["remote_workspace_default"] == \
            prompt_defaults["remote_workspace"]
        assert "shared, user-visible Puppy browser" in prompt_defaults["browser"]
        assert prompt_defaults["browser_default"] == prompt_defaults["browser"]
        assert "explicitly asks" in prompt_defaults["terminal"]
        assert prompt_defaults["terminal_default"] == prompt_defaults["terminal"]
        assert prompt_defaults["max_chars"] == 32768
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "Use terse answers.",
                                    "remote_workspace":
                                        "Treat the working tree as a synchronized mirror.",
                                    "browser": "Use the visible browser first.",
                                    "terminal": "Use a shared terminal only on request."}) as response:
            saved_prompt = await response.json()
            assert response.status == 200, saved_prompt
        assert saved_prompt["system_prompt"]["custom"] == "Use terse answers."
        assert saved_prompt["system_prompt"]["remote_workspace"] == \
            "Treat the working tree as a synchronized mirror."
        assert saved_prompt["system_prompt"]["browser"] == "Use the visible browser first."
        assert saved_prompt["system_prompt"]["terminal"] == \
            "Use a shared terminal only on request."
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "x" * 32769}) as response:
            rejected_prompt = await response.json()
            assert response.status == 400, rejected_prompt
            assert "cannot exceed" in rejected_prompt["error"]
        async with http.get(url + "/api/system-prompt", ssl=pinned) as response:
            assert response.status == 401
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "",
                                    "remote_workspace":
                                        prompt_defaults["remote_workspace_default"],
                                    "browser": prompt_defaults["browser_default"],
                                    "terminal": prompt_defaults["terminal_default"]}) as response:
            assert response.status == 200, await response.text()
        assert "terminal" not in ping["capabilities"]
        assert "terminal-instances" not in ping["capabilities"]
        assert "terminal-handoff" not in ping["capabilities"]
        # the completion-command endpoint is part of the shell surface: a node
        # deployed without a terminal must not run commands either
        assert "notify-exec" not in ping["capabilities"]
        async with http.post(url + "/api/notify/exec", headers=good, ssl=pinned,
                             json={"command": "true"}) as response:
            assert response.status == 404
        # The authenticated named-browser handoff routes are packaged in the
        # headless runtime even while Browser is off; an unknown logical ID is
        # a domain error, not an absent route.
        async with http.get(url + "/api/browser/instances/A1B2/binding",
                            headers=good, ssl=pinned) as response:
            missing_binding = await response.json()
            assert response.status == 404, missing_binding
            assert "closed or unknown" in missing_binding["error"]
        assert ("pinned-tls" in ping["capabilities"]) is bool(fingerprint)
        assert ping["transport"]["encrypted"] is bool(fingerprint)
        if fingerprint:
            assert ping["transport"]["certificate_sha256"] == fingerprint
        assert ("remote-upgrade" in ping["capabilities"]) is upgrade_enabled
        assert ping["upgrade"]["supported"] is upgrade_enabled
        assert ping["upgrade"]["api"] == "/api/node/upgrade"
        assert ping["upgrade"]["signing"] == "hmac-sha256"
        assert ping["upgrade"]["restart"] == "external-launcher"
        ping_readiness = ping["upgrade"]["readiness"]
        assert ping_readiness["ready"] is upgrade_enabled
        assert ping_readiness["state"] == \
            ("ready" if upgrade_enabled else "unsupported")
        assert ping["build"]["artifact"] == "zipapp"

        async with http.get(url + "/api/node", headers=good, ssl=pinned) as response:
            assert response.status == 200
        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            assert response.status == 200
            sessions_payload = await response.json()
            assert sessions_payload["sessions"] == []
            assert isinstance(sessions_payload["server_time"], (int, float))
        async with http.get(url + "/api/browser/status", headers=good, ssl=pinned) as response:
            assert response.status == 200
            browser_status = await response.json()
            assert browser_status["supported"] is True
            assert browser_status["enabled"] is False
            assert browser_status["running"] is False
            assert browser_status["instances"] == []
            assert isinstance(browser_status["available"], bool)
        async with http.get(url + "/api/engines", headers=good, ssl=pinned) as response:
            engine_payload = await response.json()
            assert response.status == 200, engine_payload
        by_key = {engine["key"]: engine for engine in engine_payload["engines"]}
        assert set(("claude", "codex", "opencode")).issubset(by_key)
        opencode = by_key["opencode"]
        assert opencode["availability_only"] is True
        assert opencode["dynamic_model_options"] is True
        assert opencode["allow_custom_model"] is False
        assert isinstance(opencode["model_catalog_error"], str)
        assert opencode["model_options"][0]["value"] == ""
        assert opencode["model_options"][0]["label"] == "Default"
        assert len({model["value"] for model in opencode["model_options"]}) == \
            len(opencode["model_options"])
        if opencode["installed"]:
            assert opencode["auth"] == "ok"
            assert opencode["detail"] == "binary available"
        for engine in engine_payload["engines"]:
            assert isinstance(engine["latest_version"], str)
            assert engine["update_available"] in (True, False, None)
            assert engine["latest_checked_at"] is None or \
                isinstance(engine["latest_checked_at"], (int, float))
            assert isinstance(engine["latest_check_error"], str)
            assert isinstance(engine["upgrade_supported"], bool)
            assert engine["upgrade_state"] in ("idle", "running")
            assert engine["upgrade_result"] is None or \
                isinstance(engine["upgrade_result"], dict)
            assert isinstance(engine["version_checked_at"], (int, float))
        # OpenCode's discovered choices feed model_options directly; there is
        # no separate node-owned allow-list API.
        async with http.patch(url + "/api/engines/opencode/models", headers=good,
                              ssl=pinned, json={"models": []}) as response:
            assert response.status == 404, await response.text()
        # The headless surface serves the same version-refresh route as the
        # console; a node that cannot re-check must not advertise the button.
        async with http.post(url + "/api/engines/refresh",
                             headers=good, ssl=pinned) as response:
            rechecked = await response.json()
            assert response.status == 200, rechecked
        assert isinstance(rechecked["engines"], list)
        assert "usage_refresh" in rechecked
        async with http.post(url + "/api/engines/not-an-engine/upgrade",
                             headers=good, ssl=pinned) as response:
            assert response.status == 404, await response.text()
        async with http.post(url + "/api/engines/not-an-engine/upgrade",
                             ssl=pinned) as response:
            assert response.status == 401, await response.text()
        async with http.get(url + "/api/engines/usage-refresh",
                            headers=good, ssl=pinned) as response:
            refresh = await response.json()
            assert response.status == 200, refresh
        assert refresh["usage_refresh"]["minutes"] == 0
        assert refresh["usage_refresh"]["enabled"] is False
        async with http.post(url + "/api/engines/usage-refresh",
                             headers=good, ssl=pinned) as response:
            manual_refresh = await response.json()
            assert response.status == 200, manual_refresh
        assert isinstance(manual_refresh["engines"], list)
        assert manual_refresh["usage_refresh"]["enabled"] is False
        async with http.patch(url + "/api/engines/usage-refresh",
                              headers=good, ssl=pinned,
                              json={"minutes": -1}) as response:
            assert response.status == 400
        async with http.get(url + "/api/uploads/settings",
                            headers=good, ssl=pinned) as response:
            upload_settings = await response.json()
            assert response.status == 200, upload_settings
        assert upload_settings["uploads"]["max_file_size_mb"] >= 0
        async with http.patch(url + "/api/uploads/settings", headers=good,
                              ssl=pinned, json={"max_file_size_mb": -1}) as response:
            assert response.status == 400, await response.text()
        async with http.patch(url + "/api/uploads/settings", headers=good,
                              ssl=pinned, json={"max_file_size_mb": 1}) as response:
            upload_settings = await response.json()
            assert response.status == 200, upload_settings
        assert upload_settings["uploads"]["max_file_size_bytes"] == 1024 * 1024
        updates = await http.ws_connect(url + "/api/ws/updates", headers=good, ssl=pinned)
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "sessions" and first["sessions"] == []
        assert isinstance(first["server_time"], (int, float))
        await updates.close()

        async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                "engine": "codex", "workspace_kind": "not-a-workspace",
        }) as response:
            assert response.status == 400
        async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                "engine": "codex", "workspace_kind": "temporary", "name": "scratch-test",
        }) as response:
            created = await response.json()
            assert response.status == 200, created
        scratch = created["session"]
        assert scratch["workspace_kind"] == "temporary"
        assert scratch["workspace_missing"] is False
        scratch_path = Path(scratch["cwd"])
        assert scratch_path.is_dir()
        assert scratch_path.name.startswith("session-")
        assert scratch_path.parent.parent.name.startswith("puppy-workspaces-")
        assert scratch_path.stat().st_mode & 0o777 == 0o700
        (scratch_path / "throw-away.txt").write_text("disposable", encoding="utf-8")

        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            listed_payload = await response.json()
            assert response.status == 200, listed_payload
        listed_scratch = next(row for row in listed_payload["sessions"]
                              if row["id"] == scratch["id"])
        assert listed_scratch["status"] == "idle"
        assert listed_scratch["active_since"] is None
        assert isinstance(listed_payload["server_time"], (int, float))

        # Model a boot-time /tmp cleanup. The durable transcript/session stays,
        # advertises the expiration, and can be given a fresh private workspace.
        shutil.rmtree(scratch_path)
        async with http.get(url + f"/api/sessions/{scratch['id']}",
                            headers=good, ssl=pinned) as response:
            expired = (await response.json())["session"]
            assert response.status == 200
        assert expired["workspace_missing"] is True
        async with http.post(url + f"/api/sessions/{scratch['id']}/workspace/reset",
                             headers=good, ssl=pinned) as response:
            reset = await response.json()
            assert response.status == 200, reset
        fresh_path = Path(reset["session"]["cwd"])
        assert fresh_path != scratch_path and fresh_path.is_dir()
        assert reset["session"]["workspace_missing"] is False
        async with http.get(url + f"/api/sessions/{scratch['id']}",
                            headers=good, ssl=pinned) as response:
            reset_events = (await response.json())["events"]
        assert any(event["kind"] == "info" and
                   event["data"].get("subtype") == "workspace_reset"
                   for event in reset_events)
        async with http.delete(url + f"/api/sessions/{scratch['id']}",
                               headers=good, ssl=pinned) as response:
            deleted = await response.json()
            assert response.status == 200 and deleted["workspace_removed"] is True, deleted
        assert not fresh_path.exists()
        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            assert (await response.json())["sessions"] == []

        normal_path = Path(tempfile.mkdtemp(prefix="puppy-normal-workspace-"))
        try:
            sentinel = normal_path / "must-survive-session-delete.txt"
            sentinel.write_text("persistent", encoding="utf-8")
            async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                    "engine": "codex", "cwd": str(normal_path), "name": "normal-test",
            }) as response:
                normal_created = await response.json()
                assert response.status == 200, normal_created
            normal = normal_created["session"]
            assert normal["workspace_kind"] == "directory"

            # Malformed direct clients get bounded 4xx responses and cannot
            # turn a negative SQLite LIMIT into an unbounded transcript read.
            for payload in ([], {"text": ["not text"]}):
                async with http.post(
                        url + f"/api/sessions/{normal['id']}/message",
                        headers=good, json=payload, ssl=pinned) as response:
                    rejected_message = await response.json()
                    assert response.status == 400, rejected_message
            for query in ("limit=nope", "limit=-1", "limit=0", "limit=501",
                          "before_seq=nope", "before_seq=0"):
                async with http.get(
                        url + f"/api/sessions/{normal['id']}/events?{query}",
                        headers=good, ssl=pinned) as response:
                    rejected_events = await response.json()
                    assert response.status == 400, (query, rejected_events)
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/events?limit=1",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()

            session_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            first_snapshot = await session_ws.receive_json(timeout=3)
            assert first_snapshot["type"] == "snapshot"
            assert first_snapshot["draft"] == {
                "text": "", "revision": 0, "updated_at": None}
            peer_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            assert (await peer_ws.receive_json(timeout=3))["draft"]["revision"] == 0
            await session_ws.send_json({
                "type": "draft", "text": "Test", "client_id": "device-a",
                "client_seq": 1,
            })
            first_draft = await session_ws.receive_json(timeout=3)
            peer_draft = await peer_ws.receive_json(timeout=3)
            assert first_draft == peer_draft
            assert first_draft["type"] == "draft"
            assert first_draft["text"] == "Test" and first_draft["revision"] == 1
            assert first_draft["client_id"] == "device-a"
            assert first_draft["client_seq"] == 1
            await peer_ws.close()
            later_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            later_snapshot = await later_ws.receive_json(timeout=3)
            assert later_snapshot["draft"]["text"] == "Test"
            assert later_snapshot["draft"]["revision"] == 1
            await later_ws.send_json({
                "type": "draft", "text": "", "client_id": "device-b",
                "client_seq": 9,
            })
            cleared_a = await session_ws.receive_json(timeout=3)
            cleared_b = await later_ws.receive_json(timeout=3)
            assert cleared_a == cleared_b
            assert cleared_a["text"] == "" and cleared_a["revision"] == 2
            await later_ws.close()
            await session_ws.send_json([])
            rejected_socket_object = await session_ws.receive_json(timeout=3)
            assert rejected_socket_object["type"] == "toast", rejected_socket_object
            await session_ws.send_json({"type": "message", "text": ["not text"]})
            rejected_socket_text = await session_ws.receive_json(timeout=3)
            assert rejected_socket_text["type"] == "toast", rejected_socket_text
            assert "must be text" in rejected_socket_text["text"]
            await session_ws.close()

            executable = b"MZ\x00arbitrary executable payload\n"
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/x-msdownload",
                        "X-Puppy-Filename": "%2E%2E%2Fprogram.exe",
                        "X-Puppy-Size": str(len(executable)),
                    }, data=executable, ssl=pinned) as response:
                uploaded = await response.json()
                assert response.status == 200, uploaded
            uploaded_path = Path(uploaded["path"])
            assert uploaded["name"] == "program.exe"
            assert uploaded["size"] == len(executable)
            assert uploaded_path.read_bytes() == executable
            assert uploaded_path.stat().st_mode & 0o777 == 0o600
            assert uploaded_path.parent.stat().st_mode & 0o777 == 0o700
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "too-large.bin",
                        "X-Puppy-Size": str(1024 * 1024 + 1),
                    }, data=b"x", ssl=pinned) as response:
                assert response.status == 413, await response.text()

            async def oversized_body():
                for _chunk in range(5):
                    yield b"z" * (256 * 1024)

            existing_uploads = set(uploaded_path.parent.parent.iterdir())
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "lied-about-size.bin",
                        "X-Puppy-Size": "1",
                    }, data=oversized_body(), ssl=pinned) as response:
                actual_limit = await response.json()
                assert response.status == 413, actual_limit
            assert actual_limit["uploads"]["max_file_size_mb"] == 1
            assert set(uploaded_path.parent.parent.iterdir()) == existing_uploads
            # An uploaded image reads back so a preview survives a page reload,
            # but only as a declared raster type with sniffing off - this route
            # must never become a general on-origin file server.
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{uploaded['upload_id']}",
                    headers=good, ssl=pinned) as response:
                served = await response.read()
                assert response.status == 415, (response.status, served)
            png = (b"\x89PNG\r\n\x1a\n" + b"preview-bytes")
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "pasted.png",
                        "X-Puppy-Size": str(len(png)),
                    }, data=png, ssl=pinned) as response:
                image_upload = await response.json()
                assert response.status == 200, image_upload
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
                assert await response.read() == png
                assert response.headers["Content-Type"].startswith("image/png")
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert "no-store" not in response.headers.get("Cache-Control", "")
            # unauthenticated readers get nothing, and unknown ids are not found
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    ssl=pinned) as response:
                assert response.status == 401, await response.text()
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/1700000000000-abcdef0123",
                    headers=good, ssl=pinned) as response:
                assert response.status == 404, await response.text()
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{uploaded['upload_id']}",
                    headers=good, ssl=pinned) as response:
                discarded = await response.json()
                assert response.status == 200 and discarded["removed"] is True, discarded
            assert not uploaded_path.exists()
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": ".empty", "X-Puppy-Size": "0",
                    }, data=b"", ssl=pinned) as response:
                empty_upload = await response.json()
                assert response.status == 200, empty_upload
            assert empty_upload["name"] == ".empty"
            assert Path(empty_upload["path"]).read_bytes() == b""
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{empty_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
            async with http.patch(url + "/api/uploads/settings", headers=good,
                                  ssl=pinned, json={"max_file_size_mb": 0}) as response:
                disabled_uploads = await response.json()
                assert response.status == 200, disabled_uploads
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "text/plain",
                        "X-Puppy-Filename": "off.txt",
                    }, data=b"disabled", ssl=pinned) as response:
                rejected_upload = await response.json()
                assert response.status == 403, rejected_upload
            assert rejected_upload["uploads"]["enabled"] is False
            async with http.patch(url + "/api/uploads/settings", headers=good,
                                  ssl=pinned, json={"max_file_size_mb": 1}) as response:
                assert response.status == 200, await response.text()
            async with http.delete(url + f"/api/sessions/{normal['id']}",
                                   headers=good, ssl=pinned) as response:
                normal_deleted = await response.json()
                assert response.status == 200, normal_deleted
            assert normal_deleted["workspace_removed"] is False
            assert sentinel.read_text(encoding="utf-8") == "persistent"
        finally:
            shutil.rmtree(normal_path, ignore_errors=True)

        async with http.get(url + "/api/node/upgrade", headers=good, ssl=pinned) as response:
            status = await response.json()
            assert response.status == 200 and status["supported"] is upgrade_enabled
        assert status["readiness"]["ready"] is upgrade_enabled
        assert status["readiness"]["state"] == \
            ("ready" if upgrade_enabled else "unsupported")
        for path in ("/", "/static/app.js", "/api/settings", "/api/auth/status",
                     "/api/ws/term", "/api/terminal/instances",
                     "/api/ws/terminal/A1B2"):
            async with http.get(url + path, headers=good, ssl=pinned) as response:
                assert response.status == 404, (path, response.status)
        async with http.post(url + "/api/snapshot/export", headers=good,
                             json={"ui": {}}, ssl=pinned) as response:
            assert response.status == 404  # backup/restore is a full-WebUI surface
        async with http.post(url + "/api/settings/bind/prepare", headers=good,
                             json={"host": "127.0.0.1", "origin": url},
                             ssl=pinned) as response:
            assert response.status == 404  # browser-verified binding is controller-only
        async with http.get(url + "/api/settings/bind/verify/not-a-token",
                            headers=good, ssl=pinned) as response:
            assert response.status == 404
        async with http.post(url + "/api/settings/bind/activate", headers=good,
                             json={"token": "not-a-token", "browser_state": {}},
                             ssl=pinned) as response:
            assert response.status == 404
        async with http.get(url + "/api/settings/bind/handoff/not-a-token/ready",
                            headers=good, ssl=pinned) as response:
            assert response.status == 404
        if not upgrade_enabled:
            async with http.post(url + "/api/node/upgrade", headers=good,
                                 data=b"not-an-artifact", ssl=pinned) as response:
                assert response.status == 409
                rejected = await response.json()
            assert rejected["readiness"]["state"] == "unsupported"


async def reject_bad_signature(url: str, token: str, fingerprint: str = "") -> None:
    payload = b"signed body is intentionally not a zipapp"
    manifest = {
        "format": upgrade_contract.FORMAT_VERSION,
        "artifact": "zipapp",
        "version": __version__,
        "protocol": protocol.API_PROTOCOL,
        "size": len(payload),
        "sha256": upgrade_contract.artifact_sha256(payload),
        "nonce": "bad-signature-test-nonce",
        "created_at": int(time.time()),
    }
    headers = {
        "X-Puppy-Token": token,
        upgrade_contract.MANIFEST_HEADER: upgrade_contract.encode_manifest(manifest),
        upgrade_contract.SIGNATURE_HEADER: "0" * 64,
    }
    async with aiohttp.ClientSession() as http:
        async with http.post(url + "/api/node/upgrade", headers=headers,
                             data=payload, ssl=ssl_pin(fingerprint)) as response:
            assert response.status == 403, await response.text()


async def exercise_controller(url: str, token: str, backend_url: str,
                              backend_token: str, backend_fingerprint: str,
                              old_version: str, backend_state_dir: Path) -> None:
    headers = {"X-Puppy-Token": token}
    unavailable_url = backend_url.replace("127.0.0.1", "127.0.0.2")
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", headers=headers) as response:
            full_ping = await response.json()
            assert response.status == 200
        assert full_ping["role"] == "full" and full_ping["protocol"] == 1
        assert "terminal" in full_ping["capabilities"]
        assert "terminal-instances" in full_ping["capabilities"]
        assert "terminal-handoff" in full_ping["capabilities"]
        assert "queue-pause" in full_ping["capabilities"]
        assert "queue-edit" in full_ping["capabilities"]
        assert "queue-reorder" in full_ping["capabilities"]
        assert "session-drafts" in full_ping["capabilities"]
        assert "browser-handoff" in full_ping["capabilities"]
        assert "browser-file-workflows" in full_ping["capabilities"]
        assert "shutdown-notice" not in full_ping["capabilities"]

        updates = await http.ws_connect(url + "/api/ws/updates", headers=headers)
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "sessions"
        while True:
            metric = await updates.receive_json(timeout=5)
            if metric.get("type") == "host_metrics":
                break
        assert isinstance(metric["cpu_percent"], (int, float))
        assert 0 <= metric["cpu_percent"] <= 100
        assert isinstance(metric["sampled_at"], (int, float))
        await updates.close()

        async with http.post(url + "/api/backends", headers=headers, json={
                "url": backend_url, "token": backend_token}) as response:
            unpinned = await response.json()
            assert response.status == 400, unpinned
        assert "TLS certificate verification failed" in unpinned["error"]

        wrong_pin = ("0" if backend_fingerprint[0] != "0" else "1") + backend_fingerprint[1:]
        async with http.post(url + "/api/backends", headers=headers, json={
                "url": backend_url, "token": backend_token,
                "tls_fingerprint": wrong_pin}) as response:
            mismatched = await response.json()
            assert response.status == 400, mismatched
        assert "fingerprint mismatch" in mismatched["error"]

        async with http.post(url + "/api/backends", headers=headers, json={
                "name": "", "urls": [unavailable_url, backend_url],
                "token": backend_token,
                "tls_fingerprint": backend_fingerprint,
                "auto_upgrade": False}) as response:
            added = await response.json()
            assert response.status == 200, added
        assert added["remote"]["role"] == "backend"
        assert added["remote"]["protocol"] == 1

        async with http.get(url + "/api/backends", headers=headers) as response:
            listed = (await response.json())["backends"]
            assert response.status == 200
        assert len(listed) == 1, listed
        stored = listed[0]
        assert stored["name"] == "backend-test-node"
        assert stored["protocol"] == 1
        assert stored["role"] == "backend"
        assert "sessions" in stored["capabilities"]
        assert "temporary-workspaces" in stored["capabilities"]
        assert "engine-usage-refresh" in stored["capabilities"]
        assert "engine-upgrade" in stored["capabilities"]
        assert "file-uploads" in stored["capabilities"]
        assert "queue-pause" in stored["capabilities"]
        assert "queue-edit" in stored["capabilities"]
        assert "queue-reorder" in stored["capabilities"]
        assert "session-drafts" in stored["capabilities"]
        assert "browser-handoff" in stored["capabilities"]
        assert "browser-file-workflows" in stored["capabilities"]
        assert "shutdown-notice" in stored["capabilities"]
        assert "terminal" not in stored["capabilities"]
        assert "remote-upgrade" in stored["capabilities"]
        assert "pinned-tls" in stored["capabilities"]
        assert stored["remote_version"] == old_version
        assert stored["url"] == unavailable_url
        assert stored["urls"] == [unavailable_url, backend_url]
        assert stored["active_url"] == backend_url
        assert stored["tls_fingerprint"] == backend_fingerprint
        assert stored["auto_upgrade"] is False
        assert stored["upgrade_in_progress"] is False
        assert stored["availability"]["state"] == "online"
        assert stored["availability"]["reason"] == ""
        assert isinstance(stored["availability"]["checked_at"], (int, float))
        assert stored["last_known"]["version"] == 1
        assert len(stored["last_known"]["node_uuid"]) == 32
        assert stored["last_known"]["browser"] == {"enabled": False}
        assert stored["last_known"]["uploads"]["max_file_size_mb"] >= 0
        assert "token" not in stored

        async with http.get(
                url + f"/api/b/{stored['id']}/browser/instances/A1B2/binding",
                headers=headers) as response:
            proxied_binding = await response.json()
            assert response.status == 404, proxied_binding
            assert "closed or unknown" in proxied_binding["error"]

        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": "yes"}) as response:
            assert response.status == 400, await response.text()

        # Display-only edits work without replacing the private token or
        # requiring a connection probe. A rejected credential edit is atomic:
        # the old pairing remains usable and no secret is returned to the UI.
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"name": "  Edited backend  "}) as response:
            renamed = await response.json()
            assert response.status == 200, renamed
        assert renamed["backend"]["name"] == "Edited backend", renamed
        assert renamed["connection_changed"] is False and renamed["remote"] is None
        assert "token" not in renamed["backend"]
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"token": "definitely-the-wrong-token"}) as response:
            rejected_edit = await response.json()
            assert response.status == 400, rejected_edit
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"tls_fingerprint": ""}) as response:
            unpinned_edit = await response.json()
            assert response.status == 400, unpinned_edit
        assert "TLS certificate verification failed" in unpinned_edit["error"]
        async with http.post(url + f"/api/backends/{stored['id']}/test",
                             headers=headers) as response:
            retained = await response.json()
            assert response.status == 200 and retained["ok"] is True, retained
        async with http.get(url + "/api/backends", headers=headers) as response:
            after_rejection = (await response.json())["backends"][0]
        assert after_rejection["name"] == "Edited backend", after_rejection
        assert after_rejection["urls"] == [unavailable_url, backend_url], after_rejection
        assert after_rejection["active_url"] == backend_url, after_rejection

        async with http.post(url + f"/api/backends/{stored['id']}/test",
                             headers=headers) as response:
            tested = await response.json()
            assert response.status == 200 and tested["ok"] is True, tested

        async with http.get(url + f"/api/b/{stored['id']}/sessions",
                            headers=headers) as response:
            proxied = await response.json()
            assert response.status == 200 and proxied["sessions"] == [], proxied
        assert isinstance(proxied["server_time"], (int, float))
        async with http.get(url + f"/api/b/{stored['id']}/node/upgrade",
                            headers=headers) as response:
            proxied_upgrade = await response.json()
            assert response.status == 200, proxied_upgrade
        assert proxied_upgrade["readiness"]["ready"] is True
        assert proxied_upgrade["readiness"]["state"] == "ready"

        # A backend-owned runtime blocker must disable readiness and be
        # propagated by the controller before it spends time building an artifact.
        pending_marker = backend_state_dir / "pending.json"
        pending_marker.write_text("{}\n", encoding="utf-8")
        try:
            async with http.get(url + f"/api/b/{stored['id']}/node/upgrade",
                                headers=headers) as response:
                blocked = await response.json()
                assert response.status == 200, blocked
            assert blocked["readiness"]["state"] == "blocked"
            async with http.post(url + f"/api/backends/{stored['id']}/upgrade",
                                 headers=headers) as response:
                rejected = await response.json()
                assert response.status == 409, rejected
            assert rejected["readiness"]["state"] == "blocked"
            async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                                  json={"auto_upgrade": True}) as response:
                enabled_while_blocked = await response.json()
                assert response.status == 200, enabled_while_blocked
            await asyncio.sleep(0.5)
            async with http.get(url + "/api/backends", headers=headers) as response:
                still_blocked = (await response.json())["backends"][0]
            assert still_blocked["remote_version"] == old_version
            assert still_blocked["upgrade_in_progress"] is False
            async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                                  json={"auto_upgrade": False}) as response:
                assert response.status == 200, await response.text()
        finally:
            pending_marker.unlink(missing_ok=True)
        async with http.get(url + f"/api/b/{stored['id']}/engines/usage-refresh",
                            headers=headers) as response:
            proxied_refresh = await response.json()
            assert response.status == 200, proxied_refresh
        assert proxied_refresh["usage_refresh"]["minutes"] == 0
        async with http.get(url + f"/api/b/{stored['id']}/engines",
                            headers=headers) as response:
            proxied_engines = await response.json()
            assert response.status == 200, proxied_engines
        assert isinstance(proxied_engines["engines"], list)
        remote_updates = await http.ws_connect(
            url + f"/api/b/{stored['id']}/ws/updates", headers=headers)
        first = await remote_updates.receive_json(timeout=3)
        assert first["type"] == "sessions" and first["sessions"] == []
        assert isinstance(first["server_time"], (int, float))
        # A real connection edit is probed before it is stored, and closes the
        # affected backend's existing proxy channels so they reconnect through
        # the new URL/token/pin rather than remaining attached to the old peer.
        alternate_url = backend_url.replace("127.0.0.1", "localhost")
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"urls": [alternate_url]}) as response:
            moved = await response.json()
            assert response.status == 200, moved
        assert moved["connection_changed"] is True, moved
        assert moved["backend"]["url"] == alternate_url, moved
        closed = await remote_updates.receive(timeout=5)
        assert closed.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                               aiohttp.WSMsgType.CLOSING), closed
        await remote_updates.close()
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"name": "backend-test-node",
                                    "urls": [unavailable_url, backend_url]}) as response:
            restored_connection = await response.json()
            assert response.status == 200, restored_connection
        assert restored_connection["connection_changed"] is True, restored_connection
        assert restored_connection["backend"]["name"] == "backend-test-node"

        # Prompt settings are node-owned and cross the same authenticated proxy
        # as sessions, so the controller can edit a headless node without
        # pretending the controller's own prompt applies remotely.
        async with http.get(url + f"/api/b/{stored['id']}/system-prompt",
                            headers=headers) as response:
            remote_prompt = await response.json()
            assert response.status == 200, remote_prompt
        async with http.patch(url + f"/api/b/{stored['id']}/system-prompt",
                              headers=headers,
                              json={"custom": "Controller-configured guidance.",
                                    "remote_workspace": remote_prompt["system_prompt"][
                                        "remote_workspace_default"],
                                    "browser": remote_prompt["system_prompt"]["browser_default"],
                                    "terminal": remote_prompt["system_prompt"]["terminal_default"]
                                    }) as response:
            remote_prompt = await response.json()
            assert response.status == 200, remote_prompt
        assert remote_prompt["system_prompt"]["custom"] == \
            "Controller-configured guidance."

        # Simulate the controller restarting without a remembered active URL.
        # A state-changing request may advance only after a pre-connect failure,
        # where replay cannot duplicate work on the backend.
        from puppy import backends as controller_backends
        controller_backends._active_urls.pop(stored["id"], None)
        async with http.post(url + f"/api/b/{stored['id']}/sessions", headers=headers, json={
                "engine": "codex", "workspace_kind": "temporary", "name": "proxied-scratch",
        }) as response:
            proxied_created = await response.json()
            assert response.status == 200, proxied_created
        proxied_scratch = proxied_created["session"]
        proxied_path = Path(proxied_scratch["cwd"])
        assert proxied_scratch["workspace_kind"] == "temporary" and proxied_path.is_dir()
        # Exceed both aiohttp applications' historical 8 MiB read ceiling. The
        # controller must stream the request to the node instead of buffering
        # it, while the receiving node remains the final limit authority.
        async with http.patch(
                url + f"/api/b/{stored['id']}/uploads/settings", headers=headers,
                json={"max_file_size_mb": 9}) as response:
            proxied_policy = await response.json()
            assert response.status == 200, proxied_policy
        assert proxied_policy["uploads"]["max_file_size_mb"] == 9
        async with http.get(url + "/api/backends", headers=headers) as response:
            cached_backend = (await response.json())["backends"][0]
            assert response.status == 200
        last_known = cached_backend["last_known"]
        assert last_known["version"] == 1
        assert last_known["usage_refresh"]["minutes"] == 0
        assert isinstance(last_known["engines"], list)
        assert isinstance(last_known["auto_upgrade"]["enabled"], bool)
        assert last_known["uploads"]["max_file_size_mb"] == 9
        assert last_known["browser"] == {"enabled": False}
        assert last_known["system_prompt"]["custom"] == \
            "Controller-configured guidance."
        proxied_file = b"MZ" + (b"x" * (8 * 1024 * 1024)) + b"streamed-through-controller"
        controller_backends._active_urls.pop(stored["id"], None)
        async with http.post(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}/upload",
                headers={
                    **headers, "Content-Type": "application/x-msdownload",
                    "X-Puppy-Filename": "deploy.exe",
                    "X-Puppy-Size": str(len(proxied_file)),
                }, data=proxied_file) as response:
            proxied_upload = await response.json()
            assert response.status == 200, proxied_upload
        proxied_upload_path = Path(proxied_upload["path"])
        assert proxied_upload["size"] == len(proxied_file)
        assert controller_backends._active_urls[stored["id"]] == backend_url
        assert proxied_upload_path.stat().st_size == len(proxied_file)
        assert proxied_upload_path.read_bytes() == proxied_file
        async with http.delete(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}/upload/" +
                proxied_upload["upload_id"], headers=headers) as response:
            assert response.status == 200, await response.text()
        async with http.delete(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}",
                headers=headers) as response:
            assert response.status == 200, await response.text()
        assert not proxied_path.exists()

        # New controllers allocate a node-owned, identified PTY before opening
        # its viewer socket. The process survives a viewer reconnect and keeps
        # one four-character identity for both the user and terminal MCP tools.
        async with http.post(url + "/api/terminal/instances", headers=headers,
                             json={"command": "/bin/bash", "cols": 80,
                                   "rows": 24}) as response:
            created_terminal = await response.json()
            assert response.status == 201, created_terminal
        terminal_id = created_terminal["terminal"]["id"]
        assert len(terminal_id) == 4 and terminal_id.isalnum() and \
            terminal_id == terminal_id.upper(), terminal_id
        async with http.get(url + "/api/terminal/instances",
                            headers=headers) as response:
            terminal_list = await response.json()
            assert response.status == 200, terminal_list
        assert any(item["id"] == terminal_id and item["running"]
                   for item in terminal_list["instances"]), terminal_list
        shared_terminal = await http.ws_connect(
            url + "/api/ws/terminal/" + terminal_id, headers=headers)
        terminal_status = await shared_terminal.receive_json(timeout=3)
        terminal_binding = await shared_terminal.receive_json(timeout=3)
        assert terminal_status["type"] == "status" and \
            terminal_status["terminal_id"] == terminal_id
        assert terminal_binding["type"] == "binding" and \
            terminal_binding["session_id"] is None
        await shared_terminal.send_bytes(b"echo PUPPY_SHARED_TERMINAL_OK\nexit\n")
        shared_output = b""
        deadline = asyncio.get_event_loop().time() + 5
        while b"PUPPY_SHARED_TERMINAL_OK" not in shared_output and \
                asyncio.get_event_loop().time() < deadline:
            message = await shared_terminal.receive(timeout=2)
            if message.type == aiohttp.WSMsgType.BINARY:
                shared_output += message.data
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        assert b"PUPPY_SHARED_TERMINAL_OK" in shared_output, shared_output[-200:]
        await shared_terminal.close()
        async with http.delete(url + "/api/terminal/instances/" + terminal_id,
                               headers=headers) as response:
            assert response.status == 200, await response.text()

        # Keep the anonymous create-on-connect socket for older controllers.
        terminal = await http.ws_connect(
            url + "/api/ws/term?cmd=/bin/bash&cols=80&rows=24", headers=headers)
        await terminal.send_bytes(b"echo PUPPY_BACKEND_ROUTE_OK\nexit\n")
        output = b""
        deadline = asyncio.get_event_loop().time() + 5
        while b"PUPPY_BACKEND_ROUTE_OK" not in output and \
                asyncio.get_event_loop().time() < deadline:
            message = await terminal.receive(timeout=2)
            if message.type == aiohttp.WSMsgType.BINARY:
                output += message.data
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        assert b"PUPPY_BACKEND_ROUTE_OK" in output, output[-200:]
        await terminal.close()

        # Enabling the controller-owned policy wakes its background worker.
        # The node's live readiness remains authoritative, then the exact same
        # signed/restart/rollback pipeline used by the manual action runs.
        lifecycle_updates = await http.ws_connect(
            url + f"/api/b/{stored['id']}/ws/updates", headers=headers)
        lifecycle_snapshot = await lifecycle_updates.receive_json(timeout=3)
        assert lifecycle_snapshot["type"] == "sessions"
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": True}) as response:
            toggled = await response.json()
            assert response.status == 200, toggled
        assert toggled["backend"]["auto_upgrade"] is True

        lifecycle_notice = None
        notice_deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < notice_deadline:
            message = await lifecycle_updates.receive_json(timeout=90)
            if message.get("type") == "node_stopping":
                lifecycle_notice = message
                break
        assert lifecycle_notice is not None, "remote restart sent no lifecycle notice"
        assert lifecycle_notice["reason"] == "restart", lifecycle_notice
        async with http.get(url + "/api/backends", headers=headers) as response:
            stopping_backend = (await response.json())["backends"][0]
        assert stopping_backend["availability"]["state"] == "offline", stopping_backend
        assert "restarting" in stopping_backend["availability"]["reason"].lower()
        assert stopping_backend["last_known"]["uploads"]["max_file_size_mb"] == 9
        assert stopping_backend["last_known"]["usage_refresh"]["minutes"] == 0
        assert stopping_backend["last_known"]["system_prompt"]["custom"] == \
            "Controller-configured guidance."
        await lifecycle_updates.close()

        deadline = asyncio.get_event_loop().time() + 120
        refreshed = None
        while asyncio.get_event_loop().time() < deadline:
            async with http.get(url + "/api/backends", headers=headers) as response:
                refreshed = (await response.json())["backends"][0]
                assert response.status == 200
            if refreshed["remote_version"] == __version__ and \
                    not refreshed["upgrade_in_progress"]:
                break
            await asyncio.sleep(0.25)
        assert refreshed is not None and refreshed["remote_version"] == __version__, refreshed
        assert refreshed["availability"]["state"] == "online", refreshed
        assert refreshed["auto_upgrade"] is True
        assert "remote-upgrade" in refreshed["capabilities"]


async def exercise_redirect_rejection() -> None:
    redirected_tokens = []

    async def redirect(_request):
        raise web.HTTPFound(location="/capture")

    async def capture(request):
        redirected_tokens.append(request.headers.get("X-Puppy-Token"))
        return web.Response(text="captured")

    app = web.Application()
    app.router.add_get("/api/ping", redirect)
    app.router.add_get("/api/ws/updates", redirect)
    app.router.add_get("/capture", capture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    token = "redirect-test-token-0123456789abcdef"
    try:
        from puppy import backends

        probed = await backends.probe_backend(origin, token)
        assert probed["ok"] is False
        assert redirected_tokens == []
        try:
            await backends.client().ws_connect(
                f"ws://127.0.0.1:{port}/api/ws/updates",
                headers={"X-Puppy-Token": token})
            raise AssertionError("backend WebSocket redirect was accepted")
        except (aiohttp.ClientError, RuntimeError):
            pass
        assert redirected_tokens == []
    finally:
        await runner.cleanup()


async def exercise_proxy_recovery(controller_url: str, controller_token: str) -> None:
    """HTTP and WebSocket proxies follow a node between configured origins."""
    from puppy import backends as controller_backends

    backend_token = "recovery-backend-token-0123456789abcdef"
    ports = [free_port(), free_port()]
    backend_urls = [f"http://127.0.0.1:{port}" for port in ports]
    request_counts = {port: {"ping": 0, "sessions": 0, "updates": 0} for port in ports}

    async def start_backend(port: int) -> web.AppRunner:
        async def authorized(request):
            if request.headers.get("X-Puppy-Token") != backend_token:
                return web.json_response({"error": "unauthorized"}, status=401)
            return None

        async def ping(request):
            request_counts[port]["ping"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({
                "ok": True, "name": "recovery-node", "version": __version__,
                "protocol": protocol.API_PROTOCOL, "role": "backend",
                "capabilities": ["sessions"],
                "transport": {"encrypted": False},
            })

        async def sessions(request):
            request_counts[port]["sessions"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({"type": "sessions", "sessions": []})

        async def engines(request):
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({"engines": []})

        async def updates(request):
            request_counts[port]["updates"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "sessions", "sessions": []})
            async for _message in ws:
                pass
            return ws

        app = web.Application()
        app.router.add_get("/api/ping", ping)
        app.router.add_get("/api/sessions", sessions)
        app.router.add_get("/api/engines", engines)
        app.router.add_get("/api/ws/updates", updates)
        runner = web.AppRunner(app, shutdown_timeout=0.2)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        return runner

    headers = {"X-Puppy-Token": controller_token}
    backend_runner = await start_backend(ports[0])
    backend_id = None
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(controller_url + "/api/backends", headers=headers, json={
                    "name": "recovery-node", "urls": backend_urls, "token": backend_token,
            }) as response:
                added = await response.json()
                assert response.status == 200, added
            backend_id = added["id"]
            proxy_url = controller_url + f"/api/b/{backend_id}"

            async with http.patch(controller_url + f"/api/backends/{backend_id}",
                                  headers=headers, json={"auto_upgrade": True}) as response:
                unsupported = await response.json()
                assert response.status == 409, unsupported
            assert "upgrade-capable headless backend" in unsupported["error"]

            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"

            await backend_runner.cleanup()
            backend_runner = None
            closed = await updates.receive(timeout=2)
            assert closed.type in (
                aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                               aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR), closed
            await updates.close()

            # The same authenticated node appears at its alternate address.
            # The stale primary remains first in configuration, but both HTTP
            # and WebSocket handshakes must advance to the live candidate and
            # publish that exact address as active.
            backend_runner = await start_backend(ports[1])
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            async with http.get(controller_url + "/api/backends", headers=headers) as response:
                moved = (await response.json())["backends"]
            active = next(item for item in moved if item["id"] == backend_id)
            assert active["urls"] == backend_urls and active["active_url"] == backend_urls[1]
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"

            await backend_runner.cleanup()
            backend_runner = None
            closed = await updates.receive(timeout=2)
            assert closed.type in (
                aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR), closed
            await updates.close()
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                unavailable = await response.json()
                assert response.status == 503, unavailable
                assert unavailable["availability"]["state"] == "offline", unavailable
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("dead backend completed the proxied WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status == 503, exc.status

            backend_runner = await start_backend(ports[0])
            # Starting a listener does not let browser traffic rediscover it.
            # Until the controller's authenticated health probe succeeds, the
            # proxy gate responds locally and touches no backend API route.
            sessions_before = request_counts[ports[0]]["sessions"]
            updates_before = request_counts[ports[0]]["updates"]
            started = time.monotonic()
            for _attempt in range(3):
                async with http.get(proxy_url + "/sessions", headers=headers) as response:
                    assert response.status == 503, await response.text()
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("offline gate completed a WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status == 503, exc.status
            assert time.monotonic() - started < 0.5
            assert request_counts[ports[0]]["sessions"] == sessions_before
            assert request_counts[ports[0]]["updates"] == updates_before

            # Force the normally backoff-scheduled recovery probe due now and
            # wait for its controller-owned availability broadcast/state.
            controller_backends._health_retry_after[backend_id] = 0
            controller_backends._wake_health()
            recovered = False
            for _attempt in range(50):
                async with http.get(controller_url + "/api/backends",
                                    headers=headers) as response:
                    listed = (await response.json())["backends"]
                current = next(item for item in listed if item["id"] == backend_id)
                recovered = current["availability"]["state"] == "online"
                if recovered:
                    break
                await asyncio.sleep(0.1)
            assert recovered, "controller health probe did not recover the backend"
            assert request_counts[ports[0]]["ping"] >= 1
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            async with http.get(controller_url + "/api/backends", headers=headers) as response:
                returned = (await response.json())["backends"]
            active = next(item for item in returned if item["id"] == backend_id)
            assert active["active_url"] == backend_urls[0]
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"
            await updates.close()
    finally:
        if backend_id is not None:
            async with aiohttp.ClientSession() as http:
                async with http.delete(
                        controller_url + f"/api/backends/{backend_id}", headers=headers) as response:
                    assert response.status == 200
        if backend_runner is not None:
            await backend_runner.cleanup()


async def exercise_launcher_rollback(artifact: Path, launcher: Path, state_dir: Path,
                                     data_dir: Path, backend_url: str,
                                     backend_token: str,
                                     backend_fingerprint: str) -> subprocess.Popen:
    backup = artifact.with_name(artifact.stem + ".previous" + artifact.suffix)
    shutil.copy2(artifact, backup)
    previous_sha = upgrade_contract.artifact_sha256(backup.read_bytes())
    bad_payload = b"import sys\nsys.exit(23)\n"
    artifact.write_bytes(bad_payload)
    artifact.chmod(0o755)
    status_path = state_dir / "status.json"
    marker = state_dir / "pending.json"
    marker.write_text(json.dumps({
        "format": upgrade_contract.LAUNCHER_PROTOCOL,
        "artifact": str(artifact.resolve()),
        "backup": str(backup.resolve()),
        "status_path": str(status_path.resolve()),
        "data_dir": str(data_dir.resolve()),
        "previous_version": __version__,
        "previous_sha256": previous_sha,
        "target_version": "9.9.9",
        "target_sha256": upgrade_contract.artifact_sha256(bad_payload),
        "health": {"host": "127.0.0.1", "port": int(backend_url.rsplit(":", 1)[1]),
                   "tls": True, "certificate_sha256": backend_fingerprint},
    }), encoding="utf-8")
    process = subprocess.Popen([
        sys.executable, str(launcher), "--artifact", str(artifact),
        "--state-dir", str(state_dir), "--", "serve", "--data-dir", str(data_dir),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    await wait_for_backend(backend_url, process, backend_fingerprint)
    async with aiohttp.ClientSession() as http:
        async with http.get(backend_url + "/api/ping",
                            headers={"X-Puppy-Token": backend_token},
                            ssl=ssl_pin(backend_fingerprint)) as response:
            ping = await response.json()
            assert response.status == 200 and ping["version"] == __version__, ping
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "rolled-back", status
    assert not marker.exists()
    assert upgrade_contract.artifact_sha256(artifact.read_bytes()) == previous_sha
    return process


async def check_notify_placeholders() -> None:
    """The completion-command contract both runtimes expand and export.

    Imported here, not at module scope: notify pulls in puppy.config, which
    binds its data path at import time and must not load before PUPPY_DATA.
    """
    from puppy import notify
    # the leading unit is never zero-padded, so the shape follows the span
    for seconds, expected in [
            (0, "0:00"), (7, "0:07"), (59, "0:59"), (60, "1:00"),
            (599, "9:59"),          # M:SS below ten minutes
            (600, "10:00"), (3599, "59:59"),        # MM:SS below the hour
            (3600, "1:00:00"), (35999, "9:59:59"),  # H:MM:SS below ten hours
            (36000, "10:00:00"), (86399, "23:59:59"),   # HH:MM:SS thereafter
            (360000, "100:00:00")]:
        assert notify.clock(seconds) == expected, (seconds, notify.clock(seconds))
    assert notify.clock(-5) == "0:00"
    assert "duration_hms" in notify.PLACEHOLDERS

    info = notify.clean_info({"session": "my app", "duration": "3661"})
    assert info["duration_hms"] == "1:01:01", info
    assert notify.expand("{session} {duration_hms} {duration}", info) == \
        "'my app' 1:01:01 3661"
    # derived on the node, so a reporting console cannot supply its own
    hostile = notify.clean_info({"duration": "90", "duration_hms": "$(touch /tmp/x)"})
    assert hostile["duration_hms"] == "1:30", hostile
    # and a completion with no duration simply has neither
    assert "duration_hms" not in notify.clean_info({"session": "s"})

    # A deliberate stop is not completed work. Test the hook itself so this
    # stays true even if a caller accidentally passes an interrupted outcome
    # while notification settings are armed.
    fired = []
    original_active, original_fire = notify.active, notify._fire

    async def capture_fire(payload):
        fired.append(payload)

    try:
        notify.active = lambda: True
        notify._fire = capture_fire
        session = {"id": 9, "name": "stopped", "engine": "codex",
                   "model": "test", "cwd": "/tmp"}
        notify.session_finished(session, "interrupted", 12)
        await asyncio.sleep(0)
        assert fired == [], fired
        notify.session_finished(session, "ok", 13)
        await asyncio.sleep(0)
        assert len(fired) == 1 and fired[0]["status"] == "ok", fired
    finally:
        notify.active, notify._fire = original_active, original_fire


async def exercise_interrupted_notify_report(web_module) -> None:
    """A client cannot route an interrupted remote outcome around the runner
    guard and into the controller's report endpoint."""
    class Request:
        async def json(self):
            return {"bid": 7, "sid": 11,
                    "info": {"status": "interrupted", "session": "stopped"}}

    touched = []
    original_active = web_module.notify.active
    original_backend = web_module.backends.get_backend
    try:
        web_module.notify.active = lambda: touched.append("active") or True
        web_module.backends.get_backend = \
            lambda _bid: touched.append("backend") or {"name": "remote"}
        response = await web_module.h_notify_fire(Request())
        assert response.status == 200
        assert json.loads(response.text) == {"ok": True, "fired": False}
        assert touched == [], touched
    finally:
        web_module.notify.active = original_active
        web_module.backends.get_backend = original_backend


def exercise_auth_evidence(root, db) -> None:
    """Local login verbs are optimistic - codex answers "Logged in" from its
    file while the refresh token behind it is revoked. Hard evidence from the
    vendor's own responses (a 401 account read, a failed turn) overrides an ok
    probe, survives restarts, and lifts on re-login or any authed success."""
    from puppy.drivers import base as driver_base
    from puppy.drivers.codex import CodexDriver
    root = Path(root)
    home = root / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    saved = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    try:
        for text, want in [
            ("Your access token could not be refreshed because your refresh "
             "token was revoked. Please log out and sign in again.", True),
            ("GET https://chatgpt.com/backend-api/wham/usage failed: "
             "401 Unauthorized", True),
            ("OAuth token has expired · Please run /login", True),
            ("engine exited without a result (exit 1)", False),
            ("stream error: connection reset by peer", False),
        ]:
            assert driver_base.looks_like_auth_failure(text) is want, text

        codex = CodexDriver()
        auth = home / "auth.json"
        auth.write_text("{}")
        os.utime(auth, (time.time() - 3600,) * 2)
        ok = {"installed": True, "auth": "ok", "detail": "Logged in using ChatGPT"}
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
        driver_base.note_auth_failure("codex", "401 Unauthorized")
        overlaid = driver_base.apply_auth_evidence(codex, dict(ok))
        assert overlaid["auth"] == "expired" and "401" in overlaid["detail"]
        # a probe already negative keeps its own words
        kept = driver_base.apply_auth_evidence(
            codex, {"installed": True, "auth": "missing", "detail": "Not logged in"})
        assert kept["auth"] == "missing"
        # re-login rewrites the credential file: evidence lifts by itself
        os.utime(auth, None)
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
        assert db.meta_get("auth_evidence.codex") is None
        # authenticated success is the other way out
        driver_base.note_auth_failure("codex", "401")
        os.utime(auth, (time.time() - 3600,) * 2)
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "expired"
        driver_base.clear_auth_failure("codex")
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
    finally:
        if saved is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = saved


async def exercise_auth_probes(root) -> None:
    """The ready/no-auth word must come from each CLI's own auth verb, judged
    by exit code and structure - never by substring. "Not logged in" CONTAINS
    "logged in", which once reported a logged-out codex as ready."""
    from puppy.drivers.claude import ClaudeDriver
    from puppy.drivers.codex import CodexDriver
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def stub(name, script):
        path = root / name
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(0o755)
        return str(path)

    codex = CodexDriver()
    for script, expected in [
        ('echo "Logged in using ChatGPT"; exit 0', "ok"),
        ('echo "Not logged in"; exit 1', "missing"),               # the NAS.lan case
        ('echo "WARNING: preamble"; echo "Not logged in"; exit 1', "missing"),
        ('echo "You are signed out"; exit 1', "missing"),          # future rewording
        ('echo "Session active"; exit 0', "ok"),                   # rc stays the contract
    ]:
        codex.binary = stub("codex-case", script)
        got = (await codex._auth_status())["auth"]
        assert got == expected, (script, got)
    codex.binary = str(root / "codex-absent")
    assert (await codex._auth_status())["auth"] == "unknown"

    claude = ClaudeDriver()
    home = os.environ.get("HOME")
    scratch = root / "home"
    (scratch / ".claude").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(scratch)
    try:
        for script, creds, expected in [
            ("echo '{\"loggedIn\": true, \"authMethod\": \"claude.ai\"}'; exit 0",
             False, "ok"),
            ("echo '{\"loggedIn\": false}'; exit 1", True, "missing"),  # stale creds file
            ("echo \"error: unknown command 'auth'\" >&2; exit 1", True, "ok"),
            ("echo \"error: unknown command 'auth'\" >&2; exit 1", False, "missing"),
        ]:
            cred = scratch / ".claude" / ".credentials.json"
            if creds:
                cred.write_text("{}")
            elif cred.exists():
                cred.unlink()
            claude.binary = stub("claude-case", script)
            got = (await claude._auth_status())["auth"]
            assert got == expected, (script, creds, got)
    finally:
        if home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = home


def exercise_session_show_meta(runner, db) -> None:
    """The head strip is a per-session flag on the shared session payload: on by
    default, a real boolean on the wire so a console can trust it."""
    sid = db.create_session("meta", "claude", "/tmp", "", "", "blue", "auto")
    hub = runner.hub(sid)

    def listed():
        rows = [x for x in runner.sessions_payload()["sessions"] if x["id"] == sid]
        assert rows, "session missing from the list payload"
        return rows[0]

    try:
        assert runner.session_payload(db.get_session(sid))["show_meta"] is True
        db.touch_session(sid, show_meta=0)
        assert runner.session_payload(db.get_session(sid))["show_meta"] is False
        db.touch_session(sid, show_meta=1)
        assert runner.session_payload(db.get_session(sid))["show_meta"] is True
        # BOTH payloads carry it. The sidebar menu reads the LIST, and is the
        # only way back once the head is hidden - serving it without the field
        # left that menu stuck showing "on", so every click hid it again.
        assert listed()["show_meta"] is True
        db.touch_session(sid, show_meta=0)
        assert listed()["show_meta"] is False
        # The same list and per-session snapshot expose why an activity block
        # went idle. This is transient wire state, not a persisted session flag.
        hub.last_completion_status = "interrupted"
        assert listed()["completion_status"] == "interrupted"
        assert hub.snapshot()["completion_status"] == "interrupted"
        hub.status = "running"
        assert listed()["completion_status"] == ""
        assert hub.snapshot()["completion_status"] == ""
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_queue_persistence(runner, db) -> None:
    """Queued prompts belong to the user: they survive a kill and a restart as
    held items, come back only on an explicit re-send, and never run twice."""
    sid = db.create_session("queue survival", "claude", "/tmp", "", "", "blue", "auto")
    try:
        h = runner.hub(sid)
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        h.send_message("first")
        for text in ("second", "third"):
            h.send_message(text)
        assert h.queue == ["second", "third"]
        assert db.meta_get("session_queue.{}".format(sid)) == {
            "queue": ["second", "third"], "held": [], "paused": []}

        # a deploy kill parks the queue durably instead of discarding it
        await h.kill()
        assert h.queue == [] and h.held == ["second", "third"]

        # a restart restores everything as held; a second restart adds nothing
        runner.drop_hub(sid)
        h = runner.hub(sid)
        assert h.queue == [] and h.held == ["second", "third"]
        runner.drop_hub(sid)
        h = runner.hub(sid)
        assert h.held == ["second", "third"]

        # held items move only on explicit instruction, guarded by their text
        assert "error" in h.requeue_held(0, "not the item")
        restarted = []
        h._start_turn = lambda text: (restarted.append(text),
                                      setattr(h, "status", "running"))
        assert h.requeue_held(0, "second") == {"queued": False}
        assert restarted == ["second"]
        assert h.requeue_held(0, "third") == {"queued": True}
        assert h.queue == ["third"] and h.held == []

        # during shutdown the queue is left for kill() to park, not consumed
        runner._draining = True
        try:
            assert h._take_next_turn() is None
            assert h.queue == ["third"] and h.status == "idle"
        finally:
            runner._draining = False
        await h.kill()
        assert h.held == ["third"]
        assert h.discard_held(0, "third") == {"ok": True}
        assert db.meta_get("session_queue.{}".format(sid)) is None

        # Restore accepts only the current item shape: engine rows and tagged
        # config rows come back held with recomputed cancel identities, while
        # an old untagged config row is rejected rather than converted.
        eng_fields = {"engine": "codex", "model": "gpt-x", "effort": ""}
        tagged_fields = {"model": "claude-x", "engine": "claude"}
        db.meta_set("session_queue.{}".format(sid), {
            "queue": [{"kind": "engine", "fields": dict(eng_fields), "key": "stale"}],
            "held": [{"kind": "config", "fields": dict(tagged_fields), "key": "stale"},
                     {"kind": "config", "fields": {"model": "untagged"},
                      "key": "stale"}],
        })
        runner.drop_hub(sid)
        h = runner.hub(sid)
        assert [item["fields"] for item in h.held] == [eng_fields, tagged_fields]
        assert h.held[0]["key"] == runner._queued_engine_key(eng_fields)
        assert h.held[1]["key"] == runner._queued_config_key(tagged_fields)
        assert h.discard_held(1, h.held[1]["key"]) == {"ok": True}
        assert h.discard_held(0, h.held[0]["key"]) == {"ok": True}
        assert db.meta_get("session_queue.{}".format(sid)) is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)
    assert db.meta_get("session_queue.{}".format(sid)) is None


async def exercise_queue_pause(runner, db) -> None:
    """Pausing skips only that prompt, stays durable/stale-safe, and remains
    distinct from held work."""
    sid = db.create_session("queue pause", "claude", "/tmp", "", "", "blue", "auto")
    try:
        h = runner.hub(sid)
        pending_config = {
            "kind": "config", "fields": {"model": "paused-model"},
            "key": 'config:{"model": "paused-model"}',
        }
        h.status = "running"
        h.active_since = time.time() - 5
        h.queue = ["second", pending_config, "third", "fourth"]
        h._broadcast_queue()

        # Only prompts can be paused, and the same stale text guard used by X
        # prevents a shifted index from changing some other prompt.
        assert "error" in h.set_queue_paused(1, pending_config["key"], True)
        assert "error" in h.set_queue_paused(2, "not third", True)
        assert h.set_queue_paused(2, "third", True) == {"ok": True, "paused": True}
        assert h.snapshot()["paused"] == [2]
        assert db.meta_get("session_queue.{}".format(sid))["paused"] == [2]

        # Earlier work still runs. Consuming it shifts the paused index; the
        # later unpaused prompt then passes it and sees the intervening config.
        assert h._take_next_turn() == "second"
        assert h.queue == [pending_config, "third", "fourth"]
        assert h._paused_wire() == [1]
        assert h._take_next_turn() == "fourth"
        assert h.queue == ["third"] and h._paused_wire() == [0]
        assert db.get_session(sid)["model"] == "paused-model"
        assert h._take_next_turn() is None and h.status == "idle"

        # Play resumes the only remaining prompt immediately.
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h.set_queue_paused(0, "third", False) == {"ok": True, "paused": False}
        assert started == ["third"]
        assert h.queue == [] and h._paused_wire() == []
        assert db.get_session(sid)["model"] == "paused-model"

        # Pausing the front of an idle queue starts the later prompt rather
        # than stranding it. Removing the paused row clears its sidecar state.
        h.status = "idle"
        h.queue = ["remove me", "fifth"]
        assert h.set_queue_paused(0, "remove me", True) == {
            "ok": True, "paused": True}
        assert started == ["third", "fifth"]
        assert h.queue == ["remove me"] and h._paused_wire() == [0]
        assert h.unqueue(0, "remove me") == {"ok": True}
        assert h.queue == [] and h._paused_wire() == []

        # Edit is one guarded queue -> durable-draft operation. It cannot move
        # a shifted neighbor or a pending configuration row, and popping the
        # prompt remaps pause state exactly like every other queue mutation.
        h.status = "running"
        h.queue = ["edit this", pending_config, "keep paused"]
        h.paused_queue = {0, 2}
        await h.update_draft("replace this composer")
        assert "error" in await h.edit_queued(0, "not edit this")
        assert "cannot be edited" in (await h.edit_queued(
            1, pending_config["key"]))["error"]
        edited = await h.edit_queued(0, "edit this")
        assert edited["ok"] is True and edited["started"] is False
        assert edited["draft"]["type"] == "draft"
        assert edited["draft"]["text"] == "edit this"
        assert db.get_session_draft(sid)["text"] == "edit this"
        assert h.queue == [pending_config, "keep paused"]
        assert h._paused_wire() == [1]
        assert db.meta_get("session_queue.{}".format(sid)) == {
            "queue": [pending_config, "keep paused"],
            "held": [], "paused": [1],
        }

        # If the active turn ended just before the guarded edit arrived, the
        # edited row is skipped and the next runnable prompt proceeds.
        h.status = "idle"
        h.queue = ["edit after finish", "run after edit"]
        h.paused_queue.clear()
        edited_idle = await h.edit_queued(0, "edit after finish")
        assert edited_idle["ok"] is True and edited_idle["started"] is True
        assert started[-1] == "run after edit" and h.queue == []
        assert db.get_session_draft(sid)["text"] == "edit after finish"

        # A kill instead moves prose to held, where resend/discard already
        # supply the only state transition and pause must not leak through.
        h.status = "running"
        h.queue = ["held after stop"]
        assert h.set_queue_paused(0, "held after stop", True) == {
            "ok": True, "paused": True}
        await h.kill()
        assert h.queue == [] and h.held == ["held after stop"]
        assert h.snapshot()["paused"] == []
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_queue_reorder(runner, db) -> None:
    """A revision-guarded drag holds dequeue, remaps pause indexes, and resumes
    from the user's chosen order when the hold is released."""
    sid = db.create_session("queue reorder", "claude", "/tmp", "", "",
                            "blue", "auto")
    try:
        h = runner.hub(sid)
        pending_config = {
            "kind": "config", "fields": {"model": "later-model"},
            "key": 'config:{"model": "later-model"}',
        }
        h.status = "running"
        h.queue = ["one", pending_config, "two", "three"]
        h.paused_queue = {0, 3}
        h._broadcast_queue()
        revision = h.queue_revision
        owner = object()
        other = object()

        assert "error" in h.begin_queue_reorder(owner, "stale", revision - 1)
        assert h.begin_queue_reorder(owner, "drag-1", revision) == {
            "ok": True, "queue_revision": revision}
        assert "error" in h.begin_queue_reorder(other, "drag-2", revision)

        # The turn finishes while the row is in flight. No prompt can start
        # until the acknowledged holder commits or cancels.
        h.status = "idle"
        h.active_since = time.time() - 8
        h._queue_waiting_completion = (h.active_since, False, "ok")
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h._start_queue_if_ready() is False
        assert h.queue[0] == "one"

        result = h.reorder_queue(
            owner, "drag-1", revision, [2, 1, 0, 3])
        assert result["ok"] is True and result["started"] is True
        assert started == ["two"]
        assert h.queue == [pending_config, "one", "three"]
        assert h._paused_wire() == [1, 2]
        assert h._queue_reorder is None
        assert h._queue_waiting_completion is None

        # A malformed drop releases the lease but never mutates the queue.
        h.status = "running"
        h._broadcast_queue()
        revision = h.queue_revision
        assert h.begin_queue_reorder(owner, "drag-3", revision)["ok"] is True
        before = list(h.queue)
        rejected = h.reorder_queue(owner, "drag-3", revision, [0, 0, 1])
        assert rejected["error"] == "invalid queue order"
        assert h.queue == before and h._queue_reorder is None

        # If every remaining prompt is paused, releasing the transition ends
        # the deferred activity block instead of leaving it half-complete.
        h.status = "idle"
        h.queue = ["paused one", "paused two"]
        h.paused_queue = {0, 1}
        h.active_since = time.time() - 4
        h._queue_waiting_completion = (h.active_since, False, "ok")
        h._broadcast_queue()
        revision = h.queue_revision
        assert h.begin_queue_reorder(owner, "drag-4", revision)["ok"] is True
        settled = h.finish_queue_reorder(owner, "drag-4")
        assert settled["ok"] is True and settled["started"] is False
        assert h.active_since is None and h.last_completion_status == "ok"
        assert h._queue_waiting_completion is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_session_drafts(runner, db, uploads, config) -> None:
    """Drafts are ordered, durable, conflict-safe, and own staged uploads."""
    sid = db.create_session("draft sync", "claude", "/tmp", "", "", "blue", "auto")
    session_root = Path(config.DATA_DIR).resolve() / "uploads" / str(sid)

    class Watcher:
        def __init__(self):
            self.messages = []

        async def send_json(self, value):
            self.messages.append(value)

    first = Watcher()
    second = Watcher()
    h = runner.hub(sid)
    h.attach(first)
    h.attach(second)
    try:
        assert h.snapshot()["draft"] == {
            "text": "", "revision": 0, "updated_at": None}
        assert h.snapshot()["draft_max_chars"] == db.MAX_DRAFT_CHARS
        saved = await h.update_draft("Test", "device-a", 1)
        assert saved["revision"] == 1 and saved["text"] == "Test"
        assert first.messages[-1] == second.messages[-1] == saved
        assert db.get_session_draft(sid)["text"] == "Test"

        newer = await h.update_draft("edited elsewhere", "device-b", 4)
        stale = await h.consume_draft("Test", "device-a", 2, recipient=first)
        assert stale["consumed"] is False
        assert stale["text"] == "edited elsewhere"
        assert stale["revision"] == newer["revision"] == 2
        assert db.get_session_draft(sid)["text"] == "edited elsewhere"

        too_large = await h.update_draft("x" * (db.MAX_DRAFT_CHARS + 1))
        assert "cannot exceed" in too_large["error"]
        assert db.get_session_draft(sid)["revision"] == 2

        class GateWatcher:
            def __init__(self):
                self.messages = []
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def send_json(self, value):
                self.messages.append(value)
                if value.get("type") == "snapshot":
                    self.started.set()
                    await self.release.wait()

        joining = GateWatcher()
        attach_task = asyncio.create_task(h.attach_with_snapshot(joining))
        await joining.started.wait()
        ordered_task = asyncio.create_task(
            h.update_draft("ordered after snapshot", "device-b", 5))
        await asyncio.sleep(0)
        assert not ordered_task.done()
        joining.release.set()
        await attach_task
        ordered = await ordered_task
        assert [item["type"] for item in joining.messages] == ["snapshot", "draft"]
        assert joining.messages[0]["draft"]["revision"] == 2
        assert joining.messages[1]["revision"] == ordered["revision"] == 3
        h.detach(joining)

        def uploaded(name: str):
            directory = uploads._new_upload_directory(sid)
            path = directory / name
            path.write_bytes(b"draft attachment")
            path.chmod(0o600)
            marker = "[file attached: {} ({}, 16 B) — inspect it with your file tools]".format(
                path, name)
            return path, marker

        abandoned_path, abandoned_marker = uploaded("draft-only.txt")
        await h.update_draft(abandoned_marker, "device-a", 3)
        assert abandoned_path.exists()
        await h.update_draft("", "device-a", 4)
        # A full-document update from another socket may already be in flight
        # with this marker. Keep draft-only bytes until session deletion rather
        # than accepting that later revision with a broken attachment.
        assert abandoned_path.exists()

        retained_path, retained_marker = uploaded("also-queued.txt")
        await h.update_draft(retained_marker, "device-a", 5)
        h.status = "running"  # clearing must not start the retained prompt
        h.queue = [retained_marker]
        await h.update_draft("", "device-a", 6)
        assert retained_path.exists()
        assert h.clear_queue() == 1
        assert not retained_path.exists()
        h.status = "idle"

        # Moving a queued prompt to the composer transfers ownership of its
        # attachment marker instead of treating the queue removal as cancel.
        replaced_path, replaced_marker = uploaded("replaced-composer.txt")
        edited_path, edited_marker = uploaded("edited-from-queue.txt")
        await h.update_draft(replaced_marker)
        h.status = "running"
        h.queue = [edited_marker]
        moved = await h.edit_queued(0, edited_marker)
        assert moved["ok"] is True and moved["draft"]["text"] == edited_marker
        assert h.queue == [] and db.get_session_draft(sid)["text"] == edited_marker
        assert not replaced_path.exists()
        assert edited_path.exists()
        h.status = "idle"

        durable = await h.update_draft("survives a restart", "device-a", 7)
        h.detach(first)
        h.detach(second)
        runner.drop_hub(sid)
        await asyncio.sleep(0)
        h = runner.hub(sid)
        assert h.snapshot()["draft"] == {
            "text": "survives a restart", "revision": durable["revision"],
            "updated_at": durable["updated_at"],
        }
    finally:
        h.status = "idle"
        h.queue.clear()
        h.held.clear()
        h.paused_queue.clear()
        h._persist_queue()
        runner.drop_hub(sid)
        shutil.rmtree(session_root, ignore_errors=True)
        db.delete_session(sid)
    assert db.query_one(
        "SELECT 1 FROM session_drafts WHERE session_id=?", (sid,)) is None


def exercise_abandoned_upload_cleanup(runner, db, uploads, config) -> None:
    """Queue cancellation removes only uploads with no surviving reference."""
    sid = db.create_session("upload cleanup", "claude", "/tmp", "", "", "blue", "auto")
    session_root = Path(config.DATA_DIR).resolve() / "uploads" / str(sid)

    def uploaded(name: str):
        directory = uploads._new_upload_directory(sid)
        path = directory / name
        path.write_bytes(b"private attachment")
        path.chmod(0o600)
        marker = "[file attached: {} ({}, 18 B) — inspect it with your file tools]".format(
            path, name)
        return path, marker

    h = runner.hub(sid)
    h.status = "running"  # cancellation must not auto-start a remaining prompt
    try:
        abandoned_path, abandoned_marker = uploaded("abandoned.txt")
        h.queue = [abandoned_marker]
        assert h.unqueue(0, abandoned_marker) == {"ok": True}
        assert not abandoned_path.exists()

        shared_path, shared_marker = uploaded("shared.txt")
        h.queue = [shared_marker, shared_marker]
        assert h.unqueue(0, shared_marker) == {"ok": True}
        assert shared_path.exists()  # the second queued prompt still owns it
        assert h.unqueue(0, shared_marker) == {"ok": True}
        assert not shared_path.exists()

        sent_path, sent_marker = uploaded("sent.txt")
        db.add_event(sid, "user", {"text": sent_marker})
        h.queue = [sent_marker]
        assert h.unqueue(0, sent_marker) == {"ok": True}
        assert sent_path.exists()  # durable transcript previews must survive

        active_path, active_marker = uploaded("active.txt")
        h._active_prompt_text = active_marker
        h.queue = [active_marker]
        assert h.unqueue(0, active_marker) == {"ok": True}
        assert active_path.exists()
        h._active_prompt_text = ""
        assert uploads.discard_abandoned(sid, [active_marker]) == 1
        assert not active_path.exists()

        held_path, held_marker = uploaded("held.txt")
        h.held = [held_marker]
        assert h.discard_held(0, held_marker) == {"ok": True}
        assert not held_path.exists()

        first_path, first_marker = uploaded("first.txt")
        second_path, second_marker = uploaded("second.txt")
        h.queue = [first_marker, second_marker]
        assert h.clear_queue() == 2
        assert not first_path.exists() and not second_path.exists()

        prose_path, _marker = uploaded("plain-prose.txt")
        prose = "A path mentioned as prose must stay: {}".format(prose_path)
        h.queue = [prose]
        assert h.unqueue(0, prose) == {"ok": True}
        assert prose_path.exists()
    finally:
        h.status = "idle"
        h.queue.clear()
        h.held.clear()
        h.paused_queue.clear()
        h._active_prompt_text = ""
        h._persist_queue()
        runner.drop_hub(sid)
        shutil.rmtree(session_root, ignore_errors=True)
        db.delete_session(sid)


def exercise_auth_hardening(auth) -> None:
    """Unknown accounts pay the real work factor and limiter state stays bounded."""
    captured = []
    original_verify = auth.verify_password
    auth.verify_password = lambda _password, stored: captured.append(stored) or False
    try:
        assert auth.check_login("definitely-missing-user", "wrong") is False
    finally:
        auth.verify_password = original_verify
    assert captured and int(captured[0].split("$")[1]) == auth.PBKDF2_ITERS

    auth._attempts.clear()
    auth._attempts_last_prune = time.monotonic() - auth.RATE_LIMIT_PRUNE_SECONDS
    auth._attempts["expired"] = [time.monotonic() - 301]
    assert auth._rate_limited("fresh", limit=2, window=300) is False
    assert "expired" not in auth._attempts
    assert auth._rate_limited("fresh", limit=2, window=300) is False
    assert auth._rate_limited("fresh", limit=2, window=300) is True
    for index in range(auth.RATE_LIMIT_MAX_KEYS + 20):
        auth._rate_limited("peer-{}".format(index), limit=2, window=300)
    assert len(auth._attempts) <= auth.RATE_LIMIT_MAX_KEYS
    auth._attempts.clear()
    auth._attempts_last_prune = time.monotonic()


async def exercise_auth_endpoint(url: str, auth) -> None:
    """A caller cannot mint limiter identities with a Forwarded header."""
    seen = []
    original = auth._rate_limited
    auth._rate_limited = lambda key: seen.append(key) or True
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url + "/api/auth/login",
                                 headers={"X-Forwarded-For": "198.51.100.77"},
                                 json={"username": "user", "password": "wrong"}) as response:
                assert response.status == 429, await response.text()
            async with http.post(url + "/api/auth/login", json=[]) as response:
                assert response.status == 400, await response.text()
    finally:
        auth._rate_limited = original
    assert seen == ["127.0.0.1"], seen


async def exercise_engine_switch_queue(url: str, token: str, runner, db) -> None:
    """An engine switch queues behind pending work, applies in order, and one
    engine's validated settings can never reach another engine."""
    from puppy.drivers import get_driver
    codex_default = get_driver("codex").default_model()
    codex_permission = get_driver("codex").default_permission()
    sid = db.create_session("switch queue", "claude", "/tmp", "old-model", "high",
                            "blue", "auto")
    h = runner.hub(sid)
    claude_cfg = {
        "kind": "config", "fields": {"model": "claude-only", "engine": "claude"},
        "key": runner._queued_config_key({"model": "claude-only", "engine": "claude"}),
    }
    h.queue = [claude_cfg, "paused prompt"]
    h.paused_queue = {1}
    h.held = ["held prompt"]
    h._persist_queue()
    try:
        async with aiohttp.ClientSession() as http:
            headers = {"X-Puppy-Token": token}
            for payload in ([], {"engine": ["codex"]}):
                async with http.post(
                        url + "/api/sessions/{}/switch".format(sid), headers=headers,
                        json=payload) as response:
                    assert response.status == 400, await response.text()
            # Work is pending: the switch holds its place at the queue tail
            # instead of applying, and nothing pending is discarded.
            async with http.post(
                    url + "/api/sessions/{}/switch".format(sid), headers=headers,
                    json={"engine": "codex"}) as response:
                switched = await response.json()
                assert response.status == 200, switched
        assert switched["queued"] is True
        assert switched["session"]["engine"] == "claude"
        assert h.queue[:2] == [claude_cfg, "paused prompt"]
        assert runner._is_queued_engine(h.queue[2])
        assert h.queue[2]["fields"] == {
            "engine": "codex", "model": codex_default, "effort": ""}
        assert h._paused_wire() == [1] and h.held == ["held prompt"]
        # an engine upgrade must treat the queued switch target as busy work
        assert any(b["id"] == sid for b in runner.engine_blockers("codex"))
        assert any(b["id"] == sid for b in runner.engine_blockers("claude"))

        # A model picked while the switch waits belongs to its target and
        # folds into the switch row; a stale validation tag is refused.
        assert h.queue_config({"model": "o-mini", "engine": "codex"}) == \
            {"handled": True}
        assert h.queue[2]["fields"]["model"] == "o-mini"
        assert "belongs to" in h.queue_config(
            {"model": "sonnet", "engine": "claude"})["error"]
        assert h.pending_config() == {
            "engine": "codex", "model": "o-mini", "effort": ""}

        # Re-picking collapses into the same pending row, resetting its
        # model/effort to the newly chosen target's defaults.
        assert h.request_engine_switch("claude", "") == {"queued": True}
        assert len(h.queue) == 3 and h.queue[2]["fields"] == {
            "engine": "claude", "model": "", "effort": ""}
        assert h.request_engine_switch("codex", codex_default) == {"queued": True}

        # A new prompt into the idle, fully paused queue starts itself: the
        # claude model change applies while the session is still claude, the
        # switch then lands on codex with a fresh native conversation, and the
        # paused prompt keeps its place for later.
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h.send_message("run now") == {"queued": True}
        assert started == ["run now"]
        assert h.queue == ["paused prompt"] and h._paused_wire() == [0]
        session = db.get_session(sid)
        assert session["engine"] == "codex"
        assert session["model"] == codex_default and session["effort"] == ""
        assert session["native_session_id"] == "" and session["last_model"] == ""
        assert session["used_config"] == ""
        assert session["permission_mode"] == codex_permission
        divider = [e for e in db.get_events(sid) if e["kind"] == "engine_switch"][-1]
        assert divider["data"] == {"from": "claude", "to": "codex",
                                   "from_model": "claude-only", "from_effort": "high"}

        # A row whose engine switch was cancelled is skipped with a note, not
        # applied to whatever engine is current now.
        h.status = "idle"
        h._apply_queued_config({"model": "claude-x", "engine": "claude"})
        assert db.get_session(sid)["model"] == codex_default
        skipped = db.get_events(sid)[-1]
        assert skipped["kind"] == "info" and \
            skipped["data"]["subtype"] == "config_skipped"

        # Held work survives switches for an explicit decision: a held switch
        # re-applies (or re-queues) on demand, and a held setting validated by
        # a different engine is refused rather than misapplied.
        assert h.unqueue(0, "paused prompt") == {"ok": True}
        eng_fields = {"engine": "claude", "model": "", "effort": ""}
        eng_row = {"kind": "engine", "fields": dict(eng_fields),
                   "key": runner._queued_engine_key(eng_fields)}
        h.held = [eng_row]
        assert h.requeue_held(0, eng_row["key"]) == {"ok": True}
        assert h.held == [] and db.get_session(sid)["engine"] == "claude"
        stale_fields = {"model": "gpt-x", "engine": "codex"}
        stale_cfg = {"kind": "config", "fields": dict(stale_fields),
                     "key": runner._queued_config_key(stale_fields)}
        h.held = [stale_cfg]
        assert "belongs to" in h.requeue_held(0, stale_cfg["key"])["error"]
        assert h.held == [stale_cfg]
        assert h.discard_held(0, stale_cfg["key"]) == {"ok": True}

        # With nothing pending the route still switches immediately.
        async with aiohttp.ClientSession() as http:
            async with http.post(
                    url + "/api/sessions/{}/switch".format(sid),
                    headers={"X-Puppy-Token": token},
                    json={"engine": "codex"}) as response:
                switched = await response.json()
                assert response.status == 200, switched
        assert switched["queued"] is False
        assert switched["session"]["engine"] == "codex"

        # Deleting a session discards its queue before the asynchronous hub
        # kill runs, so the durable queue record cannot be resurrected.
        h.queue = ["deleted with the session"]
        h._persist_queue()
        assert db.meta_get("session_queue.{}".format(sid)) is not None
        runner.drop_hub(sid)
        db.delete_session(sid)
        for _ in range(4):
            await asyncio.sleep(0)
        assert db.meta_get("session_queue.{}".format(sid)) is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_queue_pause_websocket(url: str, token: str, runner, db) -> None:
    """The authenticated socket carries pause and revision-guarded reorder."""
    sid = db.create_session("queue pause socket", "claude", "/tmp", "", "",
                            "blue", "auto")
    h = runner.hub(sid)
    h.status = "running"
    h.queue = ["socket queued prompt", "socket second prompt"]
    h._broadcast_queue()
    ws = None
    try:
        async with aiohttp.ClientSession() as http:
            ws = await http.ws_connect(
                url + "/api/ws/session/{}".format(sid),
                headers={"X-Puppy-Token": token})
            snapshot = await ws.receive_json(timeout=3)
            assert snapshot["queued"] == [
                "socket queued prompt", "socket second prompt"]
            assert snapshot["paused"] == []
            assert isinstance(snapshot["queue_revision"], int)

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": True})
            paused = await ws.receive_json(timeout=3)
            assert paused["type"] == "queued" and paused["paused"] == [0], paused

            await ws.send_json({
                "type": "begin_queue_reorder", "request_id": "socket-drag",
                "queue_revision": paused["queue_revision"],
            })
            ready = await ws.receive_json(timeout=3)
            assert ready == {
                "type": "queue_reorder_ready", "request_id": "socket-drag",
                "ok": True, "queue_revision": paused["queue_revision"],
            }, ready
            await ws.send_json({
                "type": "reorder_queue", "request_id": "socket-drag",
                "queue_revision": paused["queue_revision"], "order": [1, 0],
            })
            frames = [await ws.receive_json(timeout=3),
                      await ws.receive_json(timeout=3)]
            queued = next(frame for frame in frames if frame["type"] == "queued")
            complete = next(frame for frame in frames
                            if frame["type"] == "queue_reorder_complete")
            assert queued["queued"] == [
                "socket second prompt", "socket queued prompt"]
            assert queued["paused"] == [1]
            assert complete["ok"] is True and complete["started"] is False
            assert complete["queued"] == queued["queued"]

            await ws.send_json({
                "type": "edit_queue", "request_id": "stale-edit",
                "index": 0, "text": "not the queued prompt",
            })
            rejected_edit = await ws.receive_json(timeout=3)
            assert rejected_edit["type"] == "queue_edit_complete", rejected_edit
            assert rejected_edit["request_id"] == "stale-edit"
            assert "error" in rejected_edit

            await ws.send_json({
                "type": "edit_queue", "request_id": "socket-edit",
                "index": 0, "text": "socket second prompt",
            })
            edit_frames = [await ws.receive_json(timeout=3) for _ in range(3)]
            edited_queue = next(frame for frame in edit_frames
                                if frame["type"] == "queued")
            edited_draft = next(frame for frame in edit_frames
                                if frame["type"] == "draft")
            edit_complete = next(frame for frame in edit_frames
                                 if frame["type"] == "queue_edit_complete")
            assert edited_queue["queued"] == ["socket queued prompt"]
            assert edited_queue["paused"] == [0]
            assert edited_draft["text"] == "socket second prompt"
            assert edit_complete["request_id"] == "socket-edit"
            assert edit_complete["ok"] is True and edit_complete["started"] is False
            assert edit_complete["draft"] == edited_draft
            assert db.get_session_draft(sid)["text"] == "socket second prompt"

            h.queue.append("socket third prompt")
            h._broadcast_queue()
            disconnect_queue = await ws.receive_json(timeout=3)
            assert disconnect_queue["type"] == "queued"
            assert disconnect_queue["queued"] == [
                "socket queued prompt", "socket third prompt"]

            # A holder is released immediately when its owning socket leaves;
            # the 30-second lease is only a last-resort fail-open path.
            await ws.send_json({
                "type": "begin_queue_reorder", "request_id": "disconnect-drag",
                "queue_revision": disconnect_queue["queue_revision"],
            })
            ready = await ws.receive_json(timeout=3)
            assert ready["ok"] is True, ready

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": "yes"})
            rejected = await ws.receive_json(timeout=3)
            assert rejected["type"] == "toast" and \
                "true or false" in rejected["text"], rejected
            await ws.close()
            ws = None
            await asyncio.sleep(0)
            assert h._queue_reorder is None
    finally:
        if ws is not None:
            await ws.close()
        h.queue.clear()
        h.paused_queue.clear()
        h.status = "idle"
        h._persist_queue()
        runner.drop_hub(sid)
        db.delete_session(sid)


async def main() -> None:
    private_tests = BASE / "data" / "tests"
    private_tests.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_tests.chmod(0o700)
    temp_root = Path(tempfile.mkdtemp(prefix="backend-", dir=str(private_tests)))
    process = None
    disabled_process = None
    controller_runner = None
    try:
        exercise_driver_normalization()
        release_artifact = temp_root / "release" / "puppy-backend.pyz"
        subprocess.run([sys.executable, str(BASE / "backend" / "build.py"),
                        "--output", str(release_artifact)], cwd=str(BASE), check=True)
        generated_launcher = release_artifact.with_name("puppy-backend-launcher.py")
        assert generated_launcher.is_file() and os.access(generated_launcher, os.X_OK)
        with zipfile.ZipFile(release_artifact) as archive:
            names = archive.namelist()
        assert any(name.startswith("puppy/drivers/") for name in names)
        assert "puppy/browser_agent.py" in names
        assert "puppy/terminal_agent.py" in names
        assert not any(name.startswith("puppy/static/") for name in names)
        mcp_env = dict(os.environ)
        mcp_env["PYTHONPATH"] = str(release_artifact)
        mcp_env["PUPPY_DATA"] = str(temp_root / "mcp-data")
        mcp_init = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }) + "\n"
        mcp_output = subprocess.check_output(
            [sys.executable, "-m", "puppy.browser_agent"], input=mcp_init,
            env=mcp_env, cwd=str(temp_root), text=True, timeout=5)
        mcp_result = json.loads(mcp_output.strip())
        assert mcp_result["result"]["serverInfo"]["name"] == "Puppy managed browser"
        assert "four uppercase" in mcp_result["result"]["instructions"]
        assert "default for interactive web navigation" in \
            mcp_result["result"]["instructions"]
        assert "repository's own browser test suite" in \
            mcp_result["result"]["instructions"]
        terminal_mcp_output = subprocess.check_output(
            [sys.executable, "-m", "puppy.terminal_agent"], input=mcp_init,
            env=mcp_env, cwd=str(temp_root), text=True, timeout=5)
        terminal_mcp_result = json.loads(terminal_mcp_output.strip())
        assert terminal_mcp_result["result"]["serverInfo"]["name"] == \
            "Puppy shared terminal"
        assert "only when the user specifically asks" in \
            terminal_mcp_result["result"]["instructions"]
        self_test = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "self-test", "--data-dir",
            str(temp_root / "self-test-data"),
        ], text=True).splitlines()[-1])
        assert self_test["ok"] is True and self_test["version"] == __version__
        assert "/api/terminal/instances" in self_test["routes"]
        assert "/api/ws/terminal/{terminal_id}" in self_test["routes"]

        # Configuration alone is insufficient: a directly launched zipapp must
        # keep remote upgrades disabled because no external rollback exists.
        disabled_data = temp_root / "disabled-data"
        disabled_port = free_port()
        disabled_url = f"http://127.0.0.1:{disabled_port}"
        backend_token = "backend-test-token-0123456789abcdef"
        subprocess.check_output([
            sys.executable, str(release_artifact), "pairing", "--data-dir", str(disabled_data),
            "--name", "disabled-node", "--bind", "127.0.0.1", "--port", str(disabled_port),
            "--advertise-url", disabled_url, "--api-token", backend_token,
            "--disable-terminal", "--enable-remote-upgrade", "--disable-tls",
            "--usage-refresh-minutes", "0", "--max-upload-size-mb", "2",
        ], text=True)
        disabled_process = subprocess.Popen([
            sys.executable, str(release_artifact), "serve", "--data-dir", str(disabled_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_for_backend(disabled_url, disabled_process)
        await exercise_node(disabled_url, backend_token, __version__, upgrade_enabled=False)
        await stop_process_with_notice(disabled_process, disabled_url, backend_token)
        disabled_process = None

        old_version = previous_patch_version()
        artifact = temp_root / "node" / "puppy-backend.pyz"
        artifact.parent.mkdir()
        copy_with_version(release_artifact, artifact, old_version)
        backend_data = temp_root / "backend-data"
        backend_port = free_port()
        backend_url = f"https://127.0.0.1:{backend_port}"
        pairing_raw = subprocess.check_output([
            sys.executable, str(artifact), "pairing", "--data-dir", str(backend_data),
            "--name", "backend-test-node", "--bind", "127.0.0.1",
            "--port", str(backend_port), "--advertise-url", backend_url,
            "--api-token", backend_token, "--disable-terminal", "--enable-remote-upgrade",
            "--auto-tls", "--usage-refresh-minutes", "0", "--max-upload-size-mb", "3",
        ], text=True)
        pairing = json.loads(pairing_raw)
        assert pairing["url"] == backend_url and pairing["token"] == backend_token
        backend_fingerprint = pairing["tls_sha256"]
        assert len(backend_fingerprint) == 64
        assert "pinned-tls" in pairing["capabilities"]
        assert "terminal" not in pairing["capabilities"]
        assert "remote-upgrade" not in pairing["capabilities"]  # pairing command is not launcher-managed
        assert "shutdown-notice" in pairing["capabilities"]
        assert "queue-pause" in pairing["capabilities"]
        assert "queue-edit" in pairing["capabilities"]
        assert "queue-reorder" in pairing["capabilities"]
        assert "session-drafts" in pairing["capabilities"]
        assert "engine-model-selection" not in pairing["capabilities"]
        assert pairing["max_upload_size_mb"] == 3
        assert (backend_data / "config.json").stat().st_mode & 0o777 == 0o600
        identity_manifest = json.loads(
            (backend_data / "tls" / "identity.json").read_text(encoding="utf-8"))
        assert (backend_data / "tls").stat().st_mode & 0o777 == 0o700
        assert ((backend_data / "tls" / identity_manifest["private_key"])
                .stat().st_mode & 0o777) == 0o600

        enabled_pairing = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "pairing",
            "--data-dir", str(temp_root / "enabled-data"),
            "--bind", "127.0.0.1", "--port", str(backend_port),
            "--api-token", backend_token, "--usage-refresh-minutes", "30",
            "--max-upload-size-mb", "4",
        ], text=True))
        assert "terminal" in enabled_pairing["capabilities"]
        assert "terminal-instances" in enabled_pairing["capabilities"]
        assert "terminal-handoff" in enabled_pairing["capabilities"]
        assert "engine-model-selection" not in enabled_pairing["capabilities"]
        assert enabled_pairing["usage_refresh_minutes"] == 30
        assert enabled_pairing["max_upload_size_mb"] == 4
        assert "pinned-tls" in enabled_pairing["capabilities"]
        assert len(enabled_pairing["tls_sha256"]) == 64
        repeated_pairing = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "pairing",
            "--data-dir", str(temp_root / "enabled-data"),
        ], text=True))
        assert repeated_pairing["tls_sha256"] == enabled_pairing["tls_sha256"]
        assert repeated_pairing["usage_refresh_minutes"] == 30
        assert repeated_pairing["max_upload_size_mb"] == 4

        state_dir = backend_data / "upgrade"
        legacy_launcher_env = dict(os.environ)
        legacy_launcher_env.update({
            "PUPPY_BACKEND_LAUNCHER_PROTOCOL": str(upgrade_contract.LAUNCHER_PROTOCOL),
            "PUPPY_BACKEND_MANAGED_ARTIFACT": str(artifact.resolve()),
            "PUPPY_BACKEND_UPGRADE_MARKER": str((state_dir / "pending.json").resolve()),
            "PUPPY_BACKEND_UPGRADE_STATUS": str((state_dir / "status.json").resolve()),
        })
        process = subprocess.Popen([
            sys.executable, str(artifact), "serve", "--data-dir", str(backend_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=legacy_launcher_env)
        await wait_for_backend(backend_url, process, backend_fingerprint)
        await exercise_node(backend_url, backend_token, old_version, upgrade_enabled=False,
                            fingerprint=backend_fingerprint)
        await stop_process_with_notice(
            process, backend_url, backend_token, backend_fingerprint)
        process = None

        process = subprocess.Popen([
            sys.executable, str(BASE / "backend" / "launcher.py"),
            "--artifact", str(artifact), "--state-dir", str(state_dir), "--",
            "serve", "--data-dir", str(backend_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_for_backend(backend_url, process, backend_fingerprint)
        await exercise_node(backend_url, backend_token, old_version, upgrade_enabled=True,
                            fingerprint=backend_fingerprint)
        await reject_bad_signature(backend_url, backend_token, backend_fingerprint)

        # Import the full application only after its independent data path is set.
        controller_data = temp_root / "controller-data"
        controller_data.mkdir()
        os.environ["PUPPY_DATA"] = str(controller_data)
        # config binds its data path at import time, so an earlier import of it
        # anywhere above would silently point this whole test at the real
        # instance's data directory. Fail loudly instead of writing there.
        assert "puppy.config" not in sys.modules, \
            "puppy.config was imported before the test data path was set"
        from puppy import auth, config, db, host_metrics, runner, terminal, uploads
        from backend.puppy_backend import upgrade as backend_upgrade
        from puppy import web as puppy_web
        from puppy.web import build_app

        await check_notify_placeholders()
        await exercise_interrupted_notify_report(puppy_web)

        config.load()
        exercise_opencode_driver()
        await exercise_opencode_binary_fallback(temp_root / "opencode-fallback")
        controller_token = "controller-test-token-0123456789abcdef"
        config.set_value("auth.api_token", controller_token)
        config.set_value("engines.usage_refresh_minutes", 0)
        db.connect()
        exercise_auth_hardening(auth)
        exercise_activity_blocks(runner.SessionHub)
        await exercise_shutdown_broadcast(runner)
        await exercise_queue_persistence(runner, db)
        await exercise_queue_pause(runner, db)
        await exercise_queue_reorder(runner, db)
        await exercise_session_drafts(runner, db, uploads, config)
        exercise_abandoned_upload_cleanup(runner, db, uploads, config)
        exercise_session_show_meta(runner, db)
        await exercise_auth_probes(temp_root / "auth-probes")
        exercise_auth_evidence(temp_root / "auth-evidence", db)
        exercise_host_cpu_math(host_metrics)
        await exercise_upgrade_readiness(
            backend_upgrade, runner, terminal, temp_root / "readiness")
        app = build_app()
        controller_runner = web.AppRunner(app)
        await controller_runner.setup()
        site = web.TCPSite(controller_runner, "127.0.0.1", 0)
        await site.start()
        sock = site._server.sockets[0]
        controller_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        await exercise_auth_endpoint(controller_url, auth)
        await exercise_queue_pause_websocket(
            controller_url, controller_token, runner, db)
        await exercise_engine_switch_queue(
            controller_url, controller_token, runner, db)
        await exercise_controller(controller_url, controller_token, backend_url,
                                  backend_token, backend_fingerprint, old_version, state_dir)
        await exercise_proxy_recovery(controller_url, controller_token)
        await exercise_redirect_rejection()
        upgrade_status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
        assert upgrade_status["state"] == "succeeded", upgrade_status
        assert artifact.with_name(artifact.stem + ".previous" + artifact.suffix).is_file()

        await controller_runner.cleanup()
        controller_runner = None
        stop_process(process)
        process = None
        process = await exercise_launcher_rollback(
            artifact, BASE / "backend" / "launcher.py", state_dir, backend_data,
            backend_url, backend_token, backend_fingerprint)
        print("backend package, engine releases, pinned TLS, signed upgrade, restart, and rollback passed")
    finally:
        if controller_runner is not None:
            await controller_runner.cleanup()
        if disabled_process is not None:
            stop_process(disabled_process)
        if process is not None:
            stop_process(process)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
