#!/usr/bin/env python3
"""Fast tests for persisted timer validation and runtime interval consumers."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import sys
import time


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("timers-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import cli_releases, config, notify  # noqa: E402
from puppy.drivers import base  # noqa: E402
from puppy.drivers.base import Driver, ModelCatalog, ModelCatalogResult  # noqa: E402


class StatusDriver(Driver):
    key = "timer-status-test"
    label = "Timer status test"

    def __init__(self):
        self.probes = 0

    def resolved_binary(self) -> str:
        return "/fake/timer-status-test"

    async def _run_quick(self, argv, timeout: float = 5.0) -> str:
        self.probes += 1
        return "timer-status-test 1.0.0"

    async def _auth_status(self) -> dict:
        return {"auth": "ok", "detail": "signed in"}


async def main() -> None:
    try:
        config.load()
        assert config.timer_values() == config.TIMER_DEFAULTS
        assert config.timer_seconds("cli_release_minutes") == 6 * 60 * 60
        payload = config.timers_payload()
        assert payload["limits"]["remote_session_seconds"] == {
            "min": 2, "max": 300, "unit": "seconds"}

        updated = config.set_timers({
            "cli_release_minutes": 2,
            "model_catalog_minutes": 2,
            "cli_status_minutes": 1,
            "completion_sync_seconds": 7,
        })
        assert updated["remote_session_seconds"] == 12
        assert config.timer_seconds("model_catalog_minutes") == 120
        assert notify.poll_seconds() == 7
        before = config.timer_values()
        for invalid in ({}, {"remote_session_seconds": 1},
                        {"model_catalog_minutes": 2.5}, {"unknown": 4}):
            try:
                config.set_timers(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid timer patch was accepted: {}".format(invalid))
            assert config.timer_values() == before

        original_refresh = cli_releases._refresh

        async def succeeds(_drivers):
            return True

        async def fails(_drivers):
            return False

        try:
            cli_releases.reset_for_tests()
            cli_releases._refresh = succeeds
            assert await cli_releases.refresh_if_due([]) == 120
            cli_releases._next_due = 0
            cli_releases._refresh = fails
            assert await cli_releases.refresh_if_due([]) == 120
        finally:
            cli_releases._refresh = original_refresh
            cli_releases.reset_for_tests()

        catalog = ModelCatalog()
        catalog.succeeded(ModelCatalogResult([
            {"value": "", "label": "Default"},
            {"value": "new-model", "label": "New model"},
        ]), forced=False)
        remaining = catalog.next_due_mono - time.monotonic()
        assert 119 <= remaining <= 120, remaining
        config.set_timers({"model_catalog_minutes": 1})
        catalog.failures = 3
        catalog.failed(RuntimeError("temporary"), forced=False)
        retry = catalog.next_due_mono - time.monotonic()
        assert 59 <= retry <= 60, retry

        driver = StatusDriver()
        base.invalidate_status(driver.key)
        await driver.status()
        await driver.status()
        assert driver.probes == 1
        stamp, cached = base._status_cache[driver.key]
        base._status_cache[driver.key] = (stamp - 61, cached)
        await driver.status()
        assert driver.probes == 2

        print("timer settings tests passed")
    finally:
        base.invalidate_status("timer-status-test")
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
