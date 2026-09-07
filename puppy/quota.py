"""Ephemeral account-bound quota observations, never credentials or history.

Drivers establish the account and exact bucket; the console joins matching
observations across nodes. Old persisted rate_limit rows have no account
provenance and must never be assigned to the currently signed-in account.
"""
from __future__ import annotations

import hashlib
import json
import math
import time


def read_object(path):
    """Bounded private metadata read. Never log the contents or parse errors."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def account_key(provider, *identifiers):
    if not identifiers or any(not isinstance(value, str) or not value.strip()
                              or len(value) > 512 for value in identifiers):
        return None
    # Stable non-secret IDs, not email, model names, or rotating token bytes.
    return hashlib.sha256(json.dumps(
        ["puppy-quota-v1", provider, *identifiers],
        separators=(",", ":")).encode()).hexdigest()


def finite(value):
    try:
        return isinstance(value, (float, int)) and not isinstance(value, bool) and \
            math.isfinite(value)
    except OverflowError:
        return False


def weekly_sample(used, resets, observed_at=None):
    if not finite(used) or used < 0:
        return None
    return {"used_percent": float(used),
            "resets_at": resets if finite(resets) and resets > 0 else None,
            "observed_at": time.time() if observed_at is None else observed_at}


class Monitor:
    """One exact displayed bucket, cleared on account changes and restarts."""

    def __init__(self, provider, bucket):
        self.provider = provider
        self.bucket = bucket
        self.identity = None
        self.sample = None

    def bind(self, identity):
        if identity != self.identity:
            self.identity, self.sample = identity, None

    def observe(self, identity, turn_identity, sample):
        self.bind(identity)
        # An old process must not relabel its quota after a login switch.
        if turn_identity != identity or sample is None:
            return
        if self.sample and sample["observed_at"] <= self.sample["observed_at"]:
            return
        self.sample = dict(sample)
        from puppy import state_stream
        state_stream.wake("engines")

    def payload(self, identity):
        self.bind(identity)
        sample = self.sample
        if sample and sample["resets_at"] and sample["resets_at"] <= time.time():
            sample = None
        return {"version": 1, "provider": self.provider, "account": identity,
                "bucket": self.bucket, "window_minutes": 10080,
                "sample": dict(sample) if sample else None}
