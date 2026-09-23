#!/usr/bin/env python3
"""The node's token ledger: the drivers' usage vocabulary made disjoint, the
day records every engine run is counted in (turns, spawned agents, title
jobs) with each ref counted once, the exact persisted shape refused rather
than repaired, the report's buckets, totals, sessions and jobs, the route on
both runtimes with its capability and refusals, and fixture engines speaking
claude's stream-json through the real runner - a turn broken down by model
and one that is not. No engine, network or quota."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

MODE = sys.argv[1] if sys.argv[1:2] in (["--fixture"], ["--fixture-plain"]) else ""


def fixture(split: bool):
    """An engine that answers once: a model named by its init, a result with
    the main loop's usage and, when ``split``, the process's per-model usage."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-usage",
          "model": "claude-fixture-5[1m]", "tools": []})
    send({"type": "assistant", "message": {"role": "assistant", "model": "claude-fixture-5[1m]",
                                           "content": [{"type": "text", "text": "done"}]}})
    result = {"type": "result", "subtype": "success", "is_error": False,
              "session_id": "native-usage", "num_turns": 1, "result": "done",
              "total_cost_usd": 0.5,
              "usage": {"input_tokens": 10, "output_tokens": 200,
                        "cache_read_input_tokens": 3000,
                        "cache_creation_input_tokens": 40}}
    if split:
        result["modelUsage"] = {
            "claude-fixture-5[1m]": {"inputTokens": 10, "outputTokens": 200,
                                     "cacheReadInputTokens": 3000,
                                     "cacheCreationInputTokens": 40,
                                     "costUSD": 0.45, "contextWindow": 1000000},
            "claude-helper-4": {"inputTokens": 500, "outputTokens": 20,
                                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                "costUSD": 0.05, "contextWindow": 200000}}
    send(result)
    while sys.stdin.readline():
        pass


