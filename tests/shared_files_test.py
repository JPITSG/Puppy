#!/usr/bin/env python3
"""Files an agent shares into its conversation for the user to download.

What may be shared (the working folder, the temporary folder, and nothing of
Puppy's own data, an engine CLI's home, the hidden entries in the home folder
or the system's secrets; symlinks, folders, FIFOs and paths that change under
the read refused), how a share is stored (its own id form beside the uploads,
private, the name the agent gave it, the same file answered again rather than
copied, the caps per file, conversation and turn, never orphan-swept, moved to
Main with a folded task, gone with its session), the routes on both runtimes
(a download and nothing else: its headers, HEAD, ranges, refusals), a
controller relaying a backend's file as a stream under its own headers
whatever the backend says, the turn-bound bridge (its identity checks, the
backup count held until the copy ends, the socket and the stdio MCP server),
every driver carrying it, and a fixture engine speaking claude's stream-json
through the real runner that calls the bridge's MCP server itself and links
the file in its answer. No installed engine, network or model quota is used.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import warnings
from contextlib import ExitStack
from unittest.mock import patch


def fixture(mcp_config: str) -> None:
    """An engine that shares a file through Puppy's own MCP server and links it."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    server = json.loads(mcp_config)["mcpServers"]["puppy_files"]
    child = subprocess.Popen([server["command"]] + list(server["args"]), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, env=dict(os.environ, **server["env"]))

    def rpc(number, method, params):
        child.stdin.write((json.dumps({"jsonrpc": "2.0", "id": number, "method": method,
                                       "params": params}) + "\n").encode())
        child.stdin.flush()
        return json.loads(child.stdout.readline())

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-files", "tools": []})
    rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    called = rpc(2, "tools/call", {"name": "share_file", "arguments": {"path": "build/model.stl"}})
    text = called["result"]["content"][0]["text"]
    link = text.split("Markdown link: ", 1)[1].split("\n", 1)[0]
    send({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_share", "name": "mcp__puppy_files__share_file",
         "input": {"path": "build/model.stl"}}]}})
    send({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_share",
         "content": [{"type": "text", "text": text}]}]}})
    file_id = link.rsplit(":", 1)[1].rstrip(")")
    send({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "The latest STL file is [here](puppy-file:{}).".format(file_id)}]}})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-files", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    child.stdin.close()
    child.wait(timeout=10)
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture(sys.argv[2])
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("shared-files-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import (backends, config, db, files_agent, protocol, runner,  # noqa: E402
                   shared_files, uploads)
from puppy.drivers import get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

TOKEN = "shared-files-test-token-0123456789abcdef"
STL = b"solid demo\n  facet normal 0 0 1\nendsolid demo\n"


def refused(call, words):
    try:
        call()
    except shared_files.ShareError as exc:
        assert words in str(exc), (words, str(exc))
        return str(exc)
    raise AssertionError("expected a refusal naming: " + words)


def project(name):
    path = ROOT / name
    (path / "build").mkdir(parents=True)
    (path / "build" / "model.stl").write_bytes(STL)
    return path


