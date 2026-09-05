"""Prompt-completion notification: run a configured command on a chosen node
when a session finishes its work.

Controller-owned. The command, target backend and armed state persist in
config (notify.*) and ride along in Settings backup archives. Every node keeps
a bounded, durable completion sequence. The controller consumes each paired
node's sequence over its authenticated channel, so an open browser is never
part of delivery. The command itself runs through the shared /api/notify/exec
surface: locally for backend 0, else POSTed to the chosen backend (token +
pinned TLS), where it executes only if that node was built with its shell
surface (terminal) enabled.

A notification fires when a session goes idle - its turn and everything queued
behind it finished - not once per queued prompt. A turn stopped by the user is
not a completion and never fires the command.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
import shlex
import time

from puppy import config, db, protocol, workspace_sync

log = logging.getLogger("puppy.notify")

# {placeholder} substitutions (shell-quoted) and PUPPY_* environment variables
PLACEHOLDERS = ("backend", "session", "engine", "model", "status", "duration",
                "duration_hms", "cwd", "id")
EXEC_TIMEOUT = 30.0
MAX_COMMAND = 1000
COMPLETION_VERSION = 1
COMPLETION_LOG_KEY = "completion_log"
COMPLETION_SESSION_PREFIX = "session_completion."
REMOTE_CURSOR_PREFIX = "notify_completion_cursor."
COMPLETION_LOG_LIMIT = 512
POLL_SECONDS = 2.0

_worker_task = None
_worker_wake = None
_poll_inflight = set()
_snapshot_paused = False
_INVALID_META = object()


def poll_seconds() -> float:
    return config.timer_seconds("completion_sync_seconds")


def _completion_key(session_id: int) -> str:
    return COMPLETION_SESSION_PREFIX + str(int(session_id))


def _decode_json(raw):
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError("invalid JSON constant {}".format(value))))


def _meta_value(key: str):
    row = db.query_one("SELECT value FROM meta WHERE key=?", (key,))
    if row is None:
        return False, None
    try:
        return True, _decode_json(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return True, _INVALID_META


def _hex_id(value, size: int = 32) -> bool:
    return isinstance(value, str) and len(value) == size and all(
        ch in "0123456789abcdef" for ch in value)


def _valid_completion(value):
    required = {
        "seq", "completion_id", "session_id", "session", "engine", "model",
        "status", "duration", "cwd", "started_at", "finished_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        return None
    if not isinstance(value.get("seq"), int) or isinstance(value.get("seq"), bool) or \
            value["seq"] < 1 or not isinstance(value.get("session_id"), int) or \
            isinstance(value.get("session_id"), bool) or value["session_id"] < 1 or \
            not isinstance(value.get("duration"), int) or \
            isinstance(value.get("duration"), bool) or value["duration"] < 0:
        return None
    for key, limit in (("completion_id", 80), ("session", 300),
                       ("engine", 80), ("model", 300), ("status", 40),
                       ("cwd", 4096)):
        if not isinstance(value.get(key), str) or len(value[key]) > limit:
            return None
    if not _hex_id(value["completion_id"]) or value["status"] not in (
            "ok", "error", "interrupted"):
        return None
    for key in ("started_at", "finished_at"):
        if not isinstance(value.get(key), (int, float)) or \
                isinstance(value.get(key), bool) or value[key] < 0 or \
                not math.isfinite(value[key]):
            return None
    if value["finished_at"] < value["started_at"] or abs(
            value["finished_at"] - value["started_at"] -
            value["duration"]) > 0.001:
        return None
    return dict(value)


def _new_log() -> dict:
    return {"version": COMPLETION_VERSION,
            "stream_id": secrets.token_hex(16),
            "next_seq": 1, "items": []}


def _valid_log(value):
    if not isinstance(value, dict) or set(value) != {
            "version", "stream_id", "next_seq", "items"} or \
            value.get("version") != COMPLETION_VERSION or \
            not _hex_id(value.get("stream_id")) or \
            not isinstance(value.get("next_seq"), int) or \
            isinstance(value.get("next_seq"), bool) or value["next_seq"] < 1 or \
            not isinstance(value.get("items"), list):
        return None
    items = [_valid_completion(item) for item in value["items"]]
    if any(item is None for item in items) or len(items) > COMPLETION_LOG_LIMIT or \
            len({item["completion_id"] for item in items}) != len(items) or \
            any(items[index]["seq"] + 1 != items[index + 1]["seq"]
                for index in range(len(items) - 1)) or \
            (items and value["next_seq"] != items[-1]["seq"] + 1) or \
            (not items and value["next_seq"] != 1):
        return None
    return {"version": COMPLETION_VERSION, "stream_id": value["stream_id"],
            "next_seq": value["next_seq"], "items": items}


def _load_log():
    present, value = _meta_value(COMPLETION_LOG_KEY)
    if not present:
        return _new_log()
    return _valid_log(value)


def completion_record(session_id: int):
    present, value = _meta_value(_completion_key(session_id))
    if not present:
        return None
    completion = _valid_completion(value.get("completion")) \
        if isinstance(value, dict) and set(value) == {
            "version", "completion"} and \
        value.get("version") == COMPLETION_VERSION else None
    if completion is None or completion["session_id"] != int(session_id):
        raise RuntimeError("session completion has an unsupported persisted shape")
    return completion


def clear_session(session_id: int) -> None:
    db.execute("DELETE FROM meta WHERE key=?", (_completion_key(session_id),))


def completion_events(after: int) -> dict:
    log_state = _load_log()
    if log_state is None:
        raise RuntimeError("completion history has an unsupported persisted shape")
    present, _value = _meta_value(COMPLETION_LOG_KEY)
    if not present:
        db.meta_set(COMPLETION_LOG_KEY, log_state)
    cursor = log_state["next_seq"] - 1
    oldest = log_state["items"][0]["seq"] if log_state["items"] else cursor + 1
    return {
        "ok": True, "stream_id": log_state["stream_id"], "cursor": cursor,
        "truncated": bool(after < oldest - 1),
        "completions": [item for item in log_state["items"]
                        if item["seq"] > after],
    }


def reset_after_restore() -> None:
    """Give restored local history a new stream and baseline every peer.

    A restore can move sequence counters backwards. Rotating the local stream
    lets controllers detect that discontinuity, while removing controller
    cursors prevents replaying remote work merely because this controller's
    own database moved back in time.
    """
    state = _load_log()
    if state is None:
        raise RuntimeError("completion history has an unsupported persisted shape")
    state["stream_id"] = secrets.token_hex(16)
    db.meta_apply({COMPLETION_LOG_KEY: state},
                  delete_globs=(REMOTE_CURSOR_PREFIX + "*",))


def _record_completion(session: dict, status: str, duration_s: int) -> dict:
    log_state = _load_log()
    if log_state is None:
        raise RuntimeError("completion history has an unsupported persisted shape")
    duration = max(0, int(duration_s))
    finished = time.time()
    record = {
        "seq": log_state["next_seq"],
        "completion_id": secrets.token_hex(16),
        "session_id": int(session.get("id") or 0),
        "session": str(session.get("name") or
                       "session {}".format(session.get("id") or ""))[:300],
        "engine": str(session.get("engine") or "")[:80],
        "model": str(session.get("last_model") or session.get("model") or "")[:300],
        "status": str(status or "")[:40],
        "duration": duration,
        "cwd": workspace_sync.public_cwd(session)[:4096],
        "started_at": finished - duration,
        "finished_at": finished,
    }
    if _valid_completion(record) is None:
        raise RuntimeError("cannot persist an invalid completion record")
    log_state["next_seq"] += 1
    log_state["items"] = (log_state["items"] + [record])[-COMPLETION_LOG_LIMIT:]
    db.meta_apply({
        COMPLETION_LOG_KEY: log_state,
        _completion_key(record["session_id"]): {
            "version": COMPLETION_VERSION, "completion": record},
    })
    return record


def settings() -> dict:
    return {
        "enabled": bool(config.get("notify.enabled", False)),
        "backend": int(config.get("notify.backend", 0) or 0),
        "command": str(config.get("notify.command", "") or ""),
    }


def configured() -> bool:
    return bool(settings()["command"].strip())


def active() -> bool:
    s = settings()
    # Enabled and configured are intentionally independent: the Settings
    # switch may be on before a command is supplied, but that must stay inert.
    return bool(s["command"].strip()) and s["enabled"]


def public_state() -> dict:
    """What the console needs to draw the bell."""
    s = settings()
    return {"configured": bool(s["command"].strip()), "enabled": s["enabled"]}


def clock(seconds: int) -> str:
    """Whole seconds as a clock, without padding the leading unit: 0:07, 9:59,
    10:00, 1:00:00, 9:59:59, 10:00:00. Leaving the most significant field
    unpadded is what produces M:SS / MM:SS / H:MM:SS / HH:MM:SS in turn."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{}:{:02d}".format(minutes, secs)


