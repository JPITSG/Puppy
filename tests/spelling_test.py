#!/usr/bin/env python3
"""The controller's spelling dictionary: the words added to the console's
checker, the tally behind the ones it learns (a marked word sent five times
within seven days), the exact persisted shape, the routes the console adds,
removes and reports through, the state topic that carries the list, and the
guards around a backup. Both runtimes are booted; no engine, network or
quota."""
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
TEST_ROOT = private_root("spelling-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, runner, spelling
from puppy import web as webui
from backend.puppy_backend.app import build_app as backend_app

DAY = 86400
LETTERS = "oizeasgtbq"


def coin(number: int) -> str:
    """A word the dictionary does not know, one per number: w + its digits as letters."""
    return "w" + "".join(LETTERS[int(digit)] for digit in str(number))


def stored() -> dict:
    row = db.query_one("SELECT value FROM meta WHERE key=?", (spelling.META_KEY,))
    return json.loads(row["value"])


def write(value) -> None:
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
               "SET value=excluded.value",
               (spelling.META_KEY, value if isinstance(value, str) else json.dumps(value)))


def words_shape():
    normalize = spelling.normalize
    assert normalize("Eval") == "eval" and normalize("  eval ") == "eval"
    assert normalize("café") == "café" and normalize("ÉCOLE") == "école"
    assert normalize("don't") == "don't" and normalize("agents’") == "agents’"
    for value in ("", "   ", None, 42, ["eval"], "x" * (spelling.MAX_WORD + 1), "app.js",
                  "eval5", "ab cd", "e-mail", "'quoted", "a''b", "@browser"):
        assert normalize(value) == "", value
    assert normalize("x" * spelling.MAX_WORD) == "x" * spelling.MAX_WORD


