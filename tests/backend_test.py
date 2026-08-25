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
        assert "file-uploads" in ping["capabilities"]
        assert ping["uploads"]["enabled"] is \
            (ping["uploads"]["max_file_size_mb"] > 0)
        assert "terminal" not in ping["capabilities"]
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
        async with http.get(url + "/api/engines", headers=good, ssl=pinned) as response:
            engine_payload = await response.json()
            assert response.status == 200, engine_payload
        for engine in engine_payload["engines"]:
            assert isinstance(engine["latest_version"], str)
            assert engine["update_available"] in (True, False, None)
            assert engine["latest_checked_at"] is None or \
                isinstance(engine["latest_checked_at"], (int, float))
            assert isinstance(engine["latest_check_error"], str)
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
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", headers=headers) as response:
            full_ping = await response.json()
            assert response.status == 200
        assert full_ping["role"] == "full" and full_ping["protocol"] == 1
        assert "terminal" in full_ping["capabilities"]

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
                "name": "", "url": backend_url, "token": backend_token,
                "tls_fingerprint": backend_fingerprint,
                "auto_upgrade": False}) as response:
            added = await response.json()
            assert response.status == 200, added
        assert added["remote"]["role"] == "backend"
        assert added["remote"]["protocol"] == 1

        async with http.get(url + "/api/backends", headers=headers) as response:
            listed = (await response.json())["backends"]
            assert response.status == 200
        assert len(listed) == 1
        stored = listed[0]
        assert stored["name"] == "backend-test-node"
        assert stored["protocol"] == 1
        assert stored["role"] == "backend"
        assert "sessions" in stored["capabilities"]
        assert "temporary-workspaces" in stored["capabilities"]
        assert "engine-usage-refresh" in stored["capabilities"]
        assert "file-uploads" in stored["capabilities"]
        assert "terminal" not in stored["capabilities"]
        assert "remote-upgrade" in stored["capabilities"]
        assert "pinned-tls" in stored["capabilities"]
        assert stored["remote_version"] == old_version
        assert stored["tls_fingerprint"] == backend_fingerprint
        assert stored["auto_upgrade"] is False
        assert stored["upgrade_in_progress"] is False
        assert "token" not in stored

        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": "yes"}) as response:
            assert response.status == 400, await response.text()

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
        await remote_updates.close()

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
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": True}) as response:
            toggled = await response.json()
            assert response.status == 200, toggled
        assert toggled["backend"]["auto_upgrade"] is True

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
    """A dead upstream must fail its WS handshake, then recover on the same origin."""
    backend_token = "recovery-backend-token-0123456789abcdef"
    port = free_port()
    backend_url = f"http://127.0.0.1:{port}"

    async def start_backend() -> web.AppRunner:
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
    backend_runner = await start_backend()
    backend_id = None
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(controller_url + "/api/backends", headers=headers, json={
                    "name": "recovery-node", "url": backend_url, "token": backend_token,
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
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status in (502, 504), await response.text()
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("dead backend completed the proxied WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status in (502, 504), exc.status

            backend_runner = await start_backend()
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
        assert not any(name.startswith("puppy/static/") for name in names)
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
        stop_process(disabled_process)
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
        stop_process(process)
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
        old_db.commit()
        old_db.close()
        os.environ["PUPPY_DATA"] = str(controller_data)
        from puppy import config, db, host_metrics, runner, terminal
        from backend.puppy_backend import upgrade as backend_upgrade
        from puppy.web import build_app

        config.load()
        controller_token = "controller-test-token-0123456789abcdef"
        config.set_value("auth.api_token", controller_token)
        config.set_value("engines.usage_refresh_minutes", 0)
        db.connect()
        exercise_activity_blocks(runner.SessionHub)
        exercise_host_cpu_math(host_metrics)
        await exercise_upgrade_readiness(
            backend_upgrade, runner, terminal, temp_root / "readiness")
        assert "tls_fingerprint" in {
            row["name"] for row in db.query("PRAGMA table_info(backends)")}
        assert "auto_upgrade" in {
            row["name"] for row in db.query("PRAGMA table_info(backends)")}
        assert "workspace_kind" in {
            row["name"] for row in db.query("PRAGMA table_info(sessions)")}
        app = build_app()
        controller_runner = web.AppRunner(app)
        await controller_runner.setup()
        site = web.TCPSite(controller_runner, "127.0.0.1", 0)
        await site.start()
        sock = site._server.sockets[0]
        controller_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
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
