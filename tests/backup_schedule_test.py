#!/usr/bin/env python3
"""Daily backups through real archives and HTTP, without engines or external services."""
import asyncio
import datetime
import json
import os
import secrets
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
    # what Save sends: the schedule's four settings and the archive limit beside them
    body = lambda **changes: {**settings, "archive_limit_mib": 512, **changes}
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
            assert fresh["archive_limit_mib"] == 512 and \
                fresh["archive_limit"] == {"min": 16, "max": 16384, "default": 512}
            assert db.meta_get(backups.LIMIT_KEY) is None, "no record until a limit is saved"
            assert not fresh["files"] and not fresh["history"]
            app["puppy_snapshot_busy"] = "restore"
            await request("GET", "backups", status=503)
            await request("GET", "saved/" + "a" * 32, status=503)
            await request("POST", "backups/run", {}, 409)
            app["puppy_snapshot_busy"] = None
            for changes in ({"at": "24:00"}, {"keep": True}, {"keep": 0}, {"keep": 101},
                            {"enabled": 1}, {"directory": "relative/path"}, {"unknown": 1},
                            {"directory": str(Path(config.DATA_DIR) / "uploads" / "nested")}):
                await request("PUT", "backups", body(**changes), 400)
            await request("PUT", "backups", settings, 400)   # the limit is part of every save
            await request("PUT", "backups", body())
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

            # Deleting a saved copy by hand: its file, its entry and its history line
            # go; a replaced file or a missing one only leaves the list.
            def catalogue(content=b"saved archive"):
                ident = secrets.token_hex(16)
                path = folder / "puppy-backup-{}.tar.gz".format(ident)
                path.write_bytes(content)
                path.chmod(0o600)
                info = path.stat()
                current = backups._load()
                current["files"].append({"id": ident, "at": time.time(), "directory": str(folder),
                    "filename": path.name, "size": info.st_size, "device": info.st_dev,
                    "inode": info.st_ino, "protection": "none"})
                current["history"] = (current["history"] + [{"id": ident, "at": time.time(),
                    "source": "manual", "tone": "ok", "message": "Backup saved"}])[-backups.HISTORY_LIMIT:]
                backups._save(current)
                return ident, path

            ident, path = catalogue()
            listed = await request("GET", "backups")
            mine = next(item for item in listed["files"] if item["id"] == ident)
            assert mine["directory"] == str(folder) and mine["available"], "the list says where each copy lives"
            app["puppy_snapshot_busy"] = "restore"
            await request("DELETE", "saved/" + ident, status=503)
            app["puppy_snapshot_busy"] = "export"
            await request("DELETE", "saved/" + ident, status=409)
            app["puppy_snapshot_busy"] = None
            scheduler.running = True
            refusal = await request("DELETE", "saved/" + ident, status=409)
            assert "current backup" in refusal["error"]
            scheduler.running = False
            await request("DELETE", "saved/" + "f" * 32, status=404)
            async with http.delete(url + "saved/" + ident) as response:
                assert response.status in (302, 401), "signed-in only"
            assert path.exists()
            answer = await request("DELETE", "saved/" + ident)
            assert answer["deleted"] == "deleted" and not path.exists()
            assert ident not in {item["id"] for item in answer["files"]}
            assert ident not in {item["id"] for item in answer["history"]}, "never listed as rotated"
            assert ident not in {item["id"] for item in backups._load()["files"]}
            await request("DELETE", "saved/" + ident, status=404)
            async with http.get(url + "saved/" + ident, headers=headers) as response:
                assert response.status == 404
            assert unrelated.read_bytes() == b"never rotate this"

            # a file replaced under its name is left alone; the entry still goes
            ident, path = catalogue()
            moved = folder / "kept-original"
            path.rename(moved)
            path.symlink_to(unrelated)
            answer = await request("DELETE", "saved/" + ident)
            assert answer["deleted"] == "kept" and path.is_symlink() and unrelated.exists() and moved.exists()
            assert ident not in {item["id"] for item in backups._load()["files"]}
            path.unlink()
            moved.unlink()
            # a file already gone only leaves the list
            ident, path = catalogue()
            path.unlink()
            assert (await request("DELETE", "saved/" + ident))["deleted"] == "missing"
            # a file that cannot be removed stays listed, and says why
            ident, path = catalogue()
            with patch.object(backups.os, "unlink", side_effect=PermissionError(13, "Permission denied")):
                failed = await request("DELETE", "saved/" + ident, status=409)
            assert failed["error"] == "Could not delete the backup · Permission denied"
            assert path.exists() and ident in {item["id"] for item in backups._load()["files"]}
            # a backup never starts while a deletion holds the catalogue
            scheduler.deleting = True
            scheduler.queue()
            await scheduler.tick()
            assert backups._load()["pending"] is not None, "the backup waits for the deletion"
            refusal = await request("DELETE", "saved/" + ident, status=409)
            assert "other backup" in refusal["error"]
            scheduler.deleting = False
            await scheduler.tick()
            assert backups._load()["pending"] is None
            assert ident in {item["id"] for item in backups._load()["files"]}, "the backup kept the listed copy"
            await request("DELETE", "saved/" + ident)
            assert not path.exists()
            print("PASS: a saved backup deleted by hand - its own file, entry and history line - while a replaced file "
                  "is left alone, a missing one only leaves the list, a failure keeps it listed, and a delete is "
                  "refused during a backup, export, restore or another delete", flush=True)

            settings["enabled"] = True
            scheduler.settings(body())
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
            scheduler.settings(body())
            assert backups._load()["pending"] is None and backups._load()["next_at"] == 0
            print("PASS: daily scheduling, missed-day coalescing, DST/calendar boundaries, disable and restart-safe attempts", flush=True)

            # The archive limit: refused outside its range, saved with the
            # schedule, and holding every backup and import to it.
            for limit in (None, 15, 16385, True, "512", 512.5):
                await request("PUT", "backups", body(archive_limit_mib=limit), 400)
            await request("PUT", "backups", {k: v for k, v in body().items() if k != "archive_limit_mib"}, 400)
            assert snapshots.limits(512) == {"archive": 512 * snapshots.MIB, "member": snapshots.GIB,
                                             "extracted": 2 * snapshots.GIB}, "the default keeps every old bound"
            assert snapshots.limits(16)["member"] == snapshots.GIB and \
                snapshots.limits(16)["extracted"] == 2 * snapshots.GIB, "never below the floors"
            assert snapshots.limits(1024) == {"archive": snapshots.GIB, "member": 2 * snapshots.GIB,
                                              "extracted": 4 * snapshots.GIB}, "a file twice it, contents four times"
            blob = Path(config.DATA_DIR) / "uploads" / "limit-test"
            blob.mkdir(parents=True)
            (blob / "noise.bin").write_bytes(os.urandom(17 * snapshots.MIB))   # nothing to compress
            small = await request("PUT", "backups", body(archive_limit_mib=16))
            assert small["archive_limit_mib"] == 16
            assert db.meta_get(backups.LIMIT_KEY) == {"format": 1, "archive_mib": 16}
            assert backups._load()["settings"] == settings, "the schedule record keeps its own shape"
            try:
                snapshots.create_archive({})
                raise AssertionError("an archive over the limit was made")
            except snapshots.SnapshotError as exc:
                assert str(exc) == "snapshot archive is 18 MiB, over the 16 MiB archive limit", str(exc)
            await request("POST", "backups/run", {}, 202)
            await scheduler.tick()
            failed = backups._load()["history"][-1]
            assert failed["tone"] == "bad" and failed["message"] == \
                "Could not save backup · snapshot archive is 18 MiB, over the 16 MiB archive limit", failed
            await request("PUT", "backups", body(archive_limit_mib=32))
            await request("POST", "backups/run", {}, 202)
            await scheduler.tick()
            assert backups._load()["history"][-1]["tone"] == "ok", "a raised limit saves it"
            big = backups._load()["files"][-1]
            assert big["size"] > 16 * snapshots.MIB
            # lowering the limit again never turns the saved copy into an unreadable record
            await request("PUT", "backups", body(archive_limit_mib=16))
            assert backups._load()["files"][-1] == big
            listed = await request("GET", "backups")
            assert listed["files"][0]["id"] == big["id"] and listed["files"][0]["available"]
            # an import over the limit is refused before it is staged: by its length, or as it streams
            archive_bytes = (folder / big["filename"]).read_bytes()
            refused(lambda: snapshots.stage_import(str(folder / big["filename"])))
            async with http.post(url + "import", data=archive_bytes,
                                 headers={**headers, "Content-Type": "application/gzip"}) as response:
                answer = await response.json()
                assert response.status == 413 and answer["error"] == \
                    "snapshot archive is 18 MiB, over the 16 MiB archive limit", answer

            async def stream():
                for start in range(0, len(archive_bytes), snapshots.MIB):
                    yield archive_bytes[start:start + snapshots.MIB]

            async with http.post(url + "import", data=stream(),
                                 headers={**headers, "Content-Type": "application/gzip"}) as response:
                answer = await response.json()
                assert response.status == 413 and answer["error"] == \
                    "snapshot archive exceeds the 16 MiB archive limit", answer
            await request("PUT", "backups", body(archive_limit_mib=32))
            staged = snapshots.stage_import(str(folder / big["filename"]))
            snapshots.discard_staged(staged)
            # single files and unpacked contents follow the limit (floors lowered so they show)
            (blob / "noise.bin").unlink()
            with patch.object(snapshots, "MEMBER_FLOOR_BYTES", 8 * snapshots.MIB), \
                    patch.object(snapshots, "EXTRACTED_FLOOR_BYTES", 8 * snapshots.MIB):
                for number in range(3):   # zeros: large inside, nothing in the archive
                    (blob / "zeros-{}.bin".format(number)).write_bytes(bytes(30 * snapshots.MIB))
                await request("PUT", "backups", body(archive_limit_mib=16))
                try:
                    snapshots.create_archive({})
                    raise AssertionError("contents past four times the limit were archived")
                except snapshots.SnapshotError as exc:
                    assert str(exc) == "snapshot contents are too large · at most 32 MiB per file and " \
                        "64 MiB in all", str(exc)
                await request("PUT", "backups", body(archive_limit_mib=32))
                packed = snapshots.create_archive({})
                assert packed["size"] < 16 * snapshots.MIB
                await request("PUT", "backups", body(archive_limit_mib=16))
                try:
                    snapshots.stage_import(packed["path"])
                    raise AssertionError("an archive expanding past four times the limit was staged")
                except snapshots.SnapshotError as exc:
                    assert str(exc) == "snapshot expands beyond the 64 MiB limit", str(exc)
                snapshots.discard_export(packed)
                for number in range(3):
                    (blob / "zeros-{}.bin".format(number)).unlink()
                (blob / "zeros-big.bin").write_bytes(bytes(40 * snapshots.MIB))
                await request("PUT", "backups", body(archive_limit_mib=32))
                packed = snapshots.create_archive({})
                await request("PUT", "backups", body(archive_limit_mib=16))
                try:
                    snapshots.create_archive({})
                    raise AssertionError("a file past twice the limit was archived")
                except snapshots.SnapshotError as exc:
                    assert "at most 32 MiB per file" in str(exc), str(exc)
                try:
                    snapshots.stage_import(packed["path"])
                    raise AssertionError("a file past twice the limit was staged")
                except snapshots.SnapshotError as exc:
                    assert str(exc) == "snapshot member is larger than the 32 MiB per-file limit", str(exc)
                snapshots.discard_export(packed)
            await request("PUT", "backups", body(archive_limit_mib=32))
            shutil.rmtree(str(blob))
            # the record carries through a backup and comes back with it
            archive = snapshots.create_archive({})
            staged = snapshots.stage_import(archive["path"])
            db.meta_set(backups.LIMIT_KEY, {"format": 1, "archive_mib": 64})
            snapshots.commit_import(staged)
            snapshots.discard_staged(staged)
            snapshots.discard_export(archive)
            assert backups.archive_limit_mib() == 32, "the limit comes back as backed up"
            # an archive from before the setting carries no record: the default, still current
            db.meta_del(backups.LIMIT_KEY)
            archive = snapshots.create_archive({})
            db.meta_set(backups.LIMIT_KEY, {"format": 1, "archive_mib": 64})
            staged = snapshots.stage_import(archive["path"])
            snapshots.commit_import(staged)
            snapshots.discard_staged(staged)
            snapshots.discard_export(archive)
            assert db.meta_get(backups.LIMIT_KEY) is None and backups.archive_limit_mib() == 512
            # any other shape is refused, never read: at startup, by an export and in an archive
            for wrong in ({"format": 1, "archive_mib": 15}, {"format": 2, "archive_mib": 512},
                          {"format": 1, "archive_mib": 512, "extra": 1}, {"archive_mib": 512},
                          {"format": 1, "archive_mib": "512"}, 512):
                db.meta_set(backups.LIMIT_KEY, wrong)
                refused(lambda: backups.validate_limit_persisted(db.connect()))
                refused(lambda: snapshots.create_archive({}))
                copy = ROOT / "limit-check.db"
                db.backup_to(str(copy))
                refused(lambda: snapshots._validate_database(copy))
                copy.unlink()
            db.meta_del(backups.LIMIT_KEY)
            print("PASS: the archive limit - its range, saved beside the schedule, an export, a saved backup and "
                  "an import (by length and streamed) held to it, file and unpacked bounds following it, a lowered "
                  "limit keeping saved copies, the record through a backup, archives from before it, and every "
                  "other shape refused", flush=True)

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
                await request("PUT", "backups", body(), 400)
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
