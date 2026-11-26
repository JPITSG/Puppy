#!/usr/bin/env python3
"""Previews: what an agent built, shown to the user through Puppy.

What may be shown (a folder or a file in the session's working folder or the
temporary folder, with a root holding it; never the home or system folder
whole, Puppy's own data, an engine CLI's home, hidden names or system
locations), the exact record (shapes refused, never repaired, ids and tokens
unique on the node, the limit, gone with its session), serving on both
runtimes (the sandbox headers on every answer, index pages, listings, the
slash a folder needs, links that lead out, hidden names, revalidation,
gzip, HEAD, a wrong token saying nothing, a restore in progress), a
controller relaying a real headless backend's preview at its public route,
the turn-bound bridge (identity checks, show, reload, list, close, the
activity a console hears, the socket and the stdio MCP server), every driver
carrying it with its editable policy, and the policy's own optional record.
No engine, network or model quota is used.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import warnings
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("previews-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import (backends, config, db, places, preview_agent, previews,  # noqa: E402
                   protocol, runner, system_prompts)
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

TOKEN = "previews-test-token-0123456789abcdef0123"
PAGE = b"<!doctype html><title>Plate</title><script src=\"app.js\"></script>"
SCRIPT = b"fetch('model/plate.stl').then(r => r.arrayBuffer());" * 64


def project(name):
    path = ROOT / name
    (path / "viewer" / "model").mkdir(parents=True)
    (path / "viewer" / "index.html").write_bytes(PAGE)
    (path / "viewer" / "app.js").write_bytes(SCRIPT)
    (path / "viewer" / "model" / "plate.stl").write_bytes(b"solid plate\nendsolid plate\n")
    (path / "viewer" / ".secret").write_text("token")
    (path / "viewer" / "notes").mkdir()
    (path / "viewer" / "notes" / "a b.txt").write_text("hello")
    (path / "shots").mkdir()
    (path / "shots" / "one.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (path / ".hidden").mkdir()
    (path / ".hidden" / "page.html").write_text("<p>hidden</p>")
    return path


async def resolve(cwd, path, root=None):
    here = await places.visit(str(cwd))
    target = await places.visit(previews.named(str(cwd), path))
    top = await places.visit(previews.named(str(cwd), root)) if root else None
    return await places.ask(target.real, previews.resolve, here.real, target.real,
                            top.real if top else None)


async def refused(call, words):
    try:
        await call
    except previews.PreviewError as exc:
        assert words in str(exc), (words, str(exc))
        return
    raise AssertionError("expected a refusal naming: " + words)


async def test_policy():
    path = project("policy")
    found = await resolve(path, "viewer")
    assert (found["base"], found["root"], found["entry"], found["folder"]) == \
        ("cwd", "viewer", "", True)
    found = await resolve(path, "viewer/model/plate.stl")
    assert (found["root"], found["entry"], found["folder"]) == ("viewer/model", "plate.stl", False)
    found = await resolve(path, "viewer/model/plate.stl", root="viewer")
    assert (found["root"], found["entry"]) == ("viewer", "model/plate.stl")
    found = await resolve(path, "viewer/notes", root="viewer")
    assert (found["root"], found["entry"], found["folder"]) == ("viewer", "notes/", True)
    found = await resolve(path, str(path / "viewer" / "index.html"))
    assert (found["root"], found["entry"]) == ("viewer", "index.html")
    # the working folder itself is a root, unless it is the home or system folder
    found = await resolve(path, ".")
    assert (found["base"], found["root"], found["entry"]) == ("cwd", "", "")
    await refused(resolve("/", "/"), "the whole home or system folder")
    # the temporary folder, but never the whole of it
    temp = Path(previews.shared_files.temp_root()) / "puppy-preview-test-{}".format(os.getpid())
    temp.mkdir(exist_ok=True)
    try:
        (temp / "index.html").write_text("<p>temp</p>")
        found = await resolve(path, str(temp))
        assert (found["base"], found["root"]) == ("temp", temp.name)
        await refused(resolve(path, previews.shared_files.temp_root()), "only folders inside")
    finally:
        shutil.rmtree(str(temp))
    await refused(resolve(path, "missing"), "there is nothing at that path")
    await refused(resolve(path, "viewer/model/plate.stl", root="shots"),
                  "inside the root folder")
    await refused(resolve(path, "viewer/index.html", root="viewer/index.html"), "root must be a folder")
    await refused(resolve(path, str(ROOT)), "only folders inside")
    await refused(resolve(path, ".hidden"), "hidden files and folders are never served")
    await refused(resolve(path, ".hidden/page.html"), "hidden files and folders")
    await refused(resolve(path, "viewer/.secret"), "hidden files and folders")
    # a link into Puppy's own data is followed and refused
    os.symlink(config.DATA_DIR, str(path / "data-link"))
    await refused(resolve(path, "data-link"), "only folders inside")
    inside = ROOT / "data" / "workspaces" / "session-x"
    inside.mkdir(parents=True)
    (inside / "page.html").write_text("x")
    await refused(resolve(path, str(inside)), "only folders inside")
    # a session working in a workspace Puppy manages may show it
    found = await resolve(inside, ".")
    assert found["base"] == "cwd" and found["root"] == ""
    fifo = path / "pipe"
    os.mkfifo(str(fifo))
    await refused(resolve(path, "pipe"), "only a folder or a regular file")
    print("PASS: a preview is a folder or a file in the working or temporary folder, with a root "
          "holding it; never the whole home or system folder, Puppy's data, hidden names or devices")


def test_records():
    path = project("records")
    sid = db.create_session("Records", "claude", str(path), "", "", "", "default")
    other = db.create_session("Other", "claude", str(path), "", "", "", "default")
    try:
        found = {"base": "cwd", "root": "viewer", "entry": "", "real_root": str(path / "viewer"),
                 "folder": True}
        item, new = previews.remember(sid, found)
        assert new and item["name"] == "viewer" and previews.PREVIEW_ID.fullmatch(item["id"])
        assert previews.TOKEN.fullmatch(item["token"])
        again, new = previews.remember(sid, found, "Plate viewer")
        assert not new and again["id"] == item["id"] and again["token"] == item["token"]
        assert again["name"] == "Plate viewer" and again["shown_at"] >= item["shown_at"]
        kept, _ = previews.remember(sid, found)
        assert kept["name"] == "Plate viewer", "a reload without a name keeps it"
        page, _ = previews.remember(sid, dict(found, entry="model/plate.stl"))
        assert page["name"] == "plate.stl" and page["id"] != item["id"]
        index, _ = previews.remember(sid, dict(found, entry="index.html"))
        assert index["name"] == "viewer", "an index page is named for its folder"
        assert [entry["id"] for entry in previews.previews_of(sid)] == \
            [item["id"], page["id"], index["id"]]
        assert previews.where(db.get_session(sid), page) == "viewer/model/plate.stl"
        assert previews.where(db.get_session(sid), dict(item, root="")) == "./"
        assert previews.markdown_link(again) == "[Plate viewer](puppy-preview:{})".format(item["id"])
        assert previews.markdown_link(dict(again, name="a]b")) == \
            "[a\\]b](puppy-preview:{})".format(item["id"])
        assert previews.find(item["token"]) == (sid, previews.previews_of(sid)[0])
        assert previews.find("x" * 32) is None and previews.find("short") is None
        other_item, _ = previews.remember(other, found)
        assert other_item["id"] != item["id"] and other_item["token"] != item["token"]
        previews.validate_persisted(db.connect())
        # the limit
        for index_number in range(previews.MAX_PREVIEWS - 3):
            previews.remember(sid, dict(found, entry="n{}.html".format(index_number)))
        try:
            previews.remember(sid, dict(found, entry="one-too-many.html"))
        except previews.PreviewError as exc:
            assert "close one the user no longer needs" in str(exc)
        else:
            raise AssertionError("the limit must hold")
        assert previews.forget(sid, page["id"]) and not previews.forget(sid, page["id"])
        # exact shapes, refused rather than repaired
        record = db.meta_get(previews._key(sid))
        good = json.loads(json.dumps(record))

        def refuses(value, key=previews._key(sid)):
            db.meta_set(key, value)
            try:
                previews.validate_persisted(db.connect())
            except ValueError:
                pass
            else:
                raise AssertionError("accepted: {!r}".format(value)[:200])
            finally:
                db.meta_set(previews._key(sid), good)
                if key != previews._key(sid):
                    db.meta_del(key)

        first = good["items"][0]
        refuses(dict(good, format=2))
        refuses(dict(good, extra=1))
        refuses({"format": 1, "items": []})
        refuses(dict(good, items=[dict(first, id="ab12")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, token="t")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, base="home")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, root="../x")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, root="a/.git")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, root="viewer/")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, base="temp", root="")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, entry="/abs")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, name="")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, name=" padded")] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, created_at=True)] + good["items"][1:]))
        refuses(dict(good, items=[dict(first, shown_at=first["created_at"] - 1)] +
                     good["items"][1:]))
        refuses(dict(good, items=list(reversed(good["items"]))))
        refuses(dict(good, items=good["items"] + [dict(first, token="t" * 32)]))
        refuses(dict(good, items=[dict(first, token=other_item["token"])] + good["items"][1:]))
        refuses(good, key=previews.PREFIX + "999999")
        refuses(good, key=previews.PREFIX + "01")
        previews.validate_persisted(db.connect())
        # deleting a session takes its previews with it
        runner.drop_hub(other)
        db.delete_session(other)
        assert db.meta_get(previews._key(other)) is None
        assert previews.find(other_item["token"]) is None
    finally:
        for session in (sid, other):
            runner.drop_hub(session)
            if db.get_session(session):
                db.delete_session(session)
    print("PASS: previews keep one ID and token per page, reload in place, hold to their limit, "
          "refuse every other record shape, and go with their session")


def busy(app, value):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # a started app's state
        app["puppy_snapshot_busy"] = value


def check_preview_headers(response, cache="private, no-cache"):
    for key, value in previews.PREVIEW_HEADERS.items():
        if key == "Cache-Control":
            value = cache
        assert response.headers.get(key) == value, (key, response.headers.get(key),
                                                    str(response.url))
    assert "Set-Cookie" not in response.headers


async def serving(client, prefix, auth=None):
    """One preview's files as a page loads them, at ``prefix`` (``.../<token>/``)."""
    headers = dict(auth or {})
    async with client.get(prefix, headers=headers) as response:
        assert response.status == 200, (prefix, response.status, await response.text())
        check_preview_headers(response)
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert await response.read() == PAGE
        etag = response.headers["ETag"]
    async with client.get(prefix + "index.html", headers=dict(headers, **{
            "If-None-Match": etag})) as response:
        assert response.status == 304 and response.headers["ETag"] == etag
        check_preview_headers(response)
    async with client.get(prefix + "app.js", headers=dict(headers, **{
            "Accept-Encoding": "gzip"}), auto_decompress=False) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
        assert response.headers["Content-Encoding"] == "gzip"
        assert response.headers["ETag"].endswith('-gz"')
        assert gzip.decompress(await response.read()) == SCRIPT
        packed = response.headers["ETag"]
    async with client.get(prefix + "app.js", headers=dict(headers, **{
            "Accept-Encoding": "gzip", "If-None-Match": packed})) as response:
        assert response.status == 304
    async with client.get(prefix + "app.js", headers=dict(headers, **{
            "Accept-Encoding": "identity"})) as response:
        assert "Content-Encoding" not in response.headers
        assert await response.read() == SCRIPT
    async with client.get(prefix + "model/plate.stl", headers=headers) as response:
        assert response.status == 200 and response.headers["Content-Type"] == "model/stl"
        assert await response.read() == b"solid plate\nendsolid plate\n"
    async with client.head(prefix + "model/plate.stl", headers=headers) as response:
        assert response.status == 200 and response.headers["Content-Length"] == "27"
        assert await response.read() == b""
    # a folder needs its slash for the relative links of what opens there
    async with client.get(prefix + "notes", headers=headers,
                          allow_redirects=False) as response:
        assert response.status == 301 and response.headers["Location"] == "notes/"
        check_preview_headers(response, "no-store")
    async with client.get(prefix + "notes/", headers=headers) as response:
        assert response.status == 200
        listing = await response.text()
        assert 'href="a%20b.txt"' in listing and "a b.txt" in listing and "../" in listing
        check_preview_headers(response, "no-store")
    async with client.get(prefix + "notes/a%20b.txt", headers=headers) as response:
        assert response.status == 200 and await response.text() == "hello"
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    # nothing else: hidden names, the way up, a link out of the folder
    for path in (".secret", "notes/../.secret", "..%2findex.html",
                 "model/plate.stl/", "missing.html", "escape/passwd", "notes//a%20b.txt"):
        async with client.get(prefix + path, headers=headers,
                              allow_redirects=False) as response:
            assert response.status == 404, (path, response.status)
            body = await response.text()
            assert body == "Not found", (path, body)
            assert response.headers["Cache-Control"] == "no-store", path
            check_preview_headers(response, "no-store")


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    auth = {"X-Puppy-Token": TOKEN}
    path = project("routes-" + app["puppy_role"])
    os.symlink("/etc", str(path / "viewer" / "escape"))
    sid = db.create_session("Routes", "claude", str(path), "", "", "", "default")
    other = db.create_session("Other routes", "claude", str(path), "", "", "", "default")
    full = app["puppy_role"] == "full"
    try:
        assert protocol.PREVIEWS_CAPABILITY in protocol.execution_capabilities()
        found = await resolve(path, "viewer")
        item, _ = previews.remember(sid, found, "Plate viewer")
        async with TestClient(TestServer(app)) as client:
            listed_url = "/api/sessions/{}/previews".format(sid)
            async with client.get(listed_url) as response:
                assert response.status == 401
            async with client.get(listed_url, headers=auth) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                listed = (await response.json())["previews"]
            assert [(entry["id"], entry["name"], entry["token"], entry["entry"], entry["where"])
                    for entry in listed] == [(item["id"], "Plate viewer", item["token"], "",
                                              "viewer/")]
            assert listed[0]["folder"] == str(path / "viewer")
            node = "/api/previews/{}/".format(item["token"])
            async with client.get(node) as response:
                assert response.status == 401, "the node's own route is behind its sign-in"
            await serving(client, node, auth)
            if full:
                public = "/preview/0/{}/".format(item["token"])
                await serving(client, public)
                async with client.get(public[:-1], allow_redirects=False) as response:
                    assert response.status == 301
                    assert response.headers["Location"] == item["token"] + "/"
                # a wrong token, or an unknown backend, says nothing about this Puppy
                for wrong in ("/preview/0/{}/".format("A" * 32), "/preview/0/short/",
                              "/preview/77/{}/".format(item["token"])):
                    async with client.get(wrong) as response:
                        assert response.status == 404 and await response.text() == "Not found"
                busy(client.server.app, "restore")
                async with client.get(public) as response:
                    assert response.status == 503
                busy(client.server.app, None)
            else:
                async with client.get("/preview/0/{}/".format(item["token"])) as response:
                    assert response.status in (401, 404), "the headless node has no public route"
            # a folder that is gone, then back
            shutil.move(str(path / "viewer"), str(path / "moved"))
            async with client.get(node, headers=auth) as response:
                assert response.status == 404
            shutil.move(str(path / "moved"), str(path / "viewer"))
            # a folder without an index is listed; hidden names never are
            shots, _ = previews.remember(sid, await resolve(path, "shots"))
            os.symlink("/etc", str(path / "shots" / "outside"))
            os.symlink("one.png", str(path / "shots" / "again.png"))
            os.mkfifo(str(path / "shots" / "pipe"))
            async with client.get("/api/previews/{}/".format(shots["token"]),
                                  headers=auth) as response:
                text = await response.text()
                assert response.status == 200 and "one.png" in text and "../" not in text
                assert "again.png" in text, "a link inside the preview is listed"
                assert "outside" not in text and "pipe" not in text, \
                    "nor a link out of it, nor anything but files and folders"
            whole, _ = previews.remember(sid, await resolve(path, "."))
            async with client.get("/api/previews/{}/".format(whole["token"]),
                                  headers=auth) as response:
                text = await response.text()
                assert "viewer/" in text and ".hidden" not in text
            async with client.get("/api/previews/{}/.hidden/page.html".format(whole["token"]),
                                  headers=auth) as response:
                assert response.status == 404
            # the console ends one; another session's id is not this one's
            async with client.delete("/api/sessions/{}/previews/{}".format(other, item["id"]),
                                     headers=auth) as response:
                assert response.status == 404
            heard = []
            with patch.object(runner.SessionHub, "broadcast",
                              lambda self, payload: heard.append(payload)):
                async with client.delete("/api/sessions/{}/previews/{}".format(sid, item["id"]),
                                         headers=auth) as response:
                    assert response.status == 200, await response.text()
            assert heard and heard[0]["type"] == "preview_activity" and \
                heard[0]["closed"] is True and heard[0]["preview_id"] == item["id"]
            async with client.get(node, headers=auth) as response:
                assert response.status == 404
            # deleting the session ends the rest
            async with client.delete("/api/sessions/{}".format(sid), headers=auth) as response:
                assert response.status == 200, await response.text()
            async with client.get("/api/previews/{}/".format(shots["token"]),
                                  headers=auth) as response:
                assert response.status == 404
    finally:
        for session in (sid, other):
            runner.drop_hub(session)
            if db.get_session(session):
                db.delete_session(session)
    print("PASS: {} serves a preview sandboxed by its token alone - index, listing, slash, "
          "revalidation, gzip, HEAD - and nothing hidden or outside; the console lists and ends "
          "previews behind its sign-in".format(app["puppy_role"]))


