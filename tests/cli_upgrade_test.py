#!/usr/bin/env python3
"""No-quota tests for engine CLI version refresh and vendor-delegated upgrades.

A stub binary stands in for the vendor updater, so this exercises the whole
route -> idle gate -> subprocess -> re-probe path without installing anything,
invoking a real engine, or spending subscription quota.
"""
from __future__ import annotations

import asyncio
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
TEST_ROOT = Path(tempfile.mkdtemp(prefix="cli-upgrade-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import auth, cli_releases, cli_upgrade, config, db, drivers  # noqa: E402
from puppy import runner as session_runner  # noqa: E402
from puppy.drivers import base as driver_base  # noqa: E402
from puppy.drivers.base import Driver  # noqa: E402
from puppy.web import build_app  # noqa: E402

STUB_BIN = TEST_ROOT / "bin"
VERSION_FILE = TEST_ROOT / "installed-version"
BEHAVIOUR_FILE = TEST_ROOT / "behaviour"

STUB = """#!/bin/sh
if [ "$1" = "--version" ]; then cat "{version}"; exit 0; fi
if [ "$1" = "update" ]; then
  mode=$(cat "{behaviour}")
  case "$mode" in
    ok)     echo "2.0.0" > "{version}"; echo "updated to 2.0.0"; exit 0;;
    noop)   echo "already on the latest version"; exit 0;;
    fail)   echo "npm ERR! EACCES: permission denied" 1>&2; exit 7;;
    hang)   sleep 30; exit 0;;
  esac
fi
exit 3
"""


class StubDriver(Driver):
    key = "stub"
    label = "Stub Engine"
    binary = "puppy-stub-engine"
    upgrade_source = {"kind": "self", "args": ["update"]}

    async def _auth_status(self):
        return {"auth": "ok", "detail": ""}


class BareDriver(StubDriver):
    key = "bare"
    label = "Bare Engine"
    upgrade_source = None


def write_stub() -> None:
    STUB_BIN.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
    BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
    path = STUB_BIN / StubDriver.binary
    path.write_text(STUB.format(version=VERSION_FILE, behaviour=BEHAVIOUR_FILE),
                    encoding="utf-8")
    path.chmod(0o755)
    os.environ["PATH"] = "{}{}{}".format(STUB_BIN, os.pathsep, os.environ.get("PATH", ""))


def check_descriptor_validation() -> None:
    """argv is driver-owned and fixed; nothing shell-shaped may reach a spawn."""
    class Candidate(StubDriver):
        pass

    assert cli_upgrade.supported(StubDriver())
    assert not cli_upgrade.supported(BareDriver())
    for bad in ({"kind": "shell", "args": ["update"]},
                {"kind": "self", "args": []},
                {"kind": "self", "args": ["update; rm -rf /"]},
                {"kind": "self", "args": ["--flag=$(id)"]},
                {"kind": "self", "args": ["up date"]},
                {"kind": "self", "args": [1]},
                {"kind": "self", "args": "update"},
                "update", None):
        Candidate.upgrade_source = bad
        assert not cli_upgrade.supported(Candidate()), bad
    Candidate.upgrade_source = {"kind": "self", "args": ["update", "--yes"]}
    assert cli_upgrade.supported(Candidate())

    for driver in drivers.all_drivers():
        # The shipped engines delegate to their own updater and nothing else.
        assert driver.upgrade_source == {"kind": "self", "args": ["update"]}, driver.key
        assert cli_upgrade.supported(driver), driver.key


async def wait_idle(http, url, headers, key: str, timeout: float = 30.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with http.get(url + "/api/engines", headers=headers) as response:
            payload = await response.json()
            assert response.status == 200, payload
        engine = next(e for e in payload["engines"] if e["key"] == key)
        if engine["upgrade_state"] == "idle":
            return engine
        assert asyncio.get_event_loop().time() < deadline, "upgrade never finished"
        await asyncio.sleep(0.2)


async def exercise_http() -> None:
    token = config.get("auth.api_token")
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": token}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url + "/api/engines", headers=headers) as response:
                payload = await response.json()
                assert response.status == 200, payload
            engines = {e["key"]: e for e in payload["engines"]}
            assert engines["stub"]["version"] == "1.0.0"
            assert engines["stub"]["upgrade_supported"] is True
            assert engines["stub"]["upgrade_state"] == "idle"
            assert engines["stub"]["upgrade_result"] is None
            assert engines["bare"]["upgrade_supported"] is False
            assert engines["stub"]["version_checked_at"] > 0

            async with http.post(url + "/api/engines/nope/upgrade", headers=headers) as response:
                assert response.status == 404, await response.text()
            async with http.post(url + "/api/engines/bare/upgrade", headers=headers) as response:
                refused = await response.json()
                assert response.status == 400, refused
                assert "cannot be upgraded" in refused["error"]

            # Idle gate: queued work on this engine must block the rewrite,
            # while another engine's busy session must not.
            other = db.create_session("busy elsewhere", "bare", str(TEST_ROOT),
                                      "", "", "blue", "auto")
            other_hub = session_runner.hub(other)
            other_hub.queue.append("still waiting")
            busy = db.create_session("busy stub", "stub", str(TEST_ROOT),
                                     "", "", "blue", "auto")
            busy_hub = session_runner.hub(busy)
            busy_hub.queue.append("queued behind the upgrade")
            async with http.post(url + "/api/engines/stub/upgrade", headers=headers) as response:
                blocked = await response.json()
                assert response.status == 409, blocked
                assert "busy" in blocked["error"]
                assert blocked["blockers"][0]["id"] == busy
            busy_hub.queue.clear()

            async with http.post(url + "/api/engines/stub/upgrade", headers=headers) as response:
                started = await response.json()
                assert response.status == 200, started
                running = next(e for e in started["engines"] if e["key"] == "stub")
                assert running["upgrade_state"] == "running"
            # A second request while one runs is refused, not queued.
            async with http.post(url + "/api/engines/stub/upgrade", headers=headers) as response:
                clash = await response.json()
                assert response.status == 409, clash
                assert "already running" in clash["error"]

            engine = await wait_idle(http, url, headers, "stub")
            result = engine["upgrade_result"]
            assert result["ok"] is True and result["changed"] is True, result
            assert result["from_version"] == "1.0.0" and result["to_version"] == "2.0.0"
            assert result["exit_code"] == 0
            # The status cache is invalidated by the run itself, so the version
            # pill moves without waiting out the five-minute probe cache.
            assert engine["version"] == "2.0.0"

            BEHAVIOUR_FILE.write_text("noop", encoding="utf-8")
            async with http.post(url + "/api/engines/stub/upgrade", headers=headers) as response:
                assert response.status == 200, await response.text()
            engine = await wait_idle(http, url, headers, "stub")
            result = engine["upgrade_result"]
            # Exit status alone is not proof of an upgrade.
            assert result["ok"] is True and result["changed"] is False, result
            assert result["message"] == "already on the latest version"

            BEHAVIOUR_FILE.write_text("fail", encoding="utf-8")
            async with http.post(url + "/api/engines/stub/upgrade", headers=headers) as response:
                assert response.status == 200, await response.text()
            engine = await wait_idle(http, url, headers, "stub")
            result = engine["upgrade_result"]
            assert result["ok"] is False and result["exit_code"] == 7, result
            assert "EACCES" in result["error"] and "EACCES" in result["output"]
            assert engine["version"] == "2.0.0"

            # Forced re-check picks up a change made outside puppy immediately.
            VERSION_FILE.write_text("3.0.0\n", encoding="utf-8")
            async with http.get(url + "/api/engines", headers=headers) as response:
                cached = await response.json()
            assert next(e for e in cached["engines"]
                        if e["key"] == "stub")["version"] == "2.0.0"
            async with http.post(url + "/api/engines/refresh", headers=headers) as response:
                refreshed = await response.json()
                assert response.status == 200, refreshed
            assert "usage_refresh" in refreshed
            assert next(e for e in refreshed["engines"]
                        if e["key"] == "stub")["version"] == "3.0.0"
    finally:
        await runner.cleanup()


async def check_timeout() -> None:
    """A wedged updater is killed and reported, never left holding the lock."""
    BEHAVIOUR_FILE.write_text("hang", encoding="utf-8")
    driver = drivers.get_driver("stub")
    original = cli_upgrade.TIMEOUT_SECONDS
    cli_upgrade.TIMEOUT_SECONDS = 1
    try:
        await cli_upgrade.start(driver)
        for _ in range(200):
            if cli_upgrade.state(driver)["upgrade_state"] == "idle":
                break
            await asyncio.sleep(0.1)
        state = cli_upgrade.state(driver)
        assert state["upgrade_state"] == "idle", state
        assert state["upgrade_result"]["ok"] is False
        assert "timed out" in state["upgrade_result"]["error"], state
        assert cli_upgrade.running_key() is None
    finally:
        cli_upgrade.TIMEOUT_SECONDS = original
        BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")


async def main() -> None:
    try:
        write_stub()
        check_descriptor_validation()
        # Only the stubs from here on: the shipped engines would probe real
        # binaries and the real registry, which this test must not depend on.
        drivers._DRIVERS = {"stub": StubDriver(), "bare": BareDriver()}
        config.load()
        db.connect()
        auth.create_user("upgrade-user", "secret123")
        # Keep the registry out of it: this test must never touch the network.
        cli_releases.CHECK_INTERVAL_SECONDS = 10 ** 9
        driver_base.invalidate_status()
        cli_upgrade.reset_for_tests()
        await exercise_http()
        await check_timeout()
        print("engine version refresh, idle gate, upgrade and failure paths passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