def clean_info(info) -> dict:
    if not isinstance(info, dict):
        return {}
    limits = {
        "backend": 300, "session": 300, "engine": 80, "model": 300,
        "status": 40, "duration": 40, "duration_hms": 40,
        "cwd": 4096, "id": 80,
    }
    out = {key: str(info.get(key) or "")[:limits[key]]
           for key in PLACEHOLDERS if info.get(key)}
    # Always derived here from `duration`, never taken from the caller: one
    # implementation for local/remote completion records and the Test button.
    out.pop("duration_hms", None)
    try:
        out["duration_hms"] = clock(int(float(out["duration"])))
    except (KeyError, TypeError, ValueError):
        pass
    return out


def expand(command: str, info: dict) -> str:
    """Substitute placeholders shell-quoted: values drop into an sh -c command
    as single words, so names with spaces or quotes cannot break it."""
    out = command
    for key in PLACEHOLDERS:
        out = out.replace("{%s}" % key, shlex.quote(str(info.get(key) or "")))
    return out


async def run_local(command: str, info: dict) -> dict:
    """Execute on this node. Also the body of the shared /api/notify/exec."""
    env = dict(os.environ)
    for key in PLACEHOLDERS:
        env["PUPPY_" + key.upper()] = str(info.get(key) or "")
    try:
        proc = await asyncio.create_subprocess_shell(
            command, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "error": "command timed out after %ds" % int(EXEC_TIMEOUT)}
    return {"ok": proc.returncode == 0, "rc": proc.returncode,
            "output": (out or b"").decode(errors="replace")[-1000:].strip()}


