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
import time

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="cli-upgrade-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import auth, cli_auto_upgrade, cli_releases, cli_upgrade  # noqa: E402
from puppy import config, db, drivers  # noqa: E402
from puppy import runner as session_runner  # noqa: E402
from puppy.drivers import base as driver_base  # noqa: E402
from puppy.drivers.base import Driver  # noqa: E402
from puppy.web import build_app  # noqa: E402

STUB_BIN = TEST_ROOT / "bin"
VERSION_FILE = TEST_ROOT / "installed-version"
BEHAVIOUR_FILE = TEST_ROOT / "behaviour"
STARTED_FILE = TEST_ROOT / "started"
RELEASE_FILE = TEST_ROOT / "release"
OTHER_VERSION_FILE = TEST_ROOT / "other-installed-version"
OTHER_BEHAVIOUR_FILE = TEST_ROOT / "other-behaviour"
OTHER_STARTED_FILE = TEST_ROOT / "other-started"
OTHER_RELEASE_FILE = TEST_ROOT / "other-release"

STUB = """#!/bin/sh
if [ "$1" = "--version" ]; then cat "{version}"; exit 0; fi
if [ "$1" = "update" ]; then
  mode=$(cat "{behaviour}")
  case "$mode" in
    ok)     echo "2.0.0" > "{version}"; echo "updated to 2.0.0"; exit 0;;
    noop)   echo "already on the latest version"; exit 0;;
    fail)   echo "npm ERR! EACCES: permission denied" 1>&2; exit 7;;
    hang)   sleep 30; exit 0;;
    stall)  echo "resolving latest release..."; sleep 30; exit 0;;
    chatty) i=0
            while [ "$i" -lt 8 ]; do echo "step $i"; sleep 0.4; i=$((i + 1)); done
            echo "2.0.0" > "{version}"; echo "updated to 2.0.0"; exit 0;;
    hold)   : > "{started}"
            i=0
            while [ ! -f "{release}" ] && [ "$i" -lt 300 ]; do
              sleep 0.1
              i=$((i + 1))
            done
            [ -f "{release}" ] || exit 8
            echo "2.0.0" > "{version}"
            echo "updated to 2.0.0"
            exit 0;;
  esac
fi
exit 3
"""


class StubDriver(Driver):
    key = "stub"
    label = "Stub Engine"
    binary = "puppy-stub-engine"
    upgrade_source = {"kind": "self", "args": ["update"]}
    # the scheduler only acts on a known latest version; the cache is seeded
    # by hand below so this never reaches a registry
    release_source = {"kind": "npm", "package": "stub-cli"}

    async def _auth_status(self):
        return {"auth": "ok", "detail": ""}


class BareDriver(StubDriver):
    key = "bare"
    label = "Bare Engine"
    upgrade_source = None


class OtherStubDriver(StubDriver):
    key = "other"
    label = "Other Stub Engine"
    binary = "puppy-other-stub-engine"
    release_source = {"kind": "npm", "package": "other-stub-cli"}


def _write_stub_binary(binary: str, version: Path, behaviour: Path,
                       started: Path, release: Path) -> None:
    version.write_text("1.0.0\n", encoding="utf-8")
    behaviour.write_text("ok", encoding="utf-8")
    path = STUB_BIN / binary
    path.write_text(STUB.format(
        version=version, behaviour=behaviour, started=started, release=release),
        encoding="utf-8")
    path.chmod(0o755)


def write_stub() -> None:
    STUB_BIN.mkdir(parents=True, exist_ok=True)
    _write_stub_binary(StubDriver.binary, VERSION_FILE, BEHAVIOUR_FILE,
                       STARTED_FILE, RELEASE_FILE)
    _write_stub_binary(OtherStubDriver.binary, OTHER_VERSION_FILE, OTHER_BEHAVIOUR_FILE,
                       OTHER_STARTED_FILE, OTHER_RELEASE_FILE)
    os.environ["PATH"] = "{}{}{}".format(STUB_BIN, os.pathsep, os.environ.get("PATH", ""))


