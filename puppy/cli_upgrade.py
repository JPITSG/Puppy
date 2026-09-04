"""Vendor-delegated upgrades of the engine CLIs puppy drives.

Puppy deliberately does not detect how an engine was installed. Each vendor CLI
already ships its own updater that knows npm / native / brew / standalone
layouts, and that detection matrix moves with the CLI. A driver therefore
declares only a fixed, argument-free self-update verb and this module owns the
bounded subprocess around it: driver-owned argv (never client input), no shell,
at most one run per engine, a hard timeout, and a capped transcript. Different
engine vendors may update concurrently; the vendor updater remains responsible
for its own installation method and locking. npm-backed engines also pass the
shared durable release-stabilization gate immediately before either a manual or
automatic run, preventing a freshly moved dist-tag from replacing a working CLI.

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
import signal
import time
from typing import Dict, List, Optional

from puppy import cli_releases

log = logging.getLogger("puppy.cli_upgrade")

TIMEOUT_SECONDS = 15 * 60
MAX_OUTPUT_CHARS = 8000
# A wedged updater is not waited on for the whole cap. Its output is streamed
# and its entire process group is sampled: progress is output, CPU beyond a
# sliver, I/O beyond a trickle, or a change in the tree (a child starting or
# ending). A group holding sockets is presumed to be waiting on the network
# and gets the longer allowance; one holding none that stays silent and
# still is dead - a package manager downloads, unpacks, or compiles, all of
# which show. Sampling reads /proc; where that is unavailable only the cap
# applies.
IDLE_SECONDS = 90
IDLE_NETWORK_SECONDS = 240
SAMPLE_INTERVAL = 5.0
EXIT_AFTER_EOF_SECONDS = 30
_CPU_BUSY_FRACTION = 0.02
_IO_TRICKLE_BYTES = 4096
_ARG_RE = re.compile(r"^-{0,2}[A-Za-z0-9][A-Za-z0-9._=-]*$")

_runs: Dict[str, dict] = {}
_preparing = set()


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
    binary = driver.resolved_binary()
    if not binary:
        return None
    return [binary] + args


def is_running(key: str) -> bool:
    """True while this engine's updater owns its per-engine slot."""
    key = str(key)
    return key in _preparing or (_runs.get(key) or {}).get("state") == "running"


def running_keys() -> List[str]:
    """Engine keys whose independent vendor updaters are in flight."""
    return sorted(_preparing | {
        key for key, record in _runs.items() if record.get("state") == "running"})


def state(driver) -> dict:
    """Additive upgrade fields merged into one engine's status payload."""
    key = str(driver.key)
    record = _runs.get(key) or {}
    return {
        "upgrade_supported": supported(driver),
        "upgrade_state": "running" if key in _preparing or
        record.get("state") == "running" else "idle",
        "upgrade_result": record.get("result"),
    }


def _stabilization_error(driver, stability: dict) -> str:
    version = str(stability.get("version") or "the latest version")
    remaining = stability.get("remaining_seconds")
    if remaining is None:
        return ("npm release {} has not been observed on this backend yet; "
                "no update was started").format(version)
    seconds = max(1, int(float(remaining) + 0.999))
    minutes = (seconds + 59) // 60
    return ("npm release {} is still stabilizing; try again in about {} "
            "minute{} (updates wait at least 10 minutes after first sighting)".format(
                version, minutes, "" if minutes == 1 else "s"))


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