async def dispatch(info: dict, override: dict = None) -> dict:
    """Expand and run the configured (or supplied) command on its target."""
    s = dict(settings())
    if override:
        s.update({k: override[k] for k in ("backend", "command") if k in override})
    command = str(s.get("command") or "").strip()[:MAX_COMMAND]
    if not command:
        return {"ok": False, "error": "no command configured"}
    info = clean_info(info)
    expanded = expand(command, info)
    target = int(s.get("backend") or 0)
    if not target:
        return await run_local(expanded, info)
    from puppy import backends
    return await backends.notify_exec(target, expanded, info)


def session_finished(session: dict, status: str, duration_s: int):
    """Persist one authoritative completion and notify for local work."""
    if session is None:
        return None
    record = _record_completion(session, status, duration_s)
    if status != "interrupted" and active():
        asyncio.ensure_future(_fire({
            **record,
            "backend": config.get("instance_name") or "local",
            "id": str(record["session_id"]),
        }))
    return record


async def _fire(info: dict) -> None:
    try:
        result = await dispatch(info)
        if not result.get("ok"):
            log.warning("completion command failed: %s",
                        result.get("error") or "rc=%s %s" % (result.get("rc"),
                                                             (result.get("output") or "")[:200]))
    except Exception:
        log.exception("completion command failed")


