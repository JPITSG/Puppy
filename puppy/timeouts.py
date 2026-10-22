"""Settings → Timeouts on this node: the six limits config.json holds
(config.TIMEOUT_PATHS) and the longest a turn waits on engine background
tasks after its answer.

That last one is the optional exact `background_wait_limit` meta record,
`{format:1, seconds}`, absent = BACKGROUND_DEFAULT_SECONDS, rather than a
seventh config.json key: a new key is a persisted-shape change every node's
config.json must take by hand before the code that requires it starts, while
a record no older code reads changes no node's shape (backup_limit is kept
the same way). Like the config limits, it is whole seconds from 0 through
config.MAX_TIMEOUT_SECONDS and 0 is unlimited. A record of any other shape
is refused at startup and in a backup, never repaired."""
from __future__ import annotations

import json

from puppy import config, db

BACKGROUND_FIELD = "background_wait_seconds"
BACKGROUND_KEY = "background_wait_limit"
BACKGROUND_FORMAT = 1
BACKGROUND_DEFAULT_SECONDS = 86400


def background_wait_seconds(connection=None) -> int:
    sql = "SELECT value FROM meta WHERE key=?"
    row = connection.execute(sql, (BACKGROUND_KEY,)).fetchone() if connection is not None else \
        db.query_one(sql, (BACKGROUND_KEY,))
    if row is None:
        return BACKGROUND_DEFAULT_SECONDS
    try:
        value = json.loads(row[0])
        if not isinstance(value, dict) or set(value) != {"format", "seconds"} or \
                type(value["format"]) is not int or value["format"] != BACKGROUND_FORMAT or \
                type(value["seconds"]) is not int:
            raise ValueError("invalid shape")
        return config.normalize_timeout(value["seconds"], BACKGROUND_FIELD)
    except (ValueError, TypeError) as exc:
        raise ValueError("background task limit has an unsupported persisted shape") from exc


def validate_persisted(connection) -> None:
    background_wait_seconds(connection)


def values() -> dict:
    result = config.timeout_values()
    result[BACKGROUND_FIELD] = background_wait_seconds()
    return result


def defaults() -> dict:
    result = dict(config.TIMEOUT_DEFAULTS)
    result[BACKGROUND_FIELD] = BACKGROUND_DEFAULT_SECONDS
    return result


def payload() -> dict:
    return {"values": values(), "defaults": defaults(), "max_seconds": config.MAX_TIMEOUT_SECONDS}


def update(patch: dict) -> dict:
    """Validate a partial node update across both stores before writing
    either, and put the record back when config.json then fails to save."""
    if not isinstance(patch, dict) or not patch or \
            set(patch) - set(config.TIMEOUT_PATHS) - {BACKGROUND_FIELD}:
        raise ValueError("supply one or more known timeout settings")
    normalized = {name: config.normalize_timeout(value, name) for name, value in patch.items()}
    background = normalized.pop(BACKGROUND_FIELD, None)
    previous = db.query_one("SELECT value FROM meta WHERE key=?", (BACKGROUND_KEY,))
    if background is not None:
        db.meta_set(BACKGROUND_KEY, {"format": BACKGROUND_FORMAT, "seconds": background})
    try:
        if normalized:
            config.set_timeouts(normalized)
    except Exception:
        if background is not None:
            if previous is None:
                db.meta_del(BACKGROUND_KEY)
            else:
                db.meta_set(BACKGROUND_KEY, json.loads(previous[0]))
        raise
    return values()