class _GroupActivity:
    """Aggregate activity of an updater's process group, read from /proc."""

    def __init__(self, pgid: int):
        self.pgid = int(pgid)
        self.available = os.path.isdir("/proc/self")
        try:
            self.clock_ticks = float(os.sysconf("SC_CLK_TCK")) or 100.0
        except (AttributeError, ValueError, OSError):
            self.clock_ticks = 100.0

    def sample(self) -> Optional[dict]:
        if not self.available:
            return None
        members, cpu, io, sockets = [], 0, 0, 0
        try:
            names = os.listdir("/proc")
        except OSError:
            self.available = False
            return None
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                with open("/proc/{}/stat".format(pid), "rb") as handle:
                    raw = handle.read().decode("latin-1")
            except OSError:
                continue
            # the command name may contain spaces or parentheses: split after
            # the last closing one
            fields = raw[raw.rfind(")") + 2:].split()
            if len(fields) < 13:
                continue
            try:
                if int(fields[2]) != self.pgid:
                    continue
                cpu += int(fields[11]) + int(fields[12])
            except ValueError:
                continue
            members.append(pid)
            try:
                with open("/proc/{}/io".format(pid)) as handle:
                    for line in handle:
                        if line.startswith(("rchar:", "wchar:")):
                            io += int(line.split()[1])
            except (OSError, ValueError, IndexError):
                pass
            try:
                for fd in os.listdir("/proc/{}/fd".format(pid)):
                    try:
                        if os.readlink("/proc/{}/fd/{}".format(pid, fd)).startswith(
                                "socket:"):
                            sockets += 1
                    except OSError:
                        pass
            except OSError:
                pass
        return {"members": tuple(sorted(members)), "cpu": cpu, "io": io,
                "sockets": sockets}

    def progressed(self, before: Optional[dict], after: Optional[dict],
                   elapsed: float) -> bool:
        """Whether the group did real work between two samples."""
        if before is None or after is None:
            return False
        if after["members"] != before["members"]:
            return True
        cpu_seconds = (after["cpu"] - before["cpu"]) / self.clock_ticks
        if elapsed > 0 and cpu_seconds / elapsed > _CPU_BUSY_FRACTION:
            return True
        return after["io"] - before["io"] > _IO_TRICKLE_BYTES


class UpdaterFailure(RuntimeError):
    """A run puppy ended itself, with whatever the updater said until then."""

    def __init__(self, error: str, output: str):
        super().__init__(error)
        self.output = output


async def _end_group(proc) -> None:
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