def add_backend(name, url):
    db.execute("INSERT INTO backends(name,url,urls,token,protocol,capabilities,created_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (name, url, json.dumps([url]), TOKEN, protocol.API_PROTOCOL,
                json.dumps([protocol.PREVIEWS_CAPABILITY]), time.time()))
    bid = {item["name"]: item["id"] for item in backends.list_backends()}[name]
    backends._mark_backend_online(bid)
    return bid


async def relay_contract():
    """A controller relays a real headless backend's preview at its public route."""
    node = backend_app()
    node.on_startup.clear()
    node.on_shutdown.clear()
    node.on_cleanup.clear()
    node_server = TestServer(node)
    await node_server.start_server()
    controller = build_app()
    controller.on_startup.clear()
    controller.on_shutdown.clear()
    controller.on_cleanup.clear()
    path = project("relay")
    sid = db.create_session("Relay", "claude", str(path), "", "", "", "default")
    try:
        real = add_backend("preview-node", str(node_server.make_url("")).rstrip("/"))
        gone = add_backend("gone-node", "http://127.0.0.1:1")
        item, _ = previews.remember(sid, await resolve(path, "viewer"))
        async with TestClient(TestServer(controller)) as client:
            await serving(client, "/preview/{}/{}/".format(real, item["token"]))
            async with client.get("/preview/{}/{}/".format(real, "B" * 32)) as response:
                assert response.status == 404 and await response.text() == "Not found"
            async with client.get("/preview/{}/{}/".format(gone, item["token"])) as response:
                assert response.status == 503
                text = await response.text()
                assert "gone-node" not in text, "the public route names no backend"
            assert not backends.backend_is_online(gone)
            async with client.get("/preview/{}/{}/".format(gone, item["token"])) as response:
                assert response.status == 503, "and is not asked again"
            # the list reaches the console through the ordinary authenticated proxy
            async with client.get("/api/b/{}/sessions/{}/previews".format(real, sid),
                                  headers={"X-Puppy-Token": TOKEN}) as response:
                assert response.status == 200
                assert (await response.json())["previews"][0]["token"] == item["token"]
    finally:
        await backends.close_client()
        await node_server.close()
        db.execute("DELETE FROM backends")
        runner.drop_hub(sid)
        db.delete_session(sid)
    print("PASS: a controller streams a backend's preview at /preview/<backend>/<token>/ - "
          "revalidation, gzip and the folder slash included - and names no backend when it "
          "cannot reach one")


