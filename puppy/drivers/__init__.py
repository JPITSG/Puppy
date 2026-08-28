"""Engine registry. Add future engines (kimi, ...) here."""
from __future__ import annotations

from puppy.drivers.claude import ClaudeDriver
from puppy.drivers.codex import CodexDriver
from puppy.drivers.opencode import OpenCodeDriver

_DRIVERS = {d.key: d for d in (ClaudeDriver(), CodexDriver(), OpenCodeDriver())}


def get_driver(key: str):
    d = _DRIVERS.get(key)
    if d is None:
        raise KeyError(f"unknown engine: {key}")
    return d


def all_drivers():
    return list(_DRIVERS.values())


def engine_keys():
    return list(_DRIVERS.keys())
