"""Versioned, validated backups of Puppy-owned settings and session state.

Snapshots deliberately exclude ordinary project directories and external engine
credential/session stores. They include the SQLite database, complete config,
uploads, browser UI state, and Puppy-managed scratch workspace contents.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import threading
import time
from typing import Dict, List

from puppy import (__version__, config, db, listener_handoff, runner, terminal,
                   upgrade_contract, uploads, workspaces)

log = logging.getLogger("puppy.snapshots")

FORMAT_VERSION = 1
PRODUCT = "puppy-state"
ARCHIVE_ROOT = "puppy-snapshot"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 50_000
MAX_UI_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
EXPORT_TTL = 10 * 60
_UI_KEY = re.compile(r"^puppy\.[A-Za-z0-9_.:-]{1,240}$")
_exports: Dict[str, dict] = {}
_exports_lock = threading.Lock()
_work_cleanup_lock = threading.Lock()
_last_work_cleanup = 0.0


class SnapshotError(RuntimeError):
    pass


def work_root() -> Path:
    global _last_work_cleanup
    root = Path(config.DATA_DIR).resolve() / "snapshots"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
    except OSError as exc:
        raise SnapshotError("cannot create private snapshot staging: {}".format(exc)) from exc
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != int(uid_getter()):
        raise SnapshotError("snapshot staging is not a private service-owned directory")
    try:
        root.chmod(0o700)
    except OSError as exc:
        raise SnapshotError("cannot secure snapshot staging: {}".format(exc)) from exc
    now = time.time()
    with _work_cleanup_lock:
        if now - _last_work_cleanup > 60:
            _last_work_cleanup = now
            for child in root.iterdir():
                if not child.name.startswith(
                        ("export-", "import-", "upload-", ".snapshot-")):
                    continue
                try:
                    if now - child.lstat().st_mtime < 24 * 60 * 60:
                        continue
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(str(child))
                    else:
                        child.unlink()
                except OSError as exc:
                    log.warning("stale snapshot work item retained: %s", exc)
    return root


def blockers() -> List[str]:
    reasons = []
    session_blockers = runner.upgrade_blockers()
    if session_blockers:
        ids = ", ".join(str(item["id"]) for item in session_blockers)
        reasons.append("running or queued session work: {}".format(ids))
    active_terminals = terminal.active_count()
    if active_terminals:
        reasons.append("{} active terminal{}".format(
            active_terminals, "" if active_terminals == 1 else "s"))
    active_uploads = uploads.active_count()
    if active_uploads:
        reasons.append("{} active file upload{}".format(
            active_uploads, "" if active_uploads == 1 else "s"))
    # Imported lazily to keep module initialization acyclic. A controller-side
    # automatic upgrade is a durable external mutation just like a manual one,
    # so backup/restore must not race its artifact replacement and restart.
    from puppy import backends
    reasons.extend(backends.upgrade_blockers())
    return reasons


def validate_ui_state(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SnapshotError("browser state must be an object")
    cleaned = {}
    size = 0
    for key, item in value.items():
        if not isinstance(key, str) or not _UI_KEY.fullmatch(key):
            raise SnapshotError("browser state contains an invalid key")
        if not isinstance(item, str):
            raise SnapshotError("browser state values must be strings")
        size += len(key.encode("utf-8")) + len(item.encode("utf-8"))
        if size > MAX_UI_BYTES:
            raise SnapshotError("browser state is too large")
        cleaned[key] = item
    return cleaned


def _safe_link_target(relative: PurePosixPath, target: str) -> None:
    link = PurePosixPath(target)
    if link.is_absolute() or not target or len(target) > 4096 or "\\" in target:
        raise SnapshotError("unsafe symbolic link: {} -> {}".format(relative, target))
    parts = list(relative.parent.parts)
    for part in link.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise SnapshotError("symbolic link escapes snapshot: {}".format(relative))
            parts.pop()
        else:
            parts.append(part)
    if not relative.parts:
        raise SnapshotError("unsafe symbolic link path")
    if relative.parts[0] == "scratch":
        if len(relative.parts) < 3:
            raise SnapshotError("scratch workspace root cannot be a symbolic link")
        boundary = relative.parts[:2]
    elif relative.parts[0] in ("uploads", "tls"):
        boundary = relative.parts[:1]
    else:
        raise SnapshotError("symbolic links are not allowed in snapshot metadata")
    if tuple(parts[:len(boundary)]) != tuple(boundary):
        raise SnapshotError("symbolic link leaves its restored data tree: {}".format(relative))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> dict:
    entries = {}
    total = 0
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if relative.as_posix() == "manifest.json":
            continue
        info = path.lstat()
        count += 1
        if count > MAX_MEMBERS:
            raise SnapshotError("snapshot contains too many files")
        if stat.S_ISDIR(info.st_mode):
            entries[relative.as_posix()] = {
                "type": "directory", "mode": stat.S_IMODE(info.st_mode) & 0o777,
            }
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            if info.st_size > MAX_MEMBER_BYTES or total > MAX_EXTRACTED_BYTES:
                raise SnapshotError("snapshot contents are too large")
            entries[relative.as_posix()] = {
                "type": "file", "size": info.st_size, "sha256": _sha256(path),
                "mode": stat.S_IMODE(info.st_mode) & 0o777,
            }
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(str(path))
            _safe_link_target(relative, target)
            entries[relative.as_posix()] = {"type": "symlink", "target": target}
        else:
            raise SnapshotError("unsupported file type in snapshot: {}".format(relative))
    return entries


def _copy_source_tree(source: Path, destination: Path, namespace: str,
                      budget: dict) -> None:
    """Validate an owned tree before copytree can encounter a FIFO or device."""
    try:
        root_info = source.lstat()
    except FileNotFoundError:
        destination.mkdir(mode=0o700, parents=True)
        return
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise SnapshotError("{} storage is not a regular directory".format(namespace))

    stack = [(source, PurePosixPath(namespace))]
    while stack:
        directory, relative_dir = stack.pop()
        try:
            with os.scandir(str(directory)) as children:
                for child in children:
                    relative = relative_dir / child.name
                    display = relative.as_posix()
                    try:
                        component_bytes = child.name.encode("utf-8")
                        path_bytes = display.encode("utf-8")
                    except UnicodeEncodeError as exc:
                        raise SnapshotError(
                            "snapshot source contains a non-UTF-8 path") from exc
                    if len(path_bytes) > 4096 or "\\" in display or "\x00" in display or \
                            len(component_bytes) > 255:
                        raise SnapshotError("snapshot source contains an unsupported path")
                    try:
                        info = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise SnapshotError(
                            "cannot inspect {}: {}".format(relative, exc)) from exc
                    budget["members"] += 1
                    if budget["members"] > MAX_MEMBERS - 8:
                        raise SnapshotError("snapshot contains too many files")
                    if stat.S_ISDIR(info.st_mode):
                        stack.append((Path(child.path), relative))
                    elif stat.S_ISREG(info.st_mode):
                        budget["bytes"] += info.st_size
                        if info.st_size > MAX_MEMBER_BYTES or \
                                budget["bytes"] > MAX_EXTRACTED_BYTES:
                            raise SnapshotError("snapshot contents are too large")
                    elif stat.S_ISLNK(info.st_mode):
                        try:
                            target = os.readlink(child.path)
                        except OSError as exc:
                            raise SnapshotError(
                                "cannot inspect {}: {}".format(relative, exc)) from exc
                        _safe_link_target(relative, target)
                    else:
                        raise SnapshotError(
                            "snapshot source contains an unsupported file type: {}".format(
                                relative))
        except SnapshotError:
            raise
        except OSError as exc:
            raise SnapshotError("cannot inspect {}: {}".format(relative_dir, exc)) from exc
    shutil.copytree(str(source), str(destination), symlinks=True)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode &= 0o777
    return info


def create_archive(ui_state: dict) -> dict:
    """Build a private archive and return its path plus non-secret metadata."""
    ui_state = validate_ui_state(ui_state)
    root = work_root()
    temporary = Path(tempfile.mkdtemp(prefix="export-", dir=str(root)))
    stage = temporary / ARCHIVE_ROOT
    archive_tmp = root / (".snapshot-{}.tar.gz.tmp".format(secrets.token_hex(12)))
    archive_path = archive_tmp.with_suffix("")
    try:
        stage.mkdir(mode=0o700)
        (stage / "uploads").mkdir(mode=0o700)
        (stage / "scratch").mkdir(mode=0o700)
        source_budget = {"members": 0, "bytes": 0}

        db.backup_to(str(stage / "puppy.db"))
        cfg = config.export_data()
        (stage / "config.json").write_text(
            json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        (stage / "config.json").chmod(0o600)
        (stage / "ui.json").write_text(
            json.dumps(ui_state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        (stage / "ui.json").chmod(0o600)

        uploads = Path(config.DATA_DIR).resolve() / "uploads"
        if uploads.exists() or uploads.is_symlink():
            (stage / "uploads").rmdir()
            _copy_source_tree(uploads, stage / "uploads", "uploads", source_budget)
        tls_dir = Path(config.DATA_DIR).resolve() / "tls"
        if tls_dir.exists() or tls_dir.is_symlink():
            _copy_source_tree(tls_dir, stage / "tls", "tls", source_budget)

        scratch_saved = []
        scratch_missing = []
        sessions = db.list_sessions(include_archived=True)
        for session in sessions:
            if not workspaces.is_temporary(session):
                continue
            if workspaces.is_available(session):
                _copy_source_tree(
                    Path(session["cwd"]), stage / "scratch" / str(session["id"]),
                    "scratch/{}".format(session["id"]), source_budget)
                scratch_saved.append(session["id"])
            else:
                scratch_missing.append(session["id"])

        manifest = {
            "format": FORMAT_VERSION,
            "product": PRODUCT,
            "source_version": __version__,
            "created_at": int(time.time()),
            "source_data_dir": str(Path(config.DATA_DIR).resolve()),
            "sessions": len(sessions),
            "scratch_saved": scratch_saved,
            "scratch_missing": scratch_missing,
            "files": _inventory(stage),
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        (stage / "manifest.json").chmod(0o600)
        if 1 + sum(1 for _path in stage.rglob("*")) > MAX_MEMBERS:
            raise SnapshotError("snapshot contains too many entries")

        with tarfile.open(str(archive_tmp), "w:gz", compresslevel=6) as archive:
            archive.add(str(stage), arcname=ARCHIVE_ROOT, recursive=True,
                        filter=_tar_filter)
        size = archive_tmp.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise SnapshotError("snapshot archive exceeds the 512 MiB limit")
        os.replace(str(archive_tmp), str(archive_path))
        archive_path.chmod(0o600)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(manifest["created_at"]))
        return {
            "path": str(archive_path),
            "filename": "puppy-snapshot-{}.tar.gz".format(stamp),
            "size": size,
            "sessions": len(sessions),
        }
    except Exception:
        for path in (archive_tmp, archive_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        shutil.rmtree(str(temporary), ignore_errors=True)


def _clean_exports(now: float) -> None:
    expired = [token for token, item in _exports.items()
               if now - item["created"] > EXPORT_TTL or not Path(item["path"]).is_file()]
    for token in expired:
        item = _exports.pop(token, None)
        if item:
            try:
                Path(item["path"]).unlink()
            except FileNotFoundError:
                pass


def register_export(result: dict, user: str) -> str:
    token = secrets.token_urlsafe(24)
    with _exports_lock:
        _clean_exports(time.time())
        _exports[token] = {**result, "user": user, "created": time.time()}
    return token


def claim_export(token: str, user: str):
    with _exports_lock:
        _clean_exports(time.time())
        item = _exports.pop(token, None)
    if not item or item["user"] != user:
        if item:
            try:
                Path(item["path"]).unlink()
            except FileNotFoundError:
                pass
        return None
    return item


def expire_export(token: str) -> None:
    with _exports_lock:
        item = _exports.pop(token, None)
    discard_export(item)


def discard_export(item) -> None:
    if not item:
        return
    try:
        Path(item["path"]).unlink()
    except FileNotFoundError:
        pass


def _member_relative(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or len(name) > 4096 or "\x00" in name or \
            "\\" in name or name.startswith("/"):
        raise SnapshotError("snapshot contains an unsafe path")
    path = PurePosixPath(name)
    if not path.parts or path.parts[0] != ARCHIVE_ROOT or \
            any(part in ("", ".", "..") for part in path.parts):
        raise SnapshotError("snapshot contains an unsafe path: {}".format(name))
    if any(len(part.encode("utf-8")) > 255 for part in path.parts):
        raise SnapshotError("snapshot path component is too long")
    if len(path.parts) == 1:
        return PurePosixPath(".")
    return PurePosixPath(*path.parts[1:])


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    try:
        archive = tarfile.open(str(archive_path), "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise SnapshotError("invalid tar.gz snapshot") from exc
    with archive:
        members = []
        for member in archive:
            members.append(member)
            if len(members) > MAX_MEMBERS:
                raise SnapshotError("snapshot contains too many entries")
        if not members:
            raise SnapshotError("snapshot has an invalid number of entries")
        seen = set()
        links = set()
        total = 0
        validated = []
        for member in members:
            relative = _member_relative(member.name)
            key = relative.as_posix()
            if key in seen:
                raise SnapshotError("snapshot contains duplicate paths")
            seen.add(key)
            if member.isdir():
                kind = "dir"
            elif member.isreg():
                kind = "file"
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise SnapshotError("snapshot member is too large")
                total += member.size
                if total > MAX_EXTRACTED_BYTES:
                    raise SnapshotError("snapshot expands beyond the 2 GiB limit")
            elif member.issym():
                kind = "link"
                _safe_link_target(relative, member.linkname)
                links.add(key)
            else:
                raise SnapshotError("snapshot contains an unsupported entry type")
            validated.append((member, relative, kind))
        free = shutil.disk_usage(str(destination.parent)).free
        if total + 64 * 1024 * 1024 > free:
            raise SnapshotError("not enough free space to stage this snapshot")
        for _member, relative, _kind in validated:
            parts = relative.parts
            for index in range(1, len(parts)):
                if PurePosixPath(*parts[:index]).as_posix() in links:
                    raise SnapshotError("snapshot places content beneath a symbolic link")

        root = destination / ARCHIVE_ROOT
        root.mkdir(mode=0o700, parents=True)
        directory_modes = []
        for member, relative, kind in validated:
            if relative == PurePosixPath("."):
                if kind != "dir":
                    raise SnapshotError("snapshot root is not a directory")
                continue
            target = root.joinpath(*relative.parts)
            if kind == "dir":
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory_modes.append((target, member.mode & 0o777))
            elif kind == "file":
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise SnapshotError("snapshot file could not be read")
                with source, target.open("xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise SnapshotError("snapshot file ended unexpectedly")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise SnapshotError("snapshot file exceeds its declared size")
                target.chmod(member.mode & 0o777)
        for member, relative, kind in validated:
            if kind != "link":
                continue
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.symlink(member.linkname, str(target))
        for target, mode in sorted(directory_modes, key=lambda item: len(item[0].parts),
                                   reverse=True):
            target.chmod(mode)
        root.chmod(0o700)
        return root


def _load_json(path: Path, label: str, max_bytes: int = MAX_UI_BYTES):
    try:
        if path.stat().st_size > max_bytes:
            raise SnapshotError("{} is too large".format(label))
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("invalid JSON constant {}".format(value))))
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError("{} is invalid".format(label)) from exc


def _validate_database(path: Path) -> int:
    connection = None
    try:
        connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
        connection.row_factory = sqlite3.Row
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise SnapshotError("snapshot database failed its integrity check")
        objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_autoindex_%'").fetchall()
        if any(row["type"] in ("trigger", "view") for row in objects):
            raise SnapshotError("snapshot database contains unsupported executable schema")
        if any("VIRTUAL TABLE" in str(row["sql"] or "").upper() for row in objects):
            raise SnapshotError("snapshot database contains an unsupported virtual table")
        tables = {row["name"] for row in objects if row["type"] == "table"}
        required = {"meta", "users", "web_sessions", "backends", "sessions", "events"}
        if not required.issubset(tables):
            raise SnapshotError("snapshot database is missing required tables")
        required_columns = {
            "meta": {"key", "value"},
            "users": {"id", "username", "pwhash", "created_at"},
            "web_sessions": {"id", "token_hash", "username", "created_at", "expires_at"},
            # auto_upgrade and urls are intentionally optional here: older
            # archives gain the safe-off policy and a one-address failover list
            # through db._migrate() immediately after installation.
            "backends": {"id", "name", "url", "token", "protocol", "capabilities",
                         "remote_version", "role", "tls_fingerprint", "created_at"},
            "sessions": {"id", "name", "engine", "cwd", "model", "effort", "color",
                         "permission_mode", "native_session_id", "last_model", "status",
                         "archived", "workspace_kind", "sort_order", "created_at", "updated_at"},
            "events": {"id", "session_id", "seq", "kind", "payload", "created_at"},
        }
        for table, columns in required_columns.items():
            actual = {
                row["name"] for row in connection.execute("PRAGMA table_info({})".format(table))}
            if not columns.issubset(actual):
                raise SnapshotError(
                    "snapshot database has an incompatible {} table".format(table))
        backend_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(backends)")}
        if "urls" in backend_columns:
            for row in connection.execute("SELECT url,urls FROM backends"):
                try:
                    urls = json.loads(row["urls"])
                except Exception as exc:
                    raise SnapshotError(
                        "snapshot database contains an invalid backend URL list") from exc
                if not isinstance(urls, list) or not 0 < len(urls) <= 8 or \
                        not all(isinstance(value, str) and value for value in urls) or \
                        urls[0] != row["url"] or len(set(urls)) != len(urls):
                    raise SnapshotError(
                        "snapshot database contains an invalid backend URL list")
        invalid_workspace = connection.execute(
            "SELECT 1 FROM sessions WHERE workspace_kind NOT IN ('directory','temporary') "
            "LIMIT 1").fetchone()
        if invalid_workspace:
            raise SnapshotError("snapshot database contains an invalid workspace type")
        orphan = connection.execute(
            "SELECT 1 FROM events e LEFT JOIN sessions s ON s.id=e.session_id "
            "WHERE s.id IS NULL LIMIT 1").fetchone()
        if orphan:
            raise SnapshotError("snapshot database contains orphaned events")
        return int(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    except SnapshotError:
        raise
    except sqlite3.Error as exc:
        raise SnapshotError("snapshot database is invalid") from exc
    finally:
        if connection is not None:
            connection.close()


def _verify_manifest(root: Path, manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("product") != PRODUCT or \
            not isinstance(manifest.get("format"), int) or \
            isinstance(manifest.get("format"), bool) or \
            manifest.get("format") != FORMAT_VERSION:
        raise SnapshotError("unsupported Puppy snapshot format")
    source_version = str(manifest.get("source_version") or "")
    try:
        if upgrade_contract.version_key(source_version) > upgrade_contract.version_key(__version__):
            raise SnapshotError(
                "snapshot v{} is newer than this Puppy v{}".format(source_version, __version__))
    except ValueError as exc:
        raise SnapshotError("snapshot has an invalid Puppy version") from exc
    expected = manifest.get("files")
    if not isinstance(expected, dict) or expected != _inventory(root):
        raise SnapshotError("snapshot contents do not match its manifest")
    sessions = manifest.get("sessions")
    if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 0:
        raise SnapshotError("snapshot manifest has an invalid session count")
    source_data = manifest.get("source_data_dir")
    if not isinstance(source_data, str) or not source_data.startswith("/") or \
            not source_data or len(source_data) > 4096:
        raise SnapshotError("snapshot manifest has an invalid source data directory")
    for key in ("scratch_saved", "scratch_missing"):
        values = manifest.get(key)
        if not isinstance(values, list) or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in values) or len(set(values)) != len(values):
            raise SnapshotError("snapshot manifest has invalid scratch-workspace metadata")
    allowed = {"manifest.json", "config.json", "puppy.db", "ui.json",
               "uploads", "scratch", "tls"}
    if any(path.name not in allowed for path in root.iterdir()):
        raise SnapshotError("snapshot contains an unknown top-level entry")
    for name in ("config.json", "puppy.db", "ui.json"):
        if not (root / name).is_file() or (root / name).is_symlink():
            raise SnapshotError("snapshot is missing {}".format(name))
    for name in ("uploads", "scratch"):
        if not (root / name).is_dir() or (root / name).is_symlink():
            raise SnapshotError("snapshot is missing its {} directory".format(name))


def _prepare_scratch(candidate_db: Path, root: Path, manifest: dict) -> List[str]:
    created = []
    connection = sqlite3.connect(str(candidate_db))
    connection.row_factory = sqlite3.Row
    try:
        temporary = connection.execute(
            "SELECT id,cwd FROM sessions WHERE workspace_kind='temporary'").fetchall()
        expected_ids = {str(row["id"]) for row in temporary}
        scratch_root = root / "scratch"
        scratch_items = list(scratch_root.iterdir()) if scratch_root.is_dir() else []
        if any(not item.is_dir() or item.is_symlink() for item in scratch_items):
            raise SnapshotError("snapshot contains an invalid scratch workspace")
        actual_ids = {item.name for item in scratch_items}
        if not actual_ids.issubset(expected_ids):
            raise SnapshotError("snapshot contains an unknown scratch workspace")
        saved = {str(value) for value in manifest["scratch_saved"]}
        missing = {str(value) for value in manifest["scratch_missing"]}
        if saved & missing or saved | missing != expected_ids or saved != actual_ids:
            raise SnapshotError("snapshot scratch-workspace metadata is inconsistent")
        for row in temporary:
            sid = str(row["id"])
            new_path = workspaces.create_temporary()
            created.append(new_path)
            if sid in saved:
                shutil.copytree(str(scratch_root / sid), new_path,
                                symlinks=True, dirs_exist_ok=True)
                Path(new_path).chmod(0o700)
            else:
                workspaces.discard_created(new_path)
            connection.execute("UPDATE sessions SET cwd=? WHERE id=?", (new_path, row["id"]))

        source_data = str(manifest.get("source_data_dir") or "").rstrip("/")
        destination_data = str(Path(config.DATA_DIR).resolve()).rstrip("/")
        if source_data and source_data != destination_data:
            connection.execute(
                "UPDATE events SET payload=replace(payload, ?, ?)",
                (source_data + "/uploads/", destination_data + "/uploads/"))
        connection.execute("UPDATE sessions SET status='idle'")
        connection.commit()
        return created
    except Exception:
        connection.rollback()
        for path in created:
            workspaces.discard_created(path)
        raise
    finally:
        connection.close()


def stage_import(archive_path: str) -> dict:
    """Extract, validate, and prepare a restore without touching live state."""
    archive = Path(archive_path)
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SnapshotError("snapshot archive exceeds the 512 MiB limit")
    temporary = Path(tempfile.mkdtemp(prefix="import-", dir=str(work_root())))
    created_scratch = []
    try:
        root = _extract_archive(archive, temporary / "extracted")
        manifest = _load_json(
            root / "manifest.json", "snapshot manifest", MAX_MANIFEST_BYTES)
        _verify_manifest(root, manifest)
        try:
            cfg = config.normalize_import(_load_json(root / "config.json", "snapshot config"))
        except ValueError as exc:
            raise SnapshotError("snapshot config is invalid: {}".format(exc)) from exc
        ui = validate_ui_state(_load_json(root / "ui.json", "browser state"))
        candidate_db = root / "puppy.db"
        session_count = _validate_database(candidate_db)
        if session_count != manifest["sessions"]:
            raise SnapshotError("snapshot session count does not match its database")
        created_scratch = _prepare_scratch(candidate_db, root, manifest)
        return {
            "temporary": str(temporary), "root": str(root), "database": str(candidate_db),
            "config": cfg, "ui": ui, "manifest": manifest,
            "created_scratch": created_scratch,
        }
    except Exception:
        for path in created_scratch:
            workspaces.discard_created(path)
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise


def discard_staged(staged) -> None:
    if not staged:
        return
    for path in staged.get("created_scratch", []):
        workspaces.discard_created(path)
    shutil.rmtree(staged.get("temporary", ""), ignore_errors=True)


def _remove_exact(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(str(path))
    else:
        path.unlink()


def commit_import(staged: dict) -> dict:
    """Install a fully staged snapshot, rolling back every live file on error."""
    root = Path(staged["root"])
    rollback = Path(tempfile.mkdtemp(prefix="rollback-", dir=str(work_root())))
    rollback_db = rollback / "puppy.db"
    db.backup_to(str(rollback_db))
    old_config = config.export_data()
    swapped = []
    database_changed = False
    try:
        for name in ("uploads", "tls"):
            current = Path(config.DATA_DIR).resolve() / name
            incoming = root / name
            previous = rollback / name
            if current.exists() or current.is_symlink():
                os.replace(str(current), str(previous))
            # Record the tree as soon as its live name may have moved, so an
            # incoming-rename failure still restores the previous directory.
            swapped.append((current, previous))
            if incoming.exists() or incoming.is_symlink():
                os.replace(str(incoming), str(current))

        config.replace_all(staged["config"])
        db.replace_from(staged["database"])
        database_changed = True
    except Exception as exc:
        rollback_errors = []
        try:
            db.replace_from(str(rollback_db))
        except Exception as rollback_exc:
            rollback_errors.append("database: {}".format(rollback_exc))
        try:
            config.replace_all(old_config)
        except Exception as rollback_exc:
            rollback_errors.append("config: {}".format(rollback_exc))
        for current, previous in reversed(swapped):
            try:
                _remove_exact(current)
                if previous.exists() or previous.is_symlink():
                    os.replace(str(previous), str(current))
            except Exception as rollback_exc:
                rollback_errors.append("{}: {}".format(current.name, rollback_exc))
        if rollback_errors:
            detail = "; rollback errors: {}; recovery files retained in {}".format(
                ", ".join(rollback_errors), rollback)
        else:
            detail = ""
            shutil.rmtree(str(rollback), ignore_errors=True)
        raise SnapshotError("restore failed: {}{}".format(exc, detail)) from exc
    finally:
        if not database_changed:
            for path in staged.get("created_scratch", []):
                workspaces.discard_created(path)

    staged["created_scratch"] = []
    shutil.rmtree(str(rollback), ignore_errors=True)
    try:
        workspaces.cleanup_orphans()
    except Exception as exc:
        log.warning("post-restore scratch cleanup failed: %s", exc)
    # Listener handoffs are intentionally transient and excluded from the
    # archive. A successfully restored identity/configuration must not leave a
    # capability prepared against the state that was just replaced.
    listener_handoff.discard()
    return {
        "ok": True,
        "source_version": staged["manifest"]["source_version"],
        "sessions": staged["manifest"]["sessions"],
        "ui": staged["ui"],
    }
