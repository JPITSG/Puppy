"""Vendor-delegated upgrades of the engine CLIs puppy drives.

Puppy deliberately does not detect how an engine was installed. Each vendor CLI
already ships its own updater that knows npm / native / brew / standalone
layouts, and that detection matrix moves with the CLI. A driver therefore
declares only a fixed, argument-free self-update verb and this module owns the
bounded subprocess around it: driver-owned argv (never client input), no shell,
one run at a time, a hard timeout, and a capped transcript.

The run is a detached background task. Callers start it and observe progress
through the engine payload, which keeps the HTTP request short enough to survive
the controller proxy and lets a reloaded browser rejoin an upgrade already in
flight. The engine binary is spawned per turn, so nothing needs restarting
afterwards - only the cached version probe is invalidated.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import time
from typing import Dict, List, Optional

from puppy import cli_releases

log = logging.getLogger("puppy.cli_upgrade")

TIMEOUT_SECONDS = 15 * 60
MAX_OUTPUT_CHARS = 8000
# An engine updater usually shells out to a package manager writing into one
# shared global prefix, so upgrades are serialized across every engine.
_ARG_RE = re.compile(r"^-{0,2}[A-Za-z0-9][A-Za-z0-9._=-]*$")

_runs: Dict[str, dict] = {}
_running_key: Optional[str] = None


def _source(driver) -> Optional[List[str]]:
    """Validated fixed arguments for a driver's own updater, else None."""
    value = getattr(driver, "upgrade_source", None)
    if not isinstance(value, dict):
        return None
    if str(value.get("kind") or "").strip().lower() != "self":
        return None
    args = value.get("args")
    if not isinstance(args, (list, tuple)) or not args or len(args) > 6:
        return None
    out = []
    for arg in args:
        if not isinstance(arg, str) or len(arg) > 64 or not _ARG_RE.match(arg):
            return None
        out.append(arg)
    return out


def supported(driver) -> bool:
    """True when this engine declares a self-update verb puppy can run."""
    return _source(driver) is not None


def _argv(driver) -> Optional[List[str]]:
    args = _source(driver)
    if args is None:
        return None
    binary = shutil.which(driver.binary)
    if not binary:
        return None
    return [binary] + args


def running_key() -> Optional[str]:
    return _running_key


def state(driver) -> dict:
    """Additive upgrade fields merged into one engine's status payload."""
    record = _runs.get(str(driver.key)) or {}
    return {
        "upgrade_supported": supported(driver),
        "upgrade_state": "running" if record.get("state") == "running" else "idle",
        "upgrade_result": record.get("result"),
    }


def _tail(text: str) -> str:
    text = str(text or "").strip()
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return "…\n" + text[-MAX_OUTPUT_CHARS:]


def _last_line(text: str) -> str:
    for line in reversed(str(text or "").strip().splitlines()):
        if line.strip():
            return line.strip()[:200]
    return ""


async def _spawn(argv: List[str]) -> tuple:
    """Run the updater to completion and return (exit_code, combined output)."""
    # Imported here on purpose: drivers import this module, and config binds its
    # data path at import time, so pulling either in at module scope would fix
    # that path before an embedding process (or a test) has chosen it.
    from puppy import config
    from puppy.drivers.base import clean_env

    # Same environment the drivers spawn turns in (minus puppy's own nesting
    # markers), so the updater sees the installation puppy actually uses.
    env = clean_env(dict(os.environ))
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=config.DATA_DIR, env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # Its own process group: a package manager rewriting a global prefix
        # must not be torn down halfway by puppy's own shutdown signalling.
        start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        for sig in (signal.SIGINT, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except Exception:
                break
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                continue
        raise RuntimeError(
            "updater timed out after {} minutes".format(TIMEOUT_SECONDS // 60))
    return proc.returncode, out.decode(errors="replace") if out else ""


async def _run(driver, argv: List[str], from_version: str) -> None:
    global _running_key
    key = str(driver.key)
    started_at = time.time()
    exit_code = None
    output = ""
    error = ""
    try:
        exit_code, output = await _spawn(argv)
        if exit_code != 0:
            error = _last_line(output) or "updater exited with status {}".format(exit_code)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = str(exc)[:300] or exc.__class__.__name__

    # Re-probe rather than trust the exit code alone: a vendor updater that
    # reports success without moving the version is inconclusive, not done.
    to_version = from_version
    try:
        from puppy.drivers.base import invalidate_status

        invalidate_status(key)
        status = await driver.status()
        to_version = str(status.get("version") or "")
    except Exception as exc:
        log.warning("%s version re-probe after upgrade failed: %s", key, exc)
    try:
        await cli_releases.refresh_if_due([driver], force=True)
    except Exception as exc:
        log.warning("%s latest-version refresh after upgrade failed: %s", key, exc)

    record = _runs.setdefault(key, {})
    record["state"] = "idle"
    record["result"] = {
        "ok": not error,
        "changed": bool(to_version) and to_version != from_version,
        "exit_code": exit_code,
        "error": error,
        "from_version": from_version,
        "to_version": to_version,
        "started_at": started_at,
        "finished_at": time.time(),
        "message": _last_line(output),
        "output": _tail(output),
    }
    record.pop("task", None)
    if _running_key == key:
        _running_key = None
    log.info("%s upgrade finished: %s -> %s (%s)", key, from_version or "?",
             to_version or "?", error or "ok")


async def start(driver) -> dict:
    """Begin one upgrade in the background; raises RuntimeError when refused."""
    global _running_key
    key = str(driver.key)
    if not supported(driver):
        raise RuntimeError("{} does not support in-place upgrades".format(driver.label))
    if _running_key is not None:
        busy = _running_key
        raise RuntimeError("an engine upgrade is already running"
                           if busy == key else
                           "the {} upgrade is still running".format(busy))
    argv = _argv(driver)
    if argv is None:
        raise RuntimeError("{} is not installed on this node".format(driver.label))

    # Claim the slot before the first await: two clicks arriving together must
    # not both get past the check above and start competing package managers.
    record = _runs.setdefault(key, {})
    record["state"] = "running"
    record["started_at"] = time.time()
    record["result"] = None
    _running_key = key
    try:
        status = await driver.status()
        from_version = str(status.get("version") or "")
    except Exception:
        from_version = ""
    task = asyncio.ensure_future(_run(driver, argv, from_version))
    record["task"] = task

    def _done(finished) -> None:
        global _running_key
        if record.get("state") == "running":
            record["state"] = "idle"
            record["result"] = {
                "ok": False, "changed": False, "exit_code": None,
                "error": "upgrade was interrupted", "from_version": from_version,
                "to_version": from_version, "started_at": record.get("started_at"),
                "finished_at": time.time(), "message": "", "output": "",
            }
        if _running_key == key:
            _running_key = None
        if not finished.cancelled() and finished.exception() is not None:
            log.warning("%s upgrade task failed: %s", key, finished.exception())

    task.add_done_callback(_done)
    log.info("%s upgrade started: %s", key, " ".join(argv))
    return state(driver)


def reset_for_tests() -> None:
    global _running_key
    _runs.clear()
    _running_key = None
