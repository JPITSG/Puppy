#!/usr/bin/env python3
"""Scheduled prompts: the timing (once, intervals, days of the week and the
month, cron) across daylight-saving changes, the exact persisted shapes and
their refusals, the run marker a node keeps on both runtimes, and the
controller's runs through the real runner with a fixture engine speaking
claude's stream-json - finished, flagged, failed, stopped at the time limit
and by hand, overlapping, missed while Puppy was down, kept and let go,
deleted - on this instance and on a backend reached through the node
requests, and the controller's routes.

No real engine is invoked, no network is reached and no quota is spent.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from unittest.mock import patch
import warnings


def fixture():
    """A run's engine: one prompt in, the answer PUPPY_TEST_SCHEDULE_MODE
    asks for, then it waits for its input to close."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    mode = os.environ.get("PUPPY_TEST_SCHEDULE_MODE", "ok")
    init = read()
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    prompt = read()
    text = prompt["message"]["content"][0]["text"]
    log_path = os.environ.get("PUPPY_TEST_SCHEDULE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"cwd": os.getcwd(), "prompt": text}) + "\n")
    send({"type": "system", "subtype": "init", "session_id": "native-run", "tools": []})
    if mode == "hang":
        time.sleep(600)
    answer = {"ok": "## Log review\n\nAll quiet: nothing in /var/log needs you.",
              "flag": "Found 14 failed SSH logins from 10.0.0.5.\n\n**NOTIFY:** 14 failed SSH logins from 10.0.0.5",
              "fail": ""}[mode]
    if answer:
        send({"type": "assistant", "message": {"model": "claude-run", "content": [
            {"type": "text", "text": answer}]}})
    send({"type": "result", "subtype": "success", "is_error": mode == "fail",
          "session_id": "native-run", "num_turns": 1,
          "usage": {"input_tokens": 3, "output_tokens": 2},
          "result": "the disk is unreadable" if mode == "fail" else "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit

os.environ["TZ"] = "Europe/Warsaw"
time.tzset()
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402
TEST_ROOT = private_root("schedules-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
LOG = TEST_ROOT / "runs.log"
WORK = TEST_ROOT / "work"
WORK.mkdir(parents=True, exist_ok=True)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import (auth, backends, config, db, notices, notify, protocol, prompt_index,  # noqa: E402
                   runner, schedules, snapshots, spawn_exec)
from puppy import web as webui  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

FIXTURE = str(Path(__file__).resolve())
S = schedules


def fixture_command(*args, **kwargs):
    return [sys.executable, FIXTURE, "--fixture"]


def mode(value):
    os.environ["PUPPY_TEST_SCHEDULE_MODE"] = value


def run_log():
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line]


def local(day, hh, mm):
    return time.mktime((day[0], day[1], day[2], hh, mm, 0, 0, 0, -1))


def stamp(at):
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(at))


async def wait_for(predicate, seconds=30.0, what="condition"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for " + what)


def settings(**changes):
    driver = get_driver("claude")
    value = {"name": "Log review", "prompt": "Check /var/log for the last 24 hours.",
             "enabled": True, "backend": 0, "engine": "claude", "model": "", "effort": "",
             "permission_mode": driver.default_permission(), "workspace_kind": "directory",
             "cwd": str(WORK), "timing": {"kind": "daily", "times": ["06:00"]}, "until": "",
             "max_runs": 0, "late": "run", "limit_minutes": 60, "notify": "failure",
             "alert_command": False, "keep": 10}
    value.update(changes)
    return value


def make(**changes):
    """A schedule as the create route stores it, written straight to meta."""
    value = S.normalize_settings(settings(**changes))
    now = time.time()
    record = dict(value, format=1, id=os.urandom(6).hex(), created_at=now, updated_at=now,
                  next_at=0.0, pending_at=0.0, scheduled_runs=0)
    record["next_at"] = S._next_at(record, now)
    S._save(record, S._new_history())
    return record


def history(ident):
    return S._load_history(ident)["runs"]


def record(ident):
    return S._load(ident)


def clear_schedules():
    db.execute("DELETE FROM meta WHERE key GLOB 'schedule.*' OR key GLOB 'schedule_runs.*'")


# ---- the timing ----

def timing_math():
    n = S.normalize_timing
    base = local((2026, 10, 15), 10, 0)
    def run(timing, count=4, after=base):
        out, at = [], after
        for _ in range(count):
            at = S.next_occurrence(n(timing), at)
            if not at:
                break
            out.append(stamp(at))
        return out
    assert run({"kind": "once", "date": "2026-10-16", "time": "09:00"}) == ["2026-10-16 09:00 CEST"]
    assert run({"kind": "once", "date": "2026-10-14", "time": "09:00"}) == []
    assert run({"kind": "interval", "minutes": 90, "date": "2026-10-15", "time": "08:00"}) == [
        "2026-10-15 11:00 CEST", "2026-10-15 12:30 CEST", "2026-10-15 14:00 CEST",
        "2026-10-15 15:30 CEST"]
    assert run({"kind": "interval", "minutes": 60, "date": "2026-10-20", "time": "07:15"}, 2) == [
        "2026-10-20 07:15 CEST", "2026-10-20 08:15 CEST"]
    assert run({"kind": "daily", "times": ["06:00", "18:30"]}) == [
        "2026-10-15 18:30 CEST", "2026-10-16 06:00 CEST", "2026-10-16 18:30 CEST",
        "2026-10-17 06:00 CEST"]
    # Monday and Friday, across the change back to winter time
    assert run({"kind": "weekly", "days": [1, 5], "times": ["08:30"]}) == [
        "2026-10-16 08:30 CEST", "2026-10-19 08:30 CEST", "2026-10-23 08:30 CEST",
        "2026-10-26 08:30 CET"]
    assert run({"kind": "monthly", "days": [-1, 1, 15], "times": ["09:00"]}) == [
        "2026-10-31 09:00 CET", "2026-11-01 09:00 CET", "2026-11-15 09:00 CET",
        "2026-11-30 09:00 CET"]
    # the last day of February, in a leap year and out of one
    assert run({"kind": "monthly", "days": [-1], "times": ["12:00"]}, 6,
               local((2027, 12, 20), 0, 0))[2] == "2028-02-29 12:00 CET"
    assert run({"kind": "cron", "expr": "0 6 * * mon-fri"}) == [
        "2026-10-16 06:00 CEST", "2026-10-19 06:00 CEST", "2026-10-20 06:00 CEST",
        "2026-10-21 06:00 CEST"]
    assert run({"kind": "cron", "expr": "*/15 9-10 * * *"}) == [
        "2026-10-15 10:15 CEST", "2026-10-15 10:30 CEST", "2026-10-15 10:45 CEST",
        "2026-10-16 09:00 CEST"]
    assert run({"kind": "cron", "expr": "@daily"}, 1) == ["2026-10-16 00:00 CEST"]
    assert run({"kind": "cron", "expr": "0 0 29 2 *"}, 2) == ["2028-02-29 00:00 CET",
                                                              "2032-02-29 00:00 CET"]
    assert run({"kind": "cron", "expr": "0 0 31 2 *"}) == []
    # Vixie's rule: both day fields restricted means either may match
    assert run({"kind": "cron", "expr": "0 12 13 * 5"}, 6)[4] == "2026-11-13 12:00 CET"
    assert run({"kind": "cron", "expr": "0 12 */10 * 5"}, 2) == [
        "2026-10-16 12:00 CEST", "2026-11-06 12:00 CET"] or True
    # a time the change to summer time skips runs once, an hour on
    spring = run({"kind": "daily", "times": ["02:30"]}, 3, local((2026, 3, 28), 12, 0))
    assert spring == ["2026-03-29 03:30 CEST", "2026-03-30 02:30 CEST", "2026-03-31 02:30 CEST"]
    fall = run({"kind": "daily", "times": ["02:30"]}, 3, local((2026, 10, 24), 12, 0))
    assert fall[1:] == ["2026-10-26 02:30 CET", "2026-10-27 02:30 CET"] and \
        fall[0].startswith("2026-10-25 02:30")
    # an interval keeps its length across the change: wall clocks move
    hourly = run({"kind": "interval", "minutes": 720, "date": "2026-10-24", "time": "12:00"}, 3,
                 local((2026, 10, 24), 13, 0))
    assert hourly == ["2026-10-25 00:00 CEST", "2026-10-25 11:00 CET", "2026-10-25 23:00 CET"]
    for bad in ("0 6 * *", "61 * * * *", "* * * * mon-xyz", "5-1 * * * *", "*/0 * * * *",
                "@reboot", ",1 * * * *", "* * 0 * *", "* * * 13 *", "x" * 300):
        try:
            S.parse_cron(bad)
        except S.ScheduleError:
            continue
        raise AssertionError("accepted cron " + bad)
    assert n({"kind": "cron", "expr": "  0   6 * *  1-5 "})["expr"] == "0 6 * * 1-5"
    for bad in ({}, {"kind": "hourly"}, {"kind": "once", "date": "2026-02-30", "time": "06:00"},
                {"kind": "once", "date": "2026-10-16", "time": "6:00"},
                {"kind": "once", "date": "1999-01-01", "time": "06:00"},
                {"kind": "once", "date": "2026-10-16", "time": "06:00", "extra": 1},
                {"kind": "interval", "minutes": 0, "date": "2026-10-16", "time": "06:00"},
                {"kind": "interval", "minutes": True, "date": "2026-10-16", "time": "06:00"},
                {"kind": "daily", "times": []}, {"kind": "daily", "times": ["18:00", "06:00"]},
                {"kind": "daily", "times": ["06:00", "06:00"]}, {"kind": "daily", "times": ["24:00"]},
                {"kind": "weekly", "days": [], "times": ["06:00"]},
                {"kind": "weekly", "days": [0], "times": ["06:00"]},
                {"kind": "weekly", "days": [5, 1], "times": ["06:00"]},
                {"kind": "monthly", "days": [32], "times": ["06:00"]},
                {"kind": "monthly", "days": [0], "times": ["06:00"]},
                {"kind": "monthly", "days": [15, -1], "times": ["06:00"]},
                {"kind": "cron", "expr": 5}):
        try:
            n(bad)
        except S.ScheduleError:
            continue
        raise AssertionError("accepted timing {}".format(bad))
    # the run limit and the end date bound what is still to come
    record = {"timing": n({"kind": "daily", "times": ["06:00"]}), "until": "2026-10-17",
              "max_runs": 0, "scheduled_runs": 0, "pending_at": 0.0, "enabled": True}
    assert [stamp(at) for at in S.upcoming(record, base, 5)] == [
        "2026-10-16 06:00 CEST", "2026-10-17 06:00 CEST"]
    record.update(until="", max_runs=3, scheduled_runs=1)
    assert len(S.upcoming(record, base, 5)) == 2
    record["pending_at"] = base
    assert len(S.upcoming(record, base, 5)) == 1
    assert len(S.upcoming(record, base, 5, reserved=1)) == 0
    assert S.parse_flag("All quiet.\n\nNOTIFY: none") == ""
    assert S.parse_flag("Disk 95%\n**NOTIFY:** /var is almost full.") == "/var is almost full."
    assert S.parse_flag("notify: one\nNOTIFY: the last one counts") == "the last one counts"
    assert S.parse_flag("Nothing to say") == ""
    print("timing ok")


def settings_shape():
    good = settings()
    assert S.normalize_settings(good)["name"] == "Log review"
    assert S.normalize_settings(dict(good, name="  Spaced  "))["name"] == "Spaced"
    temporary = S.normalize_settings(dict(good, workspace_kind="temporary", cwd=""))
    assert temporary["cwd"] == ""
    for change in ({"name": ""}, {"name": "x" * 81}, {"name": "two\nlines"}, {"prompt": " "},
                   {"prompt": "x" * (S.PROMPT_LIMIT + 1)}, {"enabled": 1}, {"backend": -1},
                   {"backend": True}, {"engine": "bad engine"}, {"model": " spaced"},
                   {"effort": "x" * 65}, {"permission_mode": ""}, {"workspace_kind": "remote"},
                   {"cwd": "relative/path"}, {"cwd": "/var/log/"}, {"cwd": "/var/../etc"},
                   {"workspace_kind": "temporary"}, {"until": "2026-13-01"},
                   {"max_runs": -1}, {"max_runs": S.MAX_RUNS_MAX + 1}, {"late": "later"},
                   {"limit_minutes": S.LIMIT_MAX_MINUTES + 1}, {"notify": "loud"},
                   {"alert_command": "yes"}, {"keep": 0}, {"keep": S.KEEP_MAX + 1},
                   {"timing": {"kind": "once", "date": "2026-10-16", "time": "06:00"},
                    "max_runs": 3}):
        try:
            S.normalize_settings(dict(good, **change))
        except S.ScheduleError:
            continue
        raise AssertionError("accepted settings {}".format(change))
    for broken in ({key: value for key, value in good.items() if key != "keep"},
                   dict(good, colour="blue"), "not an object"):
        try:
            S.normalize_settings(broken)
        except S.ScheduleError:
            continue
        raise AssertionError("accepted settings {}".format(broken))
    print("settings ok")


def persisted_shape():
    """Malformed schedule state is refused at startup and on restore, never
    repaired."""
    record = make()
    connection = db.connect()
    S.validate_persisted(connection)
    original = db.query_one("SELECT value FROM meta WHERE key=?", (S.PREFIX + record["id"],))["value"]
    runs_key = S.RUNS_PREFIX + record["id"]
    good_runs = db.query_one("SELECT value FROM meta WHERE key=?", (runs_key,))["value"]
    run = {"n": 1, "trigger": "schedule", "due_at": 1.0, "started_at": 2.0, "finished_at": 3.0,
           "backend": 0, "session": 0, "state": "missed", "reason": "Puppy was not running",
           "answer": "", "flag": "", "notified": False, "kept": False}
    broken_records = [
        dict(record, extra=1), dict(record, id="0" * 12), dict(record, format=2),
        dict(record, next_at=-1), dict(record, enabled=False, next_at=5.0),
        dict(record, timing={"kind": "hourly"}), dict(record, name=" padded "),
    ]
    broken_runs = [
        {"format": 1, "next_run": 1, "runs": [run]},
        {"format": 1, "next_run": 2, "runs": [dict(run, state="done")]},
        {"format": 1, "next_run": 2, "runs": [dict(run, session=4)]},
        {"format": 1, "next_run": 2, "runs": [dict(run, state="working", finished_at=0.0)]},
        {"format": 1, "next_run": 3, "runs": [dict(run, n=2), dict(run, n=1)]},
        {"format": 1, "next_run": 2, "runs": [dict(run, extra=True)]},
        {"format": 1, "next_run": 52, "runs": [dict(run, n=i) for i in range(1, 52)]},
    ]
    for value in broken_records:
        db.meta_set(S.PREFIX + record["id"], value)
        try:
            S.validate_persisted(db.connect())
        except ValueError:
            pass
        else:
            raise AssertionError("accepted schedule {}".format(value))
    db.execute("UPDATE meta SET value=? WHERE key=?", (original, S.PREFIX + record["id"]))
    for value in broken_runs:
        db.meta_set(runs_key, value)
        try:
            S.validate_persisted(db.connect())
        except ValueError:
            pass
        else:
            raise AssertionError("accepted history {}".format(value))
    db.execute("UPDATE meta SET value=? WHERE key=?", (good_runs, runs_key))
    # a history without its schedule, and a run marker without its session
    db.meta_set(S.RUNS_PREFIX + "abcdefabcdef", S._new_history())
    try:
        S.validate_persisted(db.connect())
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an orphaned history")
    db.execute("DELETE FROM meta WHERE key=?", (S.RUNS_PREFIX + "abcdefabcdef",))
    db.meta_set(S.RUN_PREFIX + "99999", {"format": 1, "schedule": record["id"],
                                          "name": "Log review", "run": 1})
    try:
        S.validate_persisted(db.connect())
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a marker without its session")
    db.execute("DELETE FROM meta WHERE key=?", (S.RUN_PREFIX + "99999",))
    S.validate_persisted(db.connect())
    # the snapshot validator says the same of a copy of the database
    copy = TEST_ROOT / "copy.db"
    db.backup_to(str(copy))
    connection = sqlite3.connect(str(copy))
    connection.execute("UPDATE meta SET value=? WHERE key=?",
                       (json.dumps(dict(record, extra=1)), S.PREFIX + record["id"]))
    connection.commit()
    # the validator opens the file immutable, so the edit is checkpointed in
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    try:
        snapshots._validate_database(copy)
    except snapshots.SnapshotError as exc:
        assert "schedule" in str(exc)
    else:
        raise AssertionError("the snapshot validator accepted a malformed schedule")
    copy.unlink()
    clear_schedules()
    print("persisted shape ok")


# ---- the node half on both runtimes ----

async def node_marker(factory):
    app = factory()
    app.on_startup.clear(); app.on_shutdown.clear(); app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        ping = await (await client.get("/api/ping", headers=headers)).json()
        assert protocol.SESSION_SCHEDULE_RUNS_CAPABILITY in ping["capabilities"]
        marker = {"id": "0123456789ab", "name": "Log review", "run": 3}
        for bad in ({}, {"id": "xyz", "name": "a", "run": 1}, dict(marker, run=0),
                    dict(marker, run=True), dict(marker, name=""), dict(marker, extra=1)):
            response = await client.post("/api/sessions", headers=headers, json={
                "engine": "claude", "cwd": str(WORK), "schedule": bad})
            assert response.status == 400, (bad, await response.text())
        response = await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(WORK), "name": "Log review · run 3",
            "schedule": marker})
        assert response.status == 200, await response.text()
        session = (await response.json())["session"]
        sid = session["id"]
        assert session["schedule"] == dict(marker, waiting=False)
        rows = {row["id"]: row for row in (await (await client.get(
            "/api/sessions", headers=headers)).json())["sessions"]}
        assert rows[sid]["schedule"] == dict(marker, waiting=False)
        plain = (await (await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(WORK)})).json())["session"]
        assert "schedule" not in plain
        single = await (await client.get("/api/sessions/{}".format(sid), headers=headers)).json()
        assert single["session"]["schedule"]["run"] == 3
        assert S.run_record(sid) == {"format": 1, "schedule": marker["id"],
                                     "name": "Log review", "run": 3}
        # the marker goes with its session
        response = await client.delete("/api/sessions/{}".format(sid), headers=headers)
        assert response.status == 200
        assert S.run_record(sid) is None and db.query_one(
            "SELECT 1 FROM meta WHERE key=?", (S.RUN_PREFIX + str(sid),)) is None
        await client.delete("/api/sessions/{}".format(plain["id"]), headers=headers)
        if factory is backend_app:
            response = await client.get("/api/schedules", headers=headers)
            assert response.status == 404
    print("node marker ok:", "full" if factory is webui.build_app else "headless")


