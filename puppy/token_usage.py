"""Token consumption: what every engine run on this node used, kept by the node.

Each node keeps its own ledger, because the engines run there and a backend's
transcripts never leave it: the console asks every reachable backend for its
share and draws them side by side. The ledger is one exact-shape meta record
per UTC day, ``token_usage.<YYYY-MM-DD>``::

    {"format": 1, "rows": [[ref, at, session, source, engine, model,
                            input, output, cache_read, cache_write,
                            reasoning, cost], ...]}

A row is one engine run's tokens on one model: a session's turn (its persisted
``result`` event, ``ref`` = ``turn:<session>:<seq>``), a spawned agent or a
title job (``spawn:<job>:<started>``). A turn that used several models (a
Claude turn whose subagents or background calls ran on another model) has one
row per model, all under the one ref. The counts are the drivers' documented
usage vocabulary made disjoint (see ``counts``): fresh input, cache reads,
cache writes and output, with reasoning the part of the output spent thinking.
``cost`` is the engine's own estimate when it reports one, else null.

Rows are never derived at read time from transcripts: the ledger outlives the
conversations it counts, so deleting a session never rewrites what it used,
and a ref already present in its day is never counted twice. Records are
validated at startup and on a snapshot restore and never repaired; the rows
of a day are kept ordered by time, then ref, then model.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import re
import time

from puppy import db

log = logging.getLogger("puppy.token_usage")

PREFIX = "token_usage."
API_PATH = "/api/token-usage"
FORMAT = 1
SOURCES = ("turn", "spawn", "title")
COLUMNS = ("ref", "at", "session", "source", "engine", "model",
           "input", "output", "cache_read", "cache_write", "reasoning", "cost")
COUNTS = ("input", "output", "cache_read", "cache_write", "reasoning")
MAX_REF = 200
MAX_ENGINE = 64
MAX_MODEL = 200
DAY = 86400
# The report's time buckets: hours (the console folds them into its own local
# days, exactly), or days aligned to a fixed offset for spans too long to send
# by the hour.
STEPS = (3600, DAY)
MAX_OFFSET = 14 * 3600
MAX_SPAN = 5 * 366 * DAY
TOP_SESSIONS = 20
_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class ShapeError(ValueError):
    """A persisted record is not the current shape; it is refused, not repaired."""


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(value) or value <= 0:
        return 0
    return int(value)


def counts(usage):
    """A driver's usage in the ledger's disjoint counts, or None when it is empty.

    The drivers' vocabulary (drivers/base.py): the input a turn read is
    ``input_tokens`` plus any ``cache_read_input_tokens`` and
    ``cache_creation_input_tokens`` - a driver whose ``input_tokens`` already
    counts its cached part reports that part as ``cached_input_tokens``
    instead, a subset like ``reasoning_output_tokens`` is of the output. So
    the cache reads are whichever cached part was reported, the cache writes
    the creation count, the fresh input what remains, and the output the whole
    generated output with its reasoning part beside it.
    """
    if not isinstance(usage, dict):
        return None
    base = _count(usage.get("input_tokens"))
    read = _count(usage.get("cache_read_input_tokens"))
    write = _count(usage.get("cache_creation_input_tokens"))
    cached_subset = min(base, _count(usage.get("cached_input_tokens")))
    output = _count(usage.get("output_tokens"))
    reasoning = min(output, _count(usage.get("reasoning_output_tokens")))
    value = {"input": base - cached_subset, "output": output,
             "cache_read": read + cached_subset, "cache_write": write,
             "reasoning": reasoning}
    if not any(value[key] for key in ("input", "output", "cache_read", "cache_write")):
        return None
    return value


def _cost(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(value) or value < 0:
        return None
    return round(float(value), 6)


def _day(at: float) -> str:
    return datetime.datetime.fromtimestamp(float(at), datetime.timezone.utc).strftime("%Y-%m-%d")


def _day_start(day: str) -> float:
    return datetime.datetime.strptime(day, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def _row(ref, at, session, source, engine, model, amounts, cost) -> list:
    return [ref, round(float(at), 3), session, source, engine, model,
            amounts["input"], amounts["output"], amounts["cache_read"],
            amounts["cache_write"], amounts["reasoning"], _cost(cost)]


def _row_key(row) -> tuple:
    return (row[1], row[0], row[5])


def _valid_int(value) -> bool:
    return type(value) is int and value >= 0


def _valid_row(row, start: float) -> bool:
    if not isinstance(row, list) or len(row) != len(COLUMNS):
        return False
    ref, at, session, source, engine, model = row[:6]
    if not isinstance(ref, str) or not 0 < len(ref) <= MAX_REF:
        return False
    if isinstance(at, bool) or not isinstance(at, (int, float)) or \
            not math.isfinite(at) or not start <= at < start + DAY:
        return False
    if session is not None and (type(session) is not int or session <= 0):
        return False
    if source not in SOURCES:
        return False
    if not isinstance(engine, str) or not 0 < len(engine) <= MAX_ENGINE:
        return False
    if not isinstance(model, str) or len(model) > MAX_MODEL:
        return False
    amounts = row[6:11]
    if not all(_valid_int(value) for value in amounts) or not any(amounts[:4]):
        return False
    cost = row[11]
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or
                             not math.isfinite(cost) or cost < 0):
        return False
    return True


def _parse(key: str, raw) -> list:
    """One day's rows, exactly as stored, or ShapeError."""
    day = key[len(PREFIX):]
    try:
        if not _DAY_RE.fullmatch(day):
            raise ValueError("not a day")
        start = _day_start(day)
        value = json.loads(raw, parse_constant=lambda name: (_ for _ in ()).throw(
            ValueError("invalid JSON constant {}".format(name))))
    except (TypeError, ValueError):
        raise ShapeError("token usage record {} has an unsupported persisted shape".format(key))
    if not isinstance(value, dict) or set(value) != {"format", "rows"} or \
            value["format"] != FORMAT or not isinstance(value["rows"], list) or \
            not value["rows"] or not all(_valid_row(row, start) for row in value["rows"]):
        raise ShapeError("token usage record {} has an unsupported persisted shape".format(key))
    rows = value["rows"]
    keys = [_row_key(row) for row in rows]
    if keys != sorted(keys) or len({(row[0], row[5]) for row in rows}) != len(rows):
        raise ShapeError("token usage record {} has an unsupported persisted shape".format(key))
    return rows