if MODE:
    fixture(MODE == "--fixture")
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402
ROOT = private_root("token-usage-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import config, db, protocol, runner, token_usage  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

DAY = 86400
# 2026-09-21 12:00:00 UTC: a fixed noon keeps every row inside its own day
NOON = 1789992000.0


def stored(day: str) -> dict:
    row = db.query_one("SELECT value FROM meta WHERE key=?", (token_usage.PREFIX + day,))
    return json.loads(row["value"]) if row else None


def ledger_keys() -> list:
    return [row["key"] for row in db.query(
        "SELECT key FROM meta WHERE key GLOB 'token_usage.*' ORDER BY key")]


def clear() -> None:
    db.execute("DELETE FROM meta WHERE key GLOB 'token_usage.*'")


def vocabulary():
    counts = token_usage.counts
    # claude: input excludes the cache; reads and writes are their own keys
    assert counts({"input_tokens": 18, "output_tokens": 25721,
                   "cache_read_input_tokens": 5768181,
                   "cache_creation_input_tokens": 42564}) == {
        "input": 18, "output": 25721, "cache_read": 5768181, "cache_write": 42564,
        "reasoning": 0}
    # codex: input already counts its cached part, named as a subset
    assert counts({"input_tokens": 498274, "output_tokens": 781,
                   "cached_input_tokens": 450176, "reasoning_output_tokens": 156}) == {
        "input": 48098, "output": 781, "cache_read": 450176, "cache_write": 0,
        "reasoning": 156}
    # opencode: claude's vocabulary with a reasoning subset of the output
    assert counts({"input_tokens": 687, "output_tokens": 12522,
                   "reasoning_output_tokens": 10671, "cache_read_input_tokens": 1530752,
                   "cache_creation_input_tokens": 0}) == {
        "input": 687, "output": 12522, "cache_read": 1530752, "cache_write": 0,
        "reasoning": 10671}
    # nothing, junk and negatives count as nothing; a subset never exceeds its whole
    for empty in (None, {}, [], "x", {"input_tokens": 0}, {"input_tokens": -5},
                  {"input_tokens": True}, {"output_tokens": float("nan")},
                  {"reasoning_output_tokens": 50}):
        assert counts(empty) is None, empty
    assert counts({"input_tokens": 10, "cached_input_tokens": 99}) == {
        "input": 0, "output": 0, "cache_read": 10, "cache_write": 0, "reasoning": 0}
    assert counts({"output_tokens": 5, "reasoning_output_tokens": 9})["reasoning"] == 5
    assert counts({"input_tokens": 12.7})["input"] == 12


def rows_and_store():
    config.load()
    db.connect()
    clear()
    session = {"id": 7, "engine": "claude", "model": "opus[1m]",
               "last_model": "claude-opus-5-5[1m]"}
    event = {"seq": 41, "ts": NOON + 5.00049, "data": {
        "ok": True, "usage": {"input_tokens": 3, "output_tokens": 70,
                                                "cache_read_input_tokens": 900}}}
    rows = token_usage.turn_rows(session, event)
    assert rows == [["turn:7:41", round(NOON + 5.00049, 3), 7, "turn", "claude",
                     "claude-opus-5-5[1m]", 3, 70, 900, 0, 0]], rows
    # no confirmed model: the requested one, else the engine's default ("")
    assert token_usage.turn_rows(dict(session, last_model=""), event)[0][5] == "opus[1m]"
    assert token_usage.turn_rows(dict(session, last_model="", model=""), event)[0][5] == ""
    # a result without usage counts nothing
    assert token_usage.turn_rows(session, {"seq": 1, "ts": NOON, "data": {"ok": False}}) == []
    # a per-model breakdown that covers the main loop splits the turn by model
    split = dict(event, data=dict(event["data"], model_usage={
        "claude-opus-5-5[1m]": {"input_tokens": 3, "output_tokens": 70,
                                "cache_read_input_tokens": 900},
        "claude-haiku-4-5": {"input_tokens": 400, "output_tokens": 9},
        "junk": "x", "": {"input_tokens": 5}}))
    split_rows = token_usage.turn_rows(session, split)
    assert [(row[5], row[6]) for row in split_rows] == [
        ("", 5), ("claude-haiku-4-5", 400), ("claude-opus-5-5[1m]", 3)], split_rows
    assert {row[0] for row in split_rows} == {"turn:7:41"}, "one ref for every model of the turn"
    # a breakdown smaller than the main loop's own usage is not trusted
    thin = dict(event, data=dict(event["data"], model_usage={
        "claude-opus-5-5[1m]": {"input_tokens": 1}}))
    assert [row[5] for row in token_usage.turn_rows(session, thin)] == ["claude-opus-5-5[1m]"]

    # stored in the day of its time, ordered, each ref once whoever reports it
    assert token_usage.store(rows) == 1
    assert ledger_keys() == ["token_usage.2026-09-21"]
    assert stored("2026-09-21") == {"format": 2, "rows": rows}
    assert token_usage.store(split_rows) == 0, "a ref already counted is left alone"
    assert token_usage.store(rows) == 0
    later = token_usage.turn_rows(session, dict(event, seq=42, ts=NOON + 9))
    earlier = token_usage.turn_rows(session, dict(event, seq=40, ts=NOON + 1))
    assert token_usage.store(later + earlier) == 2
    assert [row[0] for row in stored("2026-09-21")["rows"]] == \
        ["turn:7:40", "turn:7:41", "turn:7:42"], "kept in time order"
    # a split turn keeps its rows together, ordered by model
    assert token_usage.store(token_usage.turn_rows(
        session, dict(split, seq=43, ts=NOON + 20))) == 3
    tail = stored("2026-09-21")["rows"][-3:]
    assert [row[5] for row in tail] == ["", "claude-haiku-4-5", "claude-opus-5-5[1m]"]
    # the next UTC day is its own record
    assert token_usage.store(token_usage.turn_rows(
        session, dict(event, seq=50, ts=NOON + DAY))) == 1
    assert ledger_keys() == ["token_usage.2026-09-21", "token_usage.2026-09-22"]
    # a day boundary follows the stored (rounded) time
    edge = token_usage.turn_rows(session, dict(event, seq=51, ts=NOON + 43199.9996))
    assert edge[0][1] == NOON + 43200.0
    token_usage.store(edge)
    assert stored("2026-09-22")["rows"][0][0] == "turn:7:51"
    token_usage.validate_persisted(db.connect())

    # spawned agents and title jobs: counted where they ran, with their owner
    class Job:
        id = "ab12cd34"
        created_at = NOON + 100.9
        finished_at = NOON + 160
        engine = "codex"
        model = "gpt-6-astra"
        model_used = ""
        usage = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}
        owner = ("turn", 7, "turn-1")

    assert token_usage.job_rows(Job) == [
        ["spawn:ab12cd34:{}".format(int(NOON + 100.9)), NOON + 160, 7, "spawn", "codex",
         "gpt-6-astra", 400, 50, 600, 0, 0]]
    Job.model_used = "gpt-6-astra-2"
    Job.owner = ("remote",)
    assert token_usage.job_rows(Job)[0][2:6] == [None, "spawn", "codex", "gpt-6-astra-2"]
    Job.owner = ("title",)
    assert token_usage.job_rows(Job)[0][3] == "title"
    Job.usage = {}
    assert token_usage.job_rows(Job) == [], "a run that reported nothing counts nothing"
    Job.usage = {"input_tokens": 1000}
    token_usage.record_job(Job)
    assert stored("2026-09-21")["rows"][-1][0].startswith("spawn:ab12cd34:")

    # recording never raises into the turn or the job it describes
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
               "SET value=excluded.value", ("token_usage.2026-09-23", "[]"))
    token_usage.record_turn(session, dict(event, seq=60, ts=NOON + 2 * DAY))
    token_usage.record_job(type("Broken", (), {"id": "x"})())
    assert db.query_one("SELECT value FROM meta WHERE key=?",
                        ("token_usage.2026-09-23",))["value"] == "[]", "never repaired"
    db.execute("DELETE FROM meta WHERE key=?", ("token_usage.2026-09-23",))


