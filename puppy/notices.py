"""Notification history: every notice the console shows, kept by the controller.

Controller-owned. A notice (a toast) is raised in the browser - half of them
are backend prose the console never wrote - so the console reports each one it
shows and this module keeps the last LIMIT of them in one exact-shape ``notices``
meta record. A report that repeats the newest entry (same text, same tone)
counts up on it the way the live toast folds into "2 ×"; anything else is a new
entry and the oldest falls away. Every console learns the list through the
``notices`` state topic, so the box behind the footer's tray is live in each of
them. The headless backend has no console and serves none of this.
"""
from __future__ import annotations

import json
import math
import time

from puppy import db, runner

META_KEY = "notices"
API_PATH = "/api/notices"
FORMAT = 1
LIMIT = 100
MAX_TEXT = 1000
TONES = ("info", "ok", "warn", "bad", "busy")
_ITEM_KEYS = {"id", "text", "tone", "count", "first_at", "at"}
_ELLIPSIS = "…"


class ShapeError(ValueError):
    """The persisted record is not the current shape; it is refused, not repaired."""


def clean_text(value) -> str:
    """One line, no control characters, at most MAX_TEXT characters.

    The console's ``toastText`` already collapses whitespace; this is the same
    shape enforced where the record is written, so a stored text is always
    its own cleaned form and the persisted validation can demand exactly that.
    """
    text = "".join(ch for ch in str(value if value is not None else "")
                   if ch >= " " and ch != "\x7f" or ch.isspace())
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1].rstrip() + _ELLIPSIS
    return text


def _valid_stamp(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        math.isfinite(value) and value > 0


def _valid_item(value, previous_id: int):
    if not isinstance(value, dict) or set(value) != _ITEM_KEYS:
        return None
    if not isinstance(value["id"], int) or isinstance(value["id"], bool) or \
            value["id"] <= previous_id:
        return None
    if not isinstance(value["text"], str) or not value["text"] or \
            clean_text(value["text"]) != value["text"]:
        return None
    if value["tone"] not in TONES:
        return None
    if not isinstance(value["count"], int) or isinstance(value["count"], bool) or \
            value["count"] < 1:
        return None
    if not _valid_stamp(value["first_at"]) or not _valid_stamp(value["at"]) or \
            value["first_at"] > value["at"]:
        return None
    return dict(value)


def _valid_state(value):
    if not isinstance(value, dict) or set(value) != {"format", "next_id", "items"} or \
            value["format"] != FORMAT or not isinstance(value["next_id"], int) or \
            isinstance(value["next_id"], bool) or value["next_id"] < 1 or \
            not isinstance(value["items"], list) or len(value["items"]) > LIMIT:
        return None
    items, previous = [], 0
    for raw in value["items"]:
        item = _valid_item(raw, previous)
        if item is None:
            return None
        items.append(item)
        previous = item["id"]
    if previous >= value["next_id"]:
        return None
    return {"format": FORMAT, "next_id": value["next_id"], "items": items}


def _new_state() -> dict:
    return {"format": FORMAT, "next_id": 1, "items": []}


def _load(connection) -> dict:
    """The validated record, or a fresh one while none has been written."""
    row = connection.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    if row is None:
        return _new_state()
    try:
        value = json.loads(row[0], parse_constant=lambda name: (_ for _ in ()).throw(
            ValueError("invalid JSON constant {}".format(name))))
    except (TypeError, ValueError):
        value = None
    state = _valid_state(value)
    if state is None:
        raise ShapeError("notification history has an unsupported persisted shape")
    return state


def validate_persisted(connection) -> None:
    """Reject a malformed record before startup or a restore, never repair it."""
    _load(connection)


def _payload(state: dict) -> dict:
    # Newest first: that is the order the box reads them in.
    return {"type": "notices", "limit": LIMIT,
            "items": [dict(item) for item in reversed(state["items"])]}


def payload() -> dict:
    with db._lock:
        return _payload(_load(db.connect()))


def publish() -> dict:
    """Install the current list as the ``notices`` state topic."""
    return runner.publish_state(payload())


def record(text, tone) -> dict:
    """Keep one shown notice and return the published list.

    The newest entry absorbs an identical repeat; otherwise the notice becomes
    the newest entry and whatever is past the LIMIT is let go. One immediate
    transaction serializes concurrent consoles reporting at the same moment.
    """
    body = clean_text(text) if isinstance(text, str) else ""
    if not body:
        raise ValueError("a notice needs text")
    if tone not in TONES:
        raise ValueError("tone must be one of " + ", ".join(TONES))
    with db._lock:
        connection = db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = _load(connection)
            now = time.time()
            items = state["items"]
            if items and items[-1]["text"] == body and items[-1]["tone"] == tone:
                newest = items[-1]
                newest["count"] += 1
                # A clock that stepped back must not leave a repeat before its
                # own first sighting.
                newest["at"] = max(now, newest["at"])
            else:
                items.append({"id": state["next_id"], "text": body, "tone": tone,
                              "count": 1, "first_at": now, "at": now})
                state["next_id"] += 1
                del items[:-LIMIT]
            connection.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (META_KEY, json.dumps(state)))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return runner.publish_state(_payload(state))