def validate_persisted(connection) -> None:
    """Reject a malformed record before startup or a restore, never repair it."""
    for key, raw in connection.execute(
            "SELECT key,value FROM meta WHERE key GLOB ?", (PREFIX + "*",)):
        _parse(str(key), raw)


def store(rows: list) -> int:
    """Add rows to their days and return how many were added.

    A ref already present in its day is left alone with all its rows, so a
    turn is counted once whoever reports it first. One immediate transaction
    covers every day touched: a second writer (another process) waits for it
    instead of overwriting it.
    """
    by_day = {}
    for row in rows:
        by_day.setdefault(_day(row[1]), []).append(row)
    added = 0
    with db._lock:
        connection = db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for day, fresh in sorted(by_day.items()):
                key = PREFIX + day
                stored_row = connection.execute(
                    "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                existing = _parse(key, stored_row[0]) if stored_row else []
                seen = {row[0] for row in existing}
                new = [row for row in fresh if row[0] not in seen]
                if not new:
                    continue
                merged = sorted(existing + new, key=_row_key)
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps({"format": FORMAT, "rows": merged},
                                     separators=(",", ":"))))
                added += len(new)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return added


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _model_rows(ref, at, session, source, engine, model, usage, cost, model_usage):
    """The rows of one engine run: one per model when the engine broke its
    tokens down by model and that breakdown covers the run's own usage, else
    one row on the model that served it."""
    engine = _text(engine, MAX_ENGINE)
    if not engine:
        return []
    whole = counts(usage)
    if isinstance(model_usage, dict) and model_usage:
        split = []
        for name, entry in sorted(model_usage.items()):
            amounts = counts(entry) if isinstance(entry, dict) else None
            if amounts:
                split.append((_text(name, MAX_MODEL), amounts,
                              entry.get("cost_usd")))
        covered = sum(sum(amounts[key] for key in ("input", "output", "cache_read",
                                                   "cache_write"))
                      for _, amounts, _ in split)
        needed = sum(whole[key] for key in ("input", "output", "cache_read",
                                            "cache_write")) if whole else 0
        if split and covered >= needed:
            return [_row(ref, at, session, source, engine, name, amounts, part_cost)
                    for name, amounts, part_cost in split]
    if not whole:
        return []
    return [_row(ref, at, session, source, engine, _text(model, MAX_MODEL), whole, cost)]


