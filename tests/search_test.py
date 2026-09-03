#!/usr/bin/env python3
"""No-quota tests for the node-local session search index and route."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import aiohttp
from aiohttp import web as aioweb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("search-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import cli_releases, config, db, protocol, search  # noqa: E402


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
        ("a OR b", '("a" * OR "b" *)'),
        ("x a OR b", '"x" * AND ("a" * OR "b" *)'),
        ("keep -drop", '("keep" *) NOT "drop"'),
        ('-"bad phrase" good', '("good" *) NOT "bad phrase"'),
        ('quo"te', '"quo""te" *'),
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


async def exercise_http():
    from puppy.web import build_app

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
    print("search tests passed")


if __name__ == "__main__":
    main()