def persisted_shape():
    good = stored("2026-09-21")
    connection = db.connect()
    key = "token_usage.2026-09-21"

    def write(value, at=key):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value",
                   (at, value if isinstance(value, str) else json.dumps(value)))

    def refused(value, label, at=key):
        written = value if isinstance(value, str) else json.dumps(value)
        write(written, at)
        try:
            token_usage.validate_persisted(connection)
        except ValueError:
            pass
        else:
            raise AssertionError(label)
        # a write reads only its own day and a report the days it spans: a
        # record anywhere else is the startup's and the restore's to refuse
        uses = [lambda: token_usage.report(NOON - DAY, NOON + 3 * DAY),
                lambda: token_usage.store([["turn:9:1", NOON + 30, 9, "turn", "codex", "",
                                            1, 0, 0, 0, 0]])] if at == key else []
        for call in uses:
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError(label + " (use)")
        raw = db.query_one("SELECT value FROM meta WHERE key=?", (at,))
        assert raw["value"] == written, "a malformed record is never repaired"
        if at == key:
            write(json.dumps(good))
        else:
            db.execute("DELETE FROM meta WHERE key=?", (at,))

    def row_variant(index, position, value):
        record = json.loads(json.dumps(good))
        record["rows"][index][position] = value
        return record

    refused("not json", "non-JSON record")
    refused("[]", "a list is not the record")
    refused({"format": 2, "rows": good["rows"], "extra": 1}, "an unknown key")
    refused({"format": 1, "rows": good["rows"]}, "previous format")
    refused({"format": 2, "rows": []}, "an empty day")
    refused({"format": 2, "rows": "x"}, "rows that are not a list")
    refused(good, "a day that does not match its rows", "token_usage.2026-09-20")
    refused(good, "a key that is not a day", "token_usage.20260921")
    refused(good, "an impossible day", "token_usage.2026-13-40")
    refused(row_variant(0, 1, NOON + DAY), "a row outside its day")
    refused(row_variant(0, 1, "noon"), "a time that is not a number")
    refused(row_variant(0, 1, float("inf")), "an infinite time")
    refused(row_variant(0, 0, ""), "an empty ref")
    refused(row_variant(0, 0, "x" * (token_usage.MAX_REF + 1)), "a ref past its cap")
    refused(row_variant(0, 2, 0), "a session id of zero")
    refused(row_variant(0, 2, True), "a boolean session")
    refused(row_variant(0, 2, "7"), "a session that is not a number")
    refused(row_variant(0, 3, "chat"), "a source outside the vocabulary")
    refused(row_variant(0, 4, ""), "an empty engine")
    refused(row_variant(0, 5, 5), "a model that is not text")
    refused(row_variant(0, 6, -1), "a negative count")
    refused(row_variant(0, 6, 1.5), "a fractional count")
    record = json.loads(json.dumps(good))
    record["rows"][0][6:10] = [0, 0, 0, 0]
    refused(record, "a row that counts nothing")
    record = json.loads(json.dumps(good))
    record["rows"][0].append(1)
    refused(record, "a row with an extra column")
    record = json.loads(json.dumps(good))
    record["rows"].reverse()
    refused(record, "rows out of order")
    record = json.loads(json.dumps(good))
    record["rows"].insert(1, list(record["rows"][0]))
    refused(record, "a ref counted twice on one model")
    token_usage.validate_persisted(connection)


