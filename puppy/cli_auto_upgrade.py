"""Unattended scheduling for the engine-CLI upgrades puppy already performs.

This adds no upgrade machinery. It decides *when* to call the existing
``cli_upgrade.start`` and nothing else, so an automatic run is byte-for-byte the
run the Update button performs: driver-owned argv, one at a time, hard timeout,
version re-probe afterwards.

Two rules shape the schedule:

* One attempt per version pair. A pair is (installed version -> latest version).
  Once puppy has *run* the updater for a pair it never runs it again for that
  same pair, whether it worked or not. A pair only changes when one of the two
  versions moves, which is exactly "the next current version + new version".
  The ledger is durable, so a restart does not hand a failing updater another
  go. Being refused - busy sessions, another upgrade running, a backup in
  flight - is not an attempt and consumes nothing.

* A window, not a moment. "At 03:30" means the run may start in the two hours
  after 03:30, so a node busy at the stroke of the hour still gets its update
  that night, while one busy all window waits for tomorrow rather than
  surprising someone at midday.

The setting is per node, like the usage-refresh interval: the node that owns the
binaries owns the decision to replace them, and a controller edits a backend's
copy through the ordinary proxied route.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from puppy import cli_upgrade, config, db

log = logging.getLogger("puppy.cli_auto_upgrade")

# How often the scheduler re-evaluates. Cheap: it reads cached driver status and
# the release cache, and only reaches for a subprocess when a run is actually due.
CYCLE_SECONDS = 60.0
# How long after a scheduled time a run may still begin.
WINDOW_SECONDS = 2 * 60 * 60
AT_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
LEDGER_PREFIX = "engine_upgrade_attempt."

_task = None
_wake: Optional[asyncio.Event] = None


# ---- settings ----

def settings() -> dict:
    return config.normalize_engine_auto_upgrade(config.get("engines.auto_upgrade"))


def set_settings(value) -> dict:
    """Validate and persist. Raises ValueError on anything unusable."""
    if not isinstance(value, dict):
        raise ValueError("automatic engine updates must be an object")
    current = settings()
    # a PATCH may name one field; the rest keep what is stored
    merged = {**current, **{k: value[k] for k in ("enabled", "mode", "at") if k in value}}
    stored = config.normalize_engine_auto_upgrade(merged)
    config.set_value("engines.auto_upgrade", stored)
    wake()
    return stored


# ---- the attempt ledger ----

def _ledger(key: str) -> dict:
    record = db.meta_get(LEDGER_PREFIX + str(key))
    return record if isinstance(record, dict) else {}


def attempted(key: str, from_version: str, to_version: str) -> bool:
    """Has this exact version pair already had its one run?"""
    record = _ledger(key)
    return bool(record) and \
        str(record.get("from_version") or "") == str(from_version or "") and \
        str(record.get("to_version") or "") == str(to_version or "")


def _record(key: str, from_version: str, to_version: str, result: dict) -> None:
    db.meta_set(LEDGER_PREFIX + str(key), {
        "from_version": str(from_version or ""),
        "to_version": str(to_version or ""),
        "at": time.time(),
        "ok": bool(result.get("ok")) and bool(result.get("changed")),
        "error": str(result.get("error") or "")[:300],
        "installed_after": str(result.get("to_version") or ""),
    })


def forget(key: str) -> None:
    """Drop one engine's ledger so its current pair may be tried again."""
    db.meta_set(LEDGER_PREFIX + str(key), None)


# ---- schedule ----

def _window_open(now: float, at: str) -> bool:
    """True while within WINDOW_SECONDS after today's (or yesterday's) HH:MM."""
    match = AT_RE.match(at or "")
    if not match:
        return False
    hour, minute = int(match.group(1)), int(match.group(2))
    local = time.localtime(now)
    occurrence = time.mktime((
        local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0,
        local.tm_wday, local.tm_yday, -1))
    if occurrence > now:                       # today's slot has not arrived yet
        occurrence -= 24 * 60 * 60             # so measure from yesterday's
    return 0 <= now - occurrence < WINDOW_SECONDS


