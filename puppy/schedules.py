"""Scheduled prompts: a cron for prompts.

Two halves live here, like puppy/notify.py and puppy/session_titles.py.

The node half (both runtimes, behind the additive ``session-schedule-runs``
capability) marks the sessions a schedule starts. ``POST /api/sessions``
accepts an optional ``schedule`` object (``{id, name, run}``); the node keeps
it as the exact ``session_schedule.<sid>`` meta record, deleted with the
session and validated at startup and on restore, and carries it on every
session payload as the additive ``schedule`` (``{id, name, run, waiting}``,
``waiting`` true while the run waits for an approval), re-sending its session
list when that changes. A console keeps a run out of its sidebar while the
controller that owns the schedule still keeps the run's conversation.

The controller half (full runtime only) owns the schedules: one exact
``schedule.<id>`` record per schedule - its settings and where its timing
stands - and one ``schedule_runs.<id>`` record with a bounded history of its
runs. One worker decides when an occurrence is due, in this instance's own
local time, and starts it as a new session on the chosen backend through the
node API a console uses (in-process for this instance, the authenticated node
request for a backend). It follows that session until its activity block
ends, keeps an excerpt of the final answer, notifies as the schedule says and
lets conversations past the schedule's keep go. A run is one ordinary prompt
in one ordinary session: nothing here schedules queued work.

A schedule's times are wall-clock values (``YYYY-MM-DD``, ``HH:MM``) in this
instance's local time zone, persisted as written and turned into instants only
when they are used, so a change of daylight-saving time keeps "06:00" at six.
"""
from __future__ import annotations

import asyncio
import calendar
import datetime
import functools
import json
import logging
import math
import os
import re
import secrets
import time

from puppy import config, db, protocol

log = logging.getLogger("puppy.schedules")

FORMAT = 1
ID_RE = re.compile(r"[0-9a-f]{12}\Z")
# the sessions table's name width, which every rename path cuts names to
NAME_LIMIT = 80


class ScheduleError(ValueError):
    def __init__(self, message: str, status: int = 400, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        # True: the node may have done it anyway (no answer, or a 5xx)
        self.uncertain = uncertain


def _canonical(value, limit: int, allow_empty: bool = False) -> bool:
    """One line of text as stored: no control characters and no space at
    either end, within ``limit`` characters."""
    if not isinstance(value, str) or len(value) > limit or value != value.strip() or \
            any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    return allow_empty or bool(value)


def _whole(value, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _stamp(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _decode(raw):
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError("invalid JSON constant {}".format(value))))


# ---- the node half: the run marker ----

RUN_PREFIX = "session_schedule."
RUN_KEYS = {"format", "schedule", "name", "run"}
MARKER_KEYS = {"id", "name", "run"}
RUN_MAX = 10 ** 9


def _run_key(sid) -> str:
    return RUN_PREFIX + str(int(sid))


def validate_run_marker(value) -> dict:
    """The ``schedule`` field of a session creation, as the record it becomes."""
    if not isinstance(value, dict) or set(value) != MARKER_KEYS or \
            not isinstance(value.get("id"), str) or not ID_RE.fullmatch(value["id"]) or \
            not _canonical(value.get("name"), NAME_LIMIT) or \
            not _whole(value.get("run"), 1, RUN_MAX):
        raise ScheduleError("schedule must be {id, name, run}: a schedule id, its name "
                            "and the run's number")
    return {"format": FORMAT, "schedule": value["id"], "name": value["name"],
            "run": value["run"]}


def _valid_run_record(value) -> bool:
    return isinstance(value, dict) and set(value) == RUN_KEYS and \
        type(value["format"]) is int and value["format"] == FORMAT and \
        isinstance(value["schedule"], str) and bool(ID_RE.fullmatch(value["schedule"])) and \
        _canonical(value["name"], NAME_LIMIT) and _whole(value["run"], 1, RUN_MAX)


def run_record(sid):
    """The run marker of one session, or None."""
    row = db.query_one("SELECT value FROM meta WHERE key=?", (_run_key(sid),))
    if row is None:
        return None
    try:
        value = _decode(row["value"])
    except (TypeError, ValueError):
        return None
    return value if _valid_run_record(value) else None


def run_records() -> dict:
    out = {}
    for row in db.query("SELECT key,value FROM meta WHERE key GLOB ?", (RUN_PREFIX + "*",)):
        try:
            sid = int(row["key"][len(RUN_PREFIX):])
            value = _decode(row["value"])
        except (TypeError, ValueError):
            continue
        if _valid_run_record(value):
            out[sid] = value
    return out


def mark_run(sid, record: dict) -> None:
    if not _valid_run_record(record):
        raise ScheduleError("invalid schedule run record")
    db.meta_set(_run_key(sid), record)


def run_public(value, waiting: bool = False) -> dict:
    return {"id": value["schedule"], "name": value["name"], "run": value["run"],
            "waiting": bool(waiting)}


def run_payload(sid):
    """The additive ``schedule`` field of one session payload (None when the
    session was not started by a schedule)."""
    value = run_record(sid)
    if value is None:
        return None
    from puppy import runner
    hub = runner._hubs.get(int(sid))
    return run_public(value, bool(hub and hub.pending_approval))


def decorate(rows) -> None:
    records = run_records()
    if not records:
        return
    from puppy import runner
    for row in rows:
        value = records.get(row.get("id"))
        if value is None:
            continue
        hub = runner._hubs.get(row["id"])
        row["schedule"] = run_public(value, bool(hub and hub.pending_approval))


def validate_runs_persisted(connection) -> None:
    """Every run marker is exact and belongs to a session that exists."""
    sessions = {int(row[0]) for row in connection.execute("SELECT id FROM sessions")}
    for key, raw in connection.execute(
            "SELECT key,value FROM meta WHERE key GLOB ?", (RUN_PREFIX + "*",)).fetchall():
        suffix = str(key)[len(RUN_PREFIX):]
        try:
            sid = int(suffix)
            value = _decode(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule run marker has an unsupported shape") from exc
        if str(sid) != suffix or sid not in sessions or not _valid_run_record(value):
            raise ValueError("schedule run marker has an unsupported shape")


def register_node(app) -> None:
    async def validate(_app):
        validate_runs_persisted(db.connect())
    app.on_startup.append(validate)


# ---- the controller half: the records ----

PREFIX = "schedule."
RUNS_PREFIX = "schedule_runs."
MAX_SCHEDULES = 100
PROMPT_LIMIT = 20000
HISTORY_LIMIT = 50
KEEP_MAX = 50
KEEP_DEFAULT = 10
LIMIT_MAX_MINUTES = 1440
LIMIT_DEFAULT_MINUTES = 60
INTERVAL_MAX_MINUTES = 525600
TIMES_MAX = 24
MAX_RUNS_MAX = 100000
ANSWER_LIMIT = 4000
FLAG_LIMIT = 300
REASON_LIMIT = 500
EXCERPT_LIMIT = 120
YEAR_MIN, YEAR_MAX = 2000, 2200

KINDS = ("once", "interval", "daily", "weekly", "monthly", "cron")
LATE = ("run", "skip")
NOTIFY = ("never", "failure", "always", "agent")
WORKSPACES = ("directory", "temporary")
STATES = ("starting", "working", "ok", "failed", "stopped", "missed", "skipped", "lost")
ACTIVE = ("starting", "working")
TRIGGERS = ("schedule", "late", "manual")
SETTING_KEYS = ("name", "prompt", "enabled", "backend", "engine", "model", "effort",
                "permission_mode", "workspace_kind", "cwd", "timing", "until",
                "max_runs", "late", "limit_minutes", "notify", "alert_command", "keep")
STATE_KEYS = ("format", "id", "created_at", "updated_at", "next_at", "pending_at",
              "scheduled_runs")
RECORD_KEYS = set(SETTING_KEYS) | set(STATE_KEYS)
RUNS_KEYS = {"format", "next_run", "runs"}
RUN_ENTRY_KEYS = {"n", "trigger", "due_at", "started_at", "finished_at", "backend",
                  "session", "state", "reason", "answer", "flag", "notified", "kept"}
ENGINE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
TIME_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
TIMING_KEYS = {
    "once": {"kind", "date", "time"},
    "interval": {"kind", "minutes", "date", "time"},
    "daily": {"kind", "times"},
    "weekly": {"kind", "days", "times"},
    "monthly": {"kind", "days", "times"},
    "cron": {"kind", "expr"},
}


def _date(value) -> datetime.date:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ScheduleError("Use a date like 2026-10-16")
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        raise ScheduleError("{} is not a date".format(value)) from None
    if not YEAR_MIN <= day.year <= YEAR_MAX:
        raise ScheduleError("Use a date between {} and {}".format(YEAR_MIN, YEAR_MAX))
    return day


def _times(value) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= TIMES_MAX or \
            any(not isinstance(item, str) or not TIME_RE.fullmatch(item) for item in value) or \
            value != sorted(set(value)):
        raise ScheduleError("Use 1–{} different times of day, earliest first".format(TIMES_MAX))
    return list(value)


# cron: five fields, the names and the shortcuts Vixie cron reads
CRON_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31),
               ("month", 1, 12), ("weekday", 0, 7))