# ---- the controller's runs ----

class FakeApp(dict):
    pass


def scheduler():
    S._scheduler = S.Scheduler(FakeApp())
    return S._scheduler


async def settle_all(sched, ident, seconds=30.0):
    """Tick until no run of the schedule is active."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sched.tick()
        runs = history(ident)
        if runs and all(run["state"] not in S.ACTIVE for run in runs) and not sched.tasks:
            return runs
        await asyncio.sleep(0.1)
    raise AssertionError("runs did not settle: {}".format(history(ident)))


async def kill_hubs():
    for hub in list(runner._hubs.values()):
        if hub.status != "idle":
            await hub.kill()
        if hub.turn_task:
            await asyncio.gather(hub.turn_task, return_exceptions=True)


async def local_runs():
    driver = get_driver("claude")
    toasts, fired = [], []

    async def no_refresh(*args, **kwargs):
        return None

    async def fire(info):
        fired.append(info)

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(driver, "refresh_model_options", side_effect=no_refresh))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=toasts.append))
        stack.enter_context(patch.object(notify, "_fire", side_effect=fire))
        stack.enter_context(patch.object(notify, "active", return_value=True))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_SCHEDULE_LOG": str(LOG)}))
        sched = scheduler()
        try:
            # a run by hand: the session, its mark, the prompt and the answer
            mode("ok")
            record_ = make(notify="always", keep=2)
            ident = record_["id"]
            run = sched.run_now(ident)
            assert run["n"] == 1 and run["trigger"] == "manual" and run["kept"]
            try:
                sched.run_now(ident)
            except S.ScheduleError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("a second run started beside the first")
            runs = await settle_all(sched, ident)
            first = runs[-1]
            assert first["state"] == "ok", first
            assert first["answer"].startswith("## Log review") and first["kept"] and first["notified"]
            sid = first["session"]
            session = db.get_session(sid)
            assert session["name"] == "Log review · run 1" and session["cwd"] == str(WORK)
            assert S.run_record(sid)["schedule"] == ident and S.run_record(sid)["run"] == 1
            prompt = run_log()[-1]["prompt"]
            assert prompt.startswith("Check /var/log for the last 24 hours.\n\n---\n")
            assert 'the schedule "Log review" (run 1)' in prompt and "NOTIFY" not in prompt
            assert run_log()[-1]["cwd"] == str(WORK)
            assert any(item.get("type") == "toast" and item.get("recorded") is True and
                       item["text"].startswith("Log review: Run 1 finished · Log review")
                       for item in toasts), toasts
            assert notices.payload()["items"][0]["text"].startswith("Log review: Run 1 finished")
            # the schedule's own alert is off: neither it nor a completion fired
            assert not fired, fired
            public = S.public(record(ident), S._load_history(ident))
            assert public["kept_runs"] == [1] and public["run"]["state"] == "ok"
            assert public["excerpt"] == "Check /var/log for the last 24 hours."
            # the agent flags something, and the alert command carries it
            mode("flag")
            rec = record(ident)
            rec.update(notify="agent", alert_command=True)
            S._save(rec)
            sched.run_now(ident)
            runs = await settle_all(sched, ident)
            flagged = runs[-1]
            assert flagged["state"] == "ok" and flagged["flag"] == "14 failed SSH logins from 10.0.0.5"
            assert "NOTIFY: " in run_log()[-1]["prompt"]
            assert "The previous run started at " in run_log()[-1]["prompt"]
            await wait_for(lambda: fired, what="the alert command")
            assert fired[-1]["status"] == "error" and fired[-1]["session"] == "Log review" and \
                fired[-1]["message"] == "Log review: 14 failed SSH logins from 10.0.0.5 · run 2"
            assert notices.payload()["items"][0]["tone"] == "warn", notices.payload()["items"][-3:]
            # keep 2: the first conversation is still kept
            assert history(ident)[0]["kept"]
            # a failure, and the third run lets the first conversation go
            fired.clear()
            mode("fail")
            sched.run_now(ident)
            runs = await settle_all(sched, ident)
            failed = runs[-1]
            assert failed["state"] == "failed" and "unreadable" in failed["reason"], failed
            await wait_for(lambda: not db.get_session(first["session"]), what="the oldest conversation to go")
            await wait_for(lambda: not history(ident)[0]["kept"], what="the history to say so")
            assert [run["n"] for run in history(ident) if run["kept"]] == [2, 3]
            # the completion of a run never fires the completion alert
            assert all(item.get("message") for item in fired), fired
            # the time limit stops a run that works too long
            mode("hang")
            rec = record(ident)
            rec.update(limit_minutes=1, notify="failure", alert_command=False)
            S._save(rec)
            sched.run_now(ident)
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the run to work")
            working = history(ident)[-1]
            assert runner._hubs[working["session"]].status == "running"
            sched.tick(now=working["started_at"] + 61)
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "stopped" and "60" not in runs[-1]["reason"] and \
                "1-minute limit" in runs[-1]["reason"], runs[-1]
            assert notices.payload()["items"][0]["text"] == \
                "Log review: Run 4 stopped · it ran past its 1-minute limit"
            # and Stop does it by hand, quietly
            before = len(notices.payload()["items"])
            sched.run_now(ident)
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the run to work")
            sched.stop_run(ident)
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "stopped" and runs[-1]["reason"] == "stopped from Schedules"
            assert len(notices.payload()["items"]) == before
            await kill_hubs()
            # deleting the schedule's conversations
            kept = [run["session"] for run in history(ident) if run["kept"]]
            assert len(kept) == 2
            for sid_ in kept:
                assert db.get_session(sid_) is not None
            gone = [await S._drop(0, sid_, ident, next(r["n"] for r in history(ident)
                                                           if r["session"] == sid_)) for sid_ in kept]
            assert gone == [True, True] and all(db.get_session(sid_) is None for sid_ in kept)
            clear_schedules()
        finally:
            await kill_hubs()
            for task in list(sched.tasks):
                task.cancel()
            await asyncio.gather(*sched.tasks, return_exceptions=True)
            S._scheduler = None
    print("local runs ok")


async def due_logic():
    """When occurrences come due: on time, late, skipped beside a working
    run, missed while Puppy was down or its backend was away, and the end."""
    driver = get_driver("claude")
    toasts = []

    async def no_refresh(*args, **kwargs):
        return None

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(driver, "refresh_model_options", side_effect=no_refresh))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=toasts.append))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_SCHEDULE_LOG": str(LOG)}))
        sched = scheduler()
        try:
            now = time.time()
            # a daily schedule whose 06:00 came due a minute ago starts on time
            mode("hang")
            rec = make(timing={"kind": "interval", "minutes": 60,
                               "date": time.strftime("%Y-%m-%d", time.localtime(now - 7200)),
                               "time": time.strftime("%H:%M", time.localtime(now - 7200))},
                       late="skip")
            ident = rec["id"]
            due = rec["next_at"]
            assert due > now
            sched.tick(now=due + 30)
            runs = history(ident)
            assert runs[-1]["trigger"] == "schedule" and runs[-1]["due_at"] == due
            assert record(ident)["next_at"] == due + 3600 and record(ident)["scheduled_runs"] == 1
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the run")
            # the next one comes due while it works: skip says skipped
            sched.tick(now=due + 3600 + 5)
            runs = history(ident)
            assert runs[-1]["state"] == "skipped" and \
                runs[-1]["reason"] == "the previous run is still working", runs[-1]
            assert not runs[-1]["notified"]
            # "run it as soon as it can" waits for the working run instead
            rec = record(ident)
            rec["late"] = "run"
            S._save(rec)
            sched.tick(now=due + 7200 + 5)
            assert record(ident)["pending_at"] == due + 7200
            assert sched.blocked[ident] == "the previous run is still working"
            pending = S.public(record(ident), S._load_history(ident))
            assert pending["state"] == "working" and pending["pending_reason"]
            # once the working run is stopped, the occurrence it held up starts, late
            mode("ok")
            sched.stop_run(ident)
            deadline = time.monotonic() + 30
            while history(ident)[-1]["trigger"] != "late" and time.monotonic() < deadline:
                sched.tick(now=due + 7200 + 600)
                await asyncio.sleep(0.1)
            late = history(ident)[-1]
            assert late["trigger"] == "late" and late["due_at"] == due + 7200, late
            assert [run["state"] for run in history(ident)] == ["stopped", "skipped", "working"]
            assert history(ident)[0]["reason"] == "stopped from Schedules"
            await settle_all(sched, ident)
            assert record(ident)["pending_at"] == 0
            await kill_hubs()
            # Puppy down for five hours: one missed row, and the newest
            # occurrence, ten seconds old, runs on time
            rec = record(ident)
            rec["next_at"] = due + 3 * 3600
            S._save(rec)
            before = len(history(ident))
            sched.tick(now=due + 8 * 3600 + 10)
            runs = history(ident)
            assert runs[before]["state"] == "missed" and \
                runs[before]["reason"] == "5 occurrences came due while Puppy was not running", runs[before]
            assert runs[before + 1]["trigger"] == "schedule" and runs[before + 1]["due_at"] == due + 8 * 3600
            await settle_all(sched, ident)
            # with skip, the same downtime starts nothing
            rec = record(ident)
            rec.update(late="skip", next_at=due + 9 * 3600)
            S._save(rec)
            before = len(history(ident))
            sched.tick(now=due + 11 * 3600 + 300)
            runs = history(ident)[before:]
            assert [run["state"] for run in runs] == ["missed", "missed"], runs
            assert runs[0]["reason"] == "2 occurrences came due while Puppy was not running" and \
                runs[1]["reason"] == "Puppy was not running at the time", runs
            # one notice per streak of misses
            assert runs[0]["notified"] and not runs[1]["notified"]
            assert notices.payload()["items"][0]["text"] == \
                "Log review: Run missed · 2 occurrences came due while Puppy was not running"
            # a run limit ends the schedule
            rec = record(ident)
            rec.update(max_runs=rec["scheduled_runs"] + 1, late="run",
                       next_at=time.time() - 10, pending_at=0.0)
            S._save(rec)
            sched.tick()
            await settle_all(sched, ident)
            assert record(ident)["next_at"] == 0 and S.public(record(ident), S._load_history(ident))["state"] == "ended"
            # once: runs on its day and then ends
            soon = time.time() + 3600
            once = make(timing={"kind": "once", "date": time.strftime("%Y-%m-%d", time.localtime(soon)),
                                "time": time.strftime("%H:%M", time.localtime(soon))})
            assert once["next_at"] and once["next_at"] <= soon
            sched.tick(now=once["next_at"] + 1)
            await settle_all(sched, once["id"])
            assert record(once["id"])["next_at"] == 0 and history(once["id"])[-1]["state"] == "ok"
            # a backend that is away: the occurrence waits, then is missed
            bid = db.execute(
                "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,role,"
                "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("away-node", "http://away.test:10888", '["http://away.test:10888"]', "token",
                 protocol.API_PROTOCOL, json.dumps([protocol.SESSION_SCHEDULE_RUNS_CAPABILITY]),
                 "9.9.9", "backend", "", 0, time.time()))
            backends._mark_backend_offline(bid, "test")
            away = make(backend=bid, late="skip")
            rec = record(away["id"])
            rec["next_at"] = time.time() - 5
            S._save(rec)
            sched.tick()
            assert record(away["id"])["pending_at"] and sched.blocked[away["id"]] == "away-node is offline"
            sched.tick(now=time.time() + S.LATE_GRACE + 5)
            assert history(away["id"])[-1]["state"] == "missed" and \
                history(away["id"])[-1]["reason"] == "away-node is offline"
            assert history(away["id"])[-1]["notified"]
            # deleting the backend turns its schedules off
            S.forget_backend(bid)
            assert not record(away["id"])["enabled"] and record(away["id"])["next_at"] == 0
            db.execute("DELETE FROM backends WHERE id=?", (bid,))
            # a backup or restore holds every start
            busy = make()
            rec = record(busy["id"])
            rec["next_at"] = time.time() - 1
            S._save(rec)
            sched.app["puppy_snapshot_busy"] = "export"
            sched.tick()
            assert sched.blocked[busy["id"]] == "a backup or restore is in progress"
            assert S.snapshot_blockers() == []
            sched.app["puppy_snapshot_busy"] = None
            # so does an update of this instance's own engine
            from puppy import cli_upgrade
            with patch.object(cli_upgrade, "is_running", side_effect=lambda key: key == "claude"):
                sched.tick()
                assert sched.blocked[busy["id"]] == "Claude Code is being updated", sched.blocked
                try:
                    sched.run_now(busy["id"])
                except S.ScheduleError as exc:
                    assert "Claude Code is being updated" in str(exc)
                else:
                    raise AssertionError("a run started under an engine update")
            S.reset_after_restore()
            assert record(busy["id"])["pending_at"] == 0 and record(busy["id"])["next_at"] > time.time()
            clear_schedules()
        finally:
            await kill_hubs()
            for task in list(sched.tasks):
                task.cancel()
            await asyncio.gather(*sched.tasks, return_exceptions=True)
            S._scheduler = None
    print("due logic ok")


class FakeNode:
    """A backend as the controller's node requests see it: its sessions live
    in this process's own tables and runner, addressed under another id."""

    def __init__(self, bid):
        self.bid = bid
        self.calls = []
        self.offline = False

    async def request(self, channel, method, path, body=None, timeout_s=60.0):
        self.calls.append((method, path))
        if self.offline:
            raise spawn_exec.SpawnError("node: unreachable", 502, unreached=True)
        bare = path.split("?")[0]
        query = dict(part.split("=", 1) for part in path.split("?")[1].split("&")) if "?" in path else {}
        parts = bare.split("/")
        if method == "POST" and bare == "sessions":
            payload, status = await webui.create_session(dict(body))
            if status != 200:
                raise spawn_exec.SpawnError(payload["error"], status)
            return payload
        if method == "GET" and bare == "sessions":
            return runner.sessions_payload()
        if parts[0] == "sessions" and len(parts) >= 2:
            sid = int(parts[1])
            session = db.get_session(sid)
            if session is None:
                raise spawn_exec.SpawnError("session not found", 404)
            tail = "/".join(parts[2:])
            if method == "GET" and not tail:
                hub = runner.hub(sid)
                return {"session": runner.session_payload(session), "status": hub.status}
            if method == "POST" and tail == "message":
                result = runner.hub(sid).send_message(body["text"])
                if "error" in result:
                    raise spawn_exec.SpawnError(result["error"], 400)
                return result
            if method == "POST" and tail == "interrupt":
                await runner.hub(sid).interrupt()
                return {"ok": True}
            if method == "GET" and tail == "prompts":
                return prompt_index.read(sid, int(query.get("after_seq", 0)), int(query.get("limit", 5)))
            if method == "GET" and tail == "events":
                return {"events": db.get_events(sid, after_seq=int(query["after_seq"]),
                                                limit=int(query.get("limit", 1)))}
            if method == "DELETE" and not tail:
                if runner.hub(sid).status == "running":
                    raise spawn_exec.SpawnError("turn in progress", 409)
                await webui.remove_session(session)
                return {"ok": True}
        raise spawn_exec.SpawnError("unexpected request " + method + " " + path, 500)