async def wait_file(path: Path, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not path.exists():
        assert asyncio.get_event_loop().time() < deadline, "{} was never created".format(path)
        await asyncio.sleep(0.05)


async def no_registry_refresh(_drivers) -> bool:
    """The upgrade re-probe is exercised without contacting a real registry."""
    return True


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

    expected_updaters = {
        "claude": ["update"], "codex": ["update"], "opencode": ["upgrade"],
    }
    for driver in drivers.all_drivers():
        # The shipped engines delegate to their own updater and nothing else.
        assert driver.upgrade_source == {
            "kind": "self", "args": expected_updaters[driver.key]}, driver.key
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

            # Two different engine routes own independent slots. Hold both
            # real subprocesses open until their marker files prove overlap.
            VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
            OTHER_VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
            BEHAVIOUR_FILE.write_text("hold", encoding="utf-8")
            OTHER_BEHAVIOUR_FILE.write_text("hold", encoding="utf-8")
            for path in (STARTED_FILE, RELEASE_FILE, OTHER_STARTED_FILE,
                         OTHER_RELEASE_FILE):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            driver_base.invalidate_status()
            try:
                async with http.post(
                        url + "/api/engines/stub/upgrade", headers=headers) as response:
                    assert response.status == 200, await response.text()
                async with http.post(
                        url + "/api/engines/other/upgrade", headers=headers) as response:
                    assert response.status == 200, await response.text()
                await asyncio.gather(wait_file(STARTED_FILE), wait_file(OTHER_STARTED_FILE))
                assert cli_upgrade.running_keys() == ["other", "stub"]

                # Same-engine deduplication remains strict while the other
                # engine's updater is allowed to keep running beside it.
                async with http.post(
                        url + "/api/engines/stub/upgrade", headers=headers) as response:
                    clash = await response.json()
                    assert response.status == 409, clash
                    assert "already running" in clash["error"]

                # Once claimed, the slot also prevents a new turn from
                # spawning that engine midway through its package rewrite.
                guarded = db.create_session(
                    "upgrade guard", "stub", str(TEST_ROOT), "", "", "blue", "auto")
                async with http.post(
                        url + "/api/sessions/{}/message".format(guarded), headers=headers,
                        json={"text": "do not start yet"}) as response:
                    rejected = await response.json()
                    assert response.status == 400, rejected
                    assert "being updated" in rejected["error"]
            finally:
                RELEASE_FILE.write_text("go", encoding="utf-8")
                OTHER_RELEASE_FILE.write_text("go", encoding="utf-8")

            first, second = await asyncio.gather(
                wait_idle(http, url, headers, "stub"),
                wait_idle(http, url, headers, "other"))
            assert first["upgrade_result"]["changed"] is True, first
            assert second["upgrade_result"]["changed"] is True, second
            assert cli_upgrade.running_keys() == []
            BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
            OTHER_BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
    finally:
        await runner.cleanup()


async def check_idle() -> None:
    """An updater that goes quiet and still is stopped long before the hard
    cap, keeping what it said; one that keeps talking is left alone."""
    driver = drivers.get_driver("stub")
    original = (cli_upgrade.IDLE_SECONDS, cli_upgrade.SAMPLE_INTERVAL,
                cli_upgrade.TIMEOUT_SECONDS)
    cli_upgrade.IDLE_SECONDS = 1.5
    cli_upgrade.SAMPLE_INTERVAL = 0.25
    cli_upgrade.TIMEOUT_SECONDS = 60
    try:
        BEHAVIOUR_FILE.write_text("stall", encoding="utf-8")
        started = asyncio.get_event_loop().time()
        await cli_upgrade.start(driver)
        for _ in range(400):
            if cli_upgrade.state(driver)["upgrade_state"] == "idle":
                break
            await asyncio.sleep(0.05)
        elapsed = asyncio.get_event_loop().time() - started
        state = cli_upgrade.state(driver)
        assert state["upgrade_state"] == "idle", state
        result = state["upgrade_result"]
        assert result["ok"] is False and "no sign of progress" in result["error"], result
        assert "resolving latest release" in result["output"], result
        assert elapsed < 10, elapsed
        assert cli_upgrade.running_keys() == []

        BEHAVIOUR_FILE.write_text("chatty", encoding="utf-8")
        VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
        from puppy.drivers.base import invalidate_status
        invalidate_status("stub")
        await cli_upgrade.start(driver)
        for _ in range(400):
            if cli_upgrade.state(driver)["upgrade_state"] == "idle":
                break
            await asyncio.sleep(0.05)
        result = cli_upgrade.state(driver)["upgrade_result"]
        assert result["ok"] is True and result["changed"] is True, result
        assert result["output"].count("step ") == 8, result
    finally:
        (cli_upgrade.IDLE_SECONDS, cli_upgrade.SAMPLE_INTERVAL,
         cli_upgrade.TIMEOUT_SECONDS) = original
        BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
        VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
        from puppy.drivers.base import invalidate_status
        invalidate_status("stub")


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
        assert cli_upgrade.running_keys() == []
    finally:
        cli_upgrade.TIMEOUT_SECONDS = original
        BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")


def seed_latest(version: str, key: str = "stub", package: str = "stub-cli") -> None:
    """Publish a latest version for the stub without touching the network."""
    cli_releases._cache[key] = {
        "source": "npm:" + package, "latest_version": version,
        "checked_at": time.time(), "error": "",
    }


async def check_schedule() -> None:
    """The clock half of the scheduler: off, immediate, and the timed window."""
    assert cli_auto_upgrade.settings() == {"enabled": False, "mode": "now", "at": "03:30"}
    assert cli_auto_upgrade.due_now() is False, "off must never be due"
    cli_auto_upgrade.set_settings({"enabled": True, "mode": "now"})
    assert cli_auto_upgrade.due_now() is True

    now = time.time()

    def at(minutes: int) -> str:
        stamp = time.localtime(now + minutes * 60)
        return "%02d:%02d" % (stamp.tm_hour, stamp.tm_min)

    cli_auto_upgrade.set_settings({"mode": "at", "at": at(-5)})
    assert cli_auto_upgrade.due_now() is True, "inside the window"
    cli_auto_upgrade.set_settings({"at": at(-(cli_auto_upgrade.WINDOW_SECONDS // 60) - 5)})
    assert cli_auto_upgrade.due_now() is False, "past the window: wait for tomorrow"
    cli_auto_upgrade.set_settings({"at": at(30)})
    assert cli_auto_upgrade.due_now() is False, "before the time"
    for bad in ({"mode": "whenever"}, {"at": "24:00"}, {"at": "3:30"}, {"enabled": "yes"}):
        try:
            cli_auto_upgrade.set_settings(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted an invalid schedule: {}".format(bad))


async def check_attempt_once() -> None:
    """One run per version pair, and a refusal is not a run."""
    stub = drivers.get_driver("stub")
    VERSION_FILE.write_text("1.0.0\n")
    BEHAVIOUR_FILE.write_text("fail")
    driver_base.invalidate_status()
    cli_upgrade.reset_for_tests()
    cli_auto_upgrade.forget("stub")
    cli_auto_upgrade.set_settings({"enabled": True, "mode": "now"})
    seed_latest("2.0.0")
    # Keep the second test engine current so this test continues to isolate
    # the original single-engine attempt-ledger behavior.
    OTHER_VERSION_FILE.write_text("2.0.0\n")
    seed_latest("2.0.0", "other", "other-stub-cli")

    # A busy engine defers without spending the pair's single attempt.
    busy = db.create_session("busy stub", "stub", str(TEST_ROOT), "", "", "blue", "auto")
    session_runner.hub(busy).queue.append("queued")
    assert await cli_auto_upgrade.cycle(None) is None, "a busy engine must not be upgraded"
    assert not cli_auto_upgrade.attempted("stub", "1.0.0", "2.0.0"), \
        "being refused must not consume the attempt"
    session_runner.hub(busy).queue.clear()

    # The one attempt: the stub updater fails, so the pair is spent.
    assert await cli_auto_upgrade.cycle(None) == "stub"
    assert cli_auto_upgrade.attempted("stub", "1.0.0", "2.0.0")
    record = cli_auto_upgrade.payload()["last_attempts"]["stub"]
    assert record["ok"] is False and record["error"], record

    # Same pair, still failing: no second run.
    driver_base.invalidate_status()
    assert await cli_auto_upgrade.cycle(None) is None, "a spent pair must not be retried"

    # A newer release is a new pair, so it earns its own attempt - this time
    # the updater works, and the ledger records the version that landed.
    BEHAVIOUR_FILE.write_text("ok")
    cli_auto_upgrade.forget("stub")
    driver_base.invalidate_status()
    assert await cli_auto_upgrade.cycle(None) == "stub"
    record = cli_auto_upgrade.payload()["last_attempts"]["stub"]
    assert record["ok"] is True and record["installed_after"] == "2.0.0", record

    # Nothing left to do once installed and latest agree.
    driver_base.invalidate_status()
    assert await cli_auto_upgrade.cycle(None) is None
    cli_auto_upgrade.set_settings({"enabled": False})


async def check_concurrent_automatic_updates() -> None:
    """One scheduler pass starts all eligible vendor updaters before waiting."""
    stub = drivers.get_driver("stub")
    other = drivers.get_driver("other")
    VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
    OTHER_VERSION_FILE.write_text("1.0.0\n", encoding="utf-8")
    BEHAVIOUR_FILE.write_text("hold", encoding="utf-8")
    OTHER_BEHAVIOUR_FILE.write_text("hold", encoding="utf-8")
    for path in (STARTED_FILE, RELEASE_FILE, OTHER_STARTED_FILE, OTHER_RELEASE_FILE):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    driver_base.invalidate_status()
    cli_upgrade.reset_for_tests()
    cli_auto_upgrade.forget("stub")
    cli_auto_upgrade.forget("other")
    cli_auto_upgrade.set_settings({"enabled": True, "mode": "now"})
    seed_latest("2.0.0")
    seed_latest("2.0.0", "other", "other-stub-cli")

    task = asyncio.create_task(cli_auto_upgrade.cycle(None))
    result = None
    try:
        await asyncio.gather(wait_file(STARTED_FILE), wait_file(OTHER_STARTED_FILE))
        assert cli_upgrade.running_keys() == ["other", "stub"]
        assert cli_upgrade.state(stub)["upgrade_state"] == "running"
        assert cli_upgrade.state(other)["upgrade_state"] == "running"
        # Accepted starts reserve both pairs before either subprocess is
        # released, so a process restart cannot grant a duplicate attempt.
        for key in ("stub", "other"):
            assert cli_auto_upgrade.attempted(key, "1.0.0", "2.0.0")
            pending = cli_auto_upgrade.payload()["last_attempts"][key]
            assert pending["ok"] is False
            assert "final result was not recorded" in pending["error"]
    finally:
        RELEASE_FILE.write_text("go", encoding="utf-8")
        OTHER_RELEASE_FILE.write_text("go", encoding="utf-8")
        result = await asyncio.wait_for(task, timeout=15)

    assert result == "stub"
    assert cli_upgrade.running_keys() == []
    assert cli_auto_upgrade.attempted("stub", "1.0.0", "2.0.0")
    assert cli_auto_upgrade.attempted("other", "1.0.0", "2.0.0")
    for key in ("stub", "other"):
        record = cli_auto_upgrade.payload()["last_attempts"][key]
        assert record["ok"] is True and record["installed_after"] == "2.0.0", record
    BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
    OTHER_BEHAVIOUR_FILE.write_text("ok", encoding="utf-8")
    cli_auto_upgrade.set_settings({"enabled": False})


async def main() -> None:
    try:
        write_stub()
        check_descriptor_validation()
        # Only the stubs from here on: the shipped engines would probe real
        # binaries and the real registry, which this test must not depend on.
        drivers._DRIVERS = {
            "stub": StubDriver(), "bare": BareDriver(), "other": OtherStubDriver(),
        }
        config.load()
        db.connect()
        auth.create_user("upgrade-user", "secret123")
        # Keep the registry out of it: this test must never touch the network.
        cli_releases.CHECK_INTERVAL_SECONDS = 10 ** 9
        cli_releases._refresh = no_registry_refresh
        driver_base.invalidate_status()
        cli_upgrade.reset_for_tests()
        await exercise_http()
        await check_timeout()
        await check_idle()
        await check_schedule()
        await check_attempt_once()
        await check_concurrent_automatic_updates()
        print("engine version refresh, idle gate, concurrent manual/automatic upgrades, "
              "schedule and attempt-once paths passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
