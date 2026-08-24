#!/usr/bin/env python3
"""No-quota round-trip and adversarial tests for Puppy state snapshots."""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="snapshot-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import auth, config, db, runner as session_runner, snapshots, workspaces  # noqa: E402
from puppy.web import build_app  # noqa: E402


def expect_snapshot_error(call, contains: str) -> None:
    try:
        call()
    except snapshots.SnapshotError as exc:
        assert contains.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError("invalid snapshot was accepted")


def malicious_archive(path: Path, member: tarfile.TarInfo, payload: bytes = b"") -> None:
    with tarfile.open(str(path), "w:gz") as archive:
        root = tarfile.TarInfo(snapshots.ARCHIVE_ROOT)
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        archive.addfile(root)
        archive.addfile(member, io.BytesIO(payload) if member.isreg() else None)


async def exercise_http(archive_ui: dict, session_id: int) -> None:
    token = config.get("auth.api_token")
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": token}
    try:
        async with aiohttp.ClientSession() as http:
            busy_hub = session_runner.hub(session_id)
            busy_hub.queue.append("wait until after the backup")
            async with http.post(url + "/api/snapshot/export", headers=headers,
                                 json={"ui": archive_ui}) as response:
                blocked = await response.json()
                assert response.status == 409, blocked
                assert "queued" in blocked["error"]
            busy_hub.clear_queue()

            async with http.post(url + "/api/snapshot/export", headers=headers,
                                 json={"ui": archive_ui}) as response:
                prepared = await response.json()
                assert response.status == 200, prepared
                assert prepared["sessions"] == 2
            async with http.get(url + prepared["download"], headers=headers) as response:
                payload = await response.read()
                assert response.status == 200
                assert response.headers["Content-Type"].startswith("application/gzip")
                assert "attachment" in response.headers["Content-Disposition"]
            async with http.get(url + prepared["download"], headers=headers) as response:
                assert response.status == 404

            busy_hub.queue.append("wait until after the restore")
            async with http.post(url + "/api/snapshot/import", headers={
                    **headers, "Content-Type": "application/gzip"}, data=payload) as response:
                blocked = await response.json()
                assert response.status == 409, blocked
                assert "queued" in blocked["error"]
            busy_hub.clear_queue()

            config.set_value("instance_name", "changed-over-http")
            async with http.post(url + "/api/snapshot/import", headers={
                    **headers, "Content-Type": "application/gzip"}, data=payload) as response:
                restored = await response.json()
                assert response.status == 200, restored
            assert restored["ui"] == archive_ui
            assert restored["sessions"] == 2
            assert config.get("instance_name") == "saved-instance"
    finally:
        await runner.cleanup()