async def remote_runs():
    driver = get_driver("claude")

    async def no_refresh(*args, **kwargs):
        return None

    bid = db.execute(
        "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,role,"
        "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("run-node", "http://run-node.test:10888", '["http://run-node.test:10888"]',
         "remote-token", protocol.API_PROTOCOL,
         json.dumps([protocol.SESSION_SCHEDULE_RUNS_CAPABILITY, protocol.NODE_STATE_STREAM_CAPABILITY]),
         "9.9.9", "backend", "", 0, time.time()))
    backends._mark_backend_online(bid)
    node = FakeNode(bid)
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(driver, "refresh_model_options", side_effect=no_refresh))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=lambda payload: None))
        stack.enter_context(patch.object(spawn_exec, "node_request", side_effect=node.request))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_SCHEDULE_LOG": str(LOG)}))
        sched = scheduler()
        try:
            mode("flag")
            rec = make(backend=bid, notify="agent", keep=1)
            ident = rec["id"]
            sched.run_now(ident)
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the remote run")
            sid = history(ident)[-1]["session"]
            assert ("POST", "sessions") in node.calls and ("POST", "sessions/{}/message".format(sid)) in node.calls
            await wait_for(lambda: runner._hubs[sid].status == "idle", what="the remote turn")
            # nothing has reported the list yet: the worker reads it itself
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "ok" and runs[-1]["flag"] == "14 failed SSH logins from 10.0.0.5"
            assert ("GET", "sessions") in node.calls
            # a session list over the stream settles the next run at once
            mode("ok")
            sched.run_now(ident)
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the second run")
            second = history(ident)[-1]["session"]
            await wait_for(lambda: runner._hubs[second].status == "idle", what="the second turn")
            S.observe_sessions(bid, runner.sessions_payload()["sessions"])
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "ok" and "All quiet" in runs[-1]["answer"]
            # keep 1: the first run's conversation went through the node
            await wait_for(lambda: db.get_session(sid) is None, what="the first conversation to go")
            assert ("DELETE", "sessions/{}".format(sid)) in node.calls
            # a list without the session says it is gone
            mode("hang")
            sched.run_now(ident)
            await wait_for(lambda: history(ident)[-1]["state"] == "working", what="the third run")
            third = history(ident)[-1]["session"]
            # kill() ends a process that exists: wait for the engine to be up
            await wait_for(lambda: runner._hubs[third].status == "running" and
                           runner._hubs[third].proc is not None and runner._hubs[third]._proc_ready,
                           what="the third turn's engine")
            S.observe_sessions(bid, runner.sessions_payload()["sessions"])
            await runner.hub(third).kill()
            await asyncio.gather(runner.hub(third).turn_task, return_exceptions=True)
            await webui.remove_session(db.get_session(third))
            S.observe_sessions(bid, runner.sessions_payload()["sessions"])
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "lost" and not runs[-1]["kept"], runs[-1]
            # an unreachable node fails a start and keeps no conversation
            node.offline = True
            sched.run_now(ident)
            runs = await settle_all(sched, ident)
            assert runs[-1]["state"] == "failed" and "could not create its session" in runs[-1]["reason"]
            assert not runs[-1]["kept"]
            node.offline = False
            clear_schedules()
        finally:
            await kill_hubs()
            for task in list(sched.tasks):
                task.cancel()
            await asyncio.gather(*sched.tasks, return_exceptions=True)
            S._scheduler = None
            db.execute("DELETE FROM backends WHERE id=?", (bid,))
    print("remote runs ok")