def turn_rows(session: dict, event: dict) -> list:
    """The ledger rows of one persisted ``result`` event of ``session``."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    sid = int(session["id"])
    model = session.get("last_model") or session.get("model") or ""
    return _model_rows("turn:{}:{}".format(sid, int(event["seq"])), float(event["ts"]),
                       sid, "turn", session.get("engine"), model, data.get("usage"),
                       data.get("cost_usd"), data.get("model_usage"))


def record_turn(session: dict, event: dict) -> None:
    """Count a turn the runner just finished. Never raises: a ledger that
    cannot be written must not fail the turn it describes."""
    try:
        store(turn_rows(session, event))
    except Exception:
        log.warning("could not record token usage for session %s", session.get("id"),
                    exc_info=True)


def job_rows(job) -> list:
    """The ledger rows of a finished spawned agent or title job."""
    owner = tuple(job.owner or ())
    source = "title" if owner[:1] == ("title",) else "spawn"
    session = int(owner[1]) if owner[:1] == ("turn",) and len(owner) > 1 else None
    at = job.finished_at or time.time()
    return _model_rows("spawn:{}:{}".format(job.id, int(job.created_at)), at, session,
                       source, job.engine, job.model_used or job.model, job.usage,
                       job.cost_usd, None)


def record_job(job) -> None:
    """Count a spawned agent or title job that just ended. Never raises."""
    try:
        store(job_rows(job))
    except Exception:
        log.warning("could not record token usage for spawned job %s",
                    getattr(job, "id", "?"), exc_info=True)


def _read(since: float, until: float):
    """The raw day records overlapping the span and the earliest one there is,
    read under the storage lock and parsed outside it."""
    first_key, last_key = PREFIX + _day(since), PREFIX + _day(max(since, until - 0.001))
    with db._lock:
        connection = db.connect()
        rows = connection.execute(
            "SELECT key,value FROM meta WHERE key>=? AND key<=? AND key GLOB ? ORDER BY key",
            (first_key, last_key, PREFIX + "*")).fetchall()
        earliest = connection.execute(
            "SELECT key,value FROM meta WHERE key GLOB ? ORDER BY key LIMIT 1",
            (PREFIX + "*",)).fetchone()
    return [(str(row[0]), row[1]) for row in rows], \
        (str(earliest[0]), earliest[1]) if earliest else None


def _session_names(ids) -> dict:
    """Names for the sessions a report lists, and each task's Main."""
    if not ids:
        return {}
    from puppy import session_tasks
    marks = ",".join("?" for _ in ids)
    found = {int(row["id"]): {"name": row["name"] or "", "color": row["color"] or ""}
             for row in db.query(
                 "SELECT id,name,color FROM sessions WHERE id IN ({})".format(marks),
                 tuple(ids))}
    out = {}
    for sid in ids:
        entry = found.get(sid)
        if entry is None:
            out[sid] = {"name": "", "color": "", "deleted": True, "parent": None,
                        "parent_name": ""}
            continue
        record = None
        try:
            record = session_tasks.record(sid)
        except Exception:
            record = None
        parent = record.get("parent") if isinstance(record, dict) else None
        parent_name = ""
        if parent:
            row = db.query_one("SELECT name FROM sessions WHERE id=?", (int(parent),))
            parent_name = (row["name"] if row else "") or ""
        out[sid] = {"name": entry["name"], "color": entry["color"], "deleted": False,
                    "parent": int(parent) if parent else None, "parent_name": parent_name}
    return out


def _add(target: list, row: list) -> None:
    for index in range(5):
        target[index] += row[6 + index]
    if row[11] is not None:
        target[5] = (target[5] or 0.0) + row[11]


