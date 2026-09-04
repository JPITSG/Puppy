"""Lightweight whole-host metrics for the full WebUI connection footer."""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Tuple

from puppy import runner


FIRST_SAMPLE_SECONDS = 1.0
SAMPLE_INTERVAL_SECONDS = 3.0
_STATE_KEY = "puppy_host_metrics"

CpuTimes = Tuple[int, int]  # (all accounted CPU time, idle CPU time)


def _parse_cpu_stat(contents: str) -> Optional[CpuTimes]:
    """Parse Linux's aggregate /proc/stat CPU counters.

    guest and guest_nice are already included in user and nice, respectively,
    so only the first eight counters are included in the total.
    """
    for raw_line in contents.splitlines():
        fields = raw_line.split()
        if not fields or fields[0] != "cpu":
            continue
        if len(fields) < 5:
            return None
        try:
            values = [int(value) for value in fields[1:]]
        except ValueError:
            return None
        if any(value < 0 for value in values):
            return None
        accounted = values[:8]
        total = sum(accounted)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        if total <= 0 or idle > total:
            return None
        return total, idle
    return None


def _read_cpu_times(path: str = "/proc/stat") -> Optional[CpuTimes]:
    try:
        with open(path, "r", encoding="ascii") as stream:
            return _parse_cpu_stat(stream.read())
    except (OSError, UnicodeError):
        return None


def _cpu_percent(previous: CpuTimes, current: CpuTimes) -> Optional[float]:
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))


def latest(app) -> Optional[dict]:
    state = app.get(_STATE_KEY)
    payload = state.get("latest") if isinstance(state, dict) else None
    return dict(payload) if isinstance(payload, dict) else None


async def _sample_loop(app) -> None:
    state = app[_STATE_KEY]
    previous = _read_cpu_times()
    delay = FIRST_SAMPLE_SECONDS
    while True:
        await asyncio.sleep(delay)
        delay = SAMPLE_INTERVAL_SECONDS
        current = _read_cpu_times()
        value = _cpu_percent(previous, current) if previous and current else None
        previous = current
        payload = {
            "type": "host_metrics",
            "cpu_percent": round(value, 1) if value is not None else None,
            "sampled_at": time.time(),
        }
        state["latest"] = payload
        # This is replaceable telemetry, not an edge: a slow or backgrounded
        # console needs only the newest sample and receives it on reconnect.
        runner.publish_state(payload)


async def _lifecycle(app):
    task = asyncio.create_task(_sample_loop(app), name="puppy-host-metrics")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[_STATE_KEY]["latest"] = None


def register(app) -> None:
    """Start one sampler for a full WebUI process, regardless of client count."""
    app[_STATE_KEY] = {"latest": None}
    app.cleanup_ctx.append(_lifecycle)