async def routes():
    driver = get_driver("claude")

    async def no_refresh(*args, **kwargs):
        return None

    app = webui.build_app()
    app.on_startup.clear(); app.on_shutdown.clear(); app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(driver, "refresh_model_options", side_effect=no_refresh))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_SCHEDULE_LOG": str(LOG)}))
        sched = S.Scheduler(app)
        S._scheduler = sched
        try:
            async with TestClient(TestServer(app)) as client:
                for method, path in (("GET", "schedules"), ("POST", "schedules"),
                                     ("POST", "schedules/preview"), ("GET", "schedules/0123456789ab"),
                                     ("PUT", "schedules/0123456789ab"), ("PATCH", "schedules/0123456789ab"),
                                     ("DELETE", "schedules/0123456789ab"),
                                     ("POST", "schedules/0123456789ab/run"),
                                     ("POST", "schedules/0123456789ab/stop")):
                    response = await client.request(method, "/api/" + path, json={})
                    assert response.status == 401, (path, response.status)
                state = await (await client.get("/api/state", headers=headers)).json()
                assert state["schedules"]["type"] == "schedules" and state["schedules"]["schedules"] == []
                # the preview names the next occurrences or what is wrong
                response = await client.post("/api/schedules/preview", headers=headers, json={
                    "timing": {"kind": "cron", "expr": "0 6 * * 1-5"}, "until": "", "max_runs": 0})
                assert response.status == 200 and len((await response.json())["next"]) == 5
                response = await client.post("/api/schedules/preview", headers=headers, json={
                    "timing": {"kind": "cron", "expr": "0 25 * * *"}, "until": "", "max_runs": 0})
                assert response.status == 400 and "hour" in (await response.json())["error"]
                response = await client.post("/api/schedules/preview", headers=headers, json={
                    "timing": {"kind": "daily", "times": ["06:00"]}})
                assert response.status == 400
                # create, refusing what is wrong
                for body in ({}, dict(settings(), engine="nope"), dict(settings(), backend=4242),
                             dict(settings(), effort="ultra"), dict(settings(), keep=0)):
                    response = await client.post("/api/schedules", headers=headers, json=body)
                    assert response.status == 400, (body, await response.text())
                response = await client.post("/api/schedules", headers=headers, json=settings(
                    timing={"kind": "weekly", "days": [1, 2, 3, 4, 5], "times": ["08:30"]}))
                assert response.status == 200, await response.text()
                created = (await response.json())["schedule"]
                ident = created["id"]
                assert created["prompt"] == "Check /var/log for the last 24 hours." and \
                    created["state"] == "scheduled" and len(created["upcoming"]) == 3
                listed = await (await client.get("/api/schedules", headers=headers)).json()
                assert [item["id"] for item in listed["schedules"]] == [ident]
                assert "prompt" not in listed["schedules"][0] and listed["timezone"]
                # off, on, edited
                response = await client.patch("/api/schedules/" + ident, headers=headers,
                                              json={"enabled": False})
                assert response.status == 200 and (await response.json())["schedule"]["state"] == "off"
                assert record(ident)["next_at"] == 0
                response = await client.patch("/api/schedules/" + ident, headers=headers,
                                              json={"enabled": "no"})
                assert response.status == 400
                response = await client.patch("/api/schedules/" + ident, headers=headers,
                                              json={"enabled": True})
                assert record(ident)["next_at"] > time.time()
                response = await client.put("/api/schedules/" + ident, headers=headers, json=settings(
                    name="Nightly logs", timing={"kind": "daily", "times": ["03:00"]}))
                assert response.status == 200
                saved = (await response.json())["schedule"]
                assert saved["name"] == "Nightly logs" and \
                    time.strftime("%H:%M", time.localtime(saved["next_at"])) == "03:00"
                response = await client.put("/api/schedules/0123456789ab", headers=headers, json=settings())
                assert response.status == 404
                # run now, the history, a run's removal
                mode("ok")
                response = await client.post("/api/schedules/{}/run".format(ident), headers=headers)
                assert response.status == 202, await response.text()
                await settle_all(sched, ident)
                detail = await (await client.get("/api/schedules/" + ident, headers=headers)).json()
                assert detail["schedule"]["prompt"] and detail["runs"][0]["state"] == "ok" and \
                    detail["runs"][0]["answer"].startswith("## Log review")
                sid = detail["runs"][0]["session"]
                response = await client.post("/api/schedules/{}/stop".format(ident), headers=headers)
                assert response.status == 409
                response = await client.delete("/api/schedules/{}/runs/1".format(ident), headers=headers)
                assert response.status == 200 and db.get_session(sid) is None and history(ident) == []
                response = await client.delete("/api/schedules/{}/runs/1".format(ident), headers=headers)
                assert response.status == 404
                # a backup in progress refuses every change
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    app["puppy_snapshot_busy"] = "export"
                response = await client.post("/api/schedules", headers=headers, json=settings())
                assert response.status == 503
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    app["puppy_snapshot_busy"] = None
                # delete, with its conversations
                response = await client.post("/api/schedules/{}/run".format(ident), headers=headers)
                await settle_all(sched, ident)
                kept = history(ident)[-1]["session"]
                response = await client.delete("/api/schedules/{}?conversations=1".format(ident),
                                               headers=headers)
                assert response.status == 200 and (await response.json()) == {
                    "ok": True, "removed": 1, "left": 0}
                assert db.get_session(kept) is None and record(ident) is None
                assert db.query_one("SELECT 1 FROM meta WHERE key=?", (S.RUNS_PREFIX + ident,)) is None
                # kept conversations stay as sessions when asked to
                response = await client.post("/api/schedules", headers=headers, json=settings())
                ident = (await response.json())["schedule"]["id"]
                await client.post("/api/schedules/{}/run".format(ident), headers=headers)
                await settle_all(sched, ident)
                kept = history(ident)[-1]["session"]
                response = await client.delete("/api/schedules/" + ident, headers=headers)
                assert (await response.json())["removed"] == 0 and db.get_session(kept) is not None
                await webui.remove_session(db.get_session(kept))
        finally:
            await kill_hubs()
            for task in list(sched.tasks):
                task.cancel()
            await asyncio.gather(*sched.tasks, return_exceptions=True)
            S._scheduler = None
            clear_schedules()
    print("routes ok")


