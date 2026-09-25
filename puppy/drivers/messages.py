"""Small, engine-independent helpers for protocol messages meant for people.

Drivers own the meaning of their protocol's codes. Do not guess it from CLI
output, turn arbitrary objects into prose, or treat a missing field as zero.
The original protocol data stays separate from the display payload.
"""
from __future__ import annotations

import logging
import math
import re

log = logging.getLogger("puppy.drivers")
TEXT_LIMIT = 4000
NOTICE_MEMORY = 64


def text(value):
    if not isinstance(value, str):
        return ""
    # JSON messages are plain text, never terminal commands or escape codes.
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()[:TEXT_LIMIT]


def first_text(*values):
    return next((clean for value in values if (clean := text(value))), "")


def number(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < minimum or (maximum is not None and value > maximum):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    return value


def unknown(family, code):
    # Codes alone are diagnostic. Never log the vendor's arbitrary error.data,
    # which can contain credentials, prompts, paths or HTTP response bodies.
    safe = code if isinstance(code, str) and re.fullmatch(r"[\w.-]{1,100}", code) else "unavailable"
    log.debug("Unrecognized %s code: %s", family, safe)


def notice(message, tone="warn", resets_at=None):
    value = {"text": text(message), "tone": tone}
    if not value["text"]:
        return None
    # JavaScript Date's supported epoch range, in seconds. The console formats
    # this in its own timezone with the controller's clock preference.
    stamp = number(resets_at, 1, 8640000000000)
    if stamp is not None:
        value["resets_at"] = stamp
    return value


def once(ctx, value, key=None):
    """Suppress repeated notices within a turn without unbounded memory.

    Rate-limit adapters can use the window and reported warning threshold as
    their key: another percentage sample is not another warning. This is not
    a persistent quota state machine and never infers recovery.
    """
    if value is None:
        return None
    seen = ctx.setdefault("display_notices", {})
    fingerprint = repr(key if key is not None else value)[:TEXT_LIMIT + 200]
    if fingerprint in seen:
        return None
    seen[fingerprint] = True
    if len(seen) > NOTICE_MEMORY:
        del seen[next(iter(seen))]
    return value


def actions(ctx, value, key=None):
    value = once(ctx, value, key)
    return [{"a": "notice", "notice": value}] if value is not None else []
