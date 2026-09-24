"""Controller-owned daily backups, using the ordinary snapshot exporter.

The schedule and bounded catalogue live in one strictly validated meta record,
so snapshots carry them without changing the database or configuration schema.
Archive files themselves are outside snapshot coverage. Publication is journalled:
a completed private temporary file is recorded before it becomes downloadable.
Only catalogue-owned files with matching identities may be downloaded or rotated.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from aiohttp import web
from puppy import config, db, operations, snapshots, workspaces

log = logging.getLogger("puppy.backup_schedule")
META_KEY = "backup_schedule"
FORMAT = 1
KEEP_MAX = 100
FILES_MAX = 1000
HISTORY_LIMIT = 20
POLL_SECONDS = 15
AT_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
ID_RE = re.compile(r"[a-f0-9]{32}\Z")
FILE_KEYS = {"id", "at", "directory", "filename", "size", "device", "inode", "protection"}
HEADERS = {"Cache-Control": "private, no-store"}


class BackupError(ValueError):
    pass


def _stamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _directory(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and \
        not any(ord(char) < 32 for char in value) and \
        os.path.isabs(value) and os.path.normpath(value) == value


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != {"enabled", "at", "directory", "keep"} or \
            type(value["enabled"]) is not bool or not isinstance(value["at"], str) or \
            not AT_RE.fullmatch(value["at"]) or not _directory(value["directory"]) or \
            type(value["keep"]) is not int or not 1 <= value["keep"] <= KEEP_MAX:
        raise BackupError("Use a daily time, an absolute directory, and 1–{} copies".format(KEEP_MAX))
    return dict(value)


def _valid_file(value):
    return isinstance(value, dict) and set(value) == FILE_KEYS and \
        isinstance(value["id"], str) and bool(ID_RE.fullmatch(value["id"])) and \
        _stamp(value["at"]) and _directory(value["directory"]) and \
        value["filename"] == "puppy-backup-{}.tar.gz".format(value["id"]) and \
        type(value["size"]) is int and 0 < value["size"] <= snapshots.MAX_ARCHIVE_BYTES and \
        all(type(value[key]) is int and value[key] >= 0 for key in ("device", "inode")) and \
        value["protection"] == "none"


def _new_state():
    return {"format": FORMAT, "settings": {"enabled": False, "at": "03:00",
            "directory": str(Path(config.DATA_DIR).resolve() / "backups"), "keep": 7},
            "next_at": 0, "pending": None, "files": [], "history": []}


def _load(connection=None):
    sql = "SELECT value FROM meta WHERE key=?"
    row = connection.execute(sql, (META_KEY,)).fetchone() if connection is not None else \
        db.query_one(sql, (META_KEY,))
    if row is None:
        return _new_state()
    def require(condition):
        if not condition:
            raise ValueError("invalid shape")

    try:
        value = json.loads(row[0])
        require(isinstance(value, dict) and set(value) == {"format", "settings", "next_at", "pending", "files", "history"})
        require(type(value["format"]) is int and value["format"] == FORMAT)
        validate_settings(value["settings"])
        require(_stamp(value["next_at"]))
        require(bool(value["next_at"]) == value["settings"]["enabled"])
        require(isinstance(value["files"], list) and len(value["files"]) <= FILES_MAX)
        require(all(_valid_file(item) for item in value["files"]))
        require(len({item["id"] for item in value["files"]}) == len(value["files"]))
        pending = value["pending"]
        if pending is not None:
            require(isinstance(pending, dict) and set(pending) == {"id", "at", "source", "file"})
            require(isinstance(pending["id"], str) and ID_RE.fullmatch(pending["id"]))
            require(_stamp(pending["at"]) and pending["source"] in ("manual", "scheduled"))
            require(pending["id"] not in {item["id"] for item in value["files"]})
            require(pending["file"] is None or (_valid_file(pending["file"]) and pending["file"]["id"] == pending["id"]))
        require(isinstance(value["history"], list) and len(value["history"]) <= HISTORY_LIMIT)
        for item in value["history"]:
            require(isinstance(item, dict) and set(item) == {"id", "at", "source", "tone", "message"})
            require(isinstance(item["id"], str) and ID_RE.fullmatch(item["id"]))
            require(_stamp(item["at"]) and item["source"] in ("manual", "scheduled"))
            require(item["tone"] in ("ok", "warn", "bad"))
            require(isinstance(item["message"], str) and 0 < len(item["message"]) <= 1000)
        return value
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise BackupError("backup schedule has an unsupported persisted shape") from exc


def validate_persisted(connection):
    _load(connection)


def prepare_snapshot(path):
    """Back up settings/history, never replay an in-flight Run now on restore."""
    with sqlite3.connect(str(path)) as connection:
        if connection.execute("SELECT 1 FROM meta WHERE key=?", (META_KEY,)).fetchone():
            value = _load(connection)
            value["pending"] = None
            connection.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(value), META_KEY))


def _save(value):
    db.meta_set(META_KEY, value)


def next_occurrence(at, now, after_today=False):
    """Next local wall-clock occurrence, including DST and calendar boundaries."""
    local = time.localtime(now)
    day = datetime.date(local.tm_year, local.tm_mon, local.tm_mday)
    hour, minute = map(int, at.split(":"))
    for offset in range(1 if after_today else 0, 3):
        date = day + datetime.timedelta(days=offset)
        candidate = time.mktime((date.year, date.month, date.day, hour, minute, 0, -1, -1, -1))
        if candidate > now:
            return candidate
    raise BackupError("could not determine the next backup time")


def validate_destination(directory):
    path = Path(directory)
    # A backup must never become part of the next backup's own payload or be
    # swept away as temporary snapshot work. Ordinary external folders are OK.
    root = Path(config.DATA_DIR).resolve()
    excluded = [root / name for name in ("uploads", "tls", "workspace/keeps", "snapshots")]
    excluded.extend(Path(session["cwd"]).resolve() for session in db.list_sessions(include_archived=True)
                    if workspaces.is_temporary(session))
    resolved = path.resolve()
    if any(resolved == item or item in resolved.parents for item in excluded):
        raise BackupError("Choose a backup directory outside snapshot contents and staging")
    if resolved != path:
        raise BackupError("Use the directory's absolute path without symbolic links")
    return path


def _open_directory(directory, create=False):
    path = validate_destination(directory)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _identity(info, entry):
    return stat.S_ISREG(info.st_mode) and info.st_dev == entry["device"] and \
        info.st_ino == entry["inode"] and info.st_size == entry["size"]


def _open_owned(entry, temporary=False):
    directory = _open_directory(entry["directory"])
    try:
        name = "." + entry["filename"] + ".tmp" if temporary else entry["filename"]
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not _identity(os.fstat(descriptor), entry):
            os.close(descriptor)
            raise BackupError("saved backup was replaced or changed")
        return descriptor
    finally:
        os.close(directory)


def _copy_archive(source, output):
    # Archive protection extension point. Future encryption wraps the already
    # compressed snapshot stream here; no keys or encryption UI exist today.
    operations.copyfileobj(source, output)


ARCHIVE_WRITERS = {"none": _copy_archive}


def stage_saved_archive(result, pending, directory):
    fd = _open_directory(directory, create=True)
    filename = "puppy-backup-{}.tar.gz".format(pending["id"])
    temporary = "." + filename + ".tmp"
    try:
        # A retry after a crash may own an incomplete file under its durable,
        # unpredictable job ID. Never follow a link or remove another type.
        try:
            info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise BackupError("backup staging path is not a private owned file")
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(descriptor, "wb") as output, open(result["path"], "rb") as source:
                ARCHIVE_WRITERS["none"](source, output)
                output.flush()
                os.fsync(output.fileno())
                info = os.fstat(output.fileno())
            return {"id": pending["id"], "at": time.time(), "directory": directory,
                    "filename": filename, "size": info.st_size, "device": info.st_dev,
                    "inode": info.st_ino, "protection": "none"}
        except BaseException:
            os.unlink(temporary, dir_fd=fd)
            raise
    finally:
        os.close(fd)


def publish_archive(entry):
    """Resume a journalled publication without overwriting an existing file."""
    fd = _open_directory(entry["directory"])
    temporary = "." + entry["filename"] + ".tmp"
    try:
        try:
            info = os.stat(entry["filename"], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
            if not _identity(info, entry):
                raise BackupError("backup staging file was replaced or changed")
            os.link(temporary, entry["filename"], src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        else:
            if not _identity(info, entry):
                raise BackupError("backup destination already contains another file")
        try:
            if _identity(os.stat(temporary, dir_fd=fd, follow_symlinks=False), entry):
                os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.fsync(fd)
    finally:
        os.close(fd)


def discard_staged_archive(entry):
    """Remove only this attempt's temporary file, never a replacement."""
    fd = _open_directory(entry["directory"])
    try:
        temporary = "." + entry["filename"] + ".tmp"
        try:
            if _identity(os.stat(temporary, dir_fd=fd, follow_symlinks=False), entry):
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(fd)