def reports():
    clear()
    now_sessions = {}
    for name, engine in (("Garden planner", "claude"), ("Harbor", "codex"), ("Atlas", "claude")):
        now_sessions[name] = db.create_session(name, engine, str(ROOT), "", "", "", "default")
    garden, harbor, atlas = now_sessions["Garden planner"], now_sessions["Harbor"], \
        now_sessions["Atlas"]

    def row(ref, at, session, engine, model, amounts, source="turn"):
        return [ref, at, session, source, engine, model, *amounts]

    rows = [
        row("turn:{}:1".format(garden), NOON + 60, garden, "claude", "opus", [1, 10, 100, 5, 0]),
        row("turn:{}:1".format(garden), NOON + 60, garden, "claude", "haiku", [20, 2, 0, 0, 0]),
        row("turn:{}:2".format(garden), NOON + 3700, garden, "claude", "opus", [2, 20, 200, 0, 0]),
        row("turn:{}:1".format(harbor), NOON + 120, harbor, "codex", "gpt", [50, 5, 500, 0, 3]),
        row("turn:{}:1".format(atlas), NOON - 2 * DAY, atlas, "claude", "opus", [7, 7, 7, 7, 0]),
        row("turn:999:1", NOON + 200, 999, "codex", "gpt", [1, 1, 1, 0, 0]),
        row("spawn:t1:1", NOON + 300, None, "claude", "haiku", [3, 3, 0, 0, 0], "title"),
        row("spawn:r1:1", NOON + 400, None, "codex", "gpt", [4, 4, 0, 0, 0], "spawn"),
    ]
    assert token_usage.store(rows) == len(rows)
    report = token_usage.report(NOON - 600, NOON + DAY)
    assert report["ok"] and report["step"] == 3600 and report["offset"] == 0
    assert report["first_at"] == NOON - 2 * DAY, "the earliest usage kept anywhere"
    assert report["columns"] == ["at", "engine", "model", "input", "output", "cache_read",
                                 "cache_write", "reasoning", "turns"]
    hour = NOON  # noon is on the hour
    assert report["buckets"] == [
        [hour, "claude", "haiku", 23, 5, 0, 0, 0, 1],
        [hour, "claude", "opus", 1, 10, 100, 5, 0, 1],
        [hour, "codex", "gpt", 55, 10, 501, 0, 3, 2],
        [hour + 3600, "claude", "opus", 2, 20, 200, 0, 0, 1],
    ], report["buckets"]
    totals = report["totals"]
    assert (totals["input"], totals["output"], totals["cache_read"], totals["cache_write"],
            totals["reasoning"], totals["turns"]) == (81, 45, 801, 5, 3, 4), totals
    assert set(totals) == set(token_usage.COUNTS) | {"turns"}
    # the tokens count every run; the turns only the turns - a title job and
    # a spawned agent are runs of their own, counted with their job below
    listed = [(entry["id"], entry["turns"], entry["deleted"]) for entry in report["sessions"]]
    assert listed == [(harbor, 1, False), (garden, 2, False), (999, 1, True)], listed
    first = report["sessions"][1]
    assert first["name"] == "Garden planner" and first["engines"] == ["claude"]
    assert first["input"] == 23 and first["cache_read"] == 300 and first["last_at"] == NOON + 3700
    assert report["sessions"][2]["name"] == "" and report["session_count"] == 3
    assert report["jobs"] == {
        "spawn": {"input": 4, "output": 4, "cache_read": 0, "cache_write": 0, "reasoning": 0,
                  "turns": 1},
        "title": {"input": 3, "output": 3, "cache_read": 0, "cache_write": 0, "reasoning": 0,
                  "turns": 1}}
    # the span is half-open, and a quiet span answers empty
    assert token_usage.report(NOON + 61, NOON + 3700)["totals"]["turns"] == 2
    quiet = token_usage.report(NOON + 5 * DAY, NOON + 6 * DAY)
    assert quiet["buckets"] == [] and quiet["sessions"] == [] and quiet["totals"]["turns"] == 0
    assert quiet["first_at"] == NOON - 2 * DAY
    # days at a fixed offset: UTC+2's midnight
    days = token_usage.report(NOON - 3 * DAY, NOON + DAY, step=DAY, offset=7200)
    midnight = NOON - 12 * 3600 - 7200
    assert {bucket[0] for bucket in days["buckets"]} == {midnight - 2 * DAY, midnight}
    # Hourly requests keep their exact rolling bounds while the columns
    # align to a clock that is half an hour ahead of a UTC hour.
    hours = token_usage.report(NOON + 61, NOON + 3700, offset=19800)
    assert hours["totals"]["turns"] == 2
    assert {bucket[0] for bucket in hours["buckets"]} == {NOON - 1800}
    assert (hours["since"], hours["until"]) == (NOON + 61, NOON + 3700)
    # a task is named with its Main
    from puppy import session_tasks
    record = {"format": 1, "parent": garden}
    with patch.object(session_tasks, "record", lambda sid: record if sid == harbor else None):
        named = token_usage.report(NOON - 600, NOON + DAY)["sessions"][0]
    assert named["parent"] == garden and named["parent_name"] == "Garden planner"
    # the heaviest sessions only
    with patch.object(token_usage, "TOP_SESSIONS", 1):
        assert [entry["id"] for entry in token_usage.report(NOON - 600, NOON + DAY)["sessions"]] == [harbor]
    return garden