async def _spawn(argv: List[str]) -> tuple:
    """Run the updater and return (exit_code, combined output).

    Output is streamed so a run puppy has to end still reports what the
    updater said; the group's activity is sampled every SAMPLE_INTERVAL and a
    run that shows none for its idle allowance is ended long before the hard
    cap, which remains the backstop for one that keeps busy without ever
    finishing."""
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
    activity = _GroupActivity(proc.pid)
    chunks: List[bytes] = []
    kept = 0
    started = time.monotonic()
    last_progress = started
    last_sample = activity.sample()
    sampled_at = started
    sockets = last_sample["sockets"] if last_sample else 0
    read_task = None
    failure = ""
    eof = False
    try:
        while True:
            now = time.monotonic()
            if now - started >= TIMEOUT_SECONDS:
                failure = "updater timed out after {} minutes".format(
                    TIMEOUT_SECONDS // 60)
                break
            if read_task is None:
                read_task = asyncio.ensure_future(proc.stdout.read(4096))
            done, _pending = await asyncio.wait(
                {read_task},
                timeout=max(0.05, min(SAMPLE_INTERVAL,
                                      TIMEOUT_SECONDS - (now - started))))
            now = time.monotonic()
            if read_task in done:
                chunk = read_task.result()
                read_task = None
                if not chunk:
                    eof = True
                    break
                chunks.append(chunk)
                kept += len(chunk)
                while kept > MAX_OUTPUT_CHARS * 4 and len(chunks) > 1:
                    kept -= len(chunks.pop(0))
                last_progress = now
                continue
            if now - sampled_at >= SAMPLE_INTERVAL:
                sample = activity.sample()
                if activity.progressed(last_sample, sample, now - sampled_at):
                    last_progress = now
                if sample is not None:
                    sockets = sample["sockets"]
                last_sample, sampled_at = sample, now
            allowance = IDLE_NETWORK_SECONDS if sockets else IDLE_SECONDS
            if activity.available and now - last_progress >= allowance:
                failure = (
                    "updater showed no sign of progress for {}s (no output, "
                    "CPU, I/O, or process changes{}) and was stopped".format(
                        int(now - last_progress),
                        " while holding {} open socket(s)".format(sockets)
                        if sockets else ""))
                break
    finally:
        if read_task is not None and not read_task.done():
            read_task.cancel()
    if eof:
        # every holder of its output is gone or has let go; the exit should
        # follow at once, and one that never comes is a failure of its own
        try:
            await asyncio.wait_for(proc.wait(), timeout=EXIT_AFTER_EOF_SECONDS)
        except asyncio.TimeoutError:
            failure = ("updater closed its output but did not exit within "
                       "{}s and was stopped".format(EXIT_AFTER_EOF_SECONDS))
    output = b"".join(chunks).decode(errors="replace")
    if failure:
        await _end_group(proc)
        raise UpdaterFailure(failure, output)
    return proc.returncode, output


async def _run(driver, argv: List[str], from_version: str, token) -> None:
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
    except UpdaterFailure as exc:
        error = str(exc)[:300]
        output = exc.output
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

    record = _runs.get(key)
    # A test reset or a newer run must never let this task overwrite that run's
    # state. The token also closes the tiny gap between _run returning and its
    # done callback being dispatched.
    if record is None or record.get("token") is not token:
        return
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
    # The replacement CLI may ship a different catalog protocol or newly
    # available aliases. Mark the updater idle first so the catalog guard no
    # longer treats this as an attempt against a binary being rewritten.
    try:
        driver.invalidate_model_options()
        await driver.refresh_model_options(force=True)
    except Exception as exc:
        # Catalog discovery retains its previous good list and normally owns
        # its own error state; this guard also protects third-party test drivers.
        log.warning("%s model refresh after upgrade failed: %s", key, exc)
    log.info("%s upgrade finished: %s -> %s (%s)", key, from_version or "?",
             to_version or "?", error or "ok")


async def start(driver) -> dict:
    """Begin this engine's upgrade; other engines may update concurrently."""
    key = str(driver.key)
    if not supported(driver):
        raise RuntimeError("{} does not support in-place upgrades".format(driver.label))
    if is_running(key):
        raise RuntimeError("the {} upgrade is already running".format(key))
    argv = _argv(driver)
    if argv is None:
        raise RuntimeError("{} is not installed on this backend".format(driver.label))

    # Claim this engine before the first await. The npm preflight is network
    # work, but a second click or a new turn must not slip through while it is
    # deciding whether the observed release is old enough to install.
    _preparing.add(key)
    try:
        if cli_releases.npm_based(driver):
            if not await cli_releases.refresh_before_upgrade(driver):
                raise RuntimeError(
                    "could not verify the latest npm release; no update was started")
            stability = cli_releases.release_stability(driver)
            if not stability.get("ready"):
                raise RuntimeError(_stabilization_error(driver, stability))
        # Keep the pre-existing repair path: an updater may still be useful
        # when a damaged installation cannot answer --version. Registry age is
        # the safety prerequisite; the local version probe remains advisory.
        try:
            status = await driver.status()
            from_version = str(status.get("version") or "")
        except Exception:
            from_version = ""
        record = _runs.setdefault(key, {})
        token = object()
        record["state"] = "running"
        record["started_at"] = time.time()
        record["result"] = None
        record["token"] = token
        record.pop("task", None)
        task = asyncio.ensure_future(_run(driver, argv, from_version, token))
        record["task"] = task
    finally:
        _preparing.discard(key)

    def _done(finished) -> None:
        if record.get("token") is not token:
            return
        if record.get("state") == "running":
            record["state"] = "idle"
            record["result"] = {
                "ok": False, "changed": False, "exit_code": None,
                "error": "upgrade was interrupted", "from_version": from_version,
                "to_version": from_version, "started_at": record.get("started_at"),
                "finished_at": time.time(), "message": "", "output": "",
            }
        record.pop("task", None)
        record.pop("token", None)
        if not finished.cancelled() and finished.exception() is not None:
            log.warning("%s upgrade task failed: %s", key, finished.exception())

    task.add_done_callback(_done)
    log.info("%s upgrade started: %s", key, " ".join(argv))
    return state(driver)


def reset_for_tests() -> None:
    _preparing.clear()
    for record in _runs.values():
        task = record.get("task")
        if task is not None and not task.done():
            task.cancel()
    _runs.clear()
