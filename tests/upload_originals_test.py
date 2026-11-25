#!/usr/bin/env python3
"""The picture under a marked copy, kept by the node for as long as the copy.

What a note is (a private file of its own, exactly an upload id, anything else
refused), which picture it names (the one under both when marks go on a copy
that was marked already), and how long that picture stays: through a discard,
a cancelled prompt and the orphan sweep while its marked copy is stored, and
gone with it. The sweep itself: prompts sent and still queued keep their files,
a picture being marked is in use, a note that cannot be read keeps everything,
and a session with hundreds of files costs one read of its prompts. A task
taking a marked copy over takes its original along, and leaves nothing behind
when it cannot. Then both runtimes over HTTP, a controller relaying to a
backend, and the session's deletion. No engine, network or model quota is used.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("upload-originals-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import backends, config, db, operations, protocol, runner, uploads  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"original" * 4
MARKED = b"\x89PNG\r\n\x1a\n" + b"marked" * 4
DAY = uploads.ORPHAN_AGE_SECONDS


def session(name="Originals"):
    return db.create_session(name, "claude", str(ROOT), "", "", "", "default")


def root(sid) -> Path:
    return Path(config.DATA_DIR).resolve() / "uploads" / str(sid)


def store(sid, number, name="photo.jpg", raw=PNG, age=0.0) -> str:
    """An upload as the route leaves it: one private file in its own directory."""
    upload_id = "{:013d}-{:010x}".format(1700000000000 + number, number)
    directory = root(sid) / upload_id
    directory.mkdir(parents=True, mode=0o700)
    (root(sid).parent).chmod(0o700)
    root(sid).chmod(0o700)
    target = directory / name
    target.write_bytes(raw)
    target.chmod(0o600)
    then = time.time() - age
    os.utime(str(directory), (then, then))
    return upload_id


def note(sid, upload_id) -> Path:
    return root(sid) / ("o" + upload_id)


def aged(path: Path) -> Path:
    then = time.time() - 2 * DAY
    os.utime(str(path), (then, then))
    return path


def marker(sid, upload_id, name) -> str:
    return "{}{}/{}/{}{}".format(uploads.ATTACH_IMAGE_PREFIX, root(sid), upload_id, name,
                                 uploads.ATTACH_IMAGE_SUFFIX)


def files(sid) -> set:
    return {path.name for path in root(sid).iterdir()}


def done(sid):
    runner.drop_hub(sid)
    uploads.remove_session_storage(sid)
    db.delete_session(sid)


def test_notes():
    sid = session()
    try:
        original, marked, again, plain = (store(sid, n) for n in range(1, 5))
        text = store(sid, 9, "notes.txt", b"words")
        assert uploads._noted_original(sid, marked) == ""
        assert uploads._remember_original(sid, marked, original) == original
        path = note(sid, marked)
        assert path.read_text() == original and stat.S_IMODE(path.lstat().st_mode) == 0o600
        assert uploads._original_file(sid, marked).name == "photo.jpg"
        # marks on a copy that was marked already lead back to the picture under both
        assert uploads._remember_original(sid, again, marked) == original
        assert uploads._is_original(sid, original) and not uploads._is_original(sid, marked)
        assert not uploads._is_original(sid, plain) and not uploads._is_original(99999, original)
        # nothing to note: no such upload, or one that is not a picture
        for other in ("1600000000000-0000000000", text):
            assert uploads._remember_original(sid, plain, other) == "" and not note(sid, plain).exists()
        # a note nobody but Puppy wrote is refused, never repaired
        descriptors = len(os.listdir("/proc/self/fd"))
        for spoil in (lambda: path.write_text("x" * 24), lambda: path.write_text(original[:-1]),
                      lambda: path.write_text(original + "\n"), lambda: path.write_text(""),
                      lambda: path.write_text(original * 4), lambda: path.write_bytes(b"\xff" * 24),
                      lambda: (path.unlink(), path.symlink_to(root(sid) / text / "notes.txt")),
                      lambda: (path.unlink(), (root(sid) / "elsewhere").write_text(original),
                               path.symlink_to(root(sid) / "elsewhere")),
                      lambda: (path.unlink(), path.mkdir())):
            spoil()
            try:
                uploads._noted_original(sid, marked)
            except OSError:
                pass
            else:
                raise AssertionError("a spoiled note was read")
            assert uploads._is_original(sid, plain), "it may name any upload, so every one counts"
            assert uploads._remember_original(sid, plain, marked) == "" and not note(sid, plain).exists()
        path.rmdir()
        assert len(os.listdir("/proc/self/fd")) <= descriptors, "a refused note leaves nothing open"
        # an original whose storage was tampered with, or is gone, is no original
        uploads._write_original(sid, marked, original)
        (root(sid) / original / "extra").write_bytes(b"x")
        try:
            uploads._original_file(sid, marked)
        except FileNotFoundError:
            raise AssertionError("tampered storage is a failure, not an absence")
        except OSError:
            pass
        (root(sid) / original / "extra").unlink()
        (root(sid) / original / "photo.jpg").rename(root(sid) / original / "photo.txt")
        for missing in (lambda: None, lambda: ((root(sid) / original / "photo.txt").unlink(),
                                               (root(sid) / original).rmdir())):
            missing()
            try:
                uploads._original_file(sid, marked)
            except FileNotFoundError:
                continue
            raise AssertionError("not a picture, or gone: there is no original")
        # a write that fails leaves neither a note nor half of one
        fresh, copy = store(sid, 11), store(sid, 12, "photo-marked.png", MARKED)
        before = files(sid)
        with patch.object(uploads.os, "fsync", side_effect=OSError("disk full")) as tried:
            assert uploads._remember_original(sid, copy, fresh) == ""
        assert tried.called and files(sid) == before
        assert uploads._remember_original(sid, copy, fresh) == fresh
        uploads._forget_original(sid, marked)
        uploads._forget_original(sid, marked)
        assert not path.exists()
    finally:
        done(sid)
    print("PASS: a note is one upload id in a private file, leads past an earlier copy to the picture under both, "
          "and is refused whenever it is anything else")


def test_sweep():
    sid = session()
    try:
        old = 2 * DAY
        # a sent marked copy keeps its original, however old
        kept, sent = store(sid, 1, age=old), store(sid, 2, "a-marked.png", MARKED, age=old)
        uploads._write_original(sid, sent, kept)
        aged(note(sid, sent))
        db.add_event(sid, "user", {"text": "See\n\n" + marker(sid, sent, "a-marked.png")})
        # one nobody sent goes with its original and its note
        spent, unsent = store(sid, 3, age=old), store(sid, 4, "b-marked.png", MARKED, age=old)
        uploads._write_original(sid, unsent, spent)
        # a copy in the draft, one in a queued prompt and a held one stay, with theirs
        drafted_from, drafted = store(sid, 5, age=old), store(sid, 6, "c-marked.png", MARKED, age=old)
        uploads._write_original(sid, drafted, drafted_from)
        db.set_session_draft(sid, marker(sid, drafted, "c-marked.png"))
        queued_from, queued = store(sid, 7, age=old), store(sid, 8, "d-marked.png", MARKED, age=old)
        uploads._write_original(sid, queued, queued_from)
        held = store(sid, 9, "held.png", PNG, age=old)
        db.meta_set("session_queue.{}".format(sid), {
            "queue": [marker(sid, queued, "d-marked.png")], "held": [marker(sid, held, "held.png")], "paused": []})
        # a young copy keeps an old picture; an old unmarked one nobody names goes
        young_from, young = store(sid, 10, age=old), store(sid, 11, "e-marked.png", MARKED)
        uploads._write_original(sid, young, young_from)
        stray = store(sid, 12, "stray.png", PNG, age=old)
        # two copies of one picture, one of them still sent
        shared, first, second = store(sid, 13, age=old), store(sid, 14, age=old), store(sid, 15, age=old)
        uploads._write_original(sid, first, shared)
        uploads._write_original(sid, second, shared)
        db.add_event(sid, "user", {"text": marker(sid, second, "photo.jpg")})
        # a note whose copy is gone is spent once it is old, and not before
        lost = "1700000000099-0000000063"
        uploads._write_original(sid, lost, kept)
        assert uploads.sweep_orphans(sid) == 4
        assert files(sid) == {kept, sent, "o" + sent, drafted_from, drafted, "o" + drafted, queued_from, queued,
                              "o" + queued, held, young_from, young, "o" + young, shared, second, "o" + second,
                              "o" + lost}, files(sid)
        assert spent not in files(sid) and stray not in files(sid)
        aged(note(sid, lost))
        assert uploads.sweep_orphans(sid) == 0 and "o" + lost not in files(sid)

        # a note that cannot be read may name anything: nothing of the session goes
        more = store(sid, 20, "more.png", PNG, age=old)
        good = note(sid, sent).read_text()
        note(sid, sent).write_text("?")
        before = files(sid)
        assert uploads.sweep_orphans(sid) == 0 and files(sid) == before
        note(sid, sent).write_text(good)
        assert uploads.sweep_orphans(sid) == 1 and more not in files(sid)

        # an id inside other digits, or behind another id, is still a mention
        tangled = [store(sid, 30 + n, "t.png", PNG, age=old) for n in range(3)]
        db.add_event(sid, "user", {"text": "999" + tangled[0] + "ff and 1111111111111-" + tangled[1]})
        db.meta_set("session_queue.{}".format(sid), {"queue": [], "held": [], "paused": []})
        db.set_session_draft(sid, "")
        assert uploads.sweep_orphans(sid) == 6   # tangled[2], the queue's three and the draft's two
        assert tangled[0] in files(sid) and tangled[1] in files(sid) and tangled[2] not in files(sid)

        # hundreds of files cost one read of the prompts, not one each
        for number in range(100, 160):
            store(sid, number, "many.png", PNG, age=old)
        asked = []
        real = db.query
        with patch.object(db, "query", side_effect=lambda sql, args=(): asked.append(sql) or real(sql, args)):
            assert uploads.sweep_orphans(sid) == 60
        assert len([sql for sql in asked if "FROM events" in sql]) == 1, asked
    finally:
        done(sid)

    # a picture picked a day ago and marked now is in use from that moment on:
    # the upload of its marked copy runs the very sweep that would take it
    sid = session()
    try:
        waiting = store(sid, 1, age=2 * DAY)
        uploads._touch_original(sid, waiting)
        assert uploads.sweep_orphans(sid) == 0 and waiting in files(sid)
        uploads._touch_original(sid, "1600000000000-0000000000")
    finally:
        done(sid)
    print("PASS: the sweep keeps what a prompt names - sent, drafted, queued or held - and the picture under it, takes "
          "an unsent copy with its original and note, and reads the prompts once")


def test_discards():
    sid = session()
    try:
        hub = runner.hub(sid)
        original, marked = store(sid, 1), store(sid, 2, "a-marked.png", MARKED)
        uploads._write_original(sid, marked, original)
        # cancelling a queued prompt that carried the unmarked picture leaves it to the marked copy
        hub._discard_abandoned_uploads([marker(sid, original, "photo.jpg")])
        assert files(sid) == {original, marked, "o" + marked}
        # cancelling the one that carried the copy takes the copy and its note
        hub._discard_abandoned_uploads([marker(sid, marked, "a-marked.png")])
        assert files(sid) == {original}, "the picture itself is left to the sweep"
        hub._discard_abandoned_uploads([marker(sid, original, "photo.jpg")])
        assert files(sid) == set()
    finally:
        done(sid)
    print("PASS: a cancelled prompt takes its marked copy and the note, never a picture another copy is drawn on")


def test_adoption():
    main, task = session("Main"), session("Task")
    try:
        original = store(main, 1, "photo.jpg", PNG)
        first, second = store(main, 2, "photo-marked.png", MARKED), store(main, 3, "photo-marked.png", MARKED)
        plain = store(main, 4, "plain.png", PNG)
        lost = store(main, 5, "lost-marked.png", MARKED)
        for upload_id in (first, second):
            uploads._write_original(main, upload_id, original)
        uploads._write_original(main, lost, "1600000000000-0000000000")
        text = "Do it\n\n" + "\n".join((
            marker(main, first, "photo-marked.png"), marker(main, second, "photo-marked.png"),
            marker(main, plain, "plain.png"), marker(main, lost, "lost-marked.png")))
        adopted = uploads.adopt_attachments(main, task, text)
        assert str(root(main)) not in adopted and adopted.count(str(root(task))) == 4
        assert files(task) == {first, second, plain, lost, original, "o" + first, "o" + second}, files(task)
        for upload_id in (first, second):
            found = uploads._original_file(task, upload_id)
            assert found.read_bytes() == PNG and found.parent.name == original
            assert stat.S_IMODE(found.lstat().st_mode) == 0o600
        # the originals belong to the task now: Main's deletion changes nothing
        uploads.remove_session_storage(main)
        assert uploads._original_file(task, first).read_bytes() == PNG
    finally:
        done(task)
        done(main)

    # the original named beside its own marked copy is copied once
    main, task = session("Main"), session("Task")
    try:
        original, marked = store(main, 1), store(main, 2, "photo-marked.png", MARKED)
        uploads._write_original(main, marked, original)
        uploads.adopt_attachments(main, task, marker(main, marked, "photo-marked.png") + "\n" +
                                  marker(main, original, "photo.jpg"))
        assert files(task) == {original, marked, "o" + marked}
        # an original that cannot be copied is left out; the task has its message
        uploads.remove_session_storage(task)
        real = operations.copyfileobj

        def full(reader, writer, size):
            if reader.read(len(PNG)) == PNG:
                raise OSError("disk full")
            reader.seek(0)
            return real(reader, writer, size)

        with patch.object(operations, "copyfileobj", side_effect=full):
            uploads.adopt_attachments(main, task, marker(main, marked, "photo-marked.png"))
        assert files(task) == {marked}, files(task)
        # a message that cannot be taken over leaves nothing, notes included
        uploads.remove_session_storage(task)
        twin = store(main, 3, "photo-marked.png", MARKED)
        uploads._write_original(main, twin, original)
        write, written = uploads._write_original, []

        def stopped(*args):
            if written:
                raise operations.Cancelled("stopped")
            written.append(write(*args))
            assert "o" + marked in files(task)

        with patch.object(uploads, "_write_original", side_effect=stopped):
            try:
                uploads.adopt_attachments(main, task, marker(main, marked, "photo-marked.png") + "\n" +
                                          marker(main, twin, "photo-marked.png"))
            except operations.Cancelled:
                pass
            else:
                raise AssertionError("a cancelled adoption")
        assert written and files(task) == set()
    finally:
        done(task)
        done(main)
    print("PASS: a task takes the picture under a marked copy along - once, with its note - keeps its message when it "
          "cannot, and leaves nothing of a cancelled one")


async def send(client, sid, headers, raw, name, original=None):
    values = dict(headers, **{"Content-Type": "image/png", "X-Puppy-Filename": name})
    if original is not None:
        values["X-Puppy-Original"] = original
    async with client.post("/api/sessions/{}/upload".format(sid), data=raw, headers=values) as response:
        return response.status, await response.json()


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    sid, other = session(), session("Other")
    async with TestClient(TestServer(app)) as client:
        try:
            async with client.get("/api/ping", headers=headers) as response:
                assert protocol.UPLOAD_ORIGINALS_CAPABILITY in (await response.json())["capabilities"]

            async def asked(upload_id, session_id=None, status=200):
                async with client.get("/api/sessions/{}/upload/{}/original".format(
                        session_id or sid, upload_id), headers=headers) as response:
                    assert response.status == status, (response.status, await response.text())
                    body = await response.json() if response.content_type == "application/json" else {}
                    if status == 200:
                        assert response.headers["Cache-Control"] == "no-store"
                    return body

            async def discard(upload_id):
                async with client.delete("/api/sessions/{}/upload/{}".format(sid, upload_id),
                                         headers=headers) as response:
                    return response.status, await response.json()

            status, base = await send(client, sid, headers, PNG, "photo.png")
            assert status == 200 and base["original"] == ""
            assert await asked(base["upload_id"]) == {"original": None}, "a picture is its own original"
            status, marked = await send(client, sid, headers, MARKED, "photo-marked.png", base["upload_id"])
            assert status == 200 and marked["original"] == base["upload_id"]
            assert await asked(marked["upload_id"]) == {"original": {
                "upload_id": base["upload_id"], "path": base["path"], "name": "photo.png", "size": len(PNG),
                "content_type": "image/png"}}
            # the preview route serves it, an upload like any other
            async with client.get("/api/sessions/{}/upload/{}?preview=1".format(sid, base["upload_id"]),
                                  headers=headers) as response:
                assert response.status == 200 and await response.read() == PNG
            # marks on the marked copy lead back to the same picture
            status, again = await send(client, sid, headers, MARKED, "photo-marked.png", marked["upload_id"])
            assert status == 200 and again["original"] == base["upload_id"]
            # what cannot be noted never costs the upload
            for missing in ("1600000000000-0123456789",):
                status, body = await send(client, sid, headers, MARKED, "x-marked.png", missing)
                assert status == 200 and body["original"] == "" and Path(body["path"]).read_bytes() == MARKED
                assert await asked(body["upload_id"]) == {"original": None}
            status, text = await send(client, sid, headers, b"words", "notes.txt")
            status, body = await send(client, sid, headers, MARKED, "x-marked.png", text["upload_id"])
            assert status == 200 and body["original"] == "", "a text file is no picture's original"
            # another conversation's upload is not this one's to name
            status, body = await send(client, other, headers, MARKED, "x-marked.png", base["upload_id"])
            assert status == 200 and body["original"] == ""
            for bad in ("../../etc", "o" + base["upload_id"], "t" + base["upload_id"], base["upload_id"] + "0", "x"):
                before = files(sid)
                status, body = await send(client, sid, headers, MARKED, "x.png", bad)
                assert status == 400 and files(sid) == before, (bad, status)
            # refusals: no session, no such upload, not an upload id, not signed in
            await asked(base["upload_id"], 999999, 404)
            assert await asked("1600000000000-0123456789") == {"original": None}
            await asked("t1600000000000-0123456789", status=404)
            async with client.get("/api/sessions/{}/upload/{}/original".format(sid, marked["upload_id"])) as response:
                assert response.status == 401
            # a spoiled note is a refusal, never another picture
            path = note(sid, marked["upload_id"])
            path.write_text("z" * 24)
            await asked(marked["upload_id"], status=409)
            path.write_text(base["upload_id"])

            # a picture stays while a marked copy of it is stored, and goes after the last one
            status, body = await discard(base["upload_id"])
            assert status == 200 and body == {"ok": True, "removed": False, "referenced": True}
            status, body = await discard(marked["upload_id"])
            assert status == 200 and body["removed"] and not path.exists()
            status, body = await discard(base["upload_id"])
            assert body.get("referenced"), "the second copy still holds it"
            status, body = await discard(again["upload_id"])
            assert body["removed"]
            status, body = await discard(base["upload_id"])
            assert body["removed"] and not Path(base["path"]).exists()

            # picked two days ago and marked now: the upload that runs the sweep keeps it
            waiting = store(sid, 500, "waiting.png", PNG, age=2 * DAY)
            uploads._last_sweep = 0.0
            with patch.object(uploads, "SWEEP_INTERVAL_SECONDS", 0.0):
                status, body = await send(client, sid, headers, MARKED, "waiting-marked.png", waiting)
            assert status == 200 and body["original"] == waiting and waiting in files(sid)
            # and while the copy is only drafted, two days later, both are there still
            db.set_session_draft(sid, marker(sid, body["upload_id"], "waiting-marked.png"))
            for name in (waiting, body["upload_id"], "o" + body["upload_id"]):
                aged(root(sid) / name)
            uploads.sweep_orphans(sid)
            assert {waiting, body["upload_id"], "o" + body["upload_id"]} <= files(sid)
            assert (await asked(body["upload_id"]))["original"]["upload_id"] == waiting

            # an orphan sweep's own files, an upload's hidden partial and the notes are never served
            async with client.get("/api/sessions/{}/upload/o{}".format(sid, body["upload_id"]),
                                  headers=headers) as response:
                assert response.status in (400, 404)
            async with client.delete("/api/sessions/{}".format(sid), headers=headers) as response:
                assert response.status == 200, await response.text()
            assert not root(sid).exists(), "the notes go with their session"
        finally:
            for value in (sid, other):
                done(value)
    print("PASS: {} notes what a marked copy was drawn on, says so, keeps it while the copy is stored and forgets it "
          "with the copy and the session".format(app["puppy_role"]))


async def proxy_contract():
    """A controller passes the header to the backend and its answer back."""
    node = backend_app()
    node.on_startup.clear()
    node.on_shutdown.clear()
    node.on_cleanup.clear()
    server = TestServer(node)
    await server.start_server()
    controller = build_app()
    controller.on_startup.clear()
    controller.on_shutdown.clear()
    controller.on_cleanup.clear()
    token = config.get("auth.api_token")
    headers = {"X-Puppy-Token": token}
    sid = session("Relayed")
    try:
        url = str(server.make_url("")).rstrip("/")
        db.execute("INSERT INTO backends(name,url,urls,token,protocol,capabilities,created_at) VALUES(?,?,?,?,?,?,?)",
                   ("originals-node", url, json.dumps([url]), token, protocol.API_PROTOCOL,
                    json.dumps(list(protocol.BASE_CAPABILITIES)), time.time()))
        bid = backends.list_backends()[0]["id"]
        backends._mark_backend_online(bid)
        async with TestClient(TestServer(controller)) as client:
            async def send_through(raw, name, original=None):
                values = dict(headers, **{"Content-Type": "image/png", "X-Puppy-Filename": name})
                if original:
                    values["X-Puppy-Original"] = original
                async with client.post("/api/b/{}/sessions/{}/upload".format(bid, sid), data=raw,
                                       headers=values) as response:
                    assert response.status == 200, await response.text()
                    return await response.json()

            base = await send_through(PNG, "photo.png")
            marked = await send_through(MARKED, "photo-marked.png", base["upload_id"])
            assert marked["original"] == base["upload_id"]
            async with client.get("/api/b/{}/sessions/{}/upload/{}/original".format(
                    bid, sid, marked["upload_id"]), headers=headers) as response:
                assert response.status == 200
                assert (await response.json())["original"]["path"] == base["path"]
    finally:
        await backends.close_client()
        await server.close()
        db.execute("DELETE FROM backends")
        done(sid)
    print("PASS: a controller relays what a marked copy was drawn on to the backend, and the answer back")


async def main():
    config.load()
    db.connect()
    test_notes()
    test_sweep()
    test_discards()
    test_adoption()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)
    await proxy_contract()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        import shutil
        shutil.rmtree(str(ROOT), ignore_errors=True)