def due_now(config_value: dict = None, now: float = None) -> bool:
    """Is the clock permitting a run right now?"""
    cfg = config.normalize_engine_auto_upgrade(config_value) \
        if config_value is not None else settings()
    if not cfg["enabled"]:
        return False
    if cfg["mode"] == "now":
        return True
    return _window_open(time.time() if now is None else now, cfg["at"])


def payload() -> dict:
    """Additive settings block served beside the engines list."""
    cfg = settings()
    engines = {}
    for key in _driver_keys():
        record = _ledger(key)
        if record:
            engines[key] = record
    return {**cfg, "window_minutes": int(WINDOW_SECONDS // 60), "last_attempts": engines}


def _driver_keys() -> list:
    try:
        from puppy.drivers import all_drivers
        return [str(d.key) for d in all_drivers()]
    except Exception:
        return []


# ---- the worker ----

def _skip_reason(driver, status: dict) -> Optional[str]:
    """Why this engine is not a candidate right now, or None when it is."""
    if not cli_upgrade.supported(driver):
        return "no self-update verb"
    if not status.get("installed"):
        return "not installed"
    if status.get("update_available") is not True:
        return "no update available"
    if not str(status.get("latest_version") or ""):
        return "latest version unknown"
    return None


async def cycle(app=None) -> Optional[str]:
    """One evaluation pass. Returns the engine key it started, if any."""
    from puppy.drivers import all_drivers
    from puppy import runner

    if not settings()["enabled"] or not due_now():
        return None
    if app is not None and app.get("puppy_snapshot_busy"):
        return None
    if cli_upgrade.running_key() is not None:
        return None

    for driver in all_drivers():
        key = str(driver.key)
        try:
            status = await driver.status()
        except Exception as exc:
            log.debug("auto-upgrade status probe for %s failed: %s", key, exc)
            continue
        if _skip_reason(driver, status):
            continue
        from_version = str(status.get("version") or "")
        to_version = str(status.get("latest_version") or "")
        if attempted(key, from_version, to_version):
            continue
        # Refusals are not attempts: the pair keeps its one run for later.
        if runner.engine_blockers(key):
            continue
        try:
            await cli_upgrade.start(driver)
        except RuntimeError as exc:
            log.info("automatic %s update deferred: %s", key, exc)
            return None
        log.info("automatic %s update started: %s -> %s",
                 key, from_version or "?", to_version or "?")
        record = await _await_result(driver)
        _record(key, from_version, to_version, record or {})
        if record and record.get("ok") and record.get("changed"):
            log.info("automatic %s update installed %s", key, record.get("to_version"))
        else:
            log.warning("automatic %s update did not take (%s); it will not be retried "
                        "until a new version pair appears", key,
                        (record or {}).get("error") or "version unchanged")
        return key
    return None


async def _await_result(driver, timeout: float = None) -> Optional[dict]:
    """Wait for the run this cycle started and hand back its result record."""
    limit = (timeout or cli_upgrade.TIMEOUT_SECONDS) + 60
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        state = cli_upgrade.state(driver)
        if state.get("upgrade_state") != "running":
            return state.get("upgrade_result")
        await asyncio.sleep(1.0)
    return None


def wake() -> None:
    if _wake is not None:
        _wake.set()


async def _loop(app) -> None:
    while True:
        if _wake is not None:
            _wake.clear()
        try:
            await cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automatic engine update scan failed")
        try:
            if _wake is None:
                await asyncio.sleep(CYCLE_SECONDS)
            else:
                await asyncio.wait_for(_wake.wait(), timeout=CYCLE_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _lifecycle(app):
    global _task, _wake
    _wake = asyncio.Event()
    _task = asyncio.create_task(_loop(app), name="puppy-engine-auto-upgrade")
    try:
        yield
    finally:
        task, _task = _task, None
        _wake = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def register(app) -> None:
    """Attach the scheduler to an aiohttp application (both runtimes)."""
    if app.get("puppy_engine_auto_upgrade_registered"):
        return
    app["puppy_engine_auto_upgrade_registered"] = True
    app.cleanup_ctx.append(_lifecycle)


def reset_for_tests() -> None:
    global _task, _wake
    _task = None
    _wake = None