def learning():
    config.load()
    db.connect()
    assert db.query_one("SELECT 1 FROM meta WHERE key=?", (spelling.META_KEY,)) is None
    assert spelling.payload() == {"type": "spelling", "words": [], "learn_sends": 5,
                                  "learn_days": 7}
    for bad in ("", "x1", "app.js", None, 42, "x" * 65):
        for call in (spelling.add, spelling.remove):
            try:
                call(bad)
            except ValueError:
                pass
            else:
                raise AssertionError((call.__name__, bad))
    for bad in ("eval", None, {"eval": 1}, ["x1"], [""], [42], ["eval"] * (spelling.REPORT_WORDS + 1)):
        try:
            spelling.sent(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert db.query_one("SELECT 1 FROM meta WHERE key=?", (spelling.META_KEY,)) is None, \
        "a refused request writes nothing"

    # Add to dictionary: the list's own form, sorted, published; again is nothing.
    added = spelling.add("Eval")
    assert added["state_topic"] == "spelling" and added["state_revision"] >= 1
    assert added["words"] == ["eval"] and added["type"] == "spelling"
    assert stored() == {"format": 1, "words": ["eval"], "tally": {}}
    again = spelling.add("eval")
    assert again["state_revision"] == added["state_revision"], "an unchanged list keeps its revision"
    both = spelling.add("Zorbium")
    assert both["words"] == ["eval", "zorbium"]
    first = spelling.add("alpha")
    assert first["words"] == ["alpha", "eval", "zorbium"], "kept sorted"
    # Remove from dictionary, and of a word the list does not hold.
    removed = spelling.remove("ALPHA")
    assert removed["words"] == ["eval", "zorbium"]
    assert spelling.remove("alpha")["state_revision"] == removed["state_revision"]
    gone = spelling.remove("eval")
    assert gone["words"] == ["zorbium"]

    # A sent message: each marked word counts once, lower-case; the fifth
    # send inside the window learns it.
    base = int(time.time())
    for number in range(4):
        with patch.object(time, "time", return_value=base + number):
            answer = spelling.sent(["eval", "Eval", "EVAL"])
        assert answer["learned"] == [] and answer["words"] == ["zorbium"]
        assert stored()["tally"] == {"eval": [base + n for n in range(number + 1)]}
    with patch.object(time, "time", return_value=base + 4):
        fifth = spelling.sent(["eval", "frobz"])
    assert fifth["learned"] == ["eval"] and fifth["words"] == ["eval", "zorbium"]
    assert fifth["type"] == "spelling" and fifth["learn_sends"] == 5 and fifth["learn_days"] == 7
    assert stored() == {"format": 1, "words": ["eval", "zorbium"], "tally": {"frobz": [base + 4]}}
    # A word the list holds is not counted, and a report that changes
    # nothing writes nothing and keeps the revision.
    with patch.object(time, "time", return_value=base + 5):
        nothing = spelling.sent(["eval", "zorbium"])
    assert nothing["learned"] == [] and nothing["state_revision"] == fifth["state_revision"]
    assert stored()["tally"] == {"frobz": [base + 4]}
    assert spelling.sent([])["learned"] == []

    # The window: sends older than seven days fall off before the count.
    for offset in (10, 11, 12):
        with patch.object(time, "time", return_value=base + offset):
            spelling.sent(["zorp"])
    later = base + 8 * DAY
    with patch.object(time, "time", return_value=later):
        answer = spelling.sent(["zorp"])
    assert answer["learned"] == []
    assert stored()["tally"] == {"zorp": [later]}, "three stale sends and frobz's fell off"
    for offset in (1, 2, 3):
        with patch.object(time, "time", return_value=later + offset):
            answer = spelling.sent(["zorp"])
    assert answer["learned"] == [] and stored()["tally"]["zorp"] == [later + n for n in range(4)]
    with patch.object(time, "time", return_value=later + 4):
        answer = spelling.sent(["zorp"])
    assert answer["learned"] == ["zorp"] and "zorp" in answer["words"]
    assert "zorp" not in stored()["tally"]

    # Add to dictionary and Remove from dictionary each end a word's tally.
    with patch.object(time, "time", return_value=later + 10):
        spelling.sent(["blorf"])
    assert stored()["tally"] == {"blorf": [later + 10]}
    spelling.add("blorf")
    assert stored()["tally"] == {} and "blorf" in stored()["words"]
    with patch.object(time, "time", return_value=later + 11):
        spelling.sent(["blorf"])
    assert stored()["tally"] == {}, "a word the list holds is not counted"
    spelling.remove("blorf")
    with patch.object(time, "time", return_value=later + 12):
        spelling.sent(["blorf"])
    assert stored()["tally"] == {"blorf": [later + 12]}, "its count starts over"
    revision = spelling.remove("blorf")["state_revision"]
    assert stored()["tally"] == {"blorf": [later + 12]}, \
        "removing a word the list does not hold is nothing"
    assert spelling.publish()["state_revision"] == revision

    # The tally's bound: the longest-quiet words are let go first.
    coined = ["zq{}{}".format(chr(97 + n // 26), chr(97 + n % 26))
              for n in range(spelling.TALLY_WORDS + 1)]
    for number, word in enumerate(coined):
        with patch.object(time, "time", return_value=later + 100 + number):
            spelling.sent([word])
    tally = stored()["tally"]
    assert len(tally) == spelling.TALLY_WORDS
    assert coined[0] not in tally and coined[-1] in tally
    spelling.validate_persisted(db.connect())
    # The dictionary's bound.
    state = stored()
    state["words"] = sorted(set(state["words"]) | {
        coin(n) for n in range(spelling.MAX_WORDS - len(state["words"]))})
    write(state)
    spelling.validate_persisted(db.connect())
    assert len(stored()["words"]) == spelling.MAX_WORDS
    try:
        spelling.add("onemore")
    except ValueError as exc:
        assert "at most" in str(exc)
    else:
        raise AssertionError("a full dictionary takes no more")
    assert len(stored()["words"]) == spelling.MAX_WORDS
    write({"format": 1, "words": ["eval", "zorbium"], "tally": {}})


def persisted_shape():
    good = {"format": 1, "words": ["eval", "zorbium"], "tally": {"frobz": [100, 200]}}
    write(good)
    connection = db.connect()
    spelling.validate_persisted(connection)

    def refused(value, label):
        written = value if isinstance(value, str) else json.dumps(value)
        write(written)
        try:
            spelling.validate_persisted(connection)
        except ValueError:
            pass
        else:
            raise AssertionError(label)
        for call in (spelling.payload, lambda: spelling.add("x"), lambda: spelling.remove("x"),
                     lambda: spelling.sent(["x"])):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError(label + " (use)")
        raw = db.query_one("SELECT value FROM meta WHERE key=?", (spelling.META_KEY,))
        assert raw["value"] == written, "a malformed record is never repaired"

    def variant(**changes):
        value = json.loads(json.dumps(good))
        value.update(changes)
        return value

    refused("not json", "non-JSON record")
    refused("[]", "a list is not the record")
    refused(variant(extra=1), "an unknown key")
    refused(variant(format=2), "another format")
    refused({"format": 1, "words": []}, "a missing key")
    refused(variant(words="eval"), "words that are not a list")
    refused(variant(words=["zorbium", "eval"]), "words out of order")
    refused(variant(words=["eval", "eval"]), "a word twice")
    refused(variant(words=["Eval"]), "a word not in the list's own form")
    refused(variant(words=["x1"]), "not a word")
    refused(variant(words=["x" * (spelling.MAX_WORD + 1)]), "a word past the cap")
    refused(variant(words=[1]), "a word that is not a string")
    refused(variant(words=sorted(coin(n) for n in range(spelling.MAX_WORDS + 1))),
            "more words than the bound")
    refused(variant(tally=[]), "a tally that is not a map")
    refused(variant(tally={"eval": [100]}), "a tally for a word the list holds")
    refused(variant(tally={"Frobz": [100]}), "a tally word not in the list's own form")
    refused(variant(tally={"frobz": []}), "an empty tally")
    refused(variant(tally={"frobz": [100, 200, 300, 400, 500]}), "a tally past the threshold")
    refused(variant(tally={"frobz": [200, 100]}), "stamps out of order")
    refused(variant(tally={"frobz": [100, 100.5]}), "a fractional stamp")
    refused(variant(tally={"frobz": [0]}), "a stamp at zero")
    refused(variant(tally={"frobz": [True]}), "a boolean stamp")
    refused(variant(tally={"frobz": "100"}), "stamps that are not a list")
    refused(variant(tally={"zq{}{}".format(chr(97 + n // 26), chr(97 + n % 26)): [100]
                           for n in range(spelling.TALLY_WORDS + 1)}), "more tally words than the bound")
    write(good)
    spelling.validate_persisted(connection)
    db.execute("DELETE FROM meta WHERE key=?", (spelling.META_KEY,))
    spelling.validate_persisted(connection)
    assert spelling.payload()["words"] == []
    write({"format": 1, "words": [], "tally": {}})
    spelling.validate_persisted(connection)
    assert spelling.payload()["words"] == []


async def full_runtime_routes():
    app = webui.build_app()
    assert app["puppy_role"] == "full"
    startup = [hook for hook in app.on_startup if hook.__name__ == "publish_spelling"]
    assert len(startup) == 1, "the full runtime validates and publishes the list at startup"
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    runner.configure_updates(app["puppy_runtime_id"])
    await startup[0](app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/spelling")).status == 401
        assert (await client.post("/api/spelling/words", json={"word": "eval"})).status == 401
        assert (await client.delete("/api/spelling/words/eval")).status == 401
        assert (await client.post("/api/spelling/sent", json={"words": ["eval"]})).status == 401
        headers = {"X-Puppy-Token": config.get("auth.api_token")}

        # The list is on the stream before any console has asked for it.
        socket = await client.ws_connect("/api/ws/updates", headers=headers)
        ready = await socket.receive_json(timeout=3)
        assert ready["type"] == "updates_ready" and "spelling" in ready["topics"]
        attached = None
        while attached is None:
            message = await socket.receive_json(timeout=3)
            if message.get("type") == "spelling":
                attached = message
        assert attached["words"] == [] and attached["learn_sends"] == 5 and attached["learn_days"] == 7
        assert attached["state_topic"] == "spelling" and attached["state_revision"] >= 1

        response = await client.get("/api/spelling", headers=headers)
        assert response.status == 200
        read = await response.json()
        assert read["words"] == [] and read["state_revision"] == attached["state_revision"], \
            "an unchanged list keeps its revision"

        for body in ({}, [], {"word": 1}, {"word": "eval", "extra": 1}, {"words": ["eval"]}):
            response = await client.post("/api/spelling/words", headers=headers, json=body)
            assert response.status == 400, (body, response.status)
        for body in ({"word": ""}, {"word": "x1"}, {"word": "app.js"}):
            response = await client.post("/api/spelling/words", headers=headers, json=body)
            assert response.status == 400, (body, response.status)
            assert "not a word" in (await response.json())["error"]
        response = await client.post("/api/spelling/words", data=b"not json", headers={
            **headers, "Content-Type": "application/json"})
        assert response.status == 400
        for body in ({}, [], {"words": "eval"}, {"words": [1]}, {"words": ["eval"], "at": 1},
                     {"words": ["x1"]}, {"words": ["eval"] * (spelling.REPORT_WORDS + 1)}):
            response = await client.post("/api/spelling/sent", headers=headers, json=body)
            assert response.status == 400, (body, response.status)
        response = await client.delete("/api/spelling/words/x1", headers=headers)
        assert response.status == 400
        assert spelling.payload()["words"] == []

        # Add to dictionary: the answer is the list, and so is the stream's frame.
        response = await client.post("/api/spelling/words", headers=headers, json={"word": " Zorbium "})
        assert response.status == 200, await response.text()
        added = await response.json()
        assert added["words"] == ["zorbium"] and added["state_revision"] > attached["state_revision"]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["type"] == "spelling" and streamed["words"] == ["zorbium"]
        assert streamed["state_revision"] == added["state_revision"]

        # Every send reports its marked words; the fifth learns one.
        for number in range(4):
            response = await client.post("/api/spelling/sent", headers=headers,
                                         json={"words": ["eval", "frobz"]})
            assert response.status == 200, await response.text()
            counted = await response.json()
            assert counted["learned"] == [] and counted["words"] == ["zorbium"]
            assert counted["state_revision"] == added["state_revision"], \
                "a count that moves no word keeps the list's revision"
        response = await client.post("/api/spelling/sent", headers=headers,
                                     json={"words": ["Eval"]})
        learned = await response.json()
        assert learned["learned"] == ["eval"] and learned["words"] == ["eval", "zorbium"]
        assert learned["state_revision"] > added["state_revision"]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["words"] == ["eval", "zorbium"] and \
            streamed["state_revision"] == learned["state_revision"]
        assert "learned" not in streamed, "what one send learned is that send's answer"

        # Remove from dictionary, with an encoded word too.
        response = await client.post("/api/spelling/words", headers=headers, json={"word": "agents’"})
        assert (await response.json())["words"] == ["agents’", "eval", "zorbium"]
        await socket.receive_json(timeout=3)
        response = await client.delete("/api/spelling/words/agents%E2%80%99", headers=headers)
        assert response.status == 200, await response.text()
        removed = await response.json()
        assert removed["words"] == ["eval", "zorbium"]
        streamed = await socket.receive_json(timeout=3)
        assert streamed["words"] == ["eval", "zorbium"]
        response = await client.delete("/api/spelling/words/eval", headers=headers)
        assert (await response.json())["words"] == ["zorbium"]
        await socket.receive_json(timeout=3)
        response = await client.delete("/api/spelling/words/eval", headers=headers)
        assert response.status == 200 and (await response.json())["words"] == ["zorbium"], \
            "removing a word the list does not hold is nothing"

        # A backup in progress refuses the writes (the console drops the
        # report, and says an add or remove could not be made) while the
        # read still answers, and none of them counts as a state change
        # that could make an export racing a send fail.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)   # a started app's state
            app["puppy_snapshot_busy"] = "export"
        for call in (lambda: client.post("/api/spelling/words", headers=headers, json={"word": "x"}),
                     lambda: client.delete("/api/spelling/words/zorbium", headers=headers),
                     lambda: client.post("/api/spelling/sent", headers=headers, json={"words": ["x"]})):
            assert (await call()).status == 503
        assert (await client.get("/api/spelling", headers=headers)).status == 200
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            app["puppy_snapshot_busy"] = None
        seen = []
        real = {"add": spelling.add, "remove": spelling.remove, "sent": spelling.sent}

        def counted(name):
            def call(value):
                seen.append(int(app.get("puppy_mutations", 0)))
                return real[name](value)
            return call

        with patch.object(spelling, "add", counted("add")), \
                patch.object(spelling, "remove", counted("remove")), \
                patch.object(spelling, "sent", counted("sent")):
            assert (await client.post("/api/spelling/words", headers=headers,
                                      json={"word": "x"})).status == 200
            assert (await client.delete("/api/spelling/words/x", headers=headers)).status == 200
            assert (await client.post("/api/spelling/sent", headers=headers,
                                      json={"words": ["frobz"]})).status == 200
        assert seen == [0, 0, 0], seen
        while (await socket.receive_json(timeout=3))["words"] != ["zorbium"]:
            pass
        # A record that has gone malformed underneath is the instance's
        # problem, said as such, never repaired.
        write("[]")
        try:
            assert (await client.get("/api/spelling", headers=headers)).status == 500
            assert (await client.post("/api/spelling/words", headers=headers,
                                      json={"word": "x"})).status == 500
            assert (await client.delete("/api/spelling/words/x", headers=headers)).status == 500
            assert (await client.post("/api/spelling/sent", headers=headers,
                                      json={"words": ["x"]})).status == 500
            raw = db.query_one("SELECT value FROM meta WHERE key=?", (spelling.META_KEY,))
            assert raw["value"] == "[]", "never repaired"
        finally:
            db.execute("DELETE FROM meta WHERE key=?", (spelling.META_KEY,))
        await socket.close()

    # A malformed record refuses to start rather than being repaired.
    write({"format": 2})
    try:
        app = webui.build_app()
        hook = [hook for hook in app.on_startup if hook.__name__ == "publish_spelling"][0]
        try:
            await hook(app)
        except ValueError:
            pass
        else:
            raise AssertionError("a malformed spelling dictionary must refuse startup")
    finally:
        db.execute("DELETE FROM meta WHERE key=?", (spelling.META_KEY,))


async def headless_runtime_has_none():
    app = backend_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert not any(hook.__name__ == "publish_spelling" for hook in app.on_startup)
    async with TestClient(TestServer(app)) as client:
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        assert (await client.get("/api/spelling", headers=headers)).status == 404
        assert (await client.post("/api/spelling/words", headers=headers,
                                  json={"word": "eval"})).status == 404
        assert (await client.delete("/api/spelling/words/eval", headers=headers)).status == 404
        assert (await client.post("/api/spelling/sent", headers=headers,
                                  json={"words": ["eval"]})).status == 404
        socket = await client.ws_connect("/api/ws/updates", headers=headers)
        ready = await socket.receive_json(timeout=3)
        assert ready["type"] == "updates_ready" and "spelling" not in ready["topics"]
        await socket.close()


async def main():
    try:
        words_shape()
        learning()
        persisted_shape()
        await full_runtime_routes()
        await headless_runtime_has_none()
        print("PASS: spelling dictionary record, its learning rule, shape validation, routes, "
              "stream topic, backup guards, and its absence from the headless backend")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
