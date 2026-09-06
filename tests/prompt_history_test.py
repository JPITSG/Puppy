#!/usr/bin/env python3
"""Stored prompt recall through both authenticated runtimes; no engine or quota."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import sys

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))
from tests.scratch import private_root

ROOT = private_root("prompt-history-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, protocol
from puppy.web import build_app
from puppy_backend.app import build_app as build_backend_app


def seed():
    sid = db.create_session("History", "codex", str(ROOT), "", "", "", "ask")
    other = db.create_session("Other", "codex", str(ROOT), "", "", "", "ask")
    db.add_event(other, "user", {"text": "must stay in the other session"})
    expected = []
    for i in range(1203):
        text = "same prompt" if i < 2 else "prompt {}".format(i)
        if i == 2:
            text = "line one\nline two — ąć日本語\n\n[Attached image: /demo/image.png]"
        elif i == 3:
            text = "long prompt " + "x" * (128 * 1024)
        event = db.add_event(sid, "user", {"text": text})
        expected.append((event["seq"], text))
        db.add_event(sid, "assistant", {"text": "reply"})
    # The entire socket snapshot can contain no prompts. A folded task and
    # model/tool text must not become recallable user messages.
    db.add_event(sid, "info", {"subtype": "session_task_archive", "text": "task prompt"})
    for _ in range(700):
        db.add_event(sid, "tool_result", {"text": "tool output"})
    assert not any(event["kind"] == "user" for event in db.get_events(sid))
    return sid, other, expected


async def exercise(builder, sid, other, expected):
    app = builder()
    app.on_startup.clear()  # HTTP/storage only: no CLI, network or periodic probes
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    path = "/api/sessions/{}/events".format(sid)
    async with TestClient(TestServer(app)) as client:
        async def get(tail="?kind=user", status=200, authed=True, route=path):
            response = await client.get(route + tail, headers=headers if authed else {})
            data = await response.json()
            assert response.status == status, (response.status, data)
            if status == 200:
                assert response.headers.get("Cache-Control") == "no-store"
            return data

        await get(authed=False, status=401)
        await get(route="/api/sessions/999999/events", status=404)
        ping = await get(tail="", route="/api/ping")
        assert protocol.SESSION_PROMPT_HISTORY_CAPABILITY in ping["capabilities"]
        for query in ("kind=", "kind=assistant", "kind=user%27%20OR%201=1--",
                      "kind=user&limit=0", "kind=user&limit=-1", "kind=user&limit=501",
                      "kind=user&limit=nope", "kind=user&before_seq=0",
                      "kind=user&before_seq=nope", "kind=user&after_seq=-1",
                      "kind=user&before_seq=5&after_seq=1"):
            await get("?" + query, status=400)

        collected = []
        before = None
        while True:
            cursor = "" if before is None else "&before_seq={}".format(before)
            data = await get("?kind=user&limit=50" + cursor)
            events = data["events"]
            assert len(events) <= 50
            assert all(event["kind"] == "user" for event in events)
            assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
            collected[0:0] = [(event["seq"], event["data"]["text"]) for event in events]
            if len(events) < 50:
                break
            assert before is None or events[0]["seq"] < before
            before = events[0]["seq"]
        assert collected == expected, "every stored prompt, including duplicates, is reachable"
        assert (await get("?kind=user&before_seq=1"))["events"] == []
        assert len((await get())["events"]) == 200, "the default limits prompts, not all events"
        forward = (await get("?kind=user&after_seq=0&limit=3"))["events"]
        assert [(row["seq"], row["data"]["text"]) for row in forward] == expected[:3]
        assert (await get("?limit=1"))["events"][0]["kind"] == "tool_result"
        assert (await get(route="/api/sessions/{}/events".format(other)))["events"][0]["data"]["text"] == \
            "must stay in the other session"

        # A separate connection/device sees the same prompts without a socket
        # snapshot or any browser-side sends. Reopening the DB also retains it.
        async with ClientSession() as second:
            response = await second.get(client.make_url(path + "?kind=user&limit=1"), headers=headers)
            assert response.status == 200
            latest = (await response.json())["events"][0]
            assert (latest["seq"], latest["data"]["text"]) == expected[-1]
        db._conn.close()
        db._conn = None
        assert db.get_events(sid, kind="user", limit=1)[0]["data"]["text"] == expected[-1][1]
    print("{}: auth, pagination to first prompt, full text, session isolation and persistence passed".format(
        app["puppy_role"]))


async def main():
    try:
        config.load()
        db.connect()
        sid, other, expected = seed()
        for builder in (build_app, build_backend_app):
            await exercise(builder, sid, other, expected)
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