async def main() -> None:
    project = TEST_ROOT / "ordinary-project"
    project.mkdir()
    project_file = project / "outside.txt"
    project_file.write_text("before export", encoding="utf-8")
    direct_archive = None
    original_scratch = None
    try:
        config.load()
        db.connect()
        auth.create_user("snapshot-user", "secret123")
        config.set_value("instance_name", "saved-instance")
        config.set_value("sessions.default_cwd", str(project))
        db.execute(
            "INSERT INTO backends(name,url,token,protocol,capabilities,remote_version,role,"
            "tls_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("saved-backend", "https://backend.test:10888", "private-backend-token", 1,
             '["sessions"]', "1.2.3", "backend", "ab" * 32, time.time()))

        directory_id = db.create_session(
            "directory session", "codex", str(project), "", "", "#4dd0c4",
            "workspace-write", workspace_kind="directory")
        scratch_path = workspaces.create_temporary()
        original_scratch = Path(scratch_path)
        scratch_id = db.create_session(
            "scratch session", "codex", scratch_path, "", "", "#9d7bff",
            "workspace-write", workspace_kind="temporary")
        nested = original_scratch / "nested"
        nested.mkdir()
        (nested / "file.txt").write_text("scratch contents", encoding="utf-8")
        os.symlink("nested/file.txt", str(original_scratch / "safe-link"))

        upload = Path(config.DATA_DIR) / "uploads" / str(directory_id) / "image.png"
        upload.parent.mkdir(parents=True)
        upload.write_bytes(b"saved-upload")
        tls_dir = Path(config.DATA_DIR) / "tls"
        tls_dir.mkdir(mode=0o700)
        tls_key = tls_dir / "identity.key"
        tls_key.write_bytes(b"saved-private-tls-material")
        tls_key.chmod(0o600)
        db.add_event(directory_id, "user", {
            "text": "image at {}".format(upload),
        })
        db.add_event(scratch_id, "assistant", {"text": "remembered transcript"})
        ui = {
            "puppy.theme": "light",
            "puppy.tabs": json.dumps({"active": "s:0:{}".format(scratch_id)}),
            "puppy.draft.s:0:{}".format(scratch_id): "unfinished prompt",
        }

        direct_archive = snapshots.create_archive(ui)
        archive_path = Path(direct_archive["path"])
        assert archive_path.stat().st_mode & 0o777 == 0o600
        with tarfile.open(str(archive_path), "r:gz") as archive:
            names = archive.getnames()
        assert snapshots.ARCHIVE_ROOT + "/manifest.json" in names
        assert not any("puppy.log" in name or "/snapshots/" in name for name in names)

        # Mutate every restored surface and an excluded ordinary project file.
        config.set_value("instance_name", "mutated-instance")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM backends")
        shutil.rmtree(Path(config.DATA_DIR) / "uploads")
        tls_key.write_bytes(b"mutated-private-tls-material")
        project_file.write_text("changed after export", encoding="utf-8")

        staged = snapshots.stage_import(str(archive_path))
        restored = snapshots.commit_import(staged)
        snapshots.discard_staged(staged)
        assert restored["ui"] == ui
        assert config.get("instance_name") == "saved-instance"
        assert len(db.list_sessions(include_archived=True)) == 2
        assert db.query_one("SELECT token FROM backends")["token"] == "private-backend-token"
        assert db.query_one("SELECT username FROM users")["username"] == "snapshot-user"
        restored_scratch = db.get_session(scratch_id)
        assert restored_scratch["cwd"] != str(original_scratch)
        assert (Path(restored_scratch["cwd"]) / "nested" / "file.txt").read_text(
            encoding="utf-8") == "scratch contents"
        assert (Path(restored_scratch["cwd"]) / "safe-link").read_text(
            encoding="utf-8") == "scratch contents"
        assert not original_scratch.exists()
        assert (Path(config.DATA_DIR) / "uploads" / str(directory_id) /
                "image.png").read_bytes() == b"saved-upload"
        assert tls_key.read_bytes() == b"saved-private-tls-material"
        assert tls_key.stat().st_mode & 0o777 == 0o600
        assert str(Path(config.DATA_DIR).resolve() / "uploads") in \
            db.get_events(directory_id)[0]["data"]["text"]
        assert project_file.read_text(encoding="utf-8") == "changed after export"

        unsupported = Path(restored_scratch["cwd"]) / "named-pipe"
        os.mkfifo(str(unsupported))
        expect_snapshot_error(
            lambda: snapshots.create_archive(ui), "unsupported file type")
        unsupported.unlink()

        await exercise_http(ui, directory_id)

        # A failed database install must put config and filesystem trees back.
        config.set_value("instance_name", "rollback-current")
        current_upload = Path(config.DATA_DIR) / "uploads" / "current.txt"
        current_upload.write_text("keep me", encoding="utf-8")
        tls_key.write_bytes(b"keep current tls material")
        staged = snapshots.stage_import(str(archive_path))
        original_replace = db.replace_from
        calls = {"count": 0}

        def fail_first_replace(path: str) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("simulated install failure")
            original_replace(path)

        db.replace_from = fail_first_replace
        try:
            expect_snapshot_error(lambda: snapshots.commit_import(staged), "simulated")
        finally:
            db.replace_from = original_replace
            snapshots.discard_staged(staged)
        assert calls["count"] == 2
        assert config.get("instance_name") == "rollback-current"
        assert current_upload.read_text(encoding="utf-8") == "keep me"
        assert tls_key.read_bytes() == b"keep current tls material"
        assert len(db.list_sessions(include_archived=True)) == 2
        assert not list(snapshots.work_root().glob("rollback-*"))

        traversal = TEST_ROOT / "traversal.tar.gz"
        item = tarfile.TarInfo(snapshots.ARCHIVE_ROOT + "/../escape")
        item.size = 1
        malicious_archive(traversal, item, b"x")
        expect_snapshot_error(lambda: snapshots.stage_import(str(traversal)), "unsafe path")
        assert not (TEST_ROOT / "escape").exists()

        escaping_link = TEST_ROOT / "escaping-link.tar.gz"
        item = tarfile.TarInfo(snapshots.ARCHIVE_ROOT + "/uploads/leak")
        item.type = tarfile.SYMTYPE
        item.linkname = "../config.json"
        malicious_archive(escaping_link, item)
        expect_snapshot_error(
            lambda: snapshots.stage_import(str(escaping_link)), "restored data tree")

        tamper_root = TEST_ROOT / "tamper"
        with tarfile.open(str(archive_path), "r:gz") as archive:
            archive.extractall(str(tamper_root))  # trusted archive generated above
        (tamper_root / snapshots.ARCHIVE_ROOT / "ui.json").write_text(
            '{"puppy.theme":"tampered"}', encoding="utf-8")
        tampered = TEST_ROOT / "tampered.tar.gz"
        with tarfile.open(str(tampered), "w:gz") as archive:
            archive.add(str(tamper_root / snapshots.ARCHIVE_ROOT),
                        arcname=snapshots.ARCHIVE_ROOT)
        expect_snapshot_error(lambda: snapshots.stage_import(str(tampered)), "manifest")

        print("snapshot round-trip, HTTP, rollback, and archive validation passed")
    finally:
        if direct_archive:
            snapshots.discard_export(direct_archive)
        try:
            for session in db.list_sessions(include_archived=True):
                if workspaces.is_temporary(session):
                    workspaces.remove_temporary(session)
        except Exception:
            pass
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