CRON_NAMES = {
    "month": {name: index for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)},
    "weekday": {name: index for index, name in enumerate(
        ("sun", "mon", "tue", "wed", "thu", "fri", "sat"))},
}
CRON_MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
               "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
               "@hourly": "0 * * * *"}
CRON_LIMIT = 200


def _cron_value(name: str, token: str, low: int, high: int) -> int:
    names = CRON_NAMES.get(name, {})
    if token in names:
        return names[token]
    if not token.isdigit():
        raise ScheduleError("Cron {} field: '{}' is not a value".format(name, token))
    value = int(token)
    if not low <= value <= high:
        raise ScheduleError("Cron {} field: {} is outside {}–{}".format(name, value, low, high))
    return value


def _cron_field(name: str, low: int, high: int, part: str) -> frozenset:
    values = set()
    for item in part.split(","):
        base, slash, step_text = item.partition("/")
        if not base:
            raise ScheduleError("Cron {} field: '{}' is incomplete".format(name, item or part))
        step = 1
        if slash:
            if not step_text.isdigit() or not 1 <= int(step_text) <= high:
                raise ScheduleError("Cron {} field: '/{}' is not a step".format(name, step_text))
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, last = base.split("-", 1)
            start = _cron_value(name, first, low, high)
            end = _cron_value(name, last, low, high)
            if start > end:
                raise ScheduleError("Cron {} field: {} runs backwards".format(name, base))
        else:
            start = _cron_value(name, base, low, high)
            end = high if slash else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


@functools.lru_cache(maxsize=256)
def parse_cron(expr: str) -> dict:
    """A cron expression's sets. Vixie cron's day rule: when the day of the
    month or the weekday field starts with ``*`` both must match, otherwise
    either may."""
    text = " ".join(str(expr).split()).lower()
    if not text or len(text) > CRON_LIMIT:
        raise ScheduleError("Enter a cron expression of five fields: minute hour day month weekday")
    source = CRON_MACROS.get(text, text)
    if source.startswith("@"):
        raise ScheduleError("Use @hourly, @daily, @weekly, @monthly or @yearly, or five fields")
    parts = source.split(" ")
    if len(parts) != 5:
        raise ScheduleError("A cron expression has five fields: minute hour day month weekday")
    minutes, hours, days, months, weekdays = (
        _cron_field(name, low, high, part) for (name, low, high), part in zip(CRON_FIELDS, parts))
    return {"text": text, "minutes": tuple(sorted(minutes)), "hours": tuple(sorted(hours)),
            "days": days, "months": months,
            "weekdays": frozenset(0 if day == 7 else day for day in weekdays),
            "loose": parts[2].startswith("*") or parts[4].startswith("*")}


def normalize_timing(value) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise ScheduleError("Choose when the schedule repeats")
    kind = value["kind"]
    if set(value) != TIMING_KEYS[kind]:
        raise ScheduleError("The {} timing has the fields {}".format(
            kind, ", ".join(sorted(TIMING_KEYS[kind] - {"kind"}))))
    if kind in ("once", "interval"):
        _date(value["date"])
        if not isinstance(value["time"], str) or not TIME_RE.fullmatch(value["time"]):
            raise ScheduleError("Use a time of day like 06:00")
        out = {"kind": kind, "date": value["date"], "time": value["time"]}
        if kind == "interval":
            if not _whole(value["minutes"], 1, INTERVAL_MAX_MINUTES):
                raise ScheduleError("Repeat every 1 to {} minutes".format(INTERVAL_MAX_MINUTES))
            out["minutes"] = value["minutes"]
        return out
    if kind == "cron":
        if not isinstance(value["expr"], str):
            raise ScheduleError("Enter a cron expression")
        spec = parse_cron(value["expr"])
        return {"kind": kind, "expr": spec["text"]}
    out = {"kind": kind, "times": _times(value["times"])}
    if kind == "weekly":
        days = value["days"]
        if not isinstance(days, list) or not days or \
                any(not _whole(day, 1, 7) for day in days) or days != sorted(set(days)):
            raise ScheduleError("Choose the weekdays it runs on")
        out["days"] = list(days)
    elif kind == "monthly":
        days = value["days"]
        if not isinstance(days, list) or not days or len(days) > 32 or \
                any(type(day) is not int or not (day == -1 or 1 <= day <= 31) for day in days) or \
                days != sorted(set(days)):
            raise ScheduleError("Choose the days of the month it runs on")
        out["days"] = list(days)
    return out


def normalize_settings(value) -> dict:
    """A schedule's settings exactly as stored, or a ScheduleError naming
    what is wrong. Nothing is filled in or corrected."""
    if not isinstance(value, dict):
        raise ScheduleError("A schedule is an object")
    missing = [key for key in SETTING_KEYS if key not in value]
    extra = sorted(set(value) - set(SETTING_KEYS))
    if missing or extra:
        raise ScheduleError("A schedule has exactly these fields: " + ", ".join(SETTING_KEYS))
    name = value["name"].strip() if isinstance(value["name"], str) else None
    if not _canonical(name, NAME_LIMIT):
        raise ScheduleError("Give the schedule a name of up to {} characters on one line".format(NAME_LIMIT))
    prompt = value["prompt"].strip() if isinstance(value["prompt"], str) else ""
    if not prompt:
        raise ScheduleError("Enter the prompt the schedule sends")
    if len(prompt) > PROMPT_LIMIT or "\x00" in prompt:
        raise ScheduleError("A scheduled prompt is at most {} characters".format(PROMPT_LIMIT))
    if type(value["enabled"]) is not bool or type(value["alert_command"]) is not bool:
        raise ScheduleError("enabled and alert_command are true or false")
    if not _whole(value["backend"], 0, 2 ** 31 - 1):
        raise ScheduleError("Choose a backend")
    if not isinstance(value["engine"], str) or not ENGINE_RE.fullmatch(value["engine"]):
        raise ScheduleError("Choose an engine")
    if not _canonical(value["model"], 256, allow_empty=True):
        raise ScheduleError("A model is one model ID, or empty for the engine's default")
    if not _canonical(value["effort"], 64, allow_empty=True):
        raise ScheduleError("An effort is one level, or empty for the engine's default")
    if not _canonical(value["permission_mode"], 64):
        raise ScheduleError("Choose the permissions the run works with")
    if value["workspace_kind"] not in WORKSPACES:
        raise ScheduleError("Choose a directory or a scratch workspace")
    cwd = value["cwd"]
    if value["workspace_kind"] == "directory":
        if not _canonical(cwd, 4096) or not os.path.isabs(cwd) or os.path.normpath(cwd) != cwd:
            raise ScheduleError("Use an absolute working directory, without a trailing slash")
    elif cwd != "":
        raise ScheduleError("A scratch workspace has no directory")
    timing = normalize_timing(value["timing"])
    until = value["until"]
    if until != "":
        _date(until)
    if not _whole(value["max_runs"], 0, MAX_RUNS_MAX):
        raise ScheduleError("Stop after 1 to {} runs, or 0 for no limit".format(MAX_RUNS_MAX))
    if timing["kind"] == "once" and (until or value["max_runs"]):
        raise ScheduleError("A schedule that runs once has no end date or run count")
    if value["late"] not in LATE:
        raise ScheduleError("Choose what happens to a run that cannot start on time")
    if not _whole(value["limit_minutes"], 0, LIMIT_MAX_MINUTES):
        raise ScheduleError("Use a time limit of 0 to {} minutes".format(LIMIT_MAX_MINUTES))
    if value["notify"] not in NOTIFY:
        raise ScheduleError("Choose when the schedule notifies")
    if not _whole(value["keep"], 1, KEEP_MAX):
        raise ScheduleError("Keep 1 to {} conversations".format(KEEP_MAX))
    return {"name": name, "prompt": prompt, "enabled": value["enabled"],
            "backend": value["backend"], "engine": value["engine"], "model": value["model"],
            "effort": value["effort"], "permission_mode": value["permission_mode"],
            "workspace_kind": value["workspace_kind"], "cwd": cwd,
            "timing": timing, "until": until, "max_runs": value["max_runs"],
            "late": value["late"], "limit_minutes": value["limit_minutes"],
            "notify": value["notify"], "alert_command": value["alert_command"],
            "keep": value["keep"]}


def _valid_record(value, key_id=None) -> bool:
    try:
        if not isinstance(value, dict) or set(value) != RECORD_KEYS or \
                type(value["format"]) is not int or value["format"] != FORMAT or \
                not isinstance(value["id"], str) or not ID_RE.fullmatch(value["id"]) or \
                (key_id is not None and value["id"] != key_id):
            return False
        settings = {key: value[key] for key in SETTING_KEYS}
        if normalize_settings(settings) != settings:
            return False
        return all(_stamp(value[key]) for key in ("created_at", "updated_at", "next_at", "pending_at")) and \
            _whole(value["scheduled_runs"], 0, MAX_RUNS_MAX) and \
            (value["enabled"] or not (value["next_at"] or value["pending_at"]))
    except (ScheduleError, TypeError, KeyError, AttributeError):
        return False


