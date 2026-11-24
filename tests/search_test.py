#!/usr/bin/env python3
"""No-quota tests for the node-local session search index and route, and for
what keeps one search from holding up another, the index, or the console."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from unittest.mock import patch

import aiohttp
from aiohttp import web as aioweb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("search-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import cli_releases, config, db, protocol, search, session_agent, session_links, \
    workers  # noqa: E402
from puppy.web import build_app  # noqa: E402
from puppy_backend.app import build_app as build_backend_app  # noqa: E402


async def _no_release_check(drivers, force=False):
    return 10 ** 9

# the release worker must stay off the real network during the HTTP phase
cli_releases.refresh_if_due = _no_release_check


def flat_matches(payload):
    return [match for group in payload["sessions"] for match in group["matches"]]


def doc_count():
    conn = search.connect()
    return int(conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])


def test_match_expression():
    cases = [
        ("deploy", '"deploy" *'),
        ("two words", '"two" * AND "words" *'),
        ('"exact phrase"', '"exact phrase"'),
        ("one OR two", '("one" * OR "two" *)'),
        ("big one OR two", '"big" * AND ("one" * OR "two" *)'),
        ("keep -drop", '("keep" *) NOT "drop"'),
        ('-"bad phrase" good', '("good" *) NOT "bad phrase"'),
        ('quo"ted', '"quo""ted" *'),
        # one- and two-letter words are whole words, judged by the last word
        # of what was typed - the one FTS5 would extend
        ("a OR b", '("a" OR "b")'),
        ("x a OR b", '"x" AND ("a" OR "b")'),
        ("4.7 s", '"4.7" AND "s"'),
        ("during a pause", '"during" * AND "a" AND "pause" *'),
        ("u.FL fl.oor", '"u.FL" AND "fl.oor" *'),
        ("c++ v2 v22 v222", '"c++" AND "v2" AND "v22" * AND "v222" *'),
        ("\u017ce \u017cen", '"\u017ce" AND "\u017cen" *'),
        # scripts written without spaces index whole runs of text
        ("\u6771 \u6771\u4eac", '"\u6771" * AND "\u6771\u4eac" *'),
        ("_a_ __", '"_a_" AND "__" *'),
    ]
    for query, expected in cases:
        got = search.match_expression(query)
        assert got == expected, (query, got, expected)
    for query in ("", "-only", "-a -b", "...", '"("', "- -"):
        try:
            search.match_expression(query)
        except ValueError:
            continue
        raise AssertionError("accepted unusable query {!r}".format(query))
    # operator words the translator does not special-case must stay literal
    assert "AND" in search.match_expression("AND")
    print("match expression ok")


def seed_sessions():
    sid1 = db.create_session("Deploy pipeline", "claude", "/srv/project",
                             "", "", "#e0784f", "ask")
    sid2 = db.create_session("", "codex", "/home/user/labs",
                             "", "", "#4dd0c4", "ask")
    db.add_event(sid1, "user", {"text": "please deploy the backend to the build node"})
    db.add_event(sid1, "assistant",
                 {"text": "Deployment complete; the service manager restarted the program."})
    db.add_event(sid1, "tool_use", {
        "tool": "shell",
        "input": {"command": "service-manager restart example-backend"},
        "tool_use_id": "t1"})
    db.add_event(sid1, "tool_result", {
        "tool_use_id": "t1", "is_error": False,
        "content": [{"type": "text",
                     "text": "restarted \x1b[32mcleanly\x1b[0m \x01fake\x02"}]})
    db.add_event(sid1, "result", {"ok": True})
    db.add_event(sid1, "engine_switch", {"from": "claude", "to": "codex"})
    db.add_event(sid1, "assistant", {"text": "   "})   # whitespace: no doc
    db.add_event(sid2, "thinking",
                 {"text": "the tab strip needs a singleton search view"})
    db.add_event(sid2, "assistant", {"text": "Added the search tab."})
    db.add_event(sid2, "error", {"text": "Traceback: ValueError bad flag"})
    db.add_event(sid2, "info", {"subtype": "interrupted", "text": "Turn interrupted"})
    # the forward cursor the console's jump-to-message window relies on
    forward = db.get_events(sid1, after_seq=2, limit=2)
    assert [event["seq"] for event in forward] == [3, 4], forward
    assert db.get_events(sid1, after_seq=99) == []
    backward = db.get_events(sid1, before_seq=3, limit=5)
    assert [event["seq"] for event in backward] == [1, 2], backward
    return sid1, sid2


def test_index_and_queries():
    sid1, sid2 = seed_sessions()
    search.reconcile()
    # result/engine_switch/blank events produce no docs; titles do
    # sid1: title + 4 docs, sid2: title(no name -> cwd only) + 4 docs
    assert doc_count() == 10, doc_count()

    grouped = search.query_index("deploy")
    assert grouped["ok"] and grouped["total"] == 3, grouped
    assert grouped["sessions"][0]["session"]["id"] == sid1
    kinds = {match["kind"] for match in flat_matches(grouped)}
    assert kinds == {"title", "user", "assistant"}, kinds
    snippet = flat_matches(grouped)[1]["snippet"]
    assert "\x01" in snippet and "\x02" in snippet, snippet

    # tool mapping, ANSI scrub, forged-marker scrub
    tools = search.query_index("restart", kinds=["tool"])
    assert tools["total"] == 2, tools
    dumped = json.dumps(tools)
    assert "\\u001b" not in dumped
    clean = search.query_index("cleanly")
    text = flat_matches(clean)[0]["snippet"]
    assert "fake" in text and "\x01fake" not in text, text   # forged marker scrubbed

    # phrases, exclusion, OR, kind folding of error->info
    assert search.query_index('"search tab"')["total"] == 1
    assert search.query_index("traceback OR singleton")["total"] == 2
    hits = search.query_index("search -added")
    assert all("Added" not in match["snippet"] for match in flat_matches(hits))
    assert search.query_index("valueerror", kinds=["info"])["total"] == 1

    # recency ordering: newest first
    recent = search.query_index("search OR deploy", order="recent")
    stamps = [match["ts"] for match in flat_matches(recent)]
    assert stamps == sorted(stamps, reverse=True), stamps

    # time filter
    cutoff = time.time() + 60
    assert search.query_index("deploy", after=cutoff)["total"] == 0
    assert search.query_index("deploy", before=cutoff)["total"] == 3

    # per-session cap and drill-down pagination
    for index in range(6):
        db.add_event(sid2, "assistant", {"text": "pagination filler %d" % index})
    search.reconcile([sid2])
    capped = search.query_index("pagination", per_session=2)
    assert capped["total"] == 6
    assert len(capped["sessions"][0]["matches"]) == 2
    page1 = search.query_index("pagination", session_id=sid2, limit=4)
    page2 = search.query_index("pagination", session_id=sid2, limit=4, offset=4)
    assert page1["total"] == 6 and len(page1["matches"]) == 4
    assert len(page2["matches"]) == 2
    seen = {match["seq"] for match in page1["matches"] + page2["matches"]}
    assert len(seen) == 6

    # oversized bodies are capped
    db.add_event(sid2, "assistant", {"text": "capstart " + "x" * (64 * 1024)})
    search.reconcile([sid2])
    conn = search.connect()
    largest = conn.execute("SELECT MAX(LENGTH(body)) FROM docs").fetchone()[0]
    assert largest <= search.MAX_DOC_CHARS, largest

    # rename refreshes the title doc through the db listener contract
    db.touch_session(sid2, name="Search feature build")
    search.reconcile([sid2])
    assert search.query_index("feature build")["total"] == 1

    print("index and queries ok")
    return sid1, sid2


def test_change_listener(sid):
    calls = []
    db.add_change_listener(calls.append)
    db.add_event(sid, "assistant", {"text": "listener ping"})
    db.touch_session(sid, name="Listener title")
    db.touch_session(sid, color="#4dc6ff")   # not identity: no notification
    extra = db.create_session("tmp", "claude", "/tmp", "", "", "#4dc6ff", "ask")
    db.delete_session(extra)
    assert calls == [sid, sid, extra, extra], calls
    db._change_listeners.remove(calls.append)
    print("change listener ok")


def test_delete_shrink_and_rebuild(sid1, sid2):
    # delete purges every doc for the session
    db.delete_session(sid1)
    search.reconcile([sid1])
    assert search.query_index("deploy")["total"] == 0

    # a shrunk history (backup restore) is rebuilt, not trusted
    db.execute("DELETE FROM events WHERE session_id=? AND seq>2", (sid2,))
    search.request_full_reconcile()
    search.reconcile()
    assert search.query_index("pagination")["total"] == 0
    assert search.query_index("singleton")["total"] == 1

    # wrong index shape on disk is discarded and rebuilt from the source
    search.close_for_tests()
    raw = sqlite3.connect(search.INDEX_PATH)
    raw.execute("PRAGMA user_version=99")
    raw.commit()
    raw.close()
    search.reconcile()
    assert search.query_index("singleton")["total"] == 1

    # corrupt bytes likewise
    search.close_for_tests()
    with open(search.INDEX_PATH, "wb") as handle:
        handle.write(b"not a database at all")
    search.reconcile()
    assert search.query_index("singleton")["total"] == 1
    print("delete/shrink/rebuild ok")


# ---- what a search may cost ----

def test_plan():
    """The full-text match drives every query, whatever filters it carries:
    with no statistics SQLite takes a session for ten rows and would match the
    whole index again for each row it finds in one."""
    conn = search._open_reader()
    try:
        plans = []
        for kinds in (None, ["assistant"]):
            for after in (None, 1.0):
                for sessions in (None, [7], list(range(1, 51))):
                    where, args = search._condition(kinds, after, after, sessions)
                    plans.append((search._hits_sql(where), ['"term" *'] + args))
        plans.append((search._SNIPPETS_SQL, ["", "", 10, '"term" *']))
        for sql, args in plans:
            steps = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, args)]
            assert "VIRTUAL TABLE" in steps[0] and "docs_fts" in steps[0], steps
            assert "PRIMARY KEY" in steps[1] and "SEARCH" in steps[1], steps
            assert sum("VIRTUAL TABLE" in step for step in steps) == 1, steps
    finally:
        conn.close()
    print("plan ok")


def seed_history(sessions=30, each=40):
    """Sessions with words in common, in some and in one only."""
    ids = []
    words = ["amber", "birch", "cedar", "dune", "elm", "fern", "grove", "heath"]
    for index in range(sessions):
        sid = db.create_session("History {}".format(index), "claude", "/srv/history",
                                "", "", "#e0784f", "ask")
        ids.append(sid)
        for turn in range(each):
            db.add_event(sid, "user" if turn % 4 == 0 else "assistant", {
                "text": "common ground {} {} a {} {}".format(
                    words[turn % 8], words[(turn * 3 + index) % 8], turn,
                    "unique{}".format(index) if turn == 5 else "")})
    search.reconcile(ids)
    return ids


def test_one_pass(ids):
    """Many sessions at once: what each one alone would be told, and one pass
    over the index however many they are."""
    other = db.create_session("No match", "claude", "/srv/none", "", "", "#e0784f", "ask")
    search.reconcile([other])
    for query, kinds in (("common", None), ("amber birch", ["assistant"]),
                         ('"common ground" -dune', None), ("a fern", None),
                         ("cedar OR elm", ["user"]), ("unique7", None), ("gro", None)):
        for limit, offset in ((5, 0), (3, 4), (100, 0)):
            together = search.query_sessions(query, ids + [other], kinds=kinds,
                                             limit=limit, offset=offset)
            assert other not in together
            for sid in ids:
                alone = search.query_index(query, session_id=sid, kinds=kinds,
                                           limit=limit, offset=offset)
                got = together.get(sid)
                if got is None:
                    assert alone["total"] == 0 and alone["matches"] == [], (query, sid)
                    assert alone["sid"] == sid and alone["ok"] is True
                    continue
                for row in (alone, got):
                    row.pop("elapsed_ms")
                assert got == alone, (query, sid, got, alone)
                assert len(got["matches"]) == max(0, min(limit, got["total"] - offset))
                assert all(row["session_id"] == sid for row in got["matches"])
                assert all("\x01" in row["snippet"] for row in got["matches"])
    # pages neither overlap nor leave anything out
    sid, seen = ids[0], []
    for offset in range(0, 60, 7):
        page = search.query_sessions("common", [sid], limit=7, offset=offset).get(sid)
        seen += [row["seq"] for row in page["matches"]]
    assert len(seen) == len(set(seen)) == page["total"] == 40, (len(seen), page["total"])
    # newest first keeps its order inside a session
    recent = search.query_index("common", session_id=sid, order="recent", limit=40)
    stamps = [row["ts"] for row in recent["matches"]]
    assert stamps == sorted(stamps, reverse=True)
    # ids that are unknown, repeated or none at all
    assert search.query_sessions("common", [10 ** 9, sid, sid], limit=1)[sid]["total"] == 40
    assert search.query_sessions("common", []) == {}
    for empty in (lambda: search.query_sessions("--", []), lambda: search.query_sessions("--", ids)):
        try:
            empty()
        except ValueError:
            continue
        raise AssertionError("an unusable query must be refused")
    statements = []
    with search._reading(None) as reading:
        reading.conn.set_trace_callback(statements.append)
    search.query_sessions("common", ids, limit=5)
    conn = search._readers[-1]
    conn.set_trace_callback(None)
    matches = [line for line in statements if "MATCH" in line]
    assert len(matches) == 2, matches        # the matches once, then their excerpts
    print("one pass ok")


def test_short_words(ids):
    """One- and two-letter words are whole words."""
    sid = ids[0]
    db.add_event(sid, "assistant", {"text": "zq zquartz 4.7 s of z-index on a zzz"})
    search.reconcile([sid])
    found = lambda query: search.query_index(query, session_id=sid)["total"]
    assert found("zqu") == 1 and found("zq") == 1 and found("z") == 1
    assert found("zz") == 0 and found("zzz") == 1       # not zzz by its start
    assert found("4.7 s") == 1 and found("4.7 se") == 0
    print("short words ok")


ENDLESS = (" AND (WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c LIMIT 100000000000) "
           "SELECT MAX(x) FROM c) > 0")


def endless():
    """Searches that would never end, while this is in force."""
    real = search._hits_sql
    return patch.object(search, "_hits_sql", lambda where: real(where) + ENDLESS)


def test_limits(ids):
    """A search past its time, or whose caller has gone, stops - and is never
    taken for a query SQLite could not read, which is tried again literally."""
    literal = patch.object(search, "_literal_expression",
                           side_effect=AssertionError("a stopped search was run again"))
    with endless(), literal:
        for seconds in (0.0, 0.4):
            started = time.monotonic()
            try:
                search.query_sessions("common", ids, job=search.Job(seconds))
            except search.SearchTimeoutError as exc:
                assert "took longer than {:g} seconds".format(seconds) in str(exc), exc
            else:
                raise AssertionError("never stopped")
            assert seconds <= time.monotonic() - started < seconds + 1.0
        job = search.Job(30)
        threading.Timer(0.3, job.cancel).start()
        started = time.monotonic()
        try:
            search.query_index("common", job=job)
        except search.SearchCancelled:
            assert time.monotonic() - started < 1.5
        else:
            raise AssertionError("never cancelled")
        # one that was cancelled before it began reads nothing at all
        try:
            search.query_index("common", job=job)
        except search.SearchCancelled:
            pass
    # a query SQLite cannot use still gets its literal second try
    with patch.object(search, "match_expression", lambda query: "AND ("):
        assert search.query_index("common", session_id=ids[0])["total"] == 40
    # connections come back, except one that was stopped or held a lot
    search._close_readers()
    search.query_index("common")
    search.query_index("amber")
    assert len(search._readers) == 1
    kept = search._readers[0]
    assert kept.execute("SELECT COUNT(*) FROM temp.hits").fetchone()[0] == 0
    with endless():
        try:
            search.query_index("common", job=search.Job(0.1))
        except search.SearchTimeoutError:
            pass
    assert not search._readers
    with patch.object(search, "KEEP_READER_HITS", 10):
        search.query_index("common")
    assert not search._readers
    # nor one whose index has been let go of meanwhile
    with search._reading(None):
        search._close_readers()
    assert not search._readers
    # the index itself cannot be written through one
    conn = search._open_reader()
    try:
        conn.execute("DELETE FROM docs")
    except sqlite3.OperationalError as exc:
        assert "readonly" in str(exc), exc
    else:
        raise AssertionError("a reading connection wrote")
    finally:
        conn.close()
    print("limits ok")


def test_beside_the_writer(ids):
    """A search waits for nothing: not the writer's lock, not its transaction,
    and not another search."""
    conn = search.connect()
    results = []

    def ask():
        started = time.monotonic()
        results.append((search.query_index("common")["total"], time.monotonic() - started))
    with search._lock:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO docs(session_id,seq,kind,ts,body) VALUES(?,9999,'user',1,?)",
                     (ids[0], "common uncommitted"))
        threads = [threading.Thread(target=ask) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        conn.rollback()
    assert len(results) == 6 and all(total == 1200 for total, _seconds in results), results
    assert max(seconds for _total, seconds in results) < 5, results
    # and a stuck one holds up neither the rest nor the index
    job = search.Job(30)
    with endless():
        slow = threading.Thread(target=lambda: _quietly(search.query_index, "common", job=job))
        slow.start()
        time.sleep(0.2)
    started = time.monotonic()
    assert search.query_index("common")["total"] == 1200
    db.add_event(ids[0], "assistant", {"text": "written meanwhile"})
    search.reconcile([ids[0]])
    assert search.query_index("meanwhile")["total"] == 1
    assert time.monotonic() - started < 5 and slow.is_alive()
    job.cancel()
    slow.join(5)
    assert not slow.is_alive()
    # an index pass that is told to stop does, between two sessions
    stop = threading.Event()
    stop.set()
    db.add_event(ids[0], "assistant", {"text": "notyetindexed"})
    db.touch_session(ids[0], name="Notyetrenamed")
    search.reconcile(stop=stop)
    assert search.query_index("notyetindexed OR notyetrenamed")["total"] == 0
    search.reconcile([ids[0]])
    assert search.query_index("notyetindexed")["total"] == 1
    assert search.query_index("notyetrenamed")["total"] == 1
    print("beside the writer ok")


def _quietly(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except search.SearchError:
        pass


async def until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never came true")


async def exercise_crowds(ids, build):
    """Many searches that take as long as they can, from agents and from
    people, on a running node: what the incident was."""
    app = build()
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = "http://127.0.0.1:{}".format(site._server.sockets[0].getsockname()[1])
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    full = app["puppy_role"] == "full"
    refs = [session_links.reference(sid) for sid in ids]
    ended = False
    try:
        await until(lambda: search._ready)
        async with aiohttp.ClientSession(headers=headers) as client:
            async def get(path, **kwargs):
                started = time.monotonic()
                async with client.get(url + path, **kwargs) as response:
                    return response.status, await response.read(), \
                        time.monotonic() - started, response.headers

            async def console_answers():
                for path in ["/api/ping", "/api/sessions"] + (
                        ["/", "/static/app.js", "/static/app.css", "/api/state"] if full else []):
                    status, _body, seconds, _headers = await get(path)
                    assert status == 200 and seconds < 2.0, (path, status, seconds)

            with endless(), patch.object(search, "SEARCH_SECONDS", 60.0):
                # two turns' agents, twenty searches each, and a crowd of people
                agents = [asyncio.ensure_future(session_links.node_call(
                    "search", {"refs": refs, "query": "common"}, source="turn {}".format(turn)))
                    for turn in (1, 2) for _ in range(20)]
                people = [asyncio.ensure_future(get("/api/search?q=common")) for _ in range(24)]
                await until(lambda: search._agents.running == 2 and search._people.running == 2)
                # each turn has one place, and nobody waits in a thread
                assert dict(search._agents._by_key) == {"turn 1": 1, "turn 2": 1}
                pool = workers.search.state()
                assert (pool["running"], pool["waiting"]) == (4, 0), pool
                await console_answers()
                # a caller that gives up leaves at once and takes nothing along
                line = len(search._agents._line)
                agents[-1].cancel()
                await asyncio.gather(agents[-1], return_exceptions=True)
                assert len(search._agents._line) == line - 1
                # a browser that goes away frees its place long before the limit
                short = [task for task in people if not task.done()]
                crowd = [task for task in people if task.done()]
                assert len(crowd) == 6, len(crowd)       # 2 searching, 16 in line
                for task in crowd:
                    status, body, seconds, answer = task.result()
                    assert status == 503 and seconds < 2.0 and answer["Retry-After"] == "2"
                    assert "too many searches are waiting here" in body.decode(), body
                for task in short:
                    task.cancel()
                await asyncio.gather(*short, return_exceptions=True)
                await until(lambda: search._people.running == 0)
                await console_answers()
                for task in agents:
                    task.cancel()
                await asyncio.gather(*agents, return_exceptions=True)
                await until(lambda: search._agents.running == 0 and not search._jobs)

                # left to their limit, each says what to do about it
                with patch.object(search, "SEARCH_SECONDS", 1.5):
                    status, body, seconds, answer = await get("/api/search?q=common")
                    assert status == 503 and 1.4 < seconds < 6.0, (status, seconds)
                    assert "took longer than 1.5 seconds" in body.decode() and \
                        "use more distinctive words" in body.decode(), body
                    try:
                        await session_links.node_call("search", {"refs": refs, "query": "common"})
                    except session_links.SessionLinkError as exc:
                        assert "took longer than 1.5 seconds" in str(exc) and \
                            "fewer sessions" in str(exc), exc
                    else:
                        raise AssertionError("an endless search had an answer")

                # relayed by a controller, which waits less: the line is short
                held = [asyncio.ensure_future(session_links.node_call(
                    "search", {"refs": refs, "query": "common"}, source="turn {}".format(turn)))
                    for turn in (1, 2)]
                await until(lambda: search._agents.running == 2)
                with patch.object(search, "RELAYED_WAIT", 0.2):
                    async with client.post(url + "/api/session-links/node", json={
                            "method": "search", "params": {"refs": refs, "query": "common"}}) as response:
                        data = await response.json()
                        assert response.status == 400 and "try again in a moment" in data["error"], data
                for task in held:
                    task.cancel()
                await asyncio.gather(*held, return_exceptions=True)
                await until(lambda: search._agents.running == 0)

                # a turn's own process that has gone takes its search with it
                with patch.object(session_links, "dispatch", lambda sid, turn, method, params:
                                  session_links.node_call(method, dict(params, refs=refs), source="turn")), \
                        patch.object(session_agent, "_dispatch", _open_dispatch):
                    reader, writer = await asyncio.open_unix_connection(session_agent.socket_path())
                    writer.write(session_agent._encode({
                        "session_id": ids[0], "turn_id": "turn", "method": "search",
                        "params": {"query": "common"}}))
                    await writer.drain()
                    await until(lambda: search._agents.running == 1)
                    writer.close()
                    await until(lambda: search._agents.running == 0)

                # nothing under way keeps the node from stopping
                last = [asyncio.ensure_future(session_links.node_call(
                    "search", {"refs": refs, "query": "common"}, source=str(turn)))
                    for turn in range(5)]
                await until(lambda: search._agents.running == 2)
                started = time.monotonic()
                ended = True
                await runner.cleanup()
                await asyncio.gather(*last, return_exceptions=True)
                await until(lambda: workers.search.state()["running"] == 0)
                assert time.monotonic() - started < 10.0, time.monotonic() - started
    finally:
        if not ended:
            await runner.cleanup()
    print("crowds ok on the {}".format("console" if full else "backend"))


async def _open_dispatch(request):
    """The bridge's dispatch without its running-turn check."""
    return {"text": json.dumps(await session_links.dispatch(
        request["session_id"], request["turn_id"], request["method"], request["params"]))}