def place():
    """The temporary folder, the CLI homes and the home folder, all in the scratch root."""
    temp = (ROOT / "tmp").resolve()
    home = (ROOT / "home").resolve()
    for path in (temp / "puppy-cli-0" / "claude", home / ".ssh", home / ".config" / "tool",
                 home / "work"):
        path.mkdir(parents=True, exist_ok=True)
    (temp / "export.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (temp / "puppy-cli-0" / "claude" / ".credentials.json").write_text("{}")
    (home / ".ssh" / "id_ed25519").write_text("private key")
    (home / ".bashrc").write_text("export TOKEN=1")
    (home / ".config" / "tool" / "notes.txt").write_text("notes")
    (home / ".config" / "other.txt").write_text("other")
    (home / "work" / "report.pdf").write_bytes(b"%PDF-1.4")
    stack = ExitStack()
    stack.enter_context(patch.object(shared_files, "temp_root", lambda: str(temp)))
    stack.enter_context(patch.object(shared_files, "_cli_homes", lambda: str(temp / "puppy-cli-0")))
    stack.enter_context(patch.object(shared_files, "_home", lambda: str(home)))
    return stack, temp, home


def test_policy():
    path = project("policy")
    stack, temp, home = place()
    data = Path(config.DATA_DIR).resolve()
    session = {"cwd": str(path)}
    resolve = lambda value, cwd=None: shared_files.resolve_source(
        {"cwd": str(cwd)} if cwd is not None else session, value)
    with stack:
        # the working folder by absolute or relative path, and the temporary folder
        root, relative, resolved = resolve(str(path / "build" / "model.stl"))
        assert (root, relative, resolved) == (str(path.resolve()), "build/model.stl",
                                              str((path / "build" / "model.stl").resolve()))
        assert resolve("build/model.stl")[1] == "build/model.stl"
        assert resolve("./build/../build/model.stl")[1] == "build/model.stl"
        assert resolve(str(temp / "export.zip"))[:2] == (str(temp), "export.zip")
        # a link inside the folder resolves to the file it names, still inside
        (path / "latest.stl").symlink_to(path / "build" / "model.stl")
        assert resolve("latest.stl")[1] == "build/model.stl"
        # everything else
        outside = ROOT / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        refused(lambda: resolve(str(outside / "secret.txt")), "only files inside this session's working folder")
        refused(lambda: resolve("../outside/secret.txt"), "copy the file into one of them first")
        (path / "escape.txt").symlink_to(outside / "secret.txt")
        refused(lambda: resolve("escape.txt"), "only files inside")
        (data / "puppy-secret.txt").write_text("db")
        refused(lambda: resolve(str(data / "puppy-secret.txt")), "Puppy's own data folder")
        refused(lambda: resolve(str(data / "puppy-secret.txt"), cwd=ROOT),
                "Puppy's own data folder"), "a folder holding the data folder still never reaches it"
        # ...unless the session works in a folder Puppy manages there
        scratch = data / "workspaces" / "session-0123abcd"
        scratch.mkdir(parents=True)
        (scratch / "out.txt").write_text("scratch output")
        assert resolve("out.txt", cwd=scratch)[0] == str(scratch)
        refused(lambda: resolve(str(data / "puppy-secret.txt"), cwd=scratch), "Puppy's own data folder")
        refused(lambda: resolve(str(temp / "puppy-cli-0" / "claude" / ".credentials.json")),
                "an engine CLI's own home folder")
        for secret in (home / ".ssh" / "id_ed25519", home / ".bashrc", home / ".config" / "other.txt"):
            refused(lambda: resolve(str(secret), cwd=home), "hidden files and folders directly in the home folder")
        assert resolve(str(home / "work" / "report.pdf"), cwd=home)[1] == "work/report.pdf"
        tool = home / ".config" / "tool"
        assert resolve("notes.txt", cwd=tool)[1] == "notes.txt", "a session working inside a hidden folder"
        refused(lambda: resolve(str(home / ".config" / "other.txt"), cwd=tool), "only files inside")
        for system in ("/etc/shadow", "/proc/self/environ", "/sys/kernel/notes", "/dev/null",
                       "/etc/ssh/sshd_config"):
            refused(lambda: resolve(system, cwd="/"), "system files under")
        for bad in ("", "   ", None, 5, "a\x00b", "x" * (shared_files.MAX_PATH_CHARS + 1)):
            refused(lambda: resolve(bad), "give the path of the one file to share")
        refused(lambda: resolve(str(path)), "only files inside"), "the folder itself is not a file in it"

        # opening walks the resolved path without following anything
        refused(lambda: shared_files._open_source(str(path), "build"), "only a regular file")
        os.mkfifo(str(path / "pipe"))
        refused(lambda: shared_files._open_source(str(path), "pipe"), "only a regular file")
        refused(lambda: shared_files._open_source(str(path), "missing.stl"), "there is no file at that path")
        refused(lambda: shared_files._open_source(str(path), "../outside/secret.txt"), "give the path")
        # a link swapped in after the path was resolved, as a leaf or a folder on the way
        refused(lambda: shared_files._open_source(str(path), "latest.stl"), "only a regular file")
        swapped = project("swapped")
        shutil.rmtree(str(swapped / "build"))
        (swapped / "build").symlink_to(path / "build")
        refused(lambda: shared_files._open_source(str(swapped), "build/model.stl"), "the path changed")
        fd, info = shared_files._open_source(str(path), "build/model.stl")
        try:
            assert stat.S_ISREG(info.st_mode) and os.read(fd, 100) == STL
        finally:
            os.close(fd)
        os.chmod(str(path / "build" / "model.stl"), 0)
        try:
            if os.geteuid() != 0:
                refused(lambda: shared_files._open_source(str(path), "build/model.stl"), "cannot read")
        finally:
            os.chmod(str(path / "build" / "model.stl"), 0o644)
    print("PASS: the working and temporary folders only; never Puppy's data, a CLI home, the home "
          "folder's hidden entries or system secrets; links, folders, FIFOs and swapped paths refused")


def shares_of(sid):
    return sorted(child.name for child in (Path(config.DATA_DIR).resolve() / "uploads" / str(sid)).iterdir()
                  if shared_files.FILE_ID.fullmatch(child.name))


def test_storage():
    path = project("storage")
    sid = db.create_session("Storage fixture", "claude", str(path), "", "", "", "default")
    main = db.create_session("Main fixture", "claude", str(path), "", "", "", "default")
    stack, temp, _home = place()
    try:
        with stack:
            first = shared_files.share(sid, "build/model.stl", turn="turn-1")
            assert shared_files.FILE_ID.fullmatch(first["id"]), first
            assert not uploads.UPLOAD_ID.fullmatch(first["id"]) and not uploads.TOOL_IMAGE_ID.fullmatch(first["id"])
            assert first == {"id": first["id"], "name": "model.stl", "size": len(STL), "reused": False,
                             "link": "[model.stl](puppy-file:{})".format(first["id"])}
            target = uploads._validated_upload_file(sid, first["id"])
            assert target.name == "model.stl" and target.read_bytes() == STL
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
            # the same file under the same name is the same share, and costs no turn count
            again = shared_files.share(sid, str(path / "build" / "model.stl"), turn="turn-1")
            assert again["id"] == first["id"] and again["reused"] is True
            assert shared_files._turn_counts["turn-1"] == 1
            # the copy is taken at that moment: a changed file is a new share
            (path / "build" / "model.stl").write_bytes(STL.replace(b"demo", b"next"))
            changed = shared_files.share(sid, "build/model.stl")
            assert changed["id"] != first["id"] and not changed["reused"]
            assert target.read_bytes() == STL, "the earlier link still serves what was shared"
            # names: the agent's own, one path component, printable; a link's own name
            named = shared_files.share(sid, str(temp / "export.zip"), name="Final export (v2).zip")
            assert named["name"] == "Final export (v2).zip"
            assert shared_files.share(sid, str(temp / "export.zip"), name="../../a/b\\c.zip")["name"] == "c.zip"
            assert shared_files.share(sid, str(temp / "export.zip"),
                                      name="invoice‮fdp.zip")["name"] == "invoice_fdp.zip"
            assert shared_files.share(sid, str(temp / "export.zip"), name="   ")["name"] == "export.zip"
            (path / "latest.stl").symlink_to(path / "build" / "model.stl")
            assert shared_files.share(sid, "latest.stl")["name"] == "latest.stl"
            weird = shared_files.share(sid, str(temp / "export.zip"), name="my_[draft]*.zip")
            assert weird["link"] == "[my\\_\\[draft\\]\\*.zip](puppy-file:{})".format(weird["id"])
            refused(lambda: shared_files.share(sid, "build/model.stl", name=5), "name must be text")
            # newest first, with the stored names and sizes
            listed = shared_files.shared(sid)
            assert [item["id"] for item in listed][:1] == [weird["id"]]
            assert {item["id"]: item["name"] for item in listed}[first["id"]] == "model.stl"
            assert all(set(item) == {"id", "name", "size", "created_at"} for item in listed)
            # the caps, each leaving nothing behind
            before = shares_of(sid)
            with patch.object(shared_files, "MAX_FILE_BYTES", len(STL) - 1):
                refused(lambda: shared_files.share(sid, "build/model.stl", name="big.stl"), "files up to")
            held = sum(item["size"] for item in shared_files.shared(sid))
            with patch.object(shared_files, "MAX_SESSION_BYTES", held + 1):
                refused(lambda: shared_files.share(sid, "build/model.stl", name="more.stl"),
                        "this conversation already holds")
                # sharing the same unchanged file again still answers its share
                assert shared_files.share(sid, "build/model.stl")["reused"] is True
            with patch.object(shared_files, "MAX_TURN_SHARES", 2):
                shared_files.share(sid, str(temp / "export.zip"), name="one.zip", turn="turn-2")
                shared_files.share(sid, str(temp / "export.zip"), name="two.zip", turn="turn-2")
                refused(lambda: shared_files.share(sid, str(temp / "export.zip"), name="three.zip",
                                                   turn="turn-2"), "this turn has already shared 2 files")
                assert shared_files.share(sid, str(temp / "export.zip"), name="one.zip",
                                          turn="turn-2")["reused"], "an earlier share is no new one"
                shared_files.share(sid, str(temp / "export.zip"), name="three.zip", turn="turn-3")
            assert len(shares_of(sid)) == len(before) + 3
            with patch.object(shared_files.os, "fsync", side_effect=OSError("disk full")):
                try:
                    shared_files.share(sid, str(temp / "export.zip"), name="fails.zip", turn="turn-4")
                except OSError:
                    pass
                else:
                    raise AssertionError("a failed write must surface")
            assert len(shares_of(sid)) == len(before) + 3, "a failed copy leaves no directory"
            assert not shared_files._turn_counts.get("turn-4"), "nor a turn's count"
            assert "fails.zip" not in {item["name"] for item in shared_files.shared(sid)}
            # one share at a time per session: the caps hold under parallel calls
            with patch.object(shared_files, "MAX_SESSION_BYTES",
                              sum(item["size"] for item in shared_files.shared(sid)) + 30):
                outcomes = []

                def attempt(number):
                    try:
                        outcomes.append(shared_files.share(sid, str(temp / "export.zip"),
                                                           name="race-{}.zip".format(number))["id"])
                    except shared_files.ShareError as exc:
                        outcomes.append(str(exc))
                workers = [threading.Thread(target=attempt, args=(n,)) for n in range(4)]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
                assert sum(1 for item in outcomes if shared_files.FILE_ID.fullmatch(item)) == 1, outcomes
            # the orphan sweep never takes a share, however old
            stray = Path(config.DATA_DIR).resolve() / "uploads" / str(sid) / "1000000000000-0123456789"
            stray.mkdir(mode=0o700)
            (stray / "left.txt").write_text("stray upload")
            old = time.time() - uploads.ORPHAN_AGE_SECONDS - 60
            for child in stray.parent.iterdir():
                os.utime(str(child), (old, old))
            kept = shares_of(sid)
            assert uploads.sweep_orphans(sid) == 1 and not stray.exists() and shares_of(sid) == kept
            # a folded task's shares move to Main under the same ids; anything else stays
            upload = Path(config.DATA_DIR).resolve() / "uploads" / str(sid) / "1790000000000-0123456789"
            upload.mkdir(mode=0o700)
            (upload / "note.txt").write_text("attachment")
            assert shared_files.adopt(sid, main) == len(kept)
            assert shares_of(main) == kept and shares_of(sid) == [] and upload.exists()
            assert uploads._validated_upload_file(main, first["id"]).read_bytes() == STL
            assert shared_files.adopt(sid, main) == 0 and shared_files.adopt(999999, main) == 0
            # the download's headers
            assert shared_files.content_disposition("model.stl") == \
                "attachment; filename=\"model.stl\"; filename*=UTF-8''model.stl"
            assert shared_files.content_disposition('Ünïcode "x".stl') == \
                "attachment; filename=\"_n_code _x_.stl\"; filename*=UTF-8''%C3%9Cn%C3%AFcode%20%22x%22.stl"
            assert shared_files._size(5) == "5 bytes" and shared_files._size(3 * 1024 * 1024) == "3.0 MiB"
            assert shared_files.file_name("é" * 200 + ".stl").endswith(".stl")
            assert len(shared_files.file_name("é" * 200 + ".stl").encode()) <= uploads.MAX_FILENAME_BYTES
    finally:
        for session in (sid, main):
            uploads.remove_session_storage(session)
            db.delete_session(session)
    assert not (Path(config.DATA_DIR).resolve() / "uploads" / str(sid)).exists()
    print("PASS: shares stored privately beside the uploads under their own ids and the agent's "
          "names, reused when unchanged, capped per file, conversation and turn, never swept, moved "
          "to Main with a folded task")


def busy(app, value):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # a started app's state
        app["puppy_snapshot_busy"] = value


def check_download_headers(response, name):
    for key, value in shared_files.DOWNLOAD_HEADERS.items():
        assert response.headers.get(key) == value, (key, response.headers.get(key))
    assert response.headers["Content-Disposition"] == shared_files.content_disposition(name)


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": TOKEN}
    path = project("routes-" + app["puppy_role"])
    (path / "page.html").write_text("<script>document.cookie</script>")
    async with TestClient(TestServer(app)) as client:
        sid = db.create_session("Route fixture", "claude", str(path), "", "", "", "default")
        other = db.create_session("Other fixture", "claude", str(path), "", "", "", "default")
        try:
            assert protocol.SHARED_FILES_CAPABILITY in protocol.execution_capabilities()
            share = shared_files.share(sid, "build/model.stl")
            page = shared_files.share(sid, "page.html")
            url = "/api/sessions/{}/files/{}".format(sid, share["id"])
            async with client.get("/api/sessions/{}/files".format(sid)) as response:
                assert response.status == 401
            async with client.get(url) as response:
                assert response.status == 401, "the bytes are the session's, behind its sign-in"
            async with client.get("/api/sessions/{}/files".format(sid), headers=headers) as response:
                assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
                listed = await response.json()
            assert [item["id"] for item in listed["files"]] == [page["id"], share["id"]] and \
                listed["more"] is False
            assert listed["files"][1]["name"] == "model.stl" and listed["files"][1]["size"] == len(STL)
            async with client.get(url, headers=headers) as response:
                assert response.status == 200
                check_download_headers(response, "model.stl")
                assert await response.read() == STL
            # a page is never a page here: bytes to save, sniffing off, sandboxed
            async with client.get("/api/sessions/{}/files/{}".format(sid, page["id"]),
                                  headers=headers) as response:
                assert response.status == 200
                check_download_headers(response, "page.html")
                assert response.headers["Content-Type"] == "application/octet-stream"
            async with client.head(url, headers=headers) as response:
                assert response.status == 200 and response.headers["Content-Length"] == str(len(STL))
                check_download_headers(response, "model.stl")
                assert await response.read() == b""
            async with client.get(url, headers=dict(headers, Range="bytes=2-5")) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 2-5/{}".format(len(STL))
                assert await response.read() == STL[2:6]
            # nothing else reaches it: another session, an unknown id or session, a bad id
            for refused_url, status in (
                    ("/api/sessions/{}/files/{}".format(other, share["id"]), 404),
                    ("/api/sessions/{}/files/f{}".format(sid, "0" * 32), 404),
                    ("/api/sessions/999999/files/{}".format(share["id"]), 404),
                    ("/api/sessions/999999/files", 404),
                    ("/api/sessions/{}/files/F{}".format(sid, "0" * 32), 404),
                    ("/api/sessions/{}/files/{}".format(sid, share["id"][:-1]), 404),
                    ("/api/sessions/{}/files/..%2f..%2fpuppy.db".format(sid), 404)):
                async with client.get(refused_url, headers=headers) as response:
                    assert response.status == status, (refused_url, response.status)
            async with client.get("/api/sessions/{}/files/f{}".format(sid, "0" * 32),
                                  headers=headers) as response:
                assert (await response.json())["error"] == "this file is no longer available"
            # the upload routes never serve or discard a share
            async with client.get("/api/sessions/{}/upload/{}".format(sid, share["id"]),
                                  headers=headers) as response:
                assert response.status in (400, 404), response.status
            async with client.delete("/api/sessions/{}/upload/{}".format(sid, share["id"]),
                                     headers=headers) as response:
                assert response.status in (400, 404, 405), response.status
            assert uploads._validated_upload_file(sid, share["id"]).read_bytes() == STL
            # a directory holding more than its one file fails validation
            extra = uploads._validated_upload_file(sid, page["id"]).parent / "extra"
            extra.write_text("x")
            async with client.get("/api/sessions/{}/files/{}".format(sid, page["id"]),
                                  headers=headers) as response:
                assert response.status == 409
            extra.unlink()
            busy(client.server.app, "restore")
            async with client.get(url, headers=headers) as response:
                assert response.status == 503
            busy(client.server.app, None)
            # deleting the session deletes its shares
            async with client.delete("/api/sessions/{}".format(sid), headers=headers) as response:
                assert response.status == 200, await response.text()
            assert not (Path(config.DATA_DIR).resolve() / "uploads" / str(sid)).exists()
            async with client.get(url, headers=headers) as response:
                assert response.status == 404
        finally:
            for session in (sid, other):
                runner.drop_hub(session)
                uploads.remove_session_storage(session)
                if db.get_session(session):
                    db.delete_session(session)
    print("PASS: {} serves a share only as a download with its headers, HEAD and ranges, to its "
          "session alone, and deletes it with the session".format(app["puppy_role"]))


async def stub_backend():
    """A backend that says the wrong things: a page, inline, a cookie, a cut body."""
    seen = {}

    async def files(request):
        seen["token"] = request.headers.get("X-Puppy-Token")
        seen["cookie"] = request.headers.get("Cookie")
        file_id = request.match_info["file_id"]
        if file_id.startswith("f1"):
            return web.Response(body=b"<script>alert(1)</script>", content_type="text/html", headers={
                "Content-Disposition": "inline", "Set-Cookie": "stolen=1",
                "Access-Control-Allow-Origin": "*", "Content-Security-Policy": "default-src *"})
        if file_id.startswith("f2"):
            response = web.StreamResponse(headers={
                "Content-Disposition": "attachment; filename=\"big.bin\""})
            await response.prepare(request)
            for index in range(48):
                await response.write(bytes([index]) * 65536)
            await response.write_eof()
            return response
        if file_id.startswith("f3") or file_id.startswith("f4"):
            # f3 declares its length, f4 is chunked: both stop a quarter in
            response = web.StreamResponse(headers={"Content-Length": str(4 * 1024 * 1024)}
                                          if file_id.startswith("f3") else {})
            await response.prepare(request)
            await response.write(b"x" * (256 * 1024))
            request.transport.close()
            return response
        return web.json_response({"error": "this file is no longer available"}, status=404)

    app = web.Application()
    app.router.add_get("/api/sessions/{sid}/files/{file_id}", files)
    server = TestServer(app)
    await server.start_server()
    return server, seen


def add_backend(name, url):
    db.execute("INSERT INTO backends(name,url,urls,token,protocol,capabilities,created_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (name, url, json.dumps([url]), TOKEN, protocol.API_PROTOCOL,
                json.dumps([protocol.SHARED_FILES_CAPABILITY]), time.time()))
    bid = {item["name"]: item["id"] for item in backends.list_backends()}[name]
    backends._mark_backend_online(bid)
    return bid


