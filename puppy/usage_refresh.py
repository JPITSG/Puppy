"""Rate-limited, token-free refresh of engine account-usage metadata."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from puppy import config
from puppy.drivers import all_drivers
from puppy.drivers import base as driver_base

log = logging.getLogger("puppy.usage_refresh")

_state = {
    "last_attempt_mono": 0.0,
    "last_completed_mono": 0.0,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": "",
}
_lock: Optional[asyncio.Lock] = None
_lock_loop = None


def minutes() -> int:
    return config.normalize_usage_refresh_minutes(
        config.get("engines.usage_refresh_minutes"))


def payload() -> dict:
    interval = minutes()
    return {
        "minutes": interval,
        "enabled": interval > 0,
        "last_attempt_at": _state["last_attempt_at"],
        "last_success_at": _state["last_success_at"],
        "last_error": _state["last_error"],
    }


def reset_due(clear_status: bool = False) -> None:
    """Make the next status read refresh immediately after a config change."""
    _state["last_attempt_mono"] = 0.0
    _state["last_completed_mono"] = 0.0
    if clear_status:
        _state["last_attempt_at"] = None
        _state["last_success_at"] = None
        _state["last_error"] = ""


def set_minutes(value) -> int:
    interval = config.normalize_usage_refresh_minutes(value)
    config.set_value("engines.usage_refresh_minutes", interval)
    reset_due(clear_status=True)
    return interval


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def _due(now: float, interval: int) -> bool:
    last = float(_state["last_attempt_mono"] or 0.0)
    return not last or now - last >= interval * 60


async def maybe_refresh(force: bool = False) -> dict:
    """Refresh supported drivers at most once per configured interval.

    Multiple browser/controller polls coalesce behind one process-local lock.
    Failed attempts are also rate-limited so missing auth or an older CLI cannot
    create a tight retry loop.
    """
    interval = minutes()
    if interval <= 0 and not force:
        return payload()
    requested_at = time.monotonic()
    if not force and not _due(requested_at, interval):
        return payload()

    async with _get_lock():
        now = time.monotonic()
        if _state["last_completed_mono"] >= requested_at or \
                _state["last_attempt_mono"] >= requested_at or \
                (not force and not _due(now, interval)):
            return payload()
        _state["last_attempt_mono"] = now
        _state["last_attempt_at"] = time.time()

        drivers = all_drivers()
        results = await asyncio.gather(
            *(driver.refresh_usage() for driver in drivers),
            return_exceptions=True)
        refreshed = False
        errors = []
        for driver, result in zip(drivers, results):
            if result is True:
                refreshed = True
                # an authenticated account read succeeded: any recorded auth
                # failure for this engine is stale
                driver_base.clear_auth_failure(driver.key)
            elif isinstance(result, BaseException):
                errors.append("{}: {}".format(driver.label, result))
                # the vendor's own API refusing the account read is the truth
                # about this login, whatever `login status` reads from disk
                if driver_base.looks_like_auth_failure(result):
                    driver_base.note_auth_failure(driver.key, str(result))
            elif result is False:
                errors.append("{}: refresh failed".format(driver.label))
        if refreshed:
            _state["last_success_at"] = time.time()
        _state["last_error"] = "; ".join(errors)[:500]
        if errors:
            log.warning("engine usage refresh failed: %s", _state["last_error"])
        _state["last_completed_mono"] = time.monotonic()
        return payload()