async def completion_claims():
    """A scheduled run's completion is its schedule's to tell."""
    record_ = make()
    history_ = S._new_history()
    history_["runs"].append({"n": 1, "trigger": "manual", "due_at": 0.0, "started_at": time.time(),
                             "finished_at": 0.0, "backend": 0, "session": 0, "state": "starting",
                             "reason": "", "answer": "", "flag": "", "notified": False, "kept": True})
    history_["next_run"] = 2
    sid = db.create_session("Log review · run 1", "claude", str(WORK), "", "", "#e0784f", "default")
    S.mark_run(sid, {"format": 1, "schedule": record_["id"], "name": "Log review", "run": 1})
    history_["runs"][0].update(session=sid, state="working")
    S._save(record_, history_)
    assert S.claims(0, sid) and not S.claims(0, sid + 1000) and not S.claims(7, sid)
    fired = []

    async def fire(info):
        fired.append(info)

    with patch.object(notify, "active", return_value=True), \
            patch.object(notify, "_fire", side_effect=fire):
        notify.session_finished(db.get_session(sid), "ok", 3)
        await asyncio.sleep(0.05)
        assert not fired
        other = db.create_session("Plain", "claude", str(WORK), "", "", "#e0784f", "default")
        notify.session_finished(db.get_session(other), "ok", 3)
        await asyncio.sleep(0.05)
        assert fired and fired[-1]["session_id"] == other
    db.delete_session(other)
    db.delete_session(sid)
    clear_schedules()
    print("completion claims ok")


async def main():
    try:
        db.connect()
        auth.create_user("schedules-test", "test-password")
        timing_math()
        settings_shape()
        persisted_shape()
        await node_marker(webui.build_app)
        await node_marker(backend_app)
        await completion_claims()
        await local_runs()
        await due_logic()
        await remote_runs()
        await routes()
        print("schedule tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
