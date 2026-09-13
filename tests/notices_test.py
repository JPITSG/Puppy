#!/usr/bin/env python3
"""The controller's notification history: the record every shown notice is
folded into, its exact persisted shape, the clear that empties it while its ids
keep advancing, the routes the console reports through, reads from and clears
with, the state topic that carries the list, and the guards around a backup.
Both runtimes are booted; no engine, network or quota."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch
import warnings

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
TEST_ROOT = private_root("notices-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, notices, runner
from puppy import web as webui
from backend.puppy_backend.app import build_app as backend_app


def stored() -> dict:
    row = db.query_one("SELECT value FROM meta WHERE key=?", (notices.META_KEY,))
    return json.loads(row["value"])


def texts(payload) -> list:
    return [(item["text"], item["tone"], item["count"]) for item in payload["items"]]


def text_shape():
    clean = notices.clean_text
    assert clean("  Session \n\t deleted  ") == "Session deleted"
    assert clean("Backend: Could not\x00 save\x1b[0m\x7f") == "Backend: Could not save[0m"
    assert clean("a b\x85c") == "a b c", "unicode line breaks are whitespace"
    assert clean(None) == "" and clean("") == "" and clean("   ") == ""
    assert clean(42) == "42"
    long = "x" * (notices.MAX_TEXT + 50)
    cut = clean(long)
    assert len(cut) == notices.MAX_TEXT and cut.endswith("…")
    assert clean(cut) == cut, "a stored text is its own cleaned form"
    spaced = ("word " * 300).strip()
    cut = clean(spaced)
    assert len(cut) <= notices.MAX_TEXT and not cut[:-1].endswith(" ") and clean(cut) == cut


def recording():
    config.load()
    db.connect()
    assert db.query_one("SELECT 1 FROM meta WHERE key=?", (notices.META_KEY,)) is None
    assert notices.payload() == {"type": "notices", "limit": notices.LIMIT, "items": []}
    for bad in (("", "ok"), ("   ", "ok"), (None, "ok"), (["hello"], "ok"), (42, "ok"),
                ("hello", "error"), ("hello", "stale"), ("hello", ""), ("hello", None),
                ("hello", 3)):
        try:
            notices.record(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert db.query_one("SELECT 1 FROM meta WHERE key=?", (notices.META_KEY,)) is None, \
        "a refused notice writes nothing"

    first = notices.record("Session deleted", "ok")
    assert first["state_topic"] == "notices" and first["state_revision"] >= 1
    assert texts(first) == [("Session deleted", "ok", 1)]
    item = first["items"][0]
    assert item["id"] == 1 and item["count"] == 1 and item["first_at"] == item["at"]
    assert abs(item["at"] - time.time()) < 5

    # An identical repeat counts up on the newest entry, its stamp moving on
    # while its first sighting is kept; a different tone is a different notice.
    with patch.object(time, "time", return_value=item["at"] + 30):
        again = notices.record("Session deleted", "ok")
    assert texts(again) == [("Session deleted", "ok", 2)]
    assert again["items"][0]["id"] == 1
    assert again["items"][0]["first_at"] == item["at"]
    assert again["items"][0]["at"] == item["at"] + 30
    assert again["state_revision"] > first["state_revision"]
    other = notices.record("Session deleted", "bad")
    assert texts(other) == [("Session deleted", "bad", 1), ("Session deleted", "ok", 2)]
    assert other["items"][0]["id"] == 2
    # Only the newest entry folds a repeat: a notice returning after another
    # one is its own entry, in order, so the history never rewrites the past.
    third = notices.record("Session deleted", "ok")
    assert texts(third) == [("Session deleted", "ok", 1), ("Session deleted", "bad", 1),
                            ("Session deleted", "ok", 2)]
    assert [item["id"] for item in third["items"]] == [3, 2, 1]
    # The report is cleaned the way the toast text was, so a differently
    # spaced repeat still folds.
    folded = notices.record("  Session   deleted ", "ok")
    assert texts(folded)[0] == ("Session deleted", "ok", 2)
    # A clock stepping back never leaves a repeat before its first sighting.
    with patch.object(time, "time", return_value=1.0):
        stepped = notices.record("Session deleted", "ok")
    assert stepped["items"][0]["first_at"] <= stepped["items"][0]["at"]
    assert stepped["items"][0]["at"] == folded["items"][0]["at"]

    # Pruned to the newest LIMIT; ids keep advancing past the ones let go.
    for number in range(notices.LIMIT + 20):
        latest = notices.record("notice {}".format(number), "info")
    assert len(latest["items"]) == notices.LIMIT
    assert latest["items"][0]["text"] == "notice {}".format(notices.LIMIT + 19)
    assert latest["items"][-1]["text"] == "notice 20"
    ids = [item["id"] for item in latest["items"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == len(ids)
    record = stored()
    assert set(record) == {"format", "next_id", "items"} and record["format"] == 1
    assert len(record["items"]) == notices.LIMIT
    assert record["items"][-1]["id"] == ids[0] and record["next_id"] == ids[0] + 1
    assert record["items"][0]["id"] == ids[-1], "stored oldest first, served newest first"
    assert notices.payload() == {"type": "notices", "limit": notices.LIMIT,
                                 "items": latest["items"]}
    notices.validate_persisted(db.connect())


def clearing():
    """A clear empties the list in one go; the ids keep advancing past it."""
    before = notices.publish()
    assert len(before["items"]) == notices.LIMIT
    next_id = stored()["next_id"]
    cleared = notices.clear()
    assert cleared["items"] == [] and cleared["limit"] == notices.LIMIT
    assert cleared["state_topic"] == "notices"
    assert cleared["state_revision"] > before["state_revision"]
    record = stored()
    assert record == {"format": 1, "next_id": next_id, "items": []}, \
        "the emptied record keeps its next id"
    notices.validate_persisted(db.connect())
    assert notices.payload()["items"] == []
    # Clearing an empty list changes nothing: no write, no new revision.
    raw = db.query_one("SELECT value FROM meta WHERE key=?", (notices.META_KEY,))["value"]
    again = notices.clear()
    assert again["items"] == [] and again["state_revision"] == cleared["state_revision"]
    assert db.query_one("SELECT value FROM meta WHERE key=?",
                        (notices.META_KEY,))["value"] == raw
    # The next notice takes the id the clear preserved, never one of those
    # let go, and folding starts afresh: a repeat of a cleared entry is new.
    after = notices.record("notice 0", "info")
    assert texts(after) == [("notice 0", "info", 1)]
    assert after["items"][0]["id"] == next_id
    assert stored()["next_id"] == next_id + 1
    # With no record at all, a clear writes nothing and publishes the empty list.
    db.execute("DELETE FROM meta WHERE key=?", (notices.META_KEY,))
    fresh = notices.clear()
    assert fresh["items"] == []
    assert db.query_one("SELECT 1 FROM meta WHERE key=?", (notices.META_KEY,)) is None
    # Leave a full history for the shape checks that follow.
    for number in range(notices.LIMIT + 5):
        notices.record("notice {}".format(number), "info")
    assert len(notices.payload()["items"]) == notices.LIMIT


def persisted_shape():
    good = stored()
    connection = db.connect()

    def write(value):
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value", (notices.META_KEY, value))

    def refused(value, label):
        written = value if isinstance(value, str) else json.dumps(value)
        write(written)
        try:
            notices.validate_persisted(connection)
        except ValueError:
            pass
        else:
            raise AssertionError(label)
        for call in (notices.payload, lambda: notices.record("x", "ok"), notices.clear):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError(label + " (use)")
        raw = db.query_one("SELECT value FROM meta WHERE key=?", (notices.META_KEY,))
        assert raw["value"] == written, "a malformed record is never repaired"

    def variant(**changes):
        value = json.loads(json.dumps(good))
        value.update(changes)
        return value

    def item_variant(index, **changes):
        value = json.loads(json.dumps(good))
        value["items"][index].update(changes)
        return value

    refused("not json", "non-JSON record")
    refused("[]", "a list is not the record")
    refused(variant(extra=1), "an unknown key")
    refused(variant(format=2), "another format")
    refused(variant(next_id=True), "a boolean next_id")
    refused(variant(next_id=good["items"][-1]["id"]), "next_id not past the newest id")
    refused(variant(items=good["items"] + [dict(good["items"][-1], id=good["next_id"])]),
            "more than the limit")
    refused(item_variant(0, id=good["items"][1]["id"]), "ids out of order")
    refused(item_variant(0, text=""), "an empty text")
    refused(item_variant(0, text="two  spaces"), "text that is not its cleaned form")
    refused(item_variant(0, text="x" * (notices.MAX_TEXT + 1)), "text past the cap")
    refused(item_variant(0, tone="error"), "a tone outside the vocabulary")
    refused(item_variant(0, count=0), "a zero count")
    refused(item_variant(0, count=1.0), "a fractional count")
    refused(item_variant(0, first_at=good["items"][0]["at"] + 1), "first after last")
    refused(item_variant(0, at=float("inf")), "an infinite stamp")
    refused(item_variant(0, at=-1), "a negative stamp")
    item = dict(good["items"][0])
    del item["first_at"]
    refused(variant(items=[item] + good["items"][1:]), "a missing item key")
    write(json.dumps(good))
    notices.validate_persisted(connection)
    db.execute("DELETE FROM meta WHERE key=?", (notices.META_KEY,))
    notices.validate_persisted(connection)
    assert notices.payload()["items"] == []
    write(json.dumps({"format": 1, "next_id": 1, "items": []}))
    notices.validate_persisted(connection)
    assert notices.payload()["items"] == []


async def full_runtime_routes():
    app = webui.build_app()
    assert app["puppy_role"] == "full"
    startup = [hook for hook in app.on_startup if hook.__name__ == "publish_notices"]
    assert len(startup) == 1, "the full runtime validates and publishes the list at startup"
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    runner.configure_updates(app["puppy_runtime_id"])
    await startup[0](app)
    async with TestClient(TestServer(app)) as client:
        for method in (client.get, client.post, client.delete):
            response = await method("/api/notices")
            assert response.status == 401
        headers = {"X-Puppy-Token": config.get("auth.api_token")}

        # The list is on the stream before any console has asked for it.
        socket = await client.ws_connect("/api/ws/updates", headers=headers)
        ready = await socket.receive_json(timeout=3)
        assert ready["type"] == "updates_ready" and "notices" in ready["topics"]
        attached = None
        while attached is None:
            message = await socket.receive_json(timeout=3)
            if message.get("type") == "notices":
                attached = message
        assert attached["items"] == [] and attached["limit"] == notices.LIMIT
        assert attached["state_topic"] == "notices" and attached["state_revision"] >= 1

        response = await client.get("/api/notices", headers=headers)
        assert response.status == 200
        read = await response.json()
        assert read["items"] == [] and read["state_revision"] == attached["state_revision"], \
            "an unchanged list keeps its revision"

        for body in ({}, [], {"text": "hi"}, {"text": "hi", "tone": "ok", "at": 1},
                     {"text": "", "tone": "ok"}, {"text": "hi", "tone": "stale"},
                     {"text": ["hi"], "tone": "ok"}):
            response = await client.post("/api/notices", headers=headers, json=body)
            assert response.status == 400, (body, response.status)
        response = await client.post("/api/notices", data=b"not json", headers={
            **headers, "Content-Type": "application/json"})
        assert response.status == 400
        assert (await client.get("/api/notices", headers=headers)).status == 200
        assert notices.payload()["items"] == []

        response = await client.post("/api/notices", headers=headers,
                                     json={"text": " NAS: could not save. ", "tone": "bad"})
        assert response.status == 200, await response.text()
        recorded = await response.json()
        assert texts(recorded) == [("NAS: could not save.", "bad", 1)]
        assert recorded["state_revision"] > attached["state_revision"]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["type"] == "notices" and \
            streamed["state_revision"] == recorded["state_revision"]
        assert streamed["items"] == recorded["items"]
        response = await client.post("/api/notices", headers=headers,
                                     json={"text": "NAS: could not save.", "tone": "bad"})
        assert texts(await response.json()) == [("NAS: could not save.", "bad", 2)]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["items"][0]["count"] == 2

        # The pill's clear: the list comes back empty in the answer and on the
        # stream, at one new revision, and a later notice takes a later id.
        cleared_id = streamed["items"][0]["id"]
        response = await client.delete("/api/notices", headers=headers)
        assert response.status == 200, await response.text()
        cleared = await response.json()
        assert cleared["items"] == [] and cleared["limit"] == notices.LIMIT
        assert cleared["state_revision"] > streamed["state_revision"]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["type"] == "notices" and streamed["items"] == []
        assert streamed["state_revision"] == cleared["state_revision"]
        assert notices.payload()["items"] == []
        response = await client.post("/api/notices", headers=headers,
                                     json={"text": "NAS: could not save.", "tone": "bad"})
        recorded = await response.json()
        assert texts(recorded) == [("NAS: could not save.", "bad", 1)], \
            "a repeat of a cleared entry is a new entry"
        assert recorded["items"][0]["id"] > cleared_id
        streamed = await socket.receive_json(timeout=3)
        assert streamed["items"] == recorded["items"]

        # A backup in progress refuses the report and the clear (the console
        # keeps the report for later), and neither counts as a state change
        # that could make an export racing a toast fail.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)   # a started app's state
            app["puppy_snapshot_busy"] = "export"
        response = await client.post("/api/notices", headers=headers,
                                     json={"text": "During a backup", "tone": "info"})
        assert response.status == 503
        response = await client.delete("/api/notices", headers=headers)
        assert response.status == 503
        assert (await client.get("/api/notices", headers=headers)).status == 200
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            app["puppy_snapshot_busy"] = None
        assert notices.payload()["items"][0]["text"] == "NAS: could not save."
        seen = []
        real_record, real_clear = notices.record, notices.clear

        def counted(text, tone):
            seen.append(int(app.get("puppy_mutations", 0)))
            return real_record(text, tone)

        def counted_clear():
            seen.append(int(app.get("puppy_mutations", 0)))
            return real_clear()

        with patch.object(notices, "record", counted), \
                patch.object(notices, "clear", counted_clear):
            response = await client.post("/api/notices", headers=headers,
                                         json={"text": "After the backup", "tone": "ok"})
            assert response.status == 200
            response = await client.delete("/api/notices", headers=headers)
            assert response.status == 200
        assert seen == [0, 0], seen
        assert notices.payload()["items"] == []
        while (await socket.receive_json(timeout=3))["items"] != []:
            pass
        # A record that has gone malformed underneath is the instance's
        # problem, not a refusal of the notice: the console keeps it.
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value", (notices.META_KEY, "[]"))
        try:
            response = await client.post("/api/notices", headers=headers,
                                         json={"text": "Later", "tone": "ok"})
            assert response.status == 500, response.status
            response = await client.get("/api/notices", headers=headers)
            assert response.status == 500, response.status
            response = await client.delete("/api/notices", headers=headers)
            assert response.status == 500, response.status
            raw = db.query_one("SELECT value FROM meta WHERE key=?", (notices.META_KEY,))
            assert raw["value"] == "[]", "a clear never repairs a malformed record either"
        finally:
            db.execute("DELETE FROM meta WHERE key=?", (notices.META_KEY,))
        await socket.close()

    # A malformed record refuses to start rather than being repaired.
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
               "SET value=excluded.value", (notices.META_KEY, json.dumps({"format": 2})))
    try:
        app = webui.build_app()
        hook = [hook for hook in app.on_startup if hook.__name__ == "publish_notices"][0]
        try:
            await hook(app)
        except ValueError:
            pass
        else:
            raise AssertionError("a malformed notification history must refuse startup")
    finally:
        db.execute("DELETE FROM meta WHERE key=?", (notices.META_KEY,))


async def headless_runtime_has_none():
    app = backend_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert not any(hook.__name__ == "publish_notices" for hook in app.on_startup)
    async with TestClient(TestServer(app)) as client:
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        for method in (client.get, client.post, client.delete):
            response = await method("/api/notices", headers=headers,
                                    json={"text": "hi", "tone": "ok"})
            assert response.status == 404, response.status
        socket = await client.ws_connect("/api/ws/updates", headers=headers)
        ready = await socket.receive_json(timeout=3)
        assert ready["type"] == "updates_ready" and "notices" not in ready["topics"]
        await socket.close()


async def main():
    try:
        text_shape()
        recording()
        clearing()
        persisted_shape()
        await full_runtime_routes()
        await headless_runtime_has_none()
        print("PASS: notification history record, its clear, shape validation, routes, "
              "stream topic, backup guards, and its absence from the headless backend")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