def report(since: float, until: float, step: int = 3600, offset: int = 0) -> dict:
    """Usage between ``since`` and ``until``: summed per time bucket, engine
    and model, in total, per session (the heaviest TOP_SESSIONS) and for the
    runs that belong to no session here (title jobs, a controller's spawns).
    ``turns`` counts the session turns among them; a job's entry counts its
    runs there instead."""
    days, earliest = _read(since, until)
    buckets, sessions = {}, {}
    totals = [0, 0, 0, 0, 0, None]
    refs, jobs = set(), {}
    for key, raw in days:
        for row in _parse(key, raw):
            at = row[1]
            if not since <= at < until:
                continue
            # a turn is counted as one; a spawned agent or a title job is a
            # run of its own, counted apart from the turns
            turn = row[3] == "turn"
            bucket = math.floor((at + offset) / step) * step - offset
            slot = buckets.setdefault((bucket, row[4], row[5]), [0, 0, 0, 0, 0, None, set()])
            _add(slot, row)
            _add(totals, row)
            if turn:
                slot[6].add(row[0])
                refs.add(row[0])
            if row[2] is not None:
                entry = sessions.setdefault(row[2], [0, 0, 0, 0, 0, None, set(), 0.0, set()])
                _add(entry, row)
                if turn:
                    entry[6].add(row[0])
                entry[7] = max(entry[7], at)
                entry[8].add(row[4])
            else:
                entry = jobs.setdefault(row[3], [0, 0, 0, 0, 0, None, set()])
                _add(entry, row)
                entry[6].add(row[0])
    first_at = None
    if earliest:
        first_at = _parse(*earliest)[0][1]

    def total(values) -> int:
        return values[0] + values[1] + values[2] + values[3]

    def amounts(values) -> dict:
        return {"input": values[0], "output": values[1], "cache_read": values[2],
                "cache_write": values[3], "reasoning": values[4],
                "cost": None if values[5] is None else round(values[5], 6),
                "turns": len(values[6])}

    heaviest = sorted(sessions.items(), key=lambda item: (-total(item[1]), item[0]))[:TOP_SESSIONS]
    names = _session_names([sid for sid, _ in heaviest])
    return {
        "ok": True, "since": since, "until": until, "step": step, "offset": offset,
        "first_at": first_at,
        "columns": ["at", "engine", "model", "input", "output", "cache_read",
                    "cache_write", "reasoning", "turns", "cost"],
        "buckets": [[bucket, engine, model, *values[:5], len(values[6]),
                     None if values[5] is None else round(values[5], 6)]
                    for (bucket, engine, model), values in sorted(buckets.items())],
        "totals": dict(amounts(totals[:6] + [refs])),
        "sessions": [dict(amounts(values), id=sid, last_at=values[7],
                          engines=sorted(values[8]), **names.get(sid, {}))
                     for sid, values in heaviest],
        "session_count": len(sessions),
        "jobs": {source: amounts(values) for source, values in sorted(jobs.items())},
    }


def _query_number(request, name: str, default):
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("{} must be a number".format(name))
    if not math.isfinite(value):
        raise ValueError("{} must be a number".format(name))
    return value


async def h_report(request):
    from aiohttp import web
    now = time.time()
    try:
        until = _query_number(request, "until", now)
        since = _query_number(request, "since", until - 30 * DAY)
        step = _query_number(request, "step", 3600)
        offset = _query_number(request, "offset", 0)
        if since <= 0 or until <= since or until - since > MAX_SPAN:
            raise ValueError("since and until must be a span of at most five years")
        if int(step) != step or int(step) not in STEPS:
            raise ValueError("step must be 3600 or 86400")
        if int(offset) != offset or abs(offset) > MAX_OFFSET or int(offset) % 60:
            raise ValueError("offset must be whole minutes within fourteen hours")
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        payload = await asyncio.get_running_loop().run_in_executor(
            None, report, since, until, int(step), int(offset))
    except ShapeError as exc:
        log.error("%s", exc)
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response(payload)


def register(app) -> None:
    app.router.add_get(API_PATH, h_report)

    async def validate(_app):
        # A malformed record refuses to start rather than being repaired.
        validate_persisted(db.connect())
    app.on_startup.append(validate)
