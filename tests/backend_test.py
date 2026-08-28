#!/usr/bin/env python3
"""No-quota integration test for the separately deployable headless backend."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
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
        assert "file-uploads" in ping["capabilities"]
        assert "queue-pause" in ping["capabilities"]
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
        assert "shared, user-visible Puppy browser" in prompt_defaults["browser"]
        assert prompt_defaults["browser_default"] == prompt_defaults["browser"]
        assert prompt_defaults["max_chars"] == 32768
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "Use terse answers.",
                                    "browser": "Use the visible browser first."}) as response:
            saved_prompt = await response.json()
            assert response.status == 200, saved_prompt
        assert saved_prompt["system_prompt"]["custom"] == "Use terse answers."
        assert saved_prompt["system_prompt"]["browser"] == "Use the visible browser first."
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "x" * 32769}) as response:
            rejected_prompt = await response.json()
            assert response.status == 400, rejected_prompt
            assert "cannot exceed" in rejected_prompt["error"]
        async with http.get(url + "/api/system-prompt", ssl=pinned) as response:
            assert response.status == 401
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "",
                                    "browser": prompt_defaults["browser_default"]}) as response:
            assert response.status == 200, await response.text()
        assert "terminal" not in ping["capabilities"]
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
                     "/api/ws/term"):
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
        assert "queue-pause" in full_ping["capabilities"]
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
                                    "browser": remote_prompt["system_prompt"]["browser_default"]
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
    backend_token = "recovery-backend-token-0123456789abcdef"
    ports = [free_port(), free_port()]
    backend_urls = [f"http://127.0.0.1:{port}" for port in ports]

    async def start_backend(port: int) -> web.AppRunner:
        async def authorized(request):
            if request.headers.get("X-Puppy-Token") != backend_token:
                return web.json_response({"error": "unauthorized"}, status=401)
            return None

        async def ping(request):
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
                assert response.status in (502, 504), await response.text()
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("dead backend completed the proxied WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status in (502, 504), exc.status

            backend_runner = await start_backend(ports[0])
            recovered = False
            for _attempt in range(30):
                try:
                    async with http.get(proxy_url + "/sessions", headers=headers) as response:
                        recovered = response.status == 200
                except aiohttp.ClientError:
                    recovered = False
                if recovered:
                    break
                await asyncio.sleep(0.1)
            assert recovered, "HTTP proxy did not recover after backend restart"
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
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)
    assert db.meta_get("session_queue.{}".format(sid)) is None


async def exercise_queue_pause(runner, db) -> None:
    """Pausing is ordered, durable, stale-safe, and distinct from held work."""
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

        # Earlier work still runs. Consuming it shifts the paused index, then
        # the queue stops before applying the configuration tied to "third".
        assert h._take_next_turn() == "second"
        assert h.queue == [pending_config, "third", "fourth"]
        assert h._paused_wire() == [1]
        before_model = db.get_session(sid)["model"]
        assert h._take_next_turn() is None
        assert h.status == "idle" and h.queue == [pending_config, "third", "fourth"]
        assert db.get_session(sid)["model"] == before_model

        # Play resumes an idle front prompt immediately, applying its preceding
        # configuration first and leaving the rest of the queue in order.
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h.set_queue_paused(1, "third", False) == {"ok": True, "paused": False}
        assert started == ["third"]
        assert h.queue == ["fourth"] and h._paused_wire() == []
        assert db.get_session(sid)["model"] == "paused-model"

        # Removing a paused row clears its sidecar state. A kill instead moves
        # the prose to held, where resend/discard already supply the only state
        # transition and pause must not leak through.
        h.status = "idle"       # "fourth" has now reached the paused frontier
        h.queue.append("fifth")
        assert h.set_queue_paused(0, "fourth", True) == {"ok": True, "paused": True}
        assert h.unqueue(0, "fourth") == {"ok": True}
        assert started == ["third", "fifth"]
        assert h.queue == [] and h._paused_wire() == []
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


async def exercise_queue_pause_websocket(url: str, token: str, runner, db) -> None:
    """The authenticated session socket carries the additive pause contract."""
    sid = db.create_session("queue pause socket", "claude", "/tmp", "", "",
                            "blue", "auto")
    h = runner.hub(sid)
    h.status = "running"
    h.queue = ["socket queued prompt"]
    h._broadcast_queue()
    ws = None
    try:
        async with aiohttp.ClientSession() as http:
            ws = await http.ws_connect(
                url + "/api/ws/session/{}".format(sid),
                headers={"X-Puppy-Token": token})
            snapshot = await ws.receive_json(timeout=3)
            assert snapshot["queued"] == ["socket queued prompt"]
            assert snapshot["paused"] == []

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": True})
            paused = await ws.receive_json(timeout=3)
            assert paused["type"] == "queued" and paused["paused"] == [0], paused

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": "yes"})
            rejected = await ws.receive_json(timeout=3)
            assert rejected["type"] == "toast" and \
                "true or false" in rejected["text"], rejected
            await ws.close()
            ws = None
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
        self_test = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "self-test", "--data-dir",
            str(temp_root / "self-test-data"),
        ], text=True).splitlines()[-1])
        assert self_test["ok"] is True and self_test["version"] == __version__

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
        old_db = sqlite3.connect(str(controller_data / "puppy.db"))
        old_db.execute(
            "CREATE TABLE backends (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL, created_at REAL NOT NULL)")
        old_db.execute(
            "CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL DEFAULT '', engine TEXT NOT NULL, cwd TEXT NOT NULL, "
            "model TEXT NOT NULL DEFAULT '', effort TEXT NOT NULL DEFAULT '', "
            "color TEXT NOT NULL DEFAULT '', permission_mode TEXT NOT NULL DEFAULT '', "
            "native_session_id TEXT NOT NULL DEFAULT '', last_model TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'idle', archived INTEGER NOT NULL DEFAULT 0, "
            "sort_order INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, "
            "updated_at REAL NOT NULL)")
        old_db.execute(
            "INSERT INTO backends(name,url,token,created_at) VALUES(?,?,?,?)",
            ("legacy-node", "http://192.0.2.44:10888", "legacy-token", time.time()))
        old_db.commit()
        old_db.close()
        os.environ["PUPPY_DATA"] = str(controller_data)
        # config binds its data path at import time, so an earlier import of it
        # anywhere above would silently point this whole test at the real
        # instance's data directory. Fail loudly instead of writing there.
        assert "puppy.config" not in sys.modules, \
            "puppy.config was imported before the test data path was set"
        from puppy import config, db, host_metrics, runner, terminal
        from backend.puppy_backend import upgrade as backend_upgrade
        from puppy import web as puppy_web
        from puppy.web import build_app

        await check_notify_placeholders()
        await exercise_interrupted_notify_report(puppy_web)

        config.load()
        controller_token = "controller-test-token-0123456789abcdef"
        config.set_value("auth.api_token", controller_token)
        config.set_value("engines.usage_refresh_minutes", 0)
        db.connect()
        exercise_activity_blocks(runner.SessionHub)
        await exercise_shutdown_broadcast(runner)
        await exercise_queue_persistence(runner, db)
        await exercise_queue_pause(runner, db)
        exercise_session_show_meta(runner, db)
        await exercise_auth_probes(temp_root / "auth-probes")
        exercise_auth_evidence(temp_root / "auth-evidence", db)
        exercise_host_cpu_math(host_metrics)
        await exercise_upgrade_readiness(
            backend_upgrade, runner, terminal, temp_root / "readiness")
        assert "tls_fingerprint" in {
            row["name"] for row in db.query("PRAGMA table_info(backends)")}
        assert "auto_upgrade" in {
            row["name"] for row in db.query("PRAGMA table_info(backends)")}
        assert "urls" in {
            row["name"] for row in db.query("PRAGMA table_info(backends)")}
        migrated_urls = db.query_one(
            "SELECT urls FROM backends WHERE name='legacy-node'")["urls"]
        assert json.loads(migrated_urls) == ["http://192.0.2.44:10888"]
        db.execute("DELETE FROM backends WHERE name='legacy-node'")
        assert "workspace_kind" in {
            row["name"] for row in db.query("PRAGMA table_info(sessions)")}
        app = build_app()
        controller_runner = web.AppRunner(app)
        await controller_runner.setup()
        site = web.TCPSite(controller_runner, "127.0.0.1", 0)
        await site.start()
        sock = site._server.sockets[0]
        controller_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        await exercise_queue_pause_websocket(
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
