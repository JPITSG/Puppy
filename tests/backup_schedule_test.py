#!/usr/bin/env python3
"""Daily backups through real archives and HTTP, without engines or external services."""
import asyncio
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("backup-schedule-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from aiohttp import web, ClientSession
from puppy import backup_schedule as backups, config, db, snapshots
from puppy.web import build_app


def refused(call):
    try:
        call()
    except (backups.BackupError, snapshots.SnapshotError):
        return
    raise AssertionError("invalid state accepted")


async def main():
    config.load()
    db.connect()
    app = build_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.cleanup_ctx.clear()
    scheduler = app["puppy_backups"]
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    url = "http://127.0.0.1:{}/api/snapshot/".format(site._server.sockets[0].getsockname()[1])
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    folder = ROOT / "saved"
    settings = {"enabled": False, "at": "03:00", "directory": str(folder), "keep": 2}
    try:
        async with ClientSession() as http:
            async def request(method, route, body=None, status=200):
                async with http.request(method, url + route, headers=headers, json=body) as response:
                    result = await response.json()
                    assert response.status == status, (response.status, result)
                    return result

            async with http.get(url + "backups", allow_redirects=False) as response:
                assert response.status in (302, 401)
            fresh = await request("GET", "backups")
            assert not fresh["settings"]["enabled"] and fresh["settings"]["keep"] == 7
            assert not fresh["files"] and not fresh["history"]
            app["puppy_snapshot_busy"] = "restore"
            await request("GET", "backups", status=503)
            await request("GET", "saved/" + "a" * 32, status=503)
            await request("POST", "backups/run", {}, 409)
            app["puppy_snapshot_busy"] = None
            for changes in ({"at": "24:00"}, {"keep": True}, {"keep": 0}, {"keep": 101},
                            {"enabled": 1}, {"directory": "relative/path"}, {"unknown": 1},
                            {"directory": str(Path(config.DATA_DIR) / "uploads" / "nested")}):
                await request("PUT", "backups", {**settings, **changes}, 400)
            await request("PUT", "backups", settings)
            folder.mkdir()
            unrelated = folder / "other-backup.tar.gz"
            unrelated.write_bytes(b"never rotate this")
            old = None
            for number in range(3):
                await request("POST", "backups/run", {}, 202)
                pending = backups._load()["pending"]
                # A second press coalesces into the same durable request.
                await request("POST", "backups/run", {}, 202)
                assert backups._load()["pending"]["id"] == pending["id"]
                app["puppy_snapshot_busy"] = "export"
                await request("POST", "backups/run", {}, 202)
                assert backups._load()["pending"]["id"] == pending["id"]
                app["puppy_snapshot_busy"] = None
                with patch.object(snapshots, "blockers", return_value=["running work"]):
                    await scheduler.tick()
                    waiting = await request("GET", "backups")
                    assert waiting["status"] == "waiting" and "running" in waiting["waiting"]
                await scheduler.tick()
                result = await request("GET", "backups")
                assert result["status"] == "idle" and result["history"][0]["tone"] == "ok", result
                assert len(result["files"]) == min(number + 1, 2), result
                entry = backups._load()["files"][-1]
                path = folder / entry["filename"]
                assert path.stat().st_mode & 0o777 == 0o600
                with tarfile.open(str(path), "r:gz") as archive:
                    assert json.load(archive.extractfile(snapshots.ARCHIVE_ROOT + "/ui.json")) == {}
                    archive_db = ROOT / "archive.db"
                    archive_db.write_bytes(archive.extractfile(snapshots.ARCHIVE_ROOT + "/puppy.db").read())
                with sqlite3.connect(str(archive_db)) as connection:
                    saved = backups._load(connection)
                assert saved["settings"] == settings and saved["pending"] is None
                staged = snapshots.stage_import(str(path))
                snapshots.discard_staged(staged)
                for _ in range(2):
                    async with http.get(url.replace("/api/snapshot/", "") + result["files"][0]["download"], headers=headers) as response:
                        assert response.status == 200 and await response.read() == path.read_bytes()
                        assert response.headers["Cache-Control"] == "private, no-store"
                if number == 0:
                    old = path
            assert not old.exists() and unrelated.read_bytes() == b"never rotate this"
            print("PASS: authenticated saved backups, gzip restore validation, repeated downloads, private files and owned-only rotation", flush=True)

            # The disabled schedule still supports Run now. Saving a failed
            # backup never removes the last good copies.
            before = [p.name for p in folder.iterdir()]
            await request("POST", "backups/run", {}, 202)
            with patch.object(snapshots, "create_archive", side_effect=OSError("disk unavailable")):
                await scheduler.tick()
            assert sorted(before) == sorted(p.name for p in folder.iterdir())
            assert backups._load()["history"][-1]["tone"] == "bad"

            # A failed publication leaves no incomplete file or lost download.
            # If publication reached its final link, retain it with a warning
            # and keep the older good copies until the next successful run.
            scheduler.queue()
            with patch.object(backups, "publish_archive", side_effect=OSError("publication refused")):
                await scheduler.tick()
            assert sorted(before) == sorted(p.name for p in folder.iterdir())
            publish = backups.publish_archive

            def linked_then_failed(entry):
                publish(entry)
                raise OSError("directory sync failed")

            scheduler.queue()
            with patch.object(backups, "publish_archive", linked_then_failed):
                await scheduler.tick()
            partial = await request("GET", "backups")
            assert partial["history"][0]["tone"] == "warn" and partial["files"][0]["available"]
            assert len(partial["files"]) == 3

            # A changed archive is neither downloaded nor deleted by rotation.
            entry = backups._load()["files"][0]
            path = folder / entry["filename"]
            moved = folder / "retained-original"
            path.rename(moved)
            path.symlink_to(unrelated)
            async with http.get(url + "saved/" + entry["id"], headers=headers) as response:
                assert response.status == 404
            await request("POST", "backups/run", {}, 202)
            await scheduler.tick()
            assert path.is_symlink() and unrelated.exists()
            assert backups._load()["history"][-1]["tone"] == "warn"
            path.unlink()
            moved.rename(path)

            # Journal recovery publishes the exact completed file after a crash.
            scheduler.queue()
            current = backups._load()
            archive = snapshots.create_archive({})
            entry = backups.stage_saved_archive(archive, current["pending"], settings["directory"])
            snapshots.discard_export(archive)
            current["pending"]["file"] = entry
            backups._save(current)
            replacement = backups.Scheduler(app, scheduler.exporter, scheduler.conflict)
            with patch.object(snapshots, "create_archive", side_effect=AssertionError("must recover, not rebuild")):
                await replacement.tick()
            assert backups._load()["pending"] is None and (folder / entry["filename"]).is_file()
            print("PASS: busy waits, failures keep good copies, replaced files are refused and journalled publication resumes after restart", flush=True)

            settings["enabled"] = True
            scheduler.settings(settings)
            now = time.time()
            current = backups._load()
            current["next_at"] = now - 3 * 86400
            backups._save(current)
            with patch.object(snapshots, "blockers", return_value=["active session"]):
                await scheduler.tick(now)
            assert backups._load()["pending"]["source"] == "scheduled"
            await scheduler.tick(now)
            state = backups._load()
            assert state["next_at"] > now and state["pending"] is None
            count = len(state["history"])
            await scheduler.tick(now + 1)
            assert len(backups._load()["history"]) == count
            # Changing the local calendar date uses mktime, not 24-hour steps.
            previous_tz = os.environ.get("TZ")
            os.environ["TZ"] = "Europe/Warsaw"
            time.tzset()
            try:
                for date in ((2026, 3, 29), (2026, 10, 25), (2026, 12, 31)):
                    stamp = time.mktime((*date, 0, 0, 0, -1, -1, -1))
                    due = backups.next_occurrence("03:00", stamp)
                    assert time.localtime(due).tm_hour == 3 and due > stamp
                    tomorrow = backups.next_occurrence("03:00", due)
                    assert datetime.date(*time.localtime(tomorrow)[:3]) > datetime.date(*date)
            finally:
                if previous_tz is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = previous_tz
                time.tzset()
            # Disable withdraws only a scheduled wait.
            state["next_at"] = now - 1
            backups._save(state)
            with patch.object(snapshots, "blockers", return_value=["busy"]):
                await scheduler.tick(now)
            settings["enabled"] = False
            scheduler.settings(settings)
            assert backups._load()["pending"] is None and backups._load()["next_at"] == 0
            print("PASS: daily scheduling, missed-day coalescing, DST/calendar boundaries, disable and restart-safe attempts", flush=True)

            saved = backups._load()
            db.meta_set(backups.META_KEY, {**saved, "format": 0})
            refused(lambda: backups.validate_persisted(db.connect()))
            refused(lambda: snapshots.create_archive({}))
            backups._save(saved)
            # Restore coverage is carried in the ordinary database snapshot;
            # pending commands are omitted rather than replayed on import.
            archive = snapshots.create_archive({"puppy.theme": "light"})
            staged = snapshots.stage_import(archive["path"])
            backups._save(backups._new_state())
            snapshots.commit_import(staged)
            assert backups._load() == saved
            snapshots.discard_staged(staged)
            snapshots.discard_export(archive)
            print("PASS: exact persisted shapes and snapshot round-trip of schedule, catalogue and history", flush=True)

            # The real lifecycle wakes on Run now and drains a current archive
            # during shutdown. Manual export/restore cannot race publication.
            started, finish = asyncio.Event(), asyncio.Event()
            export = scheduler.exporter

            async def held_export(app, ui):
                started.set()
                await finish.wait()
                return await export(app, ui)

            scheduler.exporter = held_export
            await scheduler.start(app)
            scheduler.queue()
            await asyncio.wait_for(started.wait(), 5)
            with patch.object(backups, "publish_archive", publish):
                assert scheduler.running
                await request("POST", "export", {"ui": {}}, 409)
                await request("PUT", "backups", settings, 400)
                stopping = asyncio.create_task(scheduler.stop(app))
                await asyncio.sleep(0)
                assert not stopping.done()
                finish.set()
                await asyncio.wait_for(stopping, 15)
            assert backups._load()["pending"] is None
            assert backups._load()["history"][-1]["tone"] == "ok"
            print("PASS: lifecycle wake, exclusive export/restore and graceful completion at shutdown", flush=True)
    finally:
        await scheduler.stop(app)
        await server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