def _cursor_key(bid: int) -> str:
    return REMOTE_CURSOR_PREFIX + str(int(bid))


def _valid_cursor(value):
    if not isinstance(value, dict) or set(value) != {
            "version", "stream_id", "cursor"} or \
            value.get("version") != COMPLETION_VERSION or \
            not _hex_id(value.get("stream_id")) or \
            not isinstance(value.get("cursor"), int) or \
            isinstance(value.get("cursor"), bool) or value["cursor"] < 0:
        return None
    return {"stream_id": value["stream_id"], "cursor": int(value["cursor"])}


def _load_cursor(bid: int):
    present, value = _meta_value(_cursor_key(bid))
    if not present:
        return None
    return _valid_cursor(value) or False


def validate_database_state(connection) -> None:
    """Reject stale or malformed durable completion state before restore."""
    session_ids = {int(row[0]) for row in connection.execute(
        "SELECT id FROM sessions")}
    backend_ids = {int(row[0]) for row in connection.execute(
        "SELECT id FROM backends")}
    rows = connection.execute(
        "SELECT key,value FROM meta WHERE key=? OR key GLOB ? OR key GLOB ?",
        (COMPLETION_LOG_KEY, COMPLETION_SESSION_PREFIX + "*",
         REMOTE_CURSOR_PREFIX + "*")).fetchall()
    values = {}
    for row in rows:
        try:
            values[str(row[0])] = _decode_json(row[1])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("completion state contains invalid JSON") from exc
    log_state = None
    if COMPLETION_LOG_KEY in values:
        log_state = _valid_log(values[COMPLETION_LOG_KEY])
        if log_state is None:
            raise ValueError("completion history has an unsupported shape")
    session_records = {}
    for key, value in values.items():
        if key.startswith(COMPLETION_SESSION_PREFIX):
            suffix = key[len(COMPLETION_SESSION_PREFIX):]
            try:
                sid = int(suffix)
            except ValueError as exc:
                raise ValueError("session completion key is invalid") from exc
            completion = _valid_completion(value.get("completion")) \
                if isinstance(value, dict) and set(value) == {
                    "version", "completion"} and \
                value.get("version") == COMPLETION_VERSION else None
            if str(sid) != suffix or sid not in session_ids or \
                    completion is None or completion["session_id"] != sid or \
                    log_state is None or completion["seq"] >= log_state["next_seq"]:
                raise ValueError("session completion has an unsupported shape")
            session_records[sid] = completion
        elif key.startswith(REMOTE_CURSOR_PREFIX):
            suffix = key[len(REMOTE_CURSOR_PREFIX):]
            try:
                bid = int(suffix)
            except ValueError as exc:
                raise ValueError("completion cursor key is invalid") from exc
            if str(bid) != suffix or bid not in backend_ids or \
                    _valid_cursor(value) is None:
                raise ValueError("completion cursor has an unsupported shape")
    if log_state is not None:
        latest = {}
        for completion in log_state["items"]:
            latest[completion["session_id"]] = completion
        for sid, completion in latest.items():
            if sid in session_ids and session_records.get(sid) != completion:
                raise ValueError("session completion does not match its history")


def forget_backend(bid: int) -> None:
    db.execute("DELETE FROM meta WHERE key=?", (_cursor_key(bid),))