async def exercise_http():
    search.reconcile()   # results below must not race the app's own worker
    token = config.get("auth.api_token")
    assert token
    app = build_app()
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": token}
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(url + "/api/ping", headers=headers) as response:
                ping = await response.json()
                assert response.status == 200
                assert protocol.SEARCH_CAPABILITY in ping["capabilities"]

            async with client.get(url + "/api/search?q=singleton") as response:
                assert response.status == 401, response.status

            async with client.get(url + "/api/search?q=singleton",
                                  headers=headers) as response:
                data = await response.json()
                assert response.status == 200, data
                assert data["ok"] and data["total"] == 1, data
                assert data["sessions"][0]["session"]["name"] == "Listener title"

            for tail in ("q=", "q=x&kinds=bogus", "q=x&order=upside",
                         "q=x&sid=0", "q=x&limit=0", "q=x&per=99",
                         "q=" + "y" * 500, "q=--"):
                async with client.get(url + "/api/search?" + tail,
                                      headers=headers) as response:
                    assert response.status == 400, (tail, response.status)

            async with client.get(
                    url + "/api/search?q=singleton&kinds=thinking,title",
                    headers=headers) as response:
                data = await response.json()
                assert response.status == 200 and data["total"] == 1, data
    finally:
        await runner.cleanup()
    print("http ok")


def main():
    test_match_expression()
    sid1, sid2 = test_index_and_queries()
    test_change_listener(sid2)
    test_delete_shrink_and_rebuild(sid1, sid2)
    asyncio.run(exercise_http())
    test_plan()
    ids = seed_history()
    test_one_pass(ids)
    test_short_words(ids)
    test_limits(ids)
    test_beside_the_writer(ids)
    for build in (build_app, build_backend_app):
        asyncio.run(exercise_crowds(ids, build))
    print("search tests passed")


if __name__ == "__main__":
    main()