def _valid_run(value) -> bool:
    if not isinstance(value, dict) or set(value) != RUN_ENTRY_KEYS:
        return False
    if not _whole(value["n"], 1, RUN_MAX) or value["trigger"] not in TRIGGERS or \
            value["state"] not in STATES or not all(
                _stamp(value[key]) for key in ("due_at", "started_at", "finished_at")) or \
            not _whole(value["backend"], 0, 2 ** 31 - 1) or \
            not _whole(value["session"], 0, 2 ** 62) or \
            type(value["notified"]) is not bool or type(value["kept"]) is not bool:
        return False
    for key, limit in (("reason", REASON_LIMIT), ("answer", ANSWER_LIMIT), ("flag", FLAG_LIMIT)):
        if not isinstance(value[key], str) or len(value[key]) > limit or "\x00" in value[key]:
            return False
    active = value["state"] in ACTIVE
    if active != (value["finished_at"] == 0):
        return False
    if value["state"] in ("missed", "skipped") and (
            value["session"] or value["kept"] or value["trigger"] == "manual"):
        return False
    if value["state"] == "working" and not value["session"]:
        return False
    return True


def _valid_runs(value) -> bool:
    if not isinstance(value, dict) or set(value) != RUNS_KEYS or \
            type(value["format"]) is not int or value["format"] != FORMAT or \
            not _whole(value["next_run"], 1, RUN_MAX + 1) or \
            not isinstance(value["runs"], list) or len(value["runs"]) > HISTORY_LIMIT:
        return False
    previous = 0
    for run in value["runs"]:
        if not _valid_run(run) or run["n"] <= previous or run["n"] >= value["next_run"]:
            return False
        previous = run["n"]
    return sum(run["state"] in ACTIVE for run in value["runs"]) <= 1