def rotate(files, keep):
    retained, errors = list(files), []
    for entry in files[:-keep]:
        try:
            fd = _open_directory(entry["directory"])
            try:
                info = os.stat(entry["filename"], dir_fd=fd, follow_symlinks=False)
                if not _identity(info, entry):
                    raise BackupError("an older backup was replaced or changed")
                os.unlink(entry["filename"], dir_fd=fd)
                os.fsync(fd)
            finally:
                os.close(fd)
        except FileNotFoundError:
            pass
        except (OSError, BackupError) as exc:
            errors.append(str(exc))
            continue
        retained.remove(entry)
    return retained, errors


class Scheduler:
    def __init__(self, app, exporter, conflict):
        self.app, self.exporter, self.conflict = app, exporter, conflict
        self.wake = asyncio.Event()
        self.task = None
        self.running = False
        self.waiting = ""
        self.stopping = False

    def settings(self, value):
        if self.running or self.app.get("puppy_snapshot_busy"):
            raise BackupError("Wait for the current backup or restore to finish")
        current = _load()
        cfg = validate_settings(value)
        validate_destination(cfg["directory"])
        if current["pending"] and current["pending"]["file"]:
            raise BackupError("Wait for backup publication to finish before changing its settings")
        if cfg["enabled"] and (not current["settings"]["enabled"] or cfg["at"] != current["settings"]["at"]):
            current["next_at"] = next_occurrence(cfg["at"], time.time())
        if not cfg["enabled"]:
            current["next_at"] = 0
            if current["pending"] and current["pending"]["source"] == "scheduled":
                current["pending"] = None
        if current["pending"]:
            raise BackupError("Wait for the queued backup before changing its settings")
        current["settings"] = cfg
        _save(current)
        self.wake.set()

    def queue(self):
        if self.app.get("puppy_snapshot_busy") == "restore":
            raise BackupError("Wait for the current backup or restore to finish")
        current = _load()
        if not current["pending"]:
            if self.app.get("puppy_snapshot_busy"):
                raise BackupError("Wait for the current backup or restore to finish")
            current["pending"] = self._pending("manual", time.time())
            _save(current)
        self.wake.set()

    @staticmethod
    def _pending(source, now):
        return {"id": secrets.token_hex(16), "at": now, "source": source, "file": None}

    async def payload(self):
        current = _load()
        files = []
        for entry in reversed(current["files"]):
            try:
                descriptor = await operations.to_thread(_open_owned, entry)
                os.close(descriptor)
                available = True
            except (OSError, BackupError):
                available = False
            files.append({"id": entry["id"], "at": entry["at"], "filename": entry["filename"],
                          "size": entry["size"], "available": available,
                          "download": "/api/snapshot/saved/" + entry["id"] if available else None})
        return {"settings": current["settings"], "next_at": current["next_at"],
                "timezone": time.strftime("%Z"), "keep_max": KEEP_MAX,
                "status": "running" if self.running else "waiting" if current["pending"] else "idle",
                "waiting": self.waiting if current["pending"] else "",
                "files": files, "history": list(reversed(current["history"]))}

    async def tick(self, now=None):
        if self.running or self.stopping or self.app.get("puppy_snapshot_busy"):
            return
        now = time.time() if now is None else now
        current = _load()
        cfg = current["settings"]
        if cfg["enabled"] and current["next_at"] <= now and not current["pending"]:
            current["pending"] = self._pending("scheduled", now)
            _save(current)
        pending = current["pending"]
        if not pending:
            return
        self.waiting = self.conflict(self.app)
        if self.waiting:
            return
        if cfg["enabled"] and current["next_at"] <= now:
            current["next_at"] = next_occurrence(cfg["at"], now, after_today=True)
        _save(current)
        self.running = True
        result = None
        tone, message = "ok", "Backup saved"
        try:
            if not pending["file"]:
                if len(current["files"]) >= FILES_MAX:
                    raise BackupError("Resolve the backup rotation failures before saving more copies")
                result = await self.exporter(self.app, {})
                pending["file"] = await operations.to_thread(stage_saved_archive, result, pending, cfg["directory"])
                _save(current)
            await operations.to_thread(publish_archive, pending["file"])
            current["files"].append(pending["file"])
            current["files"], errors = await operations.to_thread(rotate, current["files"], cfg["keep"])
            if errors:
                tone, message = "warn", "Backup saved · could not rotate older copies: " + "; ".join(errors)
        except snapshots.SnapshotBusy as exc:
            self.waiting = str(exc)
            return
        except Exception as exc:
            log.exception("saved backup failed")
            tone, message = "bad", "Could not save backup · " + str(exc)
            entry = pending["file"]
            if entry:
                # Publication may have linked the complete archive before a
                # directory fsync failed. Keep that copy discoverable, without
                # rotating the last known good copies on a partial success.
                try:
                    descriptor = await operations.to_thread(_open_owned, entry)
                    os.close(descriptor)
                except (OSError, BackupError):
                    pass
                else:
                    if entry not in current["files"]:
                        current["files"].append(entry)
                    tone, message = "warn", "Backup saved · could not finish publication: " + str(exc)
                try:
                    await operations.to_thread(discard_staged_archive, entry)
                except (OSError, BackupError):
                    log.exception("could not remove backup staging file")
        finally:
            if result:
                snapshots.discard_export(result)
            self.running = False
        current["pending"] = None
        current["history"].append({"id": pending["id"], "at": time.time(), "source": pending["source"],
                                   "tone": tone, "message": message[:1000]})
        current["history"] = current["history"][-HISTORY_LIMIT:]
        _save(current)
        self.waiting = ""

    async def start(self, _app):
        validate_persisted(db.connect())
        self.task = asyncio.create_task(self.loop())

    async def stop(self, _app):
        self.stopping = True
        self.wake.set()
        if self.task:
            await self.task  # finish publication before releasing the process
            self.task = None

    async def loop(self):
        while not self.stopping:
            self.wake.clear()
            try:
                await self.tick()
            except Exception:
                log.exception("backup scheduler failed")
            if self.stopping:
                break
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


