"""Owned lifecycle for disposable session workspaces.

Scratch sessions and task copies live in the configured data directory, so
their files survive service and host restarts. Their session still owns their
lifecycle: deletion, reset and orphan cleanup stay constrained to Puppy's
private workspace namespace, never an ordinary project directory.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile

from puppy import config, db

log = logging.getLogger("puppy.workspaces")

KIND_DIRECTORY = "directory"
# Persisted and wire vocabulary: disposable by session lifecycle, stored in data.
KIND_TEMPORARY = "temporary"
KINDS = (KIND_DIRECTORY, KIND_TEMPORARY)
_PREFIX = "session-"


class WorkspaceError(RuntimeError):
    pass


def _uid() -> int:
    getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    return int(getter())


def temporary_root() -> Path:
    """Private, persistent storage for session-owned disposable workspaces."""
    return Path(config.DATA_DIR).resolve() / "workspaces"


def _ensure_private_dir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise WorkspaceError("cannot create {}: {}".format(path, exc)) from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkspaceError("cannot inspect {}: {}".format(path, exc)) from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != _uid():
        raise WorkspaceError("{} is not a private directory owned by this service".format(path))
    try:
        if stat.S_IMODE(info.st_mode) != 0o700:
            path.chmod(0o700)
    except OSError as exc:
        raise WorkspaceError("cannot secure {}: {}".format(path, exc)) from exc


def _ensure_root() -> Path:
    root = temporary_root()
    _ensure_private_dir(root.parent)
    _ensure_private_dir(root)
    return root


def _managed_path(value) -> Path:
    candidate = Path(os.path.abspath(str(value or "")))
    root = temporary_root()
    if candidate.parent != root or not candidate.name.startswith(_PREFIX) or \
            len(candidate.name) <= len(_PREFIX):
        raise WorkspaceError("refusing unmanaged scratch workspace path: {}".format(candidate))
    try:
        info = root.lstat()
    except FileNotFoundError:
        return candidate
    except OSError as exc:
        raise WorkspaceError("cannot inspect workspace storage: {}".format(exc)) from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != _uid():
        raise WorkspaceError("refusing an unowned workspace storage directory")
    return candidate


def _owned_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == _uid()


def is_temporary(session: dict) -> bool:
    return session.get("workspace_kind") == KIND_TEMPORARY


def is_available(session: dict) -> bool:
    if not is_temporary(session):
        return True
    try:
        return _owned_directory(_managed_path(session.get("cwd")))
    except WorkspaceError:
        return False


def create_temporary() -> str:
    root = _ensure_root()
    try:
        path = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=str(root)))
        path.chmod(0o700)
    except OSError as exc:
        raise WorkspaceError("cannot create a scratch workspace: {}".format(exc)) from exc
    return str(path)


def _prune_empty_root() -> None:
    # The parent is the data directory itself, not another disposable namespace.
    try:
        temporary_root().rmdir()
    except OSError:
        pass


def _remove_path(value, prune: bool = True) -> bool:
    path = _managed_path(value)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if prune:
            _prune_empty_root()
        return False
    except OSError as exc:
        raise WorkspaceError("cannot inspect scratch workspace: {}".format(exc)) from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != _uid():
        raise WorkspaceError("refusing to remove an unowned scratch workspace")
    try:
        shutil.rmtree(str(path))
    except OSError as exc:
        raise WorkspaceError("cannot remove scratch workspace: {}".format(exc)) from exc
    if prune:
        _prune_empty_root()
    return True


def discard_created(path: str) -> None:
    """Best-effort rollback for a directory whose DB insert failed."""
    try:
        _remove_path(path)
    except WorkspaceError as exc:
        log.warning("scratch workspace rollback failed: %s", exc)


def remove_temporary(session: dict) -> bool:
    if not is_temporary(session):
        return False
    return _remove_path(session.get("cwd"))


def reset_session(session: dict) -> dict:
    """Give a scratch session a new empty directory and fresh native context."""
    if not is_temporary(session):
        raise WorkspaceError("this session does not use a scratch workspace")
    old_path = session.get("cwd") or ""
    new_path = create_temporary()
    try:
        db.touch_session(session["id"], cwd=new_path, native_session_id="", last_model="")
    except Exception:
        discard_created(new_path)
        raise
    if old_path != new_path:
        try:
            _remove_path(old_path)
        except WorkspaceError as exc:
            # The session already points at a safe new directory. Leave any
            # suspicious old path untouched rather than broadening deletion.
            log.warning("old scratch workspace retained for safety: %s", exc)
    updated = db.get_session(session["id"])
    if updated is None:
        discard_created(new_path)
        raise WorkspaceError("session disappeared while resetting its workspace")
    return updated


def ensure_session(session: dict) -> tuple:
    """Return (session, recreated) and repair privacy on a surviving folder."""
    if not is_temporary(session):
        return session, False
    if is_available(session):
        try:
            _ensure_root()
            Path(session["cwd"]).chmod(0o700)
        except (OSError, WorkspaceError) as exc:
            raise WorkspaceError("cannot secure scratch workspace: {}".format(exc)) from exc
        return session, False
    return reset_session(session), True


def cleanup_orphans() -> int:
    """Remove owned scratch directories no longer referenced by this DB."""
    root = temporary_root()
    try:
        root.lstat()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        log.warning("scratch workspace root unreadable: %s", exc)
        return 0
    try:
        _ensure_private_dir(root.parent)
        _ensure_private_dir(root)
    except WorkspaceError as exc:
        log.warning("scratch workspace root is unsafe; leaving it untouched: %s", exc)
        return 0
    referenced = set()
    for session in db.list_sessions(include_archived=True):
        if not is_temporary(session):
            continue
        try:
            path = _managed_path(session.get("cwd"))
        except WorkspaceError:
            continue
        referenced.add(str(path))
        if _owned_directory(path):
            try:
                path.chmod(0o700)
            except OSError as exc:
                log.warning("scratch workspace permissions could not be repaired: %s", exc)
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError as exc:
        log.warning("scratch workspace root cannot be listed: %s", exc)
        return 0
    for child in children:
        if not child.name.startswith(_PREFIX) or str(child) in referenced:
            continue
        try:
            if _remove_path(str(child), prune=False):
                removed += 1
        except WorkspaceError as exc:
            log.warning("orphan scratch workspace retained for safety: %s", exc)
    _prune_empty_root()
    if removed:
        log.info("removed %d orphan scratch workspace(s)", removed)
    return removed