def validate_persisted(connection) -> None:
    """Every schedule and run history is exact, and every history belongs to
    a schedule; malformed state is refused, never repaired."""
    schedules, histories = {}, {}
    for key, raw in connection.execute(
            "SELECT key,value FROM meta WHERE key GLOB ? OR key GLOB ?",
            (PREFIX + "*", RUNS_PREFIX + "*")).fetchall():
        key = str(key)
        try:
            value = _decode(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule state contains invalid JSON") from exc
        if key.startswith(RUNS_PREFIX):
            ident = key[len(RUNS_PREFIX):]
            if not ID_RE.fullmatch(ident) or not _valid_runs(value):
                raise ValueError("schedule run history has an unsupported shape")
            histories[ident] = value
        else:
            ident = key[len(PREFIX):]
            if not ID_RE.fullmatch(ident) or not _valid_record(value, ident):
                raise ValueError("schedule has an unsupported shape")
            schedules[ident] = value
    if len(schedules) > MAX_SCHEDULES or set(histories) - set(schedules):
        raise ValueError("schedule state has an unsupported shape")
    validate_runs_persisted(connection)


def _load(ident: str):
    row = db.query_one("SELECT value FROM meta WHERE key=?", (PREFIX + ident,))
    if row is None:
        return None
    value = _decode(row["value"])
    if not _valid_record(value, ident):
        raise RuntimeError("schedule has an unsupported persisted shape")
    return value


def _new_history() -> dict:
    return {"format": FORMAT, "next_run": 1, "runs": []}


def _load_history(ident: str) -> dict:
    row = db.query_one("SELECT value FROM meta WHERE key=?", (RUNS_PREFIX + ident,))
    if row is None:
        return _new_history()
    value = _decode(row["value"])
    if not _valid_runs(value):
        raise RuntimeError("schedule run history has an unsupported persisted shape")
    return value


def _load_all() -> list:
    out = []
    for row in db.query("SELECT key,value FROM meta WHERE key GLOB ? ORDER BY key",
                        (PREFIX + "*",)):
        ident = str(row["key"])[len(PREFIX):]
        value = _decode(row["value"])
        if not _valid_record(value, ident):
            raise RuntimeError("schedule has an unsupported persisted shape")
        out.append(value)
    out.sort(key=lambda item: (item["created_at"], item["id"]))
    return out


def _save(record: dict, history: dict = None) -> None:
    if not _valid_record(record, record["id"]):
        raise RuntimeError("cannot persist an invalid schedule")
    upserts = {PREFIX + record["id"]: record}
    if history is not None:
        if not _valid_runs(history):
            raise RuntimeError("cannot persist an invalid schedule history")
        upserts[RUNS_PREFIX + record["id"]] = history
    db.meta_apply(upserts)


# ---- when ----

SEARCH_DAYS = 366 * 8 + 2      # a cron 29 February across a skipped leap year


def _local(day: datetime.date, hour: int, minute: int) -> float:
    return float(time.mktime((day.year, day.month, day.day, hour, minute, 0, 0, 0, -1)))


def _wall(date_text: str, time_text: str) -> float:
    hour, minute = map(int, time_text.split(":"))
    return _local(_date(date_text), hour, minute)


def _until_instant(until: str) -> float:
    """The first instant after the end date's day, 0 without one."""
    if not until:
        return 0.0
    return _local(_date(until) + datetime.timedelta(days=1), 0, 0)


def _calendar(timing: dict):
    kind = timing["kind"]
    if kind == "cron":
        spec = parse_cron(timing["expr"])
        times = [(hour, minute) for hour in spec["hours"] for minute in spec["minutes"]]

        def day_ok(day):
            if day.month not in spec["months"]:
                return False
            date_ok = day.day in spec["days"]
            weekday_ok = day.isoweekday() % 7 in spec["weekdays"]
            return (date_ok and weekday_ok) if spec["loose"] else (date_ok or weekday_ok)
        return day_ok, times
    times = sorted(tuple(map(int, item.split(":"))) for item in timing["times"])
    if kind == "daily":
        return (lambda day: True), times
    days = set(timing["days"])
    if kind == "weekly":
        return (lambda day: day.isoweekday() in days), times
    return (lambda day: day.day in days or (
        -1 in days and day.day == calendar.monthrange(day.year, day.month)[1])), times


def next_occurrence(timing: dict, after: float) -> float:
    """The first occurrence strictly after ``after``, 0 when there is none."""
    kind = timing["kind"]
    if kind == "once":
        at = _wall(timing["date"], timing["time"])
        return at if at > after else 0.0
    if kind == "interval":
        anchor = _wall(timing["date"], timing["time"])
        step = timing["minutes"] * 60
        if after < anchor:
            return anchor
        return anchor + (math.floor((after - anchor) / step) + 1) * step
    day_ok, times = _calendar(timing)
    first = datetime.date.fromtimestamp(after) - datetime.timedelta(days=1)
    for offset in range(SEARCH_DAYS):
        day = first + datetime.timedelta(days=offset)
        if not day_ok(day):
            continue
        for hour, minute in times:
            at = _local(day, hour, minute)
            if at > after:
                return at
    return 0.0


def _remaining(record: dict, reserved: int = 0):
    """Scheduled runs still to come under the run limit (None: no limit): a
    pending occurrence, and ``reserved`` ones already counted, are spoken for."""
    if not record["max_runs"]:
        return None
    return record["max_runs"] - record["scheduled_runs"] - \
        (1 if record["pending_at"] else 0) - reserved


def upcoming(record: dict, after: float, count: int = 3, reserved: int = 0) -> list:
    if not record["enabled"]:
        return []
    remaining = _remaining(record, reserved)
    limit = _until_instant(record["until"])
    out, at = [], after
    while len(out) < count and (remaining is None or len(out) < remaining):
        at = next_occurrence(record["timing"], at)
        if not at or (limit and at >= limit):
            break
        out.append(at)
    return out


def _next_at(record: dict, after: float) -> float:
    found = upcoming(record, after, 1)
    return found[0] if found else 0.0


def preview(timing, until="", max_runs=0, count: int = 5) -> list:
    """The next occurrences of unsaved settings, for the editor."""
    record = {"timing": normalize_timing(timing), "until": "", "max_runs": 0,
              "scheduled_runs": 0, "pending_at": 0.0, "enabled": True}
    if until != "":
        _date(until)
        record["until"] = until
    if not _whole(max_runs, 0, MAX_RUNS_MAX):
        raise ScheduleError("Stop after 1 to {} runs, or 0 for no limit".format(MAX_RUNS_MAX))
    record["max_runs"] = max_runs
    return upcoming(record, time.time(), max(1, min(int(count), 10)))


# ---- what a run is told and what it answered ----

FLAG_RE = re.compile(r"^[\s>*_`#-]*NOTIFY[\s*_`]*[:：][\s*_`]*(.*?)[\s*_`]*$", re.IGNORECASE)
NO_FLAG = {"", "none", "nothing", "n/a", "na", "no", "-"}


def _clock(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(at))


def compose_prompt(record: dict, run: dict, previous=None) -> str:
    """The prompt a run sends: the schedule's own, then one visible note
    saying where it came from - and, when the agent decides about alerts,
    how it raises one."""
    note = ('Puppy started this prompt from the schedule "{}" (run {}) at {}, with nobody '
            'watching. Work on your own without asking questions, and finish with a short '
            'summary of what you found or did.').format(
                record["name"], run["n"], _clock(run["started_at"]))
    if previous is not None:
        outcome = {"ok": "finished", "failed": "failed", "stopped": "was stopped",
                   "lost": "was lost"}.get(previous["state"], "ended")
        note += " The previous run started at {} and {}.".format(
            _clock(previous["started_at"]), outcome)
    if record["notify"] == "agent":
        note += (' If the user should be alerted about something, make the last line of '
                 'your final answer "NOTIFY: " followed by one short sentence for the alert. '
                 'If nothing needs their attention, leave that line out.')
    return record["prompt"] + "\n\n---\n" + note


def parse_flag(answer: str) -> str:
    """The alert an agent asked for: its last NOTIFY line, or ""."""
    for line in reversed(str(answer or "").splitlines()):
        match = FLAG_RE.match(line)
        if match:
            text = " ".join(match.group(1).split())
            if text.lower().rstrip(".!") in NO_FLAG:
                return ""
            return text[:FLAG_LIMIT]
    return ""


def _bounded(text: str, limit: int) -> str:
    text = str(text or "").replace("\x00", "")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _first_line(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """The first line of prose, without its Markdown marks or a NOTIFY line."""
    for line in str(text or "").splitlines():
        if FLAG_RE.match(line):
            continue
        flat = " ".join(line.split()).lstrip("#>*-_` ").strip()
        if flat:
            return _bounded(flat, limit)
    return ""


def _answer_text(event) -> tuple:
    """(text, failure text) of the event a prompt's turn ended at."""
    if not isinstance(event, dict):
        return "", ""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    kind = event.get("kind")
    if kind == "assistant":
        return str(data.get("text") or ""), ""
    if kind == "result":
        error = str(data.get("error") or "")
        return ("" if error else str(data.get("result") or "")), error
    if kind == "error":
        return "", str(data.get("text") or "")
    return "", ""


# ---- reaching the node a run lives on ----

def _backend_name(bid: int) -> str:
    if not bid:
        return str(config.get("instance_name") or "this instance")
    from puppy import backends
    backend = backends.get_backend(bid)
    return str((backend or {}).get("name") or "backend {}".format(bid))


def _channel(bid: int) -> dict:
    from puppy import backends
    if backends.get_backend(bid) is None:
        raise ScheduleError("its backend no longer exists", 404)
    channel = backends.node_channel(bid)
    if channel is None:
        raise ScheduleError("{} is offline".format(_backend_name(bid)), 503)
    if protocol.SESSION_SCHEDULE_RUNS_CAPABILITY not in (channel.get("capabilities") or []):
        raise ScheduleError("{} needs a Puppy upgrade before it can run schedules".format(
            channel.get("name") or bid), 409)
    return channel


async def _request(bid: int, method: str, path: str, body=None, timeout_s: float = 30.0) -> dict:
    from puppy import spawn_exec
    channel = _channel(bid)
    try:
        return await spawn_exec.node_request(channel, method, path, body=body, timeout_s=timeout_s)
    except spawn_exec.SpawnError as exc:
        status = 502 if exc.unreached else exc.status
        raise ScheduleError(str(exc), status,
                            uncertain=exc.unreached or exc.status >= 500) from None


def _engine_blocker(record: dict) -> str:
    """This instance's own engine being updated holds a run until it is done
    (a backend refuses the prompt itself, which fails the run)."""
    if record["backend"]:
        return ""
    from puppy import cli_upgrade
    if not cli_upgrade.is_running(record["engine"]):
        return ""
    try:
        from puppy.drivers import get_driver
        label = get_driver(record["engine"]).label
    except KeyError:
        label = record["engine"]
    return "{} is being updated".format(label)


def _backend_blocker(bid: int) -> str:
    if not bid:
        return ""
    from puppy import backends
    backend = backends.get_backend(bid)
    if backend is None:
        return "its backend no longer exists"
    if not backends.backend_is_online(bid):
        return "{} is offline".format(backend.get("name") or bid)
    caps = backends._backend_capabilities(backend)
    if protocol.SESSION_SCHEDULE_RUNS_CAPABILITY not in caps:
        return "{} needs a Puppy upgrade before it can run schedules".format(
            backend.get("name") or bid)
    return ""


async def _create(bid: int, body: dict) -> int:
    if not bid:
        from puppy import web as webui
        payload, status = await webui.create_session(dict(body))
        if status != 200:
            raise ScheduleError(str(payload.get("error") or "the session was not created"), status)
        return int(payload["session"]["id"])
    data = await _request(bid, "POST", "sessions", body=body, timeout_s=120.0)
    session = data.get("session")
    if not isinstance(session, dict) or type(session.get("id")) is not int:
        raise ScheduleError("{} answered without a session".format(_backend_name(bid)), 502)
    return session["id"]


async def _send(bid: int, sid: int, text: str) -> None:
    if not bid:
        from puppy import runner
        result = runner.hub(sid).send_message(text)
        if "error" in result:
            raise ScheduleError(str(result["error"]), 409)
        return
    await _request(bid, "POST", "sessions/{}/message".format(sid), body={"text": text},
                   timeout_s=30.0)


async def _interrupt(bid: int, sid: int) -> None:
    if not bid:
        from puppy import runner
        await runner.hub(sid).interrupt()
        return
    await _request(bid, "POST", "sessions/{}/interrupt".format(sid), body={}, timeout_s=30.0)


async def _answer(bid: int, sid: int) -> tuple:
    """(answer, failure text) of a run's own prompt: the event its turn ended
    at, as the node's prompt index names it."""
    if not bid:
        from puppy import prompt_index
        loop = asyncio.get_running_loop()
        index = await loop.run_in_executor(None, prompt_index.read, sid, 0, 5)
        rows = index.get("prompts") or []
        row = next((item for item in rows if not item[2]), None)
        if row is None or not row[4]:
            return "", ""
        events = db.get_events(sid, after_seq=row[4] - 1, limit=1)
        return _answer_text(events[0] if events else None)
    index = await _request(bid, "GET", "sessions/{}/prompts?after_seq=0&limit=5".format(sid))
    rows = index.get("prompts") if isinstance(index.get("prompts"), list) else []
    row = next((item for item in rows if isinstance(item, list) and len(item) >= 6 and
                not item[2]), None)
    if row is None or type(row[4]) is not int or row[4] < 1:
        return "", ""
    data = await _request(bid, "GET", "sessions/{}/events?after_seq={}&limit=1".format(
        sid, row[4] - 1))
    events = data.get("events") if isinstance(data.get("events"), list) else []
    return _answer_text(events[0] if events else None)


async def _drop(bid: int, sid: int, ident: str, n: int) -> bool:
    """Delete one run's conversation. True once it is gone (or is no longer
    that run's), False to try again later."""
    if not bid:
        from puppy import runner
        session = db.get_session(sid)
        marker = run_record(sid) if session else None
        if session is None or marker is None or marker["schedule"] != ident or marker["run"] != n:
            return True
        hub = runner._hubs.get(sid)
        if hub is not None and (hub.status == "running" or hub.queue):
            return False
        from puppy import session_tasks
        if session_tasks.delete_blocker(session):
            return False
        from puppy import web as webui
        await webui.remove_session(session)
        return True
    try:
        data = await _request(bid, "GET", "sessions/{}".format(sid))
    except ScheduleError as exc:
        if exc.status == 404 and not exc.uncertain:
            return True
        raise
    marker = (data.get("session") or {}).get("schedule") if isinstance(data.get("session"), dict) else None
    if not isinstance(marker, dict) or marker.get("id") != ident or marker.get("run") != n:
        return True
    if data.get("status") == "running":
        return False
    try:
        await _request(bid, "DELETE", "sessions/{}".format(sid))
    except ScheduleError as exc:
        if exc.status == 404 and not exc.uncertain:
            return True
        if exc.status == 409:
            return False
        raise
    return True


# ---- notifying ----

def _wants(record: dict, kind: str) -> bool:
    policy = record["notify"]
    if policy == "never":
        return False
    if kind == "finished":
        return policy == "always"
    if kind == "flagged":
        return policy == "agent"
    return True      # failed, limit, missed, waiting


def _notice_text(record: dict, run: dict, kind: str) -> tuple:
    name = record["name"]
    n = run["n"]
    if kind == "finished":
        line = _first_line(run["answer"])
        return "{}: Run {} finished{}".format(name, n, " · " + line if line else ""), "ok"
    if kind == "flagged":
        return "{}: {} · run {}".format(name, run["flag"], n), "warn"
    if kind == "limit":
        return "{}: Run {} stopped · it ran past its {}-minute limit".format(
            name, n, record["limit_minutes"]), "warn"
    if kind == "waiting":
        return "{}: Run {} is waiting for your approval".format(name, n), "warn"
    if kind == "missed":
        return "{}: Run missed · {}".format(name, run["reason"]), "warn"
    return "{}: Run {} failed · {}".format(name, n, run["reason"] or "unknown error"), "bad"


def _deliver(record: dict, run: dict, kind: str) -> bool:
    """One notice: kept in the controller's notification history, shown on
    every open console, and through the completion alert command when the
    schedule asks for it and alerts are armed. Never raises."""
    if not _wants(record, kind):
        return False
    text, tone = _notice_text(record, run, kind)
    try:
        from puppy import notices
        notices.record(text, tone)
    except Exception:
        log.exception("could not keep a schedule notice")
    try:
        from puppy import runner
        runner.broadcast_update({"type": "toast", "level": tone, "text": text[:400],
                                 "recorded": True})
    except Exception:
        log.exception("could not show a schedule notice")
    if record["alert_command"]:
        try:
            from puppy import notify
            if notify.active():
                duration = max(0, int((run["finished_at"] or time.time()) - run["started_at"]))
                info = {"backend": _backend_name(run["backend"]), "session": record["name"],
                        "engine": record["engine"], "model": record["model"],
                        "status": "ok" if kind == "finished" else "error",
                        "duration": str(duration), "cwd": record["cwd"],
                        "id": str(run["session"] or ""), "message": text}
                asyncio.ensure_future(notify._fire(info))
        except Exception:
            log.exception("could not run the alert command for a schedule")
    return True


# ---- the public view ----

def _excerpt(prompt: str) -> str:
    return _first_line(prompt) or _bounded(" ".join(prompt.split()), EXCERPT_LIMIT)


def _run_public(run: dict, live=None, answer: bool = False) -> dict:
    out = {key: run[key] for key in ("n", "trigger", "due_at", "started_at", "finished_at",
                                     "backend", "session", "state", "reason", "flag", "kept")}
    out["waiting"] = bool(live and live.get("waiting")) if run["state"] in ACTIVE else False
    if answer:
        out["answer"] = run["answer"]
    else:
        out["excerpt"] = _first_line(run["answer"])
    return out


def _state_word(record: dict, active) -> str:
    if active is not None:
        return "working"
    if record["pending_at"]:
        return "waiting"
    if not record["enabled"]:
        return "off"
    return "scheduled" if record["next_at"] else "ended"


def public(record: dict, history: dict, full: bool = False) -> dict:
    scheduler = _scheduler
    active = next((run for run in reversed(history["runs"]) if run["state"] in ACTIVE), None)
    last = active or (history["runs"][-1] if history["runs"] else None)
    live = scheduler.live_of(record["id"], last) if scheduler and last else None
    out = {key: record[key] for key in SETTING_KEYS if key != "prompt"}
    if full:
        out["prompt"] = record["prompt"]
    out.update({
        "id": record["id"], "created_at": record["created_at"],
        "updated_at": record["updated_at"], "next_at": record["next_at"],
        "pending_at": record["pending_at"], "scheduled_runs": record["scheduled_runs"],
        "pending_reason": scheduler.blocked.get(record["id"], "") if scheduler and
        record["pending_at"] else "",
        "excerpt": _excerpt(record["prompt"]),
        "state": _state_word(record, active),
        "upcoming": [record["next_at"]] + upcoming(record, record["next_at"], 2, reserved=1)
        if record["next_at"] else [],
        "run": _run_public(last, live) if last else None,
        "runs": len(history["runs"]),
        "next_run": history["next_run"],
        # the runs whose conversations this schedule keeps (and the one it is
        # starting): a console keeps exactly those out of its sidebar
        "kept_runs": [run["n"] for run in history["runs"] if run["kept"]],
    })
    return out


def public_state() -> dict:
    try:
        records = _load_all()
        items = [public(record, _load_history(record["id"])) for record in records]
    except Exception:
        log.exception("could not read the schedules")
        items = []
    return {"type": "schedules", "schedules": items,
            "timezone": time.strftime("%Z"), "server_time": time.time(),
            "limits": {"schedules": MAX_SCHEDULES, "prompt": PROMPT_LIMIT,
                       "history": HISTORY_LIMIT, "keep": KEEP_MAX,
                       "keep_default": KEEP_DEFAULT, "limit_minutes": LIMIT_MAX_MINUTES,
                       "limit_default": LIMIT_DEFAULT_MINUTES,
                       "interval_minutes": INTERVAL_MAX_MINUTES, "times": TIMES_MAX,
                       "max_runs": MAX_RUNS_MAX}}


def publish() -> dict:
    from puppy import runner
    return runner.publish_state(public_state())


# ---- the worker ----

_scheduler = None
_paused = False
LATE_GRACE = 120.0          # an occurrence started this late is still on time
SWEEP_S = 15.0              # the longest the worker sleeps
SETTLE_S = 60.0             # an idle run without a completion is read after this
START_TIMEOUT = 150.0
ANSWER_TIMEOUT = 30.0
INTERRUPT_AGAIN_S = 60.0
READ_AGAIN_S = 30.0         # a remote run nothing has reported on is read directly
MISSED_SCAN = 10000


class Scheduler:
    def __init__(self, app):
        self.app = app
        self.wake = asyncio.Event()
        self.task = None
        self.stopping = False
        self.tasks = set()
        self.starting = set()     # (schedule id, n) being created and sent
        self.finishing = set()    # (schedule id, n) being read and settled
        self.dropping = set()     # (schedule id, n) whose conversation is being deleted
        self.rows = {}            # (bid, sid) -> (row, time.time() seen)
        self.present = set()      # (bid, sid) a session list has carried
        self.reading = set()      # bids being read directly
        self.read_at = {}         # bid -> monotonic of the last direct read
        self.idle_since = {}      # (schedule id, n) -> time an idle run had no completion
        self.waiting = {}         # (schedule id, n) -> whether it waits for an approval
        self.stop_reason = {}     # (schedule id, n) -> why this controller stopped it
        self.interrupted_at = {}  # (schedule id, n) -> monotonic of the last interrupt
        self.blocked = {}         # schedule id -> why its pending occurrence waits
        self.index = {}           # (bid, sid) -> (schedule id, n, time registered)

    # -- lifecycle --

    def spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def poke(self) -> None:
        self.wake.set()

    async def start(self) -> None:
        self._recover()
        self.task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        self.stopping = True
        self.wake.set()
        tasks = [self.task] + list(self.tasks)
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not None),
                             return_exceptions=True)

    def _recover(self) -> None:
        """A run this process was still creating when it stopped never got its
        prompt (or may have): one with a session is followed, one without is
        a failure."""
        for record in _load_all():
            history = _load_history(record["id"])
            changed = False
            for run in history["runs"]:
                if run["state"] != "starting":
                    continue
                changed = True
                if run["session"]:
                    run["state"] = "working"
                else:
                    run.update(state="failed", finished_at=time.time(), kept=False,
                               reason="Puppy stopped before it started")
            if changed:
                _save(record, history)

    async def _loop(self) -> None:
        while not self.stopping:
            self.wake.clear()
            try:
                self.tick()
            except Exception:
                log.exception("schedule pass failed")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self._sleep())
            except asyncio.TimeoutError:
                pass

    def _sleep(self) -> float:
        now = time.time()
        delay = SWEEP_S
        try:
            for record in _load_all():
                if record["enabled"] and record["next_at"]:
                    delay = min(delay, record["next_at"] - now)
        except Exception:
            pass
        return max(0.2, delay)

    # -- one pass --

    def tick(self, now: float = None) -> None:
        now = time.time() if now is None else now
        busy = bool(_paused or self.app.get("puppy_snapshot_busy"))
        changed = False
        index = {}
        for record in _load_all():
            history = _load_history(record["id"])
            if self._due(record, history, now, busy):
                changed = True
            for run in history["runs"]:
                if run["state"] not in ACTIVE:
                    continue
                if run["backend"] and run["session"]:
                    key = (run["backend"], run["session"])
                    index[key] = self.index.get(key) or (record["id"], run["n"], now)
                if self._watch(record, run, now):
                    changed = True
            if not busy:
                self._prune(record, history)
        self.index = index
        if changed:
            publish()

    def _missed_row(self, history: dict, state: str, reason: str, due_at: float,
                    now: float) -> dict:
        run = {"n": history["next_run"], "trigger": "schedule", "due_at": due_at,
               "started_at": now, "finished_at": now, "backend": 0, "session": 0,
               "state": state, "reason": _bounded(reason, REASON_LIMIT), "answer": "",
               "flag": "", "notified": False, "kept": False}
        history["next_run"] += 1
        self._append(history, run)
        return run

    @staticmethod
    def _append(history: dict, run: dict) -> None:
        history["runs"].append(run)
        while len(history["runs"]) > HISTORY_LIMIT:
            # the oldest finished entry goes; an active run is never dropped
            index = next((at for at, item in enumerate(history["runs"])
                          if item["state"] not in ACTIVE), None)
            if index is None:
                break
            del history["runs"][index]

    def _advance(self, record: dict, now: float) -> tuple:
        """The newest due occurrence up to now and how many came due, from
        next_at on."""
        timing, at = record["timing"], record["next_at"]
        limit = _until_instant(record["until"])
        if timing["kind"] == "interval":
            step = timing["minutes"] * 60
            end = min(now, limit - 1e-6) if limit else now
            count = max(0, math.floor((end - at) / step)) + 1
            return at + (count - 1) * step, count
        count, latest = 1, at
        while count < MISSED_SCAN:
            following = next_occurrence(timing, latest)
            if not following or following > now or (limit and following >= limit):
                break
            latest, count = following, count + 1
        return latest, count

    def _due(self, record: dict, history: dict, now: float, busy: bool) -> bool:
        ident = record["id"]
        changed = False
        missed = []
        if record["enabled"] and record["next_at"] and record["next_at"] <= now:
            latest, count = self._advance(record, now)
            skipped = count - 1
            if record["pending_at"]:
                missed.append(self._missed_row(
                    history, "missed", self.blocked.get(ident) or "it could not start in time",
                    record["pending_at"], now))
            if skipped:
                missed.append(self._missed_row(
                    history, "missed", "{} occurrence{} came due while Puppy was not running".format(
                        skipped, "" if skipped == 1 else "s"), record["next_at"], now))
            record["pending_at"] = latest
            record["next_at"] = _next_at(record, now)
            changed = True
        if record["pending_at"] and not record["enabled"]:
            record["pending_at"] = 0.0
            changed = True
        if record["pending_at"]:
            active = next((run for run in history["runs"] if run["state"] in ACTIVE), None)
            blocker = "a backup or restore is in progress" if busy else \
                "the previous run is still working" if active else \
                _backend_blocker(record["backend"]) or _engine_blocker(record)
            late = now - record["pending_at"] > LATE_GRACE
            if record["late"] == "skip" and (active or late):
                # skip: an occurrence that cannot start on time does not run
                missed.append(self._missed_row(
                    history, "skipped" if active else "missed",
                    blocker or "Puppy was not running at the time", record["pending_at"], now))
                record["pending_at"] = 0.0
                self.blocked.pop(ident, None)
                changed = True
            elif not blocker:
                self._begin(record, history, "late" if late else "schedule",
                            record["pending_at"], now)
                record["pending_at"] = 0.0
                record["scheduled_runs"] += 1
                if _remaining(record) is not None and _remaining(record) <= 0:
                    record["next_at"] = 0.0
                self.blocked.pop(ident, None)
                changed = True
            elif self.blocked.get(ident) != blocker:
                self.blocked[ident] = blocker
                changed = True
        if changed:
            _save(record, history)
            notified = False
            for run in missed:
                if self._streak_starts(history, run) and _deliver(record, run, "missed"):
                    run["notified"] = notified = True
            if notified:
                _save(record, history)
        return changed

    @staticmethod
    def _streak_starts(history: dict, run: dict) -> bool:
        """A missed run is told once per streak: a run skipped because the
        one before it was still working is what the schedule asked for, and
        one missed straight after another missed one says nothing new."""
        if run["state"] != "missed":
            return False
        earlier = [item for item in history["runs"] if item["n"] < run["n"]]
        return not earlier or earlier[-1]["state"] != "missed"

    def _begin(self, record: dict, history: dict, trigger: str, due_at: float,
               now: float) -> dict:
        run = {"n": history["next_run"], "trigger": trigger, "due_at": due_at,
               "started_at": now, "finished_at": 0.0, "backend": record["backend"],
               "session": 0, "state": "starting", "reason": "", "answer": "", "flag": "",
               "notified": False, "kept": True}
        history["next_run"] += 1
        self._append(history, run)
        self.starting.add((record["id"], run["n"]))
        self.spawn(self._start(record["id"], run["n"]))
        return run

    def run_now(self, ident: str) -> dict:
        record = _load(ident)
        if record is None:
            raise ScheduleError("That schedule no longer exists", 404)
        history = _load_history(ident)
        if any(run["state"] in ACTIVE for run in history["runs"]):
            raise ScheduleError("A run of this schedule is still working", 409)
        if _paused or self.app.get("puppy_snapshot_busy"):
            raise ScheduleError("Wait for the backup or restore to finish", 409)
        blocker = _backend_blocker(record["backend"]) or _engine_blocker(record)
        if blocker:
            raise ScheduleError("Could not start a run · " + blocker, 409)
        run = self._begin(record, history, "manual", 0.0, time.time())
        _save(record, history)
        publish()
        return run

    # -- starting a run --

    def _update(self, ident: str, n: int, **fields):
        record = _load(ident)
        if record is None:
            return None, None
        history = _load_history(ident)
        run = next((item for item in history["runs"] if item["n"] == n), None)
        if run is None:
            return record, None
        run.update(fields)
        _save(record, history)
        return record, run

    async def _start(self, ident: str, n: int) -> None:
        key = (ident, n)
        try:
            record = _load(ident)
            history = _load_history(ident) if record else None
            run = next((item for item in history["runs"] if item["n"] == n), None) if history else None
            if run is None or run["state"] != "starting":
                return
            previous = next((item for item in reversed(history["runs"])
                             if item["n"] < n and item["state"] in ("ok", "failed", "stopped", "lost")),
                            None)
            text = compose_prompt(record, run, previous)
            body = {"engine": record["engine"], "model": record["model"],
                    "effort": record["effort"], "permission_mode": record["permission_mode"],
                    "workspace_kind": record["workspace_kind"], "cwd": record["cwd"],
                    "name": _bounded("{} · run {}".format(record["name"], n), NAME_LIMIT),
                    "color": db.SESSION_COLORS[int(ident, 16) % len(db.SESSION_COLORS)],
                    "auto_title": False,
                    "schedule": {"id": ident, "name": record["name"], "run": n}}
            try:
                sid = await asyncio.wait_for(_create(run["backend"], body), START_TIMEOUT)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ScheduleError) else \
                    "the session was not created in time" if isinstance(exc, asyncio.TimeoutError) \
                    else "the session could not be created"
                if not isinstance(exc, (ScheduleError, asyncio.TimeoutError)):
                    log.exception("schedule %s run %s could not create its session", ident, n)
                self._fail(ident, n, "could not create its session · " + reason)
                return
            self._update(ident, n, session=sid)
            if run["backend"]:
                self.index[(run["backend"], sid)] = (ident, n, time.time())
            publish()
            try:
                await asyncio.wait_for(_send(run["backend"], sid, text), 60.0)
            except Exception as exc:
                uncertain = isinstance(exc, asyncio.TimeoutError) or \
                    (isinstance(exc, ScheduleError) and exc.uncertain)
                if not uncertain:
                    self._fail(ident, n, "could not send the prompt · " + str(exc))
                    return
                log.warning("schedule %s run %s: the prompt's delivery is unconfirmed", ident, n)
            self._update(ident, n, state="working")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("schedule %s run %s failed to start", ident, n)
            self._fail(ident, n, "an internal error stopped it from starting")
        finally:
            self.starting.discard(key)
            publish()
            self.poke()

    def _fail(self, ident: str, n: int, reason: str) -> None:
        record, run = self._update(ident, n, state="failed", finished_at=time.time(),
                                   reason=_bounded(reason, REASON_LIMIT))
        if run is not None and not run["session"]:
            self._update(ident, n, kept=False)
            run["kept"] = False
        if run is not None and _deliver(record, run, "failed"):
            self._update(ident, n, notified=True)

    # -- following a run --

    def live_of(self, ident: str, run: dict):
        if run["state"] not in ACTIVE:
            return None
        return {"waiting": self.waiting.get((ident, run["n"]), False)}

    def _observe(self, ident: str, run: dict):
        """What the run's session says now: status, completion, waiting and
        whether it is gone - or None while nothing has been heard yet."""
        bid, sid, n = run["backend"], run["session"], run["n"]
        if not bid:
            from puppy import notify, runner
            session = db.get_session(sid)
            marker = run_record(sid) if session else None
            if session is None or marker is None or marker["schedule"] != ident or marker["run"] != n:
                return {"gone": True}
            hub = runner._hubs.get(sid)
            if hub is None:
                try:
                    completion = str((notify.completion_record(sid) or {}).get("status") or "")
                except RuntimeError:
                    completion = ""
                return {"gone": False, "status": "idle", "completion": completion,
                        "waiting": False}
            return {"gone": False, "status": hub.status,
                    "completion": hub.last_completion_status if hub.status == "idle" else "",
                    "waiting": bool(hub.pending_approval)}
        entry = self.rows.get((bid, sid))
        if entry is None:
            return None
        row = entry[0]
        if row is None:
            return {"gone": True}
        marker = row.get("schedule")
        if not isinstance(marker, dict) or marker.get("id") != ident or marker.get("run") != n:
            return {"gone": True}
        status = "running" if row.get("status") == "running" else "idle"
        return {"gone": False, "status": status,
                "completion": str(row.get("completion_status") or "") if status == "idle" else "",
                "waiting": bool(marker.get("waiting"))}

    def observe(self, bid: int, sessions) -> None:
        """A whole session list from a backend (its state stream, a proxied
        read, or this worker's own read)."""
        if not isinstance(sessions, list) or not any(key[0] == bid for key in self.index):
            return
        rows = {row.get("id"): row for row in sessions if isinstance(row, dict)}
        now = time.time()
        touched = False
        for key, (_ident, _n, since) in list(self.index.items()):
            if key[0] != bid:
                continue
            row = rows.get(key[1])
            if row is not None:
                self.present.add(key)
                self.rows[key] = (row, now)
                touched = True
            elif key in self.present or now - since > 2 * READ_AGAIN_S:
                # a list taken before the session existed says nothing
                self.rows[key] = (None, now)
                touched = True
        if touched:
            self.poke()

    async def _read_backend(self, bid: int) -> None:
        try:
            data = await _request(bid, "GET", "sessions", timeout_s=20.0)
            self.observe(bid, data.get("sessions"))
        except ScheduleError as exc:
            log.debug("schedule read of backend %s failed: %s", bid, exc)
        finally:
            self.reading.discard(bid)

    def _watch(self, record: dict, run: dict, now: float) -> bool:
        ident, n = record["id"], run["n"]
        key = (ident, n)
        if key in self.starting or key in self.finishing or not run["session"]:
            return False
        bid = run["backend"]
        state = self._observe(ident, run)
        if bid and (state is None or now - self.rows.get((bid, run["session"]), (None, 0))[1] > READ_AGAIN_S):
            if bid not in self.reading and not _backend_blocker(bid) and \
                    time.monotonic() - self.read_at.get(bid, 0) > READ_AGAIN_S:
                self.reading.add(bid)
                self.read_at[bid] = time.monotonic()
                self.spawn(self._read_backend(bid))
        if state is None:
            return False
        changed = False
        if state["gone"]:
            self.finishing.add(key)
            self.spawn(self._settle(ident, n, None))
            return False
        if self.waiting.get(key, False) != state["waiting"]:
            self.waiting[key] = state["waiting"]
            changed = True
            if state["waiting"] and not run["notified"] and _deliver(record, run, "waiting"):
                self._update(ident, n, notified=True)
        limit = record["limit_minutes"] * 60
        if limit and state["status"] == "running" and now - run["started_at"] > limit and \
                time.monotonic() - self.interrupted_at.get(key, -1e9) > INTERRUPT_AGAIN_S:
            self.interrupted_at[key] = time.monotonic()
            self.stop_reason.setdefault(key, "limit")
            self.spawn(self._stop(bid, run["session"]))
        if state["status"] == "idle":
            if state["completion"] in ("ok", "error", "interrupted"):
                self.finishing.add(key)
                self.spawn(self._settle(ident, n, state["completion"]))
            else:
                since = self.idle_since.setdefault(key, now)
                if now - since > SETTLE_S:
                    self.finishing.add(key)
                    self.spawn(self._settle(ident, n, ""))
        else:
            self.idle_since.pop(key, None)
        return changed

    async def _stop(self, bid: int, sid: int) -> None:
        try:
            await _interrupt(bid, sid)
        except Exception as exc:
            log.warning("could not stop a scheduled run on %s: %s", _backend_name(bid), exc)

    def stop_run(self, ident: str) -> dict:
        history = _load_history(ident)
        run = next((item for item in history["runs"] if item["state"] in ACTIVE), None)
        if _load(ident) is None:
            raise ScheduleError("That schedule no longer exists", 404)
        if run is None:
            raise ScheduleError("No run of this schedule is working", 409)
        if not run["session"]:
            raise ScheduleError("The run is still starting · try again in a moment", 409)
        key = (ident, run["n"])
        self.stop_reason[key] = "user"
        self.interrupted_at[key] = time.monotonic()
        self.spawn(self._stop(run["backend"], run["session"]))
        return run

    async def _settle(self, ident: str, n: int, completion) -> None:
        """Close one run: ``completion`` is its block's status, "" when it
        went idle without one, None when its session is gone."""
        key = (ident, n)
        try:
            record = _load(ident)
            history = _load_history(ident) if record else None
            run = next((item for item in history["runs"] if item["n"] == n), None) if history else None
            if run is None or run["state"] not in ACTIVE:
                return
            answer, failure = "", ""
            if completion is not None:
                try:
                    answer, failure = await asyncio.wait_for(
                        _answer(run["backend"], run["session"]), ANSWER_TIMEOUT)
                except Exception as exc:
                    log.warning("could not read the answer of schedule %s run %s: %s", ident, n, exc)
            why = self.stop_reason.pop(key, "")
            if completion is None:
                state, reason = "lost", "its conversation was deleted"
            elif completion == "ok":
                state, reason = "ok", ""
            elif completion == "error":
                state, reason = "failed", " ".join(failure.split()) or "the engine reported an error"
            elif completion == "interrupted":
                state = "stopped"
                reason = "it ran past its {}-minute limit".format(record["limit_minutes"]) \
                    if why == "limit" else "stopped from Schedules" if why == "user" else \
                    "it was stopped before it finished"
            else:
                state, reason = ("failed", " ".join(failure.split())) if failure else \
                    ("failed", "the run ended without an answer")
            flag = parse_flag(answer) if state == "ok" and record["notify"] == "agent" else ""
            record, run = self._update(
                ident, n, state=state, finished_at=time.time(),
                reason=_bounded(reason, REASON_LIMIT), answer=_bounded(answer, ANSWER_LIMIT),
                flag=flag, kept=run["kept"] and completion is not None)
            if run is None:
                return
            kind = "flagged" if flag else "finished" if state == "ok" else \
                "limit" if state == "stopped" and why == "limit" else \
                None if state == "stopped" else "failed"
            if kind and _deliver(record, run, kind):
                self._update(ident, n, notified=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("could not settle schedule %s run %s", ident, n)
        finally:
            self.finishing.discard(key)
            self.idle_since.pop(key, None)
            self.waiting.pop(key, None)
            self.interrupted_at.pop(key, None)
            for where, (owner, number, _since) in list(self.index.items()):
                if (owner, number) == key:
                    self.index.pop(where, None)
                    self.rows.pop(where, None)
                    self.present.discard(where)
            publish()
            self.poke()

    # -- letting conversations go --

    def _prune(self, record: dict, history: dict) -> None:
        kept = [run for run in history["runs"]
                if run["kept"] and run["session"] and run["state"] not in ACTIVE]
        kept.sort(key=lambda run: run["n"], reverse=True)
        for run in kept[record["keep"]:]:
            self.drop_conversation(record["id"], run)

    def drop_conversation(self, ident: str, run: dict, then=None) -> bool:
        key = (ident, run["n"])
        if key in self.dropping or _backend_blocker(run["backend"]):
            return False
        self.dropping.add(key)
        self.spawn(self._drop(ident, run, then))
        return True

    async def _drop(self, ident: str, run: dict, then=None) -> None:
        key = (ident, run["n"])
        try:
            gone = await _drop(run["backend"], run["session"], ident, run["n"])
        except Exception as exc:
            log.warning("could not delete the conversation of schedule %s run %s: %s",
                        ident, run["n"], exc)
            gone = False
        finally:
            self.dropping.discard(key)
        if gone:
            self._update(ident, run["n"], kept=False)
            if then is not None:
                then()
            publish()


# ---- the controller's routes ----

def _settings_from(body) -> dict:
    settings = normalize_settings(body)
    if settings["backend"]:
        from puppy import backends
        if backends.get_backend(settings["backend"]) is None:
            raise ScheduleError("That backend no longer exists")
    return settings


async def _check_choices(settings: dict) -> None:
    """This instance validates engine, model, effort and permissions against
    its own catalog; a backend answers for its own when the run starts."""
    if settings["backend"]:
        return
    from puppy import engine_defaults
    from puppy.drivers import get_driver
    try:
        driver = get_driver(settings["engine"])
    except KeyError:
        raise ScheduleError("Unknown engine '{}'".format(settings["engine"])) from None
    await driver.refresh_model_options()
    try:
        engine_defaults.validate(driver, {"model": settings["model"], "effort": settings["effort"],
                                          "permission_mode": settings["permission_mode"]})
    except ValueError as exc:
        raise ScheduleError(str(exc)) from None


def _json(data, status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


def _error(exc: ScheduleError):
    return _json({"error": str(exc)}, exc.status if 400 <= exc.status < 600 else 400)


async def _body(request):
    try:
        return await request.json()
    except Exception:
        raise ScheduleError("Send the request as a JSON object") from None


async def h_list(_request):
    return _json(public_state())


async def h_get(request):
    ident = request.match_info["ident"]
    record = _load(ident)
    if record is None:
        return _json({"error": "That schedule no longer exists"}, 404)
    history = _load_history(ident)
    live = _scheduler.live_of if _scheduler else (lambda ident, run: None)
    return _json({"schedule": public(record, history, full=True),
                  "runs": [_run_public(run, live(ident, run), answer=True)
                           for run in reversed(history["runs"])]})


async def h_create(request):
    try:
        settings = _settings_from(await _body(request))
        await _check_choices(settings)
        if len(_load_all()) >= MAX_SCHEDULES:
            raise ScheduleError("This instance already holds {} schedules".format(MAX_SCHEDULES), 409)
        now = time.time()
        ident = secrets.token_hex(6)
        while _load(ident) is not None:
            ident = secrets.token_hex(6)
        record = dict(settings, format=FORMAT, id=ident, created_at=now, updated_at=now,
                      next_at=0.0, pending_at=0.0, scheduled_runs=0)
        record["next_at"] = _next_at(record, now)
        _save(record, _new_history())
    except ScheduleError as exc:
        return _error(exc)
    publish()
    if _scheduler:
        _scheduler.poke()
    return _json({"schedule": public(record, _new_history(), full=True)})


async def h_update(request):
    ident = request.match_info["ident"]
    try:
        settings = _settings_from(await _body(request))
        await _check_choices(settings)
        record = _load(ident)
        if record is None:
            raise ScheduleError("That schedule no longer exists", 404)
        history = _load_history(ident)
        timing_changed = any(record[key] != settings[key] for key in ("timing", "until", "max_runs"))
        record.update(settings)
        record["updated_at"] = time.time()
        if not record["enabled"]:
            record["next_at"] = record["pending_at"] = 0.0
            if _scheduler:
                _scheduler.blocked.pop(ident, None)
        elif timing_changed or not record["next_at"]:
            # a new timing starts from now: nothing it would have done before
            # is owed, and a run limit counts the runs already made
            if timing_changed:
                record["pending_at"] = 0.0
                if _scheduler:
                    _scheduler.blocked.pop(ident, None)
            record["next_at"] = _next_at(record, time.time())
        _save(record, history)
    except ScheduleError as exc:
        return _error(exc)
    publish()
    if _scheduler:
        _scheduler.poke()
    return _json({"schedule": public(record, history, full=True)})


async def h_toggle(request):
    ident = request.match_info["ident"]
    try:
        body = await _body(request)
        if not isinstance(body, dict) or set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ScheduleError("Send {enabled: true or false}")
        record = _load(ident)
        if record is None:
            raise ScheduleError("That schedule no longer exists", 404)
        history = _load_history(ident)
        if record["enabled"] != body["enabled"]:
            record["enabled"] = body["enabled"]
            record["updated_at"] = time.time()
            record["pending_at"] = 0.0
            record["next_at"] = _next_at(record, time.time()) if record["enabled"] else 0.0
            if _scheduler:
                _scheduler.blocked.pop(ident, None)
            _save(record, history)
    except ScheduleError as exc:
        return _error(exc)
    publish()
    if _scheduler:
        _scheduler.poke()
    return _json({"schedule": public(record, history)})


async def h_delete(request):
    ident = request.match_info["ident"]
    conversations = request.query.get("conversations", "0") == "1"
    record = _load(ident)
    if record is None:
        return _json({"error": "That schedule no longer exists"}, 404)
    history = _load_history(ident)
    if any(run["state"] in ACTIVE for run in history["runs"]):
        return _json({"error": "A run of this schedule is still working · stop it first"}, 409)
    # nothing starts while its conversations are let go
    record.update(enabled=False, next_at=0.0, pending_at=0.0, updated_at=time.time())
    _save(record, history)
    removed, left = 0, 0
    if conversations:
        async def drop(run):
            try:
                return await _drop(run["backend"], run["session"], ident, run["n"])
            except Exception as exc:
                log.warning("could not delete a scheduled run's conversation: %s", exc)
                return False
        kept = [item for item in history["runs"] if item["kept"] and item["session"]]
        try:
            results = await asyncio.wait_for(asyncio.gather(*(drop(run) for run in kept)), 90.0)
        except asyncio.TimeoutError:
            results = [False] * len(kept)
        removed = sum(1 for gone in results if gone)
        left = len(kept) - removed
    db.meta_apply(delete_keys=(PREFIX + ident, RUNS_PREFIX + ident))
    if _scheduler:
        _scheduler.blocked.pop(ident, None)
    publish()
    return _json({"ok": True, "removed": removed, "left": left})


async def h_run(request):
    if _scheduler is None:
        return _json({"error": "Schedules are not running"}, 503)
    try:
        run = _scheduler.run_now(request.match_info["ident"])
    except ScheduleError as exc:
        return _error(exc)
    return _json({"run": _run_public(run)}, 202)


async def h_stop(request):
    if _scheduler is None:
        return _json({"error": "Schedules are not running"}, 503)
    try:
        run = _scheduler.stop_run(request.match_info["ident"])
    except ScheduleError as exc:
        return _error(exc)
    return _json({"ok": True, "run": _run_public(run)})


async def h_remove_run(request):
    ident = request.match_info["ident"]
    n = int(request.match_info["n"])
    record = _load(ident)
    if record is None:
        return _json({"error": "That schedule no longer exists"}, 404)
    history = _load_history(ident)
    run = next((item for item in history["runs"] if item["n"] == n), None)
    if run is None:
        return _json({"error": "That run is no longer in the history"}, 404)
    if run["state"] in ACTIVE:
        return _json({"error": "That run is still working · stop it first"}, 409)
    if run["kept"] and run["session"]:
        try:
            gone = await _drop(run["backend"], run["session"], ident, n)
        except ScheduleError as exc:
            return _json({"error": "Could not delete its conversation · " + str(exc)}, 409)
        if not gone:
            return _json({"error": "Could not delete its conversation · it is still busy"}, 409)
    history = _load_history(ident)
    history["runs"] = [item for item in history["runs"] if item["n"] != n]
    record = _load(ident)
    if record is not None:
        _save(record, history)
    publish()
    return _json({"ok": True})


async def h_preview(request):
    try:
        body = await _body(request)
        if not isinstance(body, dict) or set(body) != {"timing", "until", "max_runs"}:
            raise ScheduleError("Send {timing, until, max_runs}")
        return _json({"next": preview(body["timing"], body["until"], body["max_runs"]),
                      "timezone": time.strftime("%Z")})
    except ScheduleError as exc:
        return _error(exc)


# ---- hooks ----

def claims(bid: int, sid: int) -> bool:
    """Whether one session's completion belongs to a scheduled run, whose
    schedule decides what to tell (generic completion alerts stay quiet)."""
    try:
        if not bid:
            marker = run_record(sid)
            if marker is None:
                return False
        now = time.time()
        for record in _load_all():
            for run in _load_history(record["id"])["runs"]:
                if run["backend"] == bid and run["session"] == sid and (
                        run["state"] in ACTIVE or now - run["finished_at"] < 600):
                    return True
    except Exception:
        return False
    return False


def local_changed(sid: int) -> None:
    """The runner's word that a local session's activity block ended."""
    if _scheduler is not None and run_record(sid) is not None:
        _scheduler.poke()


def observe_sessions(bid: int, sessions) -> None:
    if _scheduler is not None and bid:
        _scheduler.observe(int(bid), sessions)


def forget_backend(bid: int) -> None:
    """A deleted backend's schedules stop: nothing they name can run."""
    changed = False
    for record in _load_all():
        if record["backend"] == bid and (record["enabled"] or record["pending_at"]):
            record.update(enabled=False, next_at=0.0, pending_at=0.0, updated_at=time.time())
            _save(record)
            changed = True
    if changed:
        publish()


def snapshot_blockers() -> list:
    if _scheduler is None:
        return []
    busy = len(_scheduler.starting) + len(_scheduler.dropping)
    return ["{} scheduled run{} starting or being cleaned up".format(
        busy, "" if busy == 1 else "s")] if busy else []


def pause_for_snapshot() -> None:
    global _paused
    _paused = True


def resume_after_snapshot() -> None:
    global _paused
    _paused = False
    if _scheduler is not None:
        _scheduler.poke()


def reset_after_restore() -> None:
    """A restored schedule owes nothing for the time its copy spent in the
    archive: its next occurrence counts from now."""
    now = time.time()
    for record in _load_all():
        history = _load_history(record["id"])
        record["pending_at"] = 0.0
        record["next_at"] = _next_at(record, now) if record["enabled"] else 0.0
        _save(record, history)
    if _scheduler is not None:
        _scheduler.blocked.clear()
        _scheduler.poke()


async def start_worker(app) -> None:
    global _scheduler
    validate_persisted(db.connect())
    if _scheduler is not None:
        return
    _scheduler = Scheduler(app)
    from puppy import backends
    backends.add_sessions_observer(observe_sessions)
    await _scheduler.start()
    publish()


async def stop_worker(_app=None) -> None:
    global _scheduler
    scheduler, _scheduler = _scheduler, None
    if scheduler is not None:
        await scheduler.stop()


def register(app) -> None:
    """The controller's schedules: routes, worker and its startup check."""
    r = app.router
    r.add_get("/api/schedules", h_list)
    r.add_post("/api/schedules", h_create)
    r.add_post("/api/schedules/preview", h_preview)
    r.add_get("/api/schedules/{ident:[0-9a-f]{12}}", h_get)
    r.add_put("/api/schedules/{ident:[0-9a-f]{12}}", h_update)
    r.add_patch("/api/schedules/{ident:[0-9a-f]{12}}", h_toggle)
    r.add_delete("/api/schedules/{ident:[0-9a-f]{12}}", h_delete)
    r.add_post("/api/schedules/{ident:[0-9a-f]{12}}/run", h_run)
    r.add_post("/api/schedules/{ident:[0-9a-f]{12}}/stop", h_stop)
    r.add_delete("/api/schedules/{ident:[0-9a-f]{12}}/runs/{n:[1-9][0-9]{0,9}}", h_remove_run)
    app.on_startup.append(start_worker)
    app.on_shutdown.insert(0, stop_worker)