async def h_get(request):
    if request.app.get("puppy_snapshot_busy") == "restore":
        return web.json_response({"error": "Puppy restore in progress"}, status=503, headers=HEADERS)
    return web.json_response(await request.app["puppy_backups"].payload(), headers=HEADERS)


async def h_settings(request):
    try:
        request.app["puppy_backups"].settings(await request.json())
    except (ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=400, headers=HEADERS)
    return await h_get(request)


async def h_run(request):
    try:
        request.app["puppy_backups"].queue()
    except BackupError as exc:
        return web.json_response({"error": str(exc)}, status=409, headers=HEADERS)
    return web.json_response(await request.app["puppy_backups"].payload(), status=202, headers=HEADERS)


async def h_download(request):
    if request.app.get("puppy_snapshot_busy") == "restore":
        return web.json_response({"error": "Puppy restore in progress"}, status=503, headers=HEADERS)
    entry = next((item for item in _load()["files"] if item["id"] == request.match_info["identity"]), None)
    if entry is None:
        raise web.HTTPNotFound()
    try:
        descriptor = await operations.to_thread(_open_owned, entry)
    except (OSError, BackupError):
        return web.json_response({"error": "Saved backup is unavailable"}, status=404, headers=HEADERS)
    # Keep the verified descriptor open: rotation may unlink a file while a
    # download is in progress, but can never swap the bytes under this reader.
    with os.fdopen(descriptor, "rb") as source:
        response = web.StreamResponse(headers={**HEADERS, "Content-Type": "application/gzip",
            "Content-Length": str(entry["size"]), "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="{}"'.format(entry["filename"])})
        await response.prepare(request)
        while True:
            chunk = await operations.to_thread(source.read, 1024 * 1024)
            if not chunk:
                break
            await response.write(chunk)
        await response.write_eof()
    return response


def register(app, exporter, conflict):
    scheduler = Scheduler(app, exporter, conflict)
    app["puppy_backups"] = scheduler
    app.router.add_get("/api/snapshot/backups", h_get)
    app.router.add_put("/api/snapshot/backups", h_settings)
    app.router.add_post("/api/snapshot/backups/run", h_run)
    app.router.add_get("/api/snapshot/saved/{identity:[a-f0-9]{32}}", h_download)
    app.on_startup.append(scheduler.start)
    app.on_shutdown.insert(0, scheduler.stop)
