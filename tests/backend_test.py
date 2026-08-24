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
import zipfile

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def wait_for_backend(url: str, process: subprocess.Popen) -> None:
    async with aiohttp.ClientSession() as http:
        for _ in range(100):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"backend exited during startup:\n{output[-3000:]}")
            try:
                async with http.get(url + "/api/ping") as response:
                    if response.status == 401:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.05)
    raise AssertionError("backend did not start")


async def exercise_node(url: str, token: str) -> None:
    good = {"X-Puppy-Token": token}
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping") as response:
            assert response.status == 401
        async with http.get(url + "/api/ping",
                            headers={"X-Puppy-Token": "wrong-token-value"}) as response:
            assert response.status == 401
        async with http.get(url + "/api/ping", headers=good) as response:
            assert response.status == 200
            ping = await response.json()
        assert ping["role"] == "backend"
        assert ping["protocol"] == 1
        assert "sessions" in ping["capabilities"]
        assert "terminal" not in ping["capabilities"]
        assert "remote-upgrade" not in ping["capabilities"]
        assert ping["upgrade"]["supported"] is False
        assert ping["upgrade"]["api"] == "/api/node/upgrade"
        assert ping["build"]["artifact"] == "zipapp"

        async with http.get(url + "/api/node", headers=good) as response:
            assert response.status == 200
        async with http.get(url + "/api/sessions", headers=good) as response:
            assert response.status == 200
            assert (await response.json())["sessions"] == []
        updates = await http.ws_connect(url + "/api/ws/updates", headers=good)
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "sessions" and first["sessions"] == []
        await updates.close()
        for path in ("/", "/static/app.js", "/api/settings", "/api/auth/status",
                     "/api/ws/term", "/api/node/upgrade"):
            async with http.get(url + path, headers=good) as response:
                assert response.status == 404, (path, response.status)


async def exercise_controller(url: str, token: str, backend_url: str,
                              backend_token: str) -> None:
    headers = {"X-Puppy-Token": token}
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", headers=headers) as response:
            full_ping = await response.json()
            assert response.status == 200
        assert full_ping["role"] == "full" and full_ping["protocol"] == 1
        assert "terminal" in full_ping["capabilities"]

        async with http.post(url + "/api/backends", headers=headers, json={
                "name": "", "url": backend_url, "token": backend_token}) as response:
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
        assert "terminal" not in stored["capabilities"]
        assert "token" not in stored

        async with http.post(url + f"/api/backends/{stored['id']}/test",
                             headers=headers) as response:
            tested = await response.json()
            assert response.status == 200 and tested["ok"] is True, tested

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


async def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="puppy_backend_test_"))
    process = None
    controller_runner = None
    try:
        artifact = temp_root / "puppy-backend.pyz"
        subprocess.run([sys.executable, str(BASE / "backend" / "build.py"),
                        "--output", str(artifact)], cwd=str(BASE), check=True)
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
        assert any(name.startswith("puppy/drivers/") for name in names)
        assert not any(name.startswith("puppy/static/") for name in names)

        backend_data = temp_root / "backend-data"
        backend_port = free_port()
        backend_url = f"http://127.0.0.1:{backend_port}"
        backend_token = "backend-test-token-0123456789abcdef"
        pairing_raw = subprocess.check_output([
            str(artifact), "pairing", "--data-dir", str(backend_data),
            "--name", "backend-test-node", "--bind", "127.0.0.1",
            "--port", str(backend_port), "--advertise-url", backend_url,
            "--api-token", backend_token, "--disable-terminal",
        ], text=True)
        pairing = json.loads(pairing_raw)
        assert pairing["url"] == backend_url and pairing["token"] == backend_token
        assert "terminal" not in pairing["capabilities"]
        assert (backend_data / "config.json").stat().st_mode & 0o777 == 0o600

        enabled_pairing = json.loads(subprocess.check_output([
            str(artifact), "pairing", "--data-dir", str(temp_root / "enabled-data"),
            "--bind", "127.0.0.1", "--port", str(backend_port),
            "--api-token", backend_token,
        ], text=True))
        assert "terminal" in enabled_pairing["capabilities"]

        process = subprocess.Popen([
            str(artifact), "serve", "--data-dir", str(backend_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_for_backend(backend_url, process)
        await exercise_node(backend_url, backend_token)

        # Import the full application only after its independent data path is set.
        controller_data = temp_root / "controller-data"
        controller_data.mkdir()
        old_db = sqlite3.connect(str(controller_data / "puppy.db"))
        old_db.execute(
            "CREATE TABLE backends (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL, created_at REAL NOT NULL)")
        old_db.commit()
        old_db.close()
        os.environ["PUPPY_DATA"] = str(controller_data)
        from puppy import config, db
        from puppy.web import build_app

        config.load()
        controller_token = "controller-test-token-0123456789abcdef"
        config.set_value("auth.api_token", controller_token)
        db.connect()
        app = build_app()
        controller_runner = web.AppRunner(app)
        await controller_runner.setup()
        site = web.TCPSite(controller_runner, "127.0.0.1", 0)
        await site.start()
        sock = site._server.sockets[0]
        controller_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        await exercise_controller(controller_url, controller_token, backend_url, backend_token)
        print("backend package, auth, protocol, and controller pairing passed")
    finally:
        if controller_runner is not None:
            await controller_runner.cleanup()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