async def routes(factory, role, garden):
    app = factory()
    startup = [hook for hook in app.on_startup if hook.__name__ == "validate" and
               hook.__qualname__.startswith("register.")]
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.TOKEN_USAGE_CAPABILITY in protocol.BASE_CAPABILITIES
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/token-usage")).status == 401
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        ping = await (await client.get("/api/ping", headers=headers)).json()
        assert "token-usage" in ping["capabilities"], role
        response = await client.get(
            "/api/token-usage?since={}&until={}".format(NOON - 600, NOON + DAY), headers=headers)
        assert response.status == 200, await response.text()
        body = await response.json()
        assert body["totals"]["turns"] == 4 and body["sessions"][1]["id"] == garden
        # the default span is the last thirty days, by the hour
        default = await (await client.get("/api/token-usage", headers=headers)).json()
        assert default["step"] == 3600 and abs(default["until"] - time.time()) < 60
        assert abs(default["until"] - default["since"] - 30 * DAY) < 1
        days = await (await client.get(
            "/api/token-usage?since={}&until={}&step=86400&offset=-18000".format(
                NOON - DAY, NOON + DAY), headers=headers)).json()
        assert days["step"] == DAY and days["offset"] == -18000
        for query in ("since=x", "since=nan", "since=-5", "since=10&until=5",
                      "since=1&until={}".format(6 * 366 * DAY),
                      "step=60", "step=3600.5", "offset=90000", "offset=45"):
            response = await client.get("/api/token-usage?" + query, headers=headers)
            assert response.status == 400, (query, response.status)
        # a ledger gone malformed underneath is the node's problem, said as such
        db.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("token_usage.2026-09-17", "[]"))
        try:
            response = await client.get(
                "/api/token-usage?since={}&until={}".format(NOON - 5 * DAY, NOON), headers=headers)
            assert response.status == 500
        finally:
            db.execute("DELETE FROM meta WHERE key=?", ("token_usage.2026-09-17",))
    # and refuses to start rather than being repaired
    assert startup, "the ledger is validated at startup on the {} runtime".format(role)
    db.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("token_usage.2026-09-17", "[]"))
    try:
        try:
            await startup[0](app)
        except ValueError:
            pass
        else:
            raise AssertionError("a malformed ledger must refuse startup")
    finally:
        db.execute("DELETE FROM meta WHERE key=?", ("token_usage.2026-09-17",))