async def _poll_backend(backend: dict) -> None:
    from puppy import backends
    bid = int(backend["id"])
    _poll_inflight.add(bid)
    try:
        cursor = _load_cursor(bid)
        if cursor is False:
            log.error("remote completion cursor for backend %s has an unsupported "
                      "persisted shape", bid)
            return
        after = 0 if cursor is None else cursor["cursor"]
        result = await backends.fetch_completion_events(bid, after)
        if not result or result.get("ok") is not True:
            return
        next_cursor = result.get("cursor")
        stream_id = result.get("stream_id")
        items = result.get("completions")
        truncated = result.get("truncated")
        if not _hex_id(stream_id) or \
                not isinstance(next_cursor, int) or isinstance(next_cursor, bool) or \
                next_cursor < 0 or not isinstance(items, list) or \
                type(truncated) is not bool:
            log.warning("backend %s returned invalid completion history", bid)
            return
        checked = [_valid_completion(item) for item in items]
        same_stream = cursor is not None and cursor["stream_id"] == stream_id
        if any(item is None for item in checked) or \
                any(item["seq"] <= (after if same_stream else 0)
                    for item in checked) or \
                any(checked[index]["seq"] + 1 != checked[index + 1]["seq"]
                    for index in range(len(checked) - 1)) or \
                (checked and checked[-1]["seq"] != next_cursor) or \
                (same_stream and next_cursor >= after and checked and
                 ((not truncated and checked[0]["seq"] != after + 1) or
                  (truncated and checked[0]["seq"] <= after + 1))) or \
                (same_stream and next_cursor >= after and not checked and
                 next_cursor != after):
            log.warning("backend %s returned invalid completion records", bid)
            return
        # A first sighting or stream discontinuity establishes a baseline. This
        # never replays historical work after pairing, node replacement or restore.
        if cursor is None or not same_stream or next_cursor < after:
            db.meta_set(_cursor_key(bid), {
                "version": COMPLETION_VERSION, "stream_id": stream_id,
                "cursor": next_cursor})
            return
        if truncated:
            log.warning("backend %s completion history rolled over before cursor %s",
                        bid, after)
        for item in checked:
            if item["status"] != "interrupted" and active():
                await _fire({
                    **item, "backend": str(backend.get("name") or bid),
                    "id": str(item["session_id"]),
                })
            # Advance only after the attempted delivery. A process death before
            # this write can duplicate a command, but can no longer lose it.
            db.meta_set(_cursor_key(bid), {
                "version": COMPLETION_VERSION, "stream_id": stream_id,
                "cursor": item["seq"]})
        if not checked or checked[-1]["seq"] < next_cursor:
            db.meta_set(_cursor_key(bid), {
                "version": COMPLETION_VERSION, "stream_id": stream_id,
                "cursor": next_cursor})
    finally:
        _poll_inflight.discard(bid)


async def poll_remote_completions(app) -> None:
    """Read every online capable node concurrently under the controller gate."""
    if _snapshot_paused or app.get("puppy_snapshot_busy"):
        return
    from puppy import backends
    rows = [item for item in backends.list_backends()
            if protocol.COMPLETION_EVENTS_CAPABILITY in
            (item.get("capabilities") or []) and
            backends.backend_is_online(int(item["id"]))]
    await asyncio.gather(*(_poll_backend(item) for item in rows))


def snapshot_blockers() -> list:
    count = len(_poll_inflight)
    return (["{} completion poll{} in progress".format(
        count, "" if count == 1 else "s")] if count else [])


def pause_for_snapshot() -> None:
    global _snapshot_paused
    _snapshot_paused = True


def resume_after_snapshot() -> None:
    global _snapshot_paused
    _snapshot_paused = False
    wake_worker()


async def _worker_loop(app) -> None:
    while True:
        if _worker_wake is not None:
            _worker_wake.clear()
        try:
            await poll_remote_completions(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("remote completion polling failed")
        delay = poll_seconds()
        try:
            if _worker_wake is None:
                await asyncio.sleep(delay)
            else:
                await asyncio.wait_for(_worker_wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


async def start_worker(app) -> None:
    global _worker_task, _worker_wake
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_wake = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(app))


async def stop_worker(_app=None) -> None:
    global _worker_task, _worker_wake
    task = _worker_task
    _worker_task = None
    _worker_wake = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def wake_worker() -> None:
    if _worker_wake is not None:
        _worker_wake.set()
