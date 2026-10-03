#!/usr/bin/env python3
"""The prompt index through both authenticated runtimes: one bounded line per
prompt, paged, after a cursor, with the session's total - no engine, network
or quota."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))
from tests.scratch import private_root

ROOT = private_root("prompt-index-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, prompt_index, protocol, uploads
from puppy.web import build_app
from puppy_backend.app import build_app as build_backend_app

IMAGE = uploads.ATTACH_IMAGE_PREFIX + "/data/uploads/1/a/shot.png" + uploads.ATTACH_IMAGE_SUFFIX
FILE = (uploads.ATTACH_FILE_PREFIX + "/data/uploads/1/b/notes, v2 (draft).txt (notes, v2 (draft).txt, 3 KB)" +
        uploads.ATTACH_FILE_SUFFIX)


def excerpts():
    e = prompt_index.excerpt
    assert e("  \n  Fix the   coupon\treset.\nsecond line") == "Fix the coupon reset."
    assert e(IMAGE) == "shot.png", "a prompt of attachments alone is listed by its files"
    assert e(FILE + "\n" + IMAGE) == "notes, v2 (draft).txt, shot.png"
    assert e(IMAGE + "\nLook at this\n" + FILE) == "Look at this", "text wins over files"
    lookalike = uploads.ATTACH_FILE_PREFIX + "not a marker" + uploads.ATTACH_FILE_SUFFIX
    assert e(lookalike) == lookalike, "a line that only looks like a file marker is text"
    long = e("x" * 300)
    assert len(long) == prompt_index.EXCERPT_LIMIT and long.endswith("…")
    assert e("") == "" and e(None) == "" and e("\n \n") == ""
    assert e("ąć 日本語 — ok") == "ąć 日本語 — ok"
    print("excerpt: first line, collapsed and bounded; files for a prompt of attachments alone")


def seed():
    sid = db.create_session("Index", "codex", str(ROOT), "", "", "", "ask")
    other = db.create_session("Other", "codex", str(ROOT), "", "", "", "ask")
    db.add_event(other, "user", {"text": "must stay in the other session"})
    expected = []
    for i in range(2405):
        steering = i % 40 == 7
        text = "prompt {}".format(i)
        if i == 2:
            text = "line one\nline two — ąć日本語"
        elif i == 3:
            text = "long prompt " + "x" * (128 * 1024)
        elif i == 4:
            text = IMAGE
        elif i == 5:
            text = FILE + "\n" + IMAGE
        data = {"text": text}
        if steering:
            data.update(steering=True, request_id="steer-{}".format(i), turn_id="turn-{}".format(i))
        event = db.add_event(sid, "user", data)
        expected.append([event["seq"], 1 if steering else 0, prompt_index.excerpt(text)])
        db.add_event(sid, "assistant", {"text": "reply"})
        db.add_event(sid, "tool_result", {"text": "tool output " * 50})
    # a payload that is not JSON and one whose text is not a string still list
    conn = db.connect()
    for payload in ("not json", json.dumps({"text": 42, "steering": True})):
        seq = conn.execute("SELECT max(seq) FROM events WHERE session_id=?", (sid,)).fetchone()[0] + 1
        conn.execute("INSERT INTO events(session_id, seq, kind, payload, created_at) VALUES(?,?,?,?,?)",
                     (sid, seq, "user", payload, 1000.0))
        expected.append([seq, 1 if "steering" in payload else 0, ""])
    conn.commit()
    return sid, other, expected


def reads(sid, expected):
    prompt_index._json1 = None
    whole = prompt_index.read(sid)
    assert prompt_index._json1 is True, "this SQLite reads the line inside the query"
    assert whole["total"] == len(expected) and whole["more"] is True
    assert len(whole["prompts"]) == prompt_index.PAGE_LIMIT
    rest = prompt_index.read(sid, whole["prompts"][-1][0])
    assert rest["more"] is False
    rows = whole["prompts"] + rest["prompts"]
    assert [[r[0], r[2], r[3]] for r in rows] == expected
    # the same index without SQLite's JSON functions
    prompt_index._json1 = False
    try:
        fallback = prompt_index.read(sid)["prompts"] + prompt_index.read(sid, whole["prompts"][-1][0])["prompts"]
    finally:
        prompt_index._json1 = None
    assert fallback == rows, "the Python reading answers the same rows"
    assert prompt_index.read(sid, expected[-1][0]) == {"prompts": [], "total": len(expected), "more": False}
    print("read: every prompt in pages, the line from SQLite or from Python alike")


async def exercise(builder, sid, other, expected):
    app = builder()
    app.on_startup.clear()  # HTTP/storage only: no CLI, network or periodic probes
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    path = "/api/sessions/{}/prompts".format(sid)
    async with TestClient(TestServer(app)) as client:
        async def get(tail="", status=200, authed=True, route=path, raw=False):
            response = await client.get(route + tail, headers=headers if authed else {})
            body = await response.read()
            data = json.loads(body)
            assert response.status == status, (response.status, data)
            if status == 200:
                assert response.headers.get("Cache-Control") == "no-store"
            return (data, body) if raw else data

        await get(authed=False, status=401)
        await get(route="/api/sessions/999999/prompts", status=404)
        ping = await get(route="/api/ping")
        assert protocol.SESSION_PROMPT_INDEX_CAPABILITY in ping["capabilities"]
        for query in ("after_seq=-1", "after_seq=nope", "limit=0", "limit=-1",
                      "limit={}".format(prompt_index.PAGE_LIMIT + 1), "limit=nope"):
            await get("?" + query, status=400)

        # the whole index, page after page, as a console reads it
        collected, after, pages, size = [], 0, 0, 0
        while True:
            data, body = await get("?after_seq={}".format(after), raw=True)
            pages += 1
            size += len(body)
            assert data["total"] == len(expected)
            collected += [[row[0], row[2], row[3]] for row in data["prompts"]]
            if not data["more"]:
                break
            after = data["prompts"][-1][0]
        assert pages == 2 and collected == expected, "every prompt once, oldest first"
        assert all(len(row[2]) <= prompt_index.EXCERPT_LIMIT for row in collected)
        assert size < len(expected) * 120, "a line per prompt, never the prompts themselves ({})".format(size)
        # after a cursor: only what follows; a small page says there is more
        data = await get("?after_seq={}&limit=3".format(expected[-6][0]))
        assert [[r[0], r[2], r[3]] for r in data["prompts"]] == expected[-5:-2] and data["more"] is True
        assert (await get("?after_seq={}".format(expected[-1][0])))["prompts"] == []
        row = (await get("?limit=1"))["prompts"][0]
        assert isinstance(row[1], float) and row[1] > 0, "the time the prompt was sent"
        assert (await get(route="/api/sessions/{}/prompts".format(other)))["prompts"][0][3] == \
            "must stay in the other session"
    print("{}: auth, refusals, pages after a cursor, the total, bounded lines and session isolation passed".format(
        app["puppy_role"]))


async def main():
    try:
        config.load()
        db.connect()
        excerpts()
        sid, other, expected = seed()
        reads(sid, expected)
        for builder in (build_app, build_backend_app):
            await exercise(builder, sid, other, expected)
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