async def frame(ws, kind, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        message = await ws.receive_json(timeout=max(.1, deadline - time.monotonic()))
        if message.get("type") == kind:
            return message


async def fixture_turn(mode: str):
    """One prompt through the real runner against a fixture engine."""
    driver = get_driver("claude")
    sid = db.create_session("Usage fixture " + mode, "claude", str(ROOT), "", "", "", "default")
    hub = runner.hub(sid)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), mode]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        try:
            assert hub.send_message("count me") == {"queued": False}
            deadline = time.monotonic() + 20
            while hub.status != "idle" or hub.turn_task is None:
                assert time.monotonic() < deadline, "the fixture turn did not end"
                await asyncio.sleep(.02)
            await hub.turn_task
        finally:
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    result = [event for event in db.get_events(sid) if event["kind"] == "result"][-1]
    return sid, result


async def through_the_runner():
    clear()
    app = build_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    async with TestClient(TestServer(app)):
        sid, result = await fixture_turn("--fixture")
        # Native price fields never enter the result or the ledger.
        assert "cost_usd" not in result["data"]
        # the transcript keeps the breakdown; the ledger counts it by model
        assert set(result["data"]["model_usage"]) == {"claude-fixture-5[1m]", "claude-helper-4"}
        assert result["data"]["model_usage"]["claude-helper-4"] == {
            "input_tokens": 500, "output_tokens": 20, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}
        rows = [row for key in ledger_keys()
                for row in json.loads(db.query_one(
                    "SELECT value FROM meta WHERE key=?", (key,))["value"])["rows"]]
        ref = "turn:{}:{}".format(sid, result["seq"])
        assert [(row[0], row[2], row[3], row[4], row[5], row[6:11]) for row in rows] == [
            (ref, sid, "turn", "claude", "claude-fixture-5[1m]", [10, 200, 3000, 40, 0]),
            (ref, sid, "turn", "claude", "claude-helper-4", [500, 20, 0, 0, 0])], rows
        assert rows[0][1] == round(result["ts"], 3)
        # a turn without the breakdown is one row on the model its init named
        clear()
        plain_sid, plain = await fixture_turn("--fixture-plain")
        assert "model_usage" not in plain["data"]
        assert db.get_session(plain_sid)["last_model"] == "claude-fixture-5[1m]"
        rows = json.loads(db.query_one("SELECT value FROM meta WHERE key GLOB 'token_usage.*'")[
            "value"])["rows"]
        assert [(row[0], row[5], row[6:11]) for row in rows] == [
            ("turn:{}:{}".format(plain_sid, plain["seq"]), "claude-fixture-5[1m]",
             [10, 200, 3000, 40, 0])], rows


async def main():
    try:
        vocabulary()
        rows_and_store()
        persisted_shape()
        garden = reports()
        await routes(build_app, "full", garden)
        await routes(backend_app, "headless", garden)
        await through_the_runner()
        print("PASS: token ledger vocabulary, day records counted once per ref, exact shape "
              "refused rather than repaired, the report, the route and capability on both "
              "runtimes, and fixture turns through the runner split by model")
    finally:
        import shutil
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