def hold_turn(sid, turn):
    hub = runner.hub(sid)
    hub.status = "running"
    hub._active_turn_id = turn
    return hub


def release_turn(hub):
    hub.status = "idle"
    hub._active_turn_id = None


async def bridge_contract():
    path = project("bridge")
    sid = db.create_session("Bridge", "claude", str(path), "", "", "", "default")
    app = {}
    assert preview_agent.turn_mcp(sid, "turn-a") is None, "no descriptor while the bridge is down"
    await preview_agent.start(app)
    hub = hold_turn(sid, "turn-a")
    heard = []
    try:
        descriptor = preview_agent.turn_mcp(sid, "turn-a")
        socket_path = Path(descriptor["env"]["PUPPY_PREVIEW_SOCKET"])
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        assert descriptor["args"] == ["-m", "puppy.preview_agent"]
        assert descriptor["read_only_tools"] == ["previews"]
        assert descriptor["engine_guidance"] == config.DEFAULT_PREVIEW_SYSTEM_PROMPT
        assert "guidance_in_instructions" not in descriptor
        assert len(preview_agent.TOOL_INSTRUCTIONS) <= 4000

        def call(method, params, **extra):
            return preview_agent._dispatch(dict(
                {"session_id": sid, "turn_id": "turn-a", "method": method, "params": params},
                **extra))

        with patch.object(runner.SessionHub, "broadcast",
                          lambda self, payload: heard.append(payload)):
            shown = (await call("show_preview", {"path": "viewer", "name": "Plate viewer"}))["text"]
            lines = shown.split("\n")
            item = previews.previews_of(sid)[0]
            assert lines[0] == "Showing Plate viewer as Preview {} in a tab beside this chat.".format(
                item["id"])
            assert lines[1] == "Serves: viewer/ (its index.html, or a listing of the folder)"
            assert lines[2] == "Markdown link: [Plate viewer](puppy-preview:{})".format(item["id"])
            assert item["token"] not in shown, "the token never reaches the agent"
            again = (await call("show_preview", {"path": "./viewer/"}))["text"]
            assert again.startswith("Reloaded Preview {} (Plate viewer)".format(item["id"]))
            page = (await call("show_preview", {"path": "viewer/model/plate.stl",
                                                "root": "viewer"}))["text"]
            assert "Serves: viewer/model/plate.stl\n" in page
            listed = (await call("previews", {}))["text"]
            assert listed.startswith("2 previews (newest last):")
            assert "{}  Plate viewer  viewer/  shown just now".format(item["id"]) in listed
            closed = (await call("close_preview", {"preview_id": item["id"].lower()}))["text"]
            assert closed == "Closed Preview {}: its link and tab no longer show it.".format(
                item["id"])
        assert heard[0]["name"] == "Plate viewer" and "name" not in heard[-1]
        assert [(event["preview_id"], event.get("closed", False)) for event in heard] == \
            [(item["id"], False), (item["id"], False),
             (previews.previews_of(sid)[0]["id"], False), (item["id"], True)]
        assert all(event["turn_id"] == "turn-a" for event in heard)
        assert len({event["event"] for event in heard}) == 4, "each one is heard once"

        async def refused_call(request, words):
            try:
                await preview_agent._dispatch(request)
            except preview_agent.PreviewAgentError as exc:
                assert words in str(exc), (words, str(exc))
                return
            raise AssertionError("expected a refusal naming: " + words)

        base = {"session_id": sid, "turn_id": "turn-a", "method": "show_preview",
                "params": {"path": "viewer"}}
        await refused_call(dict(base, turn_id="turn-b"), "no longer running")
        await refused_call(dict(base, turn_id=""), "invalid preview tool turn identity")
        await refused_call(dict(base, session_id="x"), "invalid originating session identity")
        await refused_call(dict(base, session_id=999999), "no longer exists")
        await refused_call(dict(base, method="open_preview"), "unknown preview tool")
        await refused_call(dict(base, params={"path": "viewer", "port": 8080}),
                           "show_preview does not take port")
        await refused_call(dict(base, params={"path": "/etc"}), "only folders inside")
        await refused_call(dict(base, params={"path": ""}), "give the path")
        await refused_call(dict(base, method="close_preview", params={"preview_id": "ZZZZ"}),
                           "no Preview ZZZZ")
        await refused_call(dict(base, method="close_preview", params={"preview_id": "toolong"}),
                           "four-character ID")
        app["puppy_snapshot_busy"] = "export"
        await refused_call(base, "backup or restore is in progress")
        app["puppy_snapshot_busy"] = None
        # through the real socket, as the MCP child calls it
        environment = {"PUPPY_PREVIEW_SOCKET": str(socket_path),
                       "PUPPY_PREVIEW_SESSION_ID": str(sid), "PUPPY_PREVIEW_TURN_ID": "turn-a"}
        with patch.dict(os.environ, environment):
            text = (await asyncio.get_running_loop().run_in_executor(
                None, preview_agent._bridge_call, "previews", {}))["text"]
            assert text.startswith("1 preview (newest last):")
        # the stdio MCP server the engine starts
        child = await asyncio.create_subprocess_exec(
            descriptor["command"], *descriptor["args"], cwd=str(ROOT),
            env=dict(os.environ, **descriptor["env"]),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

        async def rpc(number, method, params=None):
            child.stdin.write((json.dumps({"jsonrpc": "2.0", "id": number, "method": method,
                                           "params": params or {}}) + "\n").encode())
            await child.stdin.drain()
            return json.loads(await asyncio.wait_for(child.stdout.readline(), 15))

        initialized = (await rpc(1, "initialize", {"protocolVersion": "2025-06-18"}))["result"]
        assert initialized["serverInfo"]["name"] == "Puppy previews"
        assert initialized["instructions"] == preview_agent.TOOL_INSTRUCTIONS
        tools = (await rpc(2, "tools/list"))["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["show_preview", "previews", "close_preview"]
        assert tools[0]["inputSchema"]["required"] == ["path"]
        assert tools[1]["annotations"]["readOnlyHint"] is True
        assert tools[2]["annotations"]["destructiveHint"] is True
        with patch.object(runner.SessionHub, "broadcast", lambda self, payload: None):
            shown = (await rpc(3, "tools/call", {"name": "show_preview",
                                                 "arguments": {"path": "shots"}}))["result"]
        assert shown["isError"] is False and "puppy-preview:" in shown["content"][0]["text"]
        failed = (await rpc(4, "tools/call", {"name": "show_preview",
                                              "arguments": {"path": str(ROOT)}}))["result"]
        assert failed["isError"] is True and "only folders inside" in failed["content"][0]["text"]
        child.stdin.close()
        assert await asyncio.wait_for(child.wait(), 10) == 0
    finally:
        release_turn(hub)
        await preview_agent.stop(app)
        runner.drop_hub(sid)
        db.delete_session(sid)
    assert not Path(preview_agent.socket_path()).exists()
    print("PASS: the turn-bound bridge checks its turn and fields, shows, reloads, lists and "
          "closes previews, announces each to the console, never hands the agent a token, and "
          "answers over its socket and as a stdio MCP server")


def descriptors():
    """Every driver hands the bridge to its engine with the editable policy."""
    from puppy.drivers.claude import ClaudeDriver
    from puppy.drivers.codex import CodexDriver
    from puppy.drivers.opencode import PUPPY_AGENT, OpenCodeDriver
    preview_agent._server = object()
    try:
        descriptor = preview_agent.turn_mcp(5, "turn")
        policy = config.DEFAULT_PREVIEW_SYSTEM_PROMPT
        session = {"cwd": str(ROOT)}
        claude = ClaudeDriver().build_cmd(session, True, "p", "t", preview_mcp=descriptor)
        servers = json.loads(claude[claude.index("--mcp-config") + 1])["mcpServers"]
        assert servers["puppy_preview"]["args"] == ["-m", "puppy.preview_agent"]
        assert claude[claude.index("--append-system-prompt") + 1] == policy
        assert claude[claude.index("--allowedTools") + 1] == "mcp__puppy_preview__previews"
        codex = CodexDriver()
        argv = codex.build_cmd(session, True, "p", "t", preview_mcp=descriptor)
        assert any(arg.startswith("mcp_servers.puppy_preview.command=") for arg in argv)
        prompt = codex.turn_context(session, True, "p", "t", preview_mcp=descriptor)["prompt"]
        assert "<puppy_preview_policy>\n" + policy + "\n</puppy_preview_policy>" in prompt
        opencode = OpenCodeDriver()
        context = opencode.turn_context(session, True, "p", "t", preview_mcp=descriptor)
        assert [server["name"] for server in context["mcp_servers"]] == ["puppy_preview"]
        assert policy in json.loads(opencode.build_env(
            session, True, "p", "t", preview_mcp=descriptor)["OPENCODE_CONFIG_CONTENT"]
        )["agent"][PUPPY_AGENT]["prompt"]
    finally:
        preview_agent._server = None
    print("PASS: claude, codex and opencode carry the preview bridge with its editable policy, "
          "listing previews allowed up front")


async def policy_record():
    """The editable policy: an optional exact record, its default while absent."""
    app = build_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    auth = {"X-Puppy-Token": TOKEN}
    default = config.DEFAULT_PREVIEW_SYSTEM_PROMPT
    assert system_prompts.preview_prompt() == default and \
        db.meta_get(system_prompts.PREVIEW_KEY) is None
    async with TestClient(TestServer(app)) as client:
        async with client.get("/api/system-prompt", headers=auth) as response:
            payload = (await response.json())["system_prompt"]
        assert payload["previews"] == default and payload["previews_default"] == default
        async with client.patch("/api/system-prompt", headers=auth,
                                json={"previews": "Show pages\r\nwith previews."}) as response:
            assert response.status == 200, await response.text()
            payload = (await response.json())["system_prompt"]
        assert payload["previews"] == "Show pages\nwith previews."
        assert db.meta_get(system_prompts.PREVIEW_KEY) == {
            "format": 1, "text": "Show pages\nwith previews."}
        preview_agent._server = object()
        try:
            assert preview_agent.turn_mcp(1, "t")["engine_guidance"] == "Show pages\nwith previews."
        finally:
            preview_agent._server = None
        # a refusal of any field writes nothing
        async with client.patch("/api/system-prompt", headers=auth,
                                json={"previews": "Other", "custom": 5}) as response:
            assert response.status == 400
        assert system_prompts.preview_prompt() == "Show pages\nwith previews."
        # config.json refusing the rest puts the record back
        with patch.object(config, "set_system_prompts", side_effect=OSError("disk full")), \
                patch.object(logging.getLogger("aiohttp.server"), "disabled", True):
            try:
                async with client.patch("/api/system-prompt", headers=auth,
                                        json={"previews": "Lost"}) as response:
                    assert response.status == 500
            except Exception:
                pass
        assert system_prompts.preview_prompt() == "Show pages\nwith previews."
        # the default again is no record at all
        async with client.patch("/api/system-prompt", headers=auth,
                                json={"previews": default}) as response:
            assert response.status == 200
        assert db.meta_get(system_prompts.PREVIEW_KEY) is None
        async with client.patch("/api/system-prompt", headers=auth,
                                json={"previews": 7}) as response:
            assert response.status == 400
    for bad in ({"format": 2, "text": "x"}, {"format": 1}, {"format": 1, "text": 3},
                {"format": 1, "text": "a\r\nb"}, {"format": 1, "text": "x", "more": 1},
                {"format": 1, "text": "x" * (config.MAX_SYSTEM_PROMPT_CHARS + 1)}, "text"):
        db.meta_set(system_prompts.PREVIEW_KEY, bad)
        try:
            system_prompts.validate_persisted(db.connect())
        except ValueError:
            pass
        else:
            raise AssertionError("accepted: {!r}".format(bad)[:100])
        preview_agent._server = object()
        try:
            assert preview_agent.turn_mcp(1, "t")["engine_guidance"] == default, \
                "a turn never fails over it"
        finally:
            preview_agent._server = None
    db.meta_del(system_prompts.PREVIEW_KEY)
    system_prompts.validate_persisted(db.connect())
    print("PASS: the preview policy is an optional exact record - Puppy's default while absent, "
          "saved and taken back with the other prompts, any other shape refused")


async def main():
    config.load()
    config.set_value("auth.api_token", TOKEN)
    db.connect()
    await test_policy()
    test_records()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)
    await relay_contract()
    await bridge_contract()
    descriptors()
    await policy_record()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