async def proxy_contract():
    """A controller relays a backend's share as a stream under its own headers."""
    node = backend_app()
    node.on_startup.clear()
    node.on_shutdown.clear()
    node.on_cleanup.clear()
    node_server = TestServer(node)
    await node_server.start_server()
    stub, seen = await stub_backend()
    controller = build_app()
    controller.on_startup.clear()
    controller.on_shutdown.clear()
    controller.on_cleanup.clear()
    headers = {"X-Puppy-Token": TOKEN, "Cookie": "puppy_session=browser-only"}
    path = project("proxy")
    sid = db.create_session("Proxy fixture", "claude", str(path), "", "", "", "default")
    try:
        real = add_backend("files-node", str(node_server.make_url("")).rstrip("/"))
        hostile = add_backend("hostile-node", str(stub.make_url("")).rstrip("/"))
        gone = add_backend("gone-node", "http://127.0.0.1:1")
        share = shared_files.share(sid, "build/model.stl")
        async with TestClient(TestServer(controller)) as client:
            url = "/api/b/{}/sessions/{}/files/{}".format(real, sid, share["id"])
            async with client.get(url, headers=headers) as response:
                assert response.status == 200, await response.text()
                check_download_headers(response, "model.stl")
                assert response.headers["Content-Length"] == str(len(STL))
                assert await response.read() == STL
            async with client.head(url, headers=headers) as response:
                assert response.status == 200 and response.headers["Content-Length"] == str(len(STL))
                assert await response.read() == b""
            async with client.get(url, headers=dict(headers, Range="bytes=0-3")) as response:
                assert response.status == 206 and await response.read() == STL[:4]
                assert response.headers["Content-Range"] == "bytes 0-3/{}".format(len(STL))
            missing = "/api/b/{}/sessions/{}/files/f{}".format(real, sid, "0" * 32)
            async with client.get(missing, headers=headers) as response:
                assert response.status == 404
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Cache-Control"] == "no-store"
                assert (await response.json())["error"] == "this file is no longer available"
            # every buffered relay is held to the same rules
            async with client.get("/api/b/{}/sessions/{}/files".format(real, sid), headers=headers) as response:
                assert response.status == 200
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
                assert [item["id"] for item in (await response.json())["files"]] == [share["id"]]
            # whatever the backend says, this controller answers a download
            async with client.get("/api/b/{}/sessions/1/files/f1{}".format(hostile, "0" * 31),
                                  headers=headers) as response:
                assert response.status == 200
                for key, value in shared_files.DOWNLOAD_HEADERS.items():
                    assert response.headers.get(key) == value, (key, response.headers.get(key))
                assert response.headers["Content-Disposition"] == "attachment"
                assert "Set-Cookie" not in response.headers
                assert "Access-Control-Allow-Origin" not in response.headers
                assert await response.read() == b"<script>alert(1)</script>"
            assert seen["token"] == TOKEN and seen["cookie"] is None, "the browser's cookie stays here"
            # a large file streams through whole, with the backend's own name
            async with client.get("/api/b/{}/sessions/1/files/f2{}".format(hostile, "0" * 31),
                                  headers=headers) as response:
                assert response.status == 200
                assert response.headers["Content-Disposition"] == 'attachment; filename="big.bin"'
                body = await response.read()
            assert body == b"".join(bytes([index]) * 65536 for index in range(48))
            # a backend that stops mid-file cuts the download short - with a
            # length or chunked, never a short file complete - and stays online
            for cut in ("f3", "f4"):
                try:
                    async with client.get("/api/b/{}/sessions/1/files/{}{}".format(
                            hostile, cut, "0" * 31), headers=headers) as response:
                        await response.read()
                except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError,
                        aiohttp.ClientOSError):
                    pass
                else:
                    raise AssertionError("a cut file must not read as complete: " + cut)
            assert backends.backend_is_online(hostile)
            # an unreachable backend is unavailable before any byte, and marked so
            async with client.get("/api/b/{}/sessions/1/files/{}".format(gone, share["id"]),
                                  headers=headers) as response:
                assert response.status == 503
            assert not backends.backend_is_online(gone)
            async with client.get("/api/b/{}/sessions/1/files/{}".format(gone, share["id"]),
                                  headers=headers) as response:
                assert response.status == 503, "and is not asked again"
    finally:
        await backends.close_client()
        await stub.close()
        await node_server.close()
        db.execute("DELETE FROM backends")
        uploads.remove_session_storage(sid)
        db.delete_session(sid)
    print("PASS: a controller streams a backend's share whole, ranges and HEAD included, under its "
          "own download headers whatever the backend answers, and cuts a broken one short")


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
    sid = db.create_session("Bridge fixture", "claude", str(path), "", "", "", "default")
    app = {}
    assert files_agent.turn_mcp(sid, "turn-a") is None, "no descriptor while the bridge is down"
    await files_agent.start(app)
    hub = hold_turn(sid, "turn-a")
    try:
        descriptor = files_agent.turn_mcp(sid, "turn-a")
        socket_path = Path(descriptor["env"]["PUPPY_FILES_SOCKET"])
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        assert descriptor["args"] == ["-m", "puppy.files_agent"]
        assert descriptor["env"]["PUPPY_FILES_SESSION_ID"] == str(sid)
        assert descriptor["env"]["PUPPY_FILES_TURN_ID"] == "turn-a"
        call = lambda params, **extra: files_agent._dispatch(dict(
            {"session_id": sid, "turn_id": "turn-a", "method": "share_file", "params": params}, **extra))
        answer = await call({"path": "build/model.stl"})
        lines = answer["text"].split("\n")
        assert lines[0] == "Shared model.stl ({} bytes) for download in this conversation.".format(len(STL))
        assert lines[1].startswith("Markdown link: [model.stl](puppy-file:f") and lines[1].endswith(")")
        assert uploads.active_count() == 0
        again = await call({"path": "build/model.stl"})
        assert again["text"].startswith("Already shared, unchanged: model.stl")
        named = await call({"path": "build/model.stl", "name": "part.stl"})
        assert "[part.stl](puppy-file:f" in named["text"]

        async def refused_call(request, words):
            try:
                await files_agent._dispatch(request)
            except files_agent.FilesAgentError as exc:
                assert words in str(exc), (words, str(exc))
                return
            raise AssertionError("expected a refusal naming: " + words)

        base = {"session_id": sid, "turn_id": "turn-a", "method": "share_file", "params": {"path": "x"}}
        await refused_call(dict(base, turn_id="turn-b"), "no longer running")
        await refused_call(dict(base, turn_id=""), "invalid file tool turn identity")
        await refused_call(dict(base, session_id="x"), "invalid originating session identity")
        await refused_call(dict(base, session_id=0), "invalid file tool turn identity")
        await refused_call(dict(base, session_id=999999), "no longer exists")
        await refused_call(dict(base, method="delete_file"), "unknown file tool")
        await refused_call(dict(base, params=["x"]), "must be an object")
        await refused_call(dict(base, params={"path": "x", "mode": "w"}), "unknown share_file field: mode")
        await refused_call(dict(base, params={"path": "/etc/shadow"}), "system files under /etc/shadow")
        app["puppy_snapshot_busy"] = "export"
        await refused_call(base, "backup or restore is in progress")
        app["puppy_snapshot_busy"] = None
        # the backup count is the copy's own until its thread ends, even when
        # the request stopped waiting for it
        gate = threading.Event()
        real_share = shared_files.share

        def slow_share(*args):
            gate.wait(10)
            return real_share(*args)

        with patch.object(shared_files, "share", side_effect=slow_share):
            try:
                await asyncio.wait_for(call({"path": "build/model.stl", "name": "slow.stl"}), .3)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError("the slow share should not have answered yet")
            assert uploads.active_count() == 1, "a backup must not start beside the copy"
            gate.set()
            deadline = time.monotonic() + 10
            while uploads.active_count():
                assert time.monotonic() < deadline, "the copy's count was never released"
                await asyncio.sleep(.02)
        assert "slow.stl" in {item["name"] for item in shared_files.shared(sid)}
        # through the real socket, as the MCP child calls it
        environment = {"PUPPY_FILES_SOCKET": str(socket_path), "PUPPY_FILES_SESSION_ID": str(sid),
                       "PUPPY_FILES_TURN_ID": "turn-a"}
        with patch.dict(os.environ, environment):
            text = (await asyncio.get_running_loop().run_in_executor(
                None, files_agent._bridge_call, "share_file", {"path": "build/model.stl"}))["text"]
            assert text.startswith("Already shared, unchanged: model.stl")
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, files_agent._bridge_call, "share_file", {"path": "missing.stl"})
            except files_agent.FilesAgentError as exc:
                assert "there is no file at that path" in str(exc)
            else:
                raise AssertionError("a missing file must be refused")
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
        assert initialized["serverInfo"]["name"] == "Puppy files"
        assert initialized["protocolVersion"] == "2025-06-18"
        assert initialized["instructions"] == files_agent.TOOL_INSTRUCTIONS
        tools = (await rpc(2, "tools/list"))["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["share_file"]
        assert tools[0]["inputSchema"]["required"] == ["path"]
        shared = (await rpc(3, "tools/call", {"name": "share_file",
                                              "arguments": {"path": "build/model.stl"}}))["result"]
        assert shared["isError"] is False and "puppy-file:f" in shared["content"][0]["text"]
        failed = (await rpc(4, "tools/call", {"name": "share_file",
                                              "arguments": {"path": str(ROOT / "nowhere.txt")}}))["result"]
        assert failed["isError"] is True and "only files inside" in failed["content"][0]["text"]
        unknown = (await rpc(5, "tools/call", {"name": "rm", "arguments": {}}))["result"]
        assert unknown["isError"] is True
        assert (await rpc(6, "resources/list"))["error"]["code"] == -32601
        child.stdin.close()
        assert await asyncio.wait_for(child.wait(), 10) == 0
    finally:
        release_turn(hub)
        await files_agent.stop(app)
        runner.drop_hub(sid)
        uploads.remove_session_storage(sid)
        db.delete_session(sid)
    assert not Path(files_agent.socket_path()).exists()
    print("PASS: the turn-bound bridge checks its turn, its fields and backups, holds the backup "
          "count for the copy's whole life, and answers over its socket and as a stdio MCP server")


def descriptors():
    """Every driver hands the bridge to its engine with its guidance."""
    from puppy.drivers.claude import ClaudeDriver
    from puppy.drivers.codex import CodexDriver
    from puppy.drivers.opencode import OpenCodeDriver
    files_agent._server = object()
    try:
        descriptor = files_agent.turn_mcp(5, "turn")
        session = {"cwd": str(ROOT)}
        claude = ClaudeDriver().build_cmd(session, True, "p", "t", files_mcp=descriptor)
        servers = json.loads(claude[claude.index("--mcp-config") + 1])["mcpServers"]
        assert servers["puppy_files"]["args"] == ["-m", "puppy.files_agent"]
        assert files_agent.ENGINE_GUIDANCE in claude[claude.index("--append-system-prompt") + 1]
        codex = CodexDriver()
        argv = codex.build_cmd(session, True, "p", "t", files_mcp=descriptor)
        assert any(arg.startswith("mcp_servers.puppy_files.command=") for arg in argv)
        prompt = codex.turn_context(session, True, "p", "t", files_mcp=descriptor)["prompt"]
        assert "<puppy_files_policy>\n" + files_agent.ENGINE_GUIDANCE + "\n</puppy_files_policy>" in prompt
        assert codex.build_cmd(session, True, "p", "t", files_mcp=descriptor, tool={"tool": "compact"}) \
            == [codex.binary, "app-server", "--stdio"], "a maintenance turn gets no bridges"
        opencode = OpenCodeDriver()
        context = opencode.turn_context(session, True, "p", "t", files_mcp=descriptor)
        assert [server["name"] for server in context["mcp_servers"]] == ["puppy_files"]
        assert files_agent.ENGINE_GUIDANCE in json.dumps(
            opencode.build_env(session, True, "p", "t", files_mcp=descriptor))
    finally:
        files_agent._server = None
    print("PASS: claude, codex and opencode carry the file bridge and its guidance; a maintenance "
          "turn carries none")


async def frame(ws, kind, timeout=20):
    while True:
        value = await ws.receive_json(timeout=timeout)
        if value["type"] == kind:
            return value


async def fixture_turn():
    """A real turn: the engine calls the bridge's MCP server itself and links the file."""
    app = build_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": TOKEN}
    path = project("turn")
    sid = db.create_session("Turn fixture", "claude", str(path), "", "", "", "default")
    driver = get_driver("claude")
    real_build = driver.build_cmd
    kwargs_seen = {}

    def command(*args, **kwargs):
        kwargs_seen.update(kwargs)
        argv = real_build(*args, **kwargs)
        return [sys.executable, str(Path(__file__).resolve()), "--fixture",
                argv[argv.index("--mcp-config") + 1]]

    await files_agent.start(app)
    hub = runner.hub(sid)
    try:
        async with TestClient(TestServer(app)) as client:
            with ExitStack() as stack:
                stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
                stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
                stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
                ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
                try:
                    await frame(ws, "snapshot")
                    assert hub.send_message("give me the model") == {"queued": False}
                    deadline = time.monotonic() + 30
                    while hub.status != "idle":
                        assert time.monotonic() < deadline, "the fixture turn did not end"
                        await asyncio.sleep(.05)
                    await hub.turn_task
                finally:
                    await ws.close()
            assert kwargs_seen["files_mcp"]["env"]["PUPPY_FILES_SESSION_ID"] == str(sid)
            events = db.get_events(sid)
            result = next(e for e in events if e["kind"] == "tool_result")["data"]
            answer = [e for e in events if e["kind"] == "assistant"][-1]["data"]["text"]
            listed = shared_files.shared(sid)
            assert len(listed) == 1 and listed[0]["name"] == "model.stl", listed
            file_id = listed[0]["id"]
            assert "[model.stl](puppy-file:{})".format(file_id) in result["content"]
            assert answer == "The latest STL file is [here](puppy-file:{}).".format(file_id)
            async with client.get("/api/sessions/{}/files/{}".format(sid, file_id),
                                  headers=headers) as response:
                assert response.status == 200 and await response.read() == STL
    finally:
        if hub.status != "idle":
            await hub.kill()
        if hub.turn_task:
            await asyncio.gather(hub.turn_task, return_exceptions=True)
        await files_agent.stop(app)
        runner.drop_hub(sid)
        uploads.remove_session_storage(sid)
        db.delete_session(sid)
    print("PASS: a fixture engine's real turn shares a file through the bridge's own MCP server "
          "and links it in its answer, and the link's file downloads")


async def main():
    config.load()
    config.set_value("auth.api_token", TOKEN)
    db.connect()
    test_policy()
    test_storage()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)
    await proxy_contract()
    await bridge_contract()
    descriptors()
    await fixture_turn()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
