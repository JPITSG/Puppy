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

from puppy import (auth, config, db, listener_handoff, runner as session_runner,
                   snapshots, terminal, uploads, web_tls, workspaces)  # noqa: E402
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


async def exercise_http(archive_ui: dict, session_id: int, project: Path) -> None:
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

            uploads._active_uploads = 1
            try:
                async with http.post(url + "/api/snapshot/export", headers=headers,
                                     json={"ui": archive_ui}) as response:
                    blocked = await response.json()
                    assert response.status == 409, blocked
                    assert "file upload" in blocked["error"]
            finally:
                uploads._active_uploads = 0

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
            config.set_value("engines.usage_refresh_minutes", 60)
            config.set_value("uploads.max_file_size_mb", 2)
            app["puppy_bind_verifications"]["stale-before-restore"] = {
                "timer": None, "server": None,
            }
            # Ended PTYs do not block a restore, but their memory-only replay
            # and old database session binding must not cross that boundary.
            stale_terminal = await terminal.manager().create(
                command="/bin/true", cwd=str(project), owner_session=session_id)
            for _ in range(100):
                if not stale_terminal.running:
                    break
                await asyncio.sleep(0.02)
            assert not stale_terminal.running
            assert terminal.manager().instance_payloads()
            async with http.post(url + "/api/snapshot/import", headers={
                    **headers, "Content-Type": "application/gzip"}, data=payload) as response:
                restored = await response.json()
                assert response.status == 200, restored
            assert app["puppy_bind_verifications"] == {}
            assert terminal.manager().instance_payloads() == []
            assert restored["ui"] == archive_ui
            assert restored["sessions"] == 2
            assert config.get("instance_name") == "saved-instance"
            assert config.get("sessions.default_cwd") == str(project)
            assert config.get("engines.usage_refresh_minutes") == 30
            assert config.get("engines.opencode") is None
            assert config.get("uploads.max_file_size_mb") == 19
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
        config.set_value("engines.usage_refresh_minutes", 30)
        config.set_value("uploads.max_file_size_mb", 19)
        config.set_value("browser.enabled", True)
        config.set_value("browser.color_scheme", "light")
        config.set_value("browser.shared_storage", True)
        config.set_system_prompts(
            "Keep answers concise.\nPreserve operator terminology.",
            "Remember that this project is stored on another node.",
            "Use the shared browser before standalone automation.",
            "Use the shared terminal only when explicitly requested.",
            "Spawn delegate agents only on an explicit request.")
        config.set_value("engines.auto_upgrade",
                         {"enabled": True, "mode": "at", "at": "04:15"})
        config.set_value("notify.enabled", True)
        config.set_value("notify.backend", 1)
        config.set_value("notify.command", "printf done: %s {session}")
        backend_id = db.execute(
            "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,role,"
            "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("saved-backend", "https://backend.test:10888",
             '["https://backend.test:10888","https://backend-vpn.test:10888"]',
             "private-backend-token", 1, '["sessions"]', "1.2.3", "backend",
             "ab" * 32, 1, time.time()))
        db.meta_set("backend_last_known.{}".format(backend_id), {
            "version": 1,
            "node_uuid": "1" * 32,
            "usage_refresh": {"minutes": 30, "enabled": True},
            "uploads": {"enabled": True, "max_file_size_mb": 19,
                        "max_file_size_bytes": 19 * 1024 * 1024},
        })
        db.meta_set("backend_last_sessions.{}".format(backend_id), {
            "version": 1,
            "sessions": [{"id": 91, "name": "cached remote session",
                          "status": "idle", "active_since": None}],
        })

        directory_id = db.create_session(
            "directory session", "codex", str(project), "", "", "#4dd0c4",
            "workspace-write", workspace_kind="directory")
        # what the last turn ran with: transcript dividers are read against it
        db.touch_session(directory_id,
                         used_config='{"model": "gpt-5.6-sol", "effort": "max"}')
        scratch_path = workspaces.create_temporary()
        original_scratch = Path(scratch_path)
        scratch_id = db.create_session(
            "scratch session", "codex", scratch_path, "", "", "#9d7bff",
            "workspace-write", workspace_kind="temporary")
        nested = original_scratch / "nested"
        nested.mkdir()
        (nested / "file.txt").write_text("scratch contents", encoding="utf-8")
        os.symlink("nested/file.txt", str(original_scratch / "safe-link"))

        upload = (Path(config.DATA_DIR) / "uploads" / str(directory_id) /
                  "1700000000000-abcdef0123" / "image.png")
        upload.parent.mkdir(parents=True)
        upload.write_bytes(b"saved-upload")
        tls_dir = Path(config.DATA_DIR) / "tls"
        tls_dir.mkdir(mode=0o700)
        tls_key = tls_dir / "identity.key"
        tls_key.write_bytes(b"saved-private-tls-material")
        tls_key.chmod(0o600)
        web_tls.commit_change(web_tls.prepare_change(
            "https", "auto", "", "", ("snapshot.test", "127.0.0.1")))
        saved_web_identity = web_tls.settings_payload()["identities"]["auto"]
        assert saved_web_identity["available"] is True
        web_tls.commit_change(web_tls.prepare_change(
            "http", "auto", "", "", ("snapshot.test", "127.0.0.1")))
        db.add_event(directory_id, "user", {
            "text": "image at {}".format(upload),
        })
        db.add_event(scratch_id, "assistant", {"text": "remembered transcript"})
        directory_draft = "still editing\n\n[image attached: {} — view it with your image/file tools]".format(
            upload)
        db.set_session_draft(directory_id, directory_draft)
        db.set_session_draft(scratch_id, "unfinished prompt")
        invalid_draft_db = TEST_ROOT / "invalid-draft.db"
        db.backup_to(str(invalid_draft_db))
        invalid_connection = sqlite3.connect(str(invalid_draft_db))
        invalid_connection.execute(
            "UPDATE session_drafts SET revision=-1 WHERE session_id=?", (scratch_id,))
        invalid_connection.commit()
        invalid_connection.close()
        expect_snapshot_error(
            lambda: snapshots._validate_database(invalid_draft_db), "invalid session draft")
        invalid_draft_db.unlink()

        incomplete_draft_db = TEST_ROOT / "missing-drafts.db"
        db.backup_to(str(incomplete_draft_db))
        incomplete_connection = sqlite3.connect(str(incomplete_draft_db))
        incomplete_connection.execute("DROP TABLE session_drafts")
        incomplete_connection.commit()
        incomplete_connection.close()
        expect_snapshot_error(
            lambda: snapshots._validate_database(incomplete_draft_db),
            "schema is not current")
        incomplete_draft_db.unlink()
        missing_transport_db = TEST_ROOT / "missing-web-transport.db"
        db.backup_to(str(missing_transport_db))
        missing_transport_connection = sqlite3.connect(str(missing_transport_db))
        missing_transport_connection.execute(
            "DELETE FROM meta WHERE key=?", (web_tls.STATE_KEY,))
        missing_transport_connection.commit()
        missing_transport_connection.close()
        expect_snapshot_error(
            lambda: snapshots._validate_database(missing_transport_db),
            "transport state")
        missing_transport_db.unlink()
        tab_id = "s:0:{}".format(scratch_id)
        pane_id = "pane:snapshot"
        ui = {
            "puppy.theme": "light",
            "puppy.tabs": json.dumps({
                "version": 2,
                "tabs": [{"id": tab_id, "type": "session", "bid": 0,
                          "sid": scratch_id, "ended": False}],
                "active": tab_id,
                "activeGroup": pane_id,
                "layout": {"kind": "pane", "id": pane_id,
                           "tabs": [tab_id], "active": tab_id},
            }, separators=(",", ":")),
            "puppy.draft.s:0:{}".format(scratch_id): json.dumps({
                "_puppy_draft": 1, "text": "unfinished prompt",
                "base_revision": 1, "submitted": False,
            }, separators=(",", ":")),
        }
        expect_snapshot_error(
            lambda: snapshots.validate_ui_state({
                "puppy.tabs": json.dumps({"active": tab_id})}),
            "not current")

        listener_handoff.create(
            {"puppy_runtime_id": "pre-snapshot-runtime"}, "snapshot-user",
            "127.0.0.2", 10888, "127.0.0.2", "http://127.0.0.1:10888",
            "http", "auto", "")
        handoff_path = Path(config.DATA_DIR) / "runtime" / "listener-handoff.json"
        assert handoff_path.is_file()

        direct_archive = snapshots.create_archive(ui)
        archive_path = Path(direct_archive["path"])
        assert archive_path.stat().st_mode & 0o777 == 0o600
        with tarfile.open(str(archive_path), "r:gz") as archive:
            names = archive.getnames()
        assert snapshots.ARCHIVE_ROOT + "/manifest.json" in names
        assert not any("puppy.log" in name or "/snapshots/" in name or "/runtime/" in name
                       for name in names)

        # Mutate every restored surface and an excluded ordinary project file.
        config.set_value("instance_name", "mutated-instance")
        config.set_value("engines.usage_refresh_minutes", 5)
        config.set_value("uploads.max_file_size_mb", 2)
        config.set_value("browser.enabled", False)
        config.set_value("browser.color_scheme", "dark")
        config.set_value("browser.shared_storage", False)
        config.set_system_prompts(
            "mutated custom prompt", "mutated remote prompt",
            "mutated browser prompt", "mutated terminal prompt",
            "mutated spawn prompt")
        config.set_value("engines.auto_upgrade",
                         {"enabled": False, "mode": "now", "at": "03:30"})
        config.set_value("notify.enabled", False)
        config.set_value("notify.command", "mutated")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM session_drafts")
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM backends")
        db.execute("DELETE FROM meta WHERE key=?",
                   ("backend_last_known.{}".format(backend_id),))
        db.execute("DELETE FROM meta WHERE key=?",
                   ("backend_last_sessions.{}".format(backend_id),))
        web_tls.commit_change(web_tls.prepare_change(
            "http", "custom", "", "", ("snapshot.test", "127.0.0.1")))
        shutil.rmtree(Path(config.DATA_DIR) / "uploads")
        tls_key.write_bytes(b"mutated-private-tls-material")
        project_file.write_text("changed after export", encoding="utf-8")

        staged = snapshots.stage_import(str(archive_path))
        restored = snapshots.commit_import(staged)
        snapshots.discard_staged(staged)
        assert not handoff_path.exists()
        assert restored["ui"] == ui
        assert config.get("instance_name") == "saved-instance"
        assert config.get("sessions.default_cwd") == str(project)
        assert config.get("engines.usage_refresh_minutes") == 30
        assert config.get("engines.opencode") is None
        assert config.get("uploads.max_file_size_mb") == 19
        assert config.get("browser.enabled") is True
        assert config.get("browser.color_scheme") == "light"
        assert config.get("browser.shared_storage") is True
        assert web_tls.load_state() == {
            "format": 1, "scheme": "http", "https_source": "auto"}
        assert web_tls.settings_payload()["identities"]["auto"]["sha256"] == \
            saved_web_identity["sha256"]
        assert config.get("system_prompt.custom") == \
            "Keep answers concise.\nPreserve operator terminology."
        assert config.get("system_prompt.remote_workspace") == \
            "Remember that this project is stored on another node."
        assert config.get("system_prompt.browser") == \
            "Use the shared browser before standalone automation."
        assert config.get("system_prompt.terminal") == \
            "Use the shared terminal only when explicitly requested."
        assert config.get("system_prompt.spawn") == \
            "Spawn delegate agents only on an explicit request."
        missing_prompts = config.export_data()
        missing_prompts.pop("system_prompt", None)
        previous_prompt_shape = config.export_data()
        previous_prompt_shape["system_prompt"].pop("remote_workspace", None)
        previous_terminal_prompt_shape = config.export_data()
        previous_terminal_prompt_shape["system_prompt"].pop("terminal", None)
        previous_spawn_prompt_shape = config.export_data()
        previous_spawn_prompt_shape["system_prompt"].pop("spawn", None)
        missing_cwd = config.export_data()
        missing_cwd["sessions"].pop("default_cwd", None)
        unknown_selection = config.export_data()
        unknown_selection["engines"]["opencode"] = {
            "models": ["provider/model-a", "second/model-b"]}
        for invalid in (missing_prompts, previous_prompt_shape,
                        previous_terminal_prompt_shape,
                        previous_spawn_prompt_shape,
                        missing_cwd, unknown_selection):
            try:
                config.normalize_import(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("outdated config shape was accepted")
        noncanonical = config.export_data()
        noncanonical["web"]["port"] = float(noncanonical["web"]["port"])
        try:
            config.normalize_import(noncanonical)
        except ValueError as exc:
            assert "canonical" in str(exc), str(exc)
        else:
            raise AssertionError("noncanonical config values were accepted")
        assert config.get("engines.auto_upgrade") == \
            {"enabled": True, "mode": "at", "at": "04:15"}
        # an unattended upgrade schedule must survive import validation intact
        for bad in ({"mode": "whenever"}, {"at": "24:00"}, {"enabled": "yes"}):
            try:
                config.normalize_import(dict(config.export_data(),
                                             engines={"usage_refresh_minutes": 30,
                                                      "auto_upgrade": bad}))
            except ValueError:
                pass
            else:
                raise AssertionError("accepted an invalid schedule: {}".format(bad))
        # and a tampered archive cannot smuggle in an unknown rendering mode
        try:
            config.normalize_import(dict(config.export_data(),
                                         browser={"enabled": True,
                                                  "color_scheme": "neon",
                                                  "shared_storage": False}))
        except ValueError as exc:
            assert "color_scheme" in str(exc), str(exc)
        else:
            raise AssertionError("an invalid browser color scheme was accepted")
        # nor a non-boolean shared sign-in store setting
        try:
            config.normalize_import(dict(config.export_data(),
                                         browser={"enabled": True,
                                                  "color_scheme": "dark",
                                                  "shared_storage": "yes"}))
        except ValueError as exc:
            assert "shared_storage" in str(exc), str(exc)
        else:
            raise AssertionError("an invalid shared storage setting was accepted")
        for field in ("custom", "remote_workspace", "browser", "terminal",
                      "spawn"):
            for invalid_prompt in (
                    None, "x" * (config.MAX_SYSTEM_PROMPT_CHARS + 1), "bad\x00text"):
                prompts = dict(config.export_data()["system_prompt"])
                prompts[field] = invalid_prompt
                try:
                    config.normalize_import(dict(
                        config.export_data(), system_prompt=prompts))
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        "an invalid {} system prompt was accepted".format(field))
        assert config.get("notify.enabled") is True
        assert config.get("notify.backend") == 1
        assert config.get("notify.command") == "printf done: %s {session}"
        assert len(db.list_sessions(include_archived=True)) == 2
        assert session_runner.parse_used_config(
            db.get_session(directory_id)["used_config"]) == {"model": "gpt-5.6-sol", "effort": "max"}
        assert db.query_one("SELECT token FROM backends")["token"] == "private-backend-token"
        assert db.query_one("SELECT auto_upgrade FROM backends")["auto_upgrade"] == 1
        assert json.loads(db.query_one("SELECT urls FROM backends")["urls"]) == [
            "https://backend.test:10888", "https://backend-vpn.test:10888"]
        restored_backend_cache = db.meta_get(
            "backend_last_known.{}".format(backend_id))
        assert restored_backend_cache["uploads"]["max_file_size_mb"] == 19
        restored_session_cache = db.meta_get(
            "backend_last_sessions.{}".format(backend_id))
        assert restored_session_cache == {"version": 1, "sessions": [
            {"id": 91, "name": "cached remote session",
             "status": "idle", "active_since": None}]}
        assert db.query_one("SELECT username FROM users")["username"] == "snapshot-user"
        restored_scratch = db.get_session(scratch_id)
        assert restored_scratch["cwd"] != str(original_scratch)
        assert (Path(restored_scratch["cwd"]) / "nested" / "file.txt").read_text(
            encoding="utf-8") == "scratch contents"
        assert (Path(restored_scratch["cwd"]) / "safe-link").read_text(
            encoding="utf-8") == "scratch contents"
        assert not original_scratch.exists()
        restored_upload = (Path(config.DATA_DIR) / "uploads" / str(directory_id) /
                           "1700000000000-abcdef0123" / "image.png")
        assert restored_upload.read_bytes() == b"saved-upload"
        assert tls_key.read_bytes() == b"saved-private-tls-material"
        assert tls_key.stat().st_mode & 0o777 == 0o600
        assert str(Path(config.DATA_DIR).resolve() / "uploads") in \
            db.get_events(directory_id)[0]["data"]["text"]
        assert db.get_session_draft(scratch_id)["text"] == "unfinished prompt"
        restored_directory_draft = db.get_session_draft(directory_id)
        assert restored_directory_draft["revision"] == 1
        assert restored_directory_draft["text"] == directory_draft
        assert str(restored_upload) in restored_directory_draft["text"]
        assert project_file.read_text(encoding="utf-8") == "changed after export"

        unsupported = Path(restored_scratch["cwd"]) / "named-pipe"
        os.mkfifo(str(unsupported))
        expect_snapshot_error(
            lambda: snapshots.create_archive(ui), "unsupported file type")
        unsupported.unlink()

        await exercise_http(ui, directory_id, project)

        # A failed database install must put config and filesystem trees back.
        config.set_value("instance_name", "rollback-current")
        config.set_value("engines.usage_refresh_minutes", 60)
        config.set_value("uploads.max_file_size_mb", 23)
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
        assert config.get("engines.usage_refresh_minutes") == 60
        assert config.get("uploads.max_file_size_mb") == 23
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
        tamper_stage = tamper_root / snapshots.ARCHIVE_ROOT
        manifest_path = tamper_stage / "manifest.json"
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        unknown_manifest = dict(original_manifest, obsolete_field=True)
        manifest_path.write_text(json.dumps(unknown_manifest), encoding="utf-8")
        noncurrent_manifest = TEST_ROOT / "noncurrent-manifest.tar.gz"
        with tarfile.open(str(noncurrent_manifest), "w:gz") as archive:
            archive.add(str(tamper_stage), arcname=snapshots.ARCHIVE_ROOT)
        expect_snapshot_error(
            lambda: snapshots.stage_import(str(noncurrent_manifest)),
            "unsupported Puppy snapshot format")
        manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
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
