"""Per-session agent notes: the AGENTS.md and CLAUDE.md files in a session's
working directory, which the engines read for standing instructions.

The sidebar shows whether a session has either, and a small editor reads and
writes them in place. The surface is deliberately narrow: exactly these two
names, only inside the session's own working directory, bounded in size, and
registered in ``register_execution_api`` so a remote session's notes are
served by the node that runs it (the console reaches them through the
ordinary proxy). A linked remote workspace is edited in its private mirror,
which is then marked dirty so the controller's next reconcile carries the
change to the authoritative project.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile

from aiohttp import web

from puppy import db, workspace_sync

log = logging.getLogger("puppy.agent_notes")

NOTE_FILES = ("AGENTS.md", "CLAUDE.md")
MAX_NOTE_BYTES = 256 * 1024


class NotesError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def present(cwd) -> list:
    """Names of the note files that exist as regular files in ``cwd``. Used
    by every session payload, so it is two stat calls and nothing more."""
    out = []
    root = str(cwd or "")
    if not root:
        return out
    for name in NOTE_FILES:
        try:
            info = os.stat(os.path.join(root, name))
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            out.append(name)
    return out


def _validated_name(value) -> str:
    name = str(value or "")
    if name not in NOTE_FILES:
        raise NotesError("name must be one of {}".format(", ".join(NOTE_FILES)))
    return name


def _root(session: dict) -> str:
    root = str(session.get("cwd") or "")
    try:
        info = os.stat(root) if root else None
    except OSError:
        info = None
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise NotesError("the session's working directory is not available on "
                         "this node", 404)
    return root


def _describe_file(root: str, name: str) -> dict:
    path = os.path.join(root, name)
    entry = {"name": name, "exists": False, "text": "", "size": 0,
             "symlink": "", "truncated": False, "readable": True}
    try:
        link = os.readlink(path)
    except OSError:
        link = ""
    if link:
        entry["symlink"] = link
    try:
        info = os.stat(path)
    except OSError:
        return entry
    if not stat.S_ISREG(info.st_mode):
        entry["readable"] = False
        return entry
    entry["exists"] = True
    entry["size"] = int(info.st_size)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_NOTE_BYTES + 1)
    except OSError as exc:
        entry["readable"] = False
        entry["error"] = str(exc)
        return entry
    if len(raw) > MAX_NOTE_BYTES:
        raw = raw[:MAX_NOTE_BYTES]
        entry["truncated"] = True
    entry["text"] = raw.decode("utf-8", errors="replace")
    return entry


def describe(session: dict) -> dict:
    root = _root(session)
    return {
        "cwd": workspace_sync.public_cwd(session),
        "max_bytes": MAX_NOTE_BYTES,
        "files": [_describe_file(root, name) for name in NOTE_FILES],
        "present": present(root),
    }


def write(session: dict, name: str, text: str) -> None:
    """Replace one note file atomically. A symlinked note (CLAUDE.md pointing
    at AGENTS.md is a common layout) is written through to its target so the
    link survives; a link to anything but a regular file is refused."""
    root = _root(session)
    name = _validated_name(name)
    if not isinstance(text, str):
        raise NotesError("text must be a string")
    data = text.encode("utf-8")
    if len(data) > MAX_NOTE_BYTES:
        raise NotesError("the file may be at most {} KiB".format(
            MAX_NOTE_BYTES // 1024), 413)
    path = os.path.join(root, name)
    target = os.path.realpath(path) if os.path.islink(path) else path
    mode = 0o644
    try:
        info = os.stat(target)
        if not stat.S_ISREG(info.st_mode):
            raise NotesError("{} is not a regular file here".format(name), 409)
        mode = stat.S_IMODE(info.st_mode)
    except FileNotFoundError:
        if os.path.islink(path):
            raise NotesError("{} is a dangling link".format(name), 409)
    fd, temporary = tempfile.mkstemp(prefix=".{}.".format(name), suffix=".tmp",
                                     dir=os.path.dirname(target) or root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise NotesError("could not write {}: {}".format(name, exc), 500)


def remove(session: dict, name: str) -> None:
    root = _root(session)
    name = _validated_name(name)
    path = os.path.join(root, name)
    try:
        os.unlink(path)   # a link is removed as a link, its target untouched
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise NotesError("could not remove {}: {}".format(name, exc), 500)


def _session_or_404(request):
    session = db.get_session(int(request.match_info["sid"]))
    if session is None:
        raise web.HTTPNotFound(text=json.dumps({"error": "session not found"}),
                               content_type="application/json")
    return session


def _touch_linked(session: dict) -> None:
    """An edit inside a linked workspace's mirror is a change the authoritative
    project has not accepted yet; the dirty flag makes the controller carry
    it across at its next reconcile."""
    if workspace_sync.session_workspace(session):
        try:
            db.touch_session(int(session["id"]), ws_dirty=1)
        except Exception:
            log.warning("could not mark session %s workspace dirty",
                        session.get("id"))


async def h_get(request: web.Request):
    session = _session_or_404(request)
    try:
        return web.json_response(describe(session))
    except NotesError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


async def h_put(request: web.Request):
    from puppy import runner
    session = _session_or_404(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid agent notes request"},
                                 status=400)
    try:
        name = _validated_name(body.get("name"))
        if body.get("delete") is True:
            remove(session, name)
        else:
            write(session, name, body.get("text"))
        _touch_linked(session)
        payload = describe(session)
    except NotesError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    runner.broadcast_sessions()
    return web.json_response(dict(payload, ok=True))


def register(app) -> None:
    app.router.add_get("/api/sessions/{sid:\\d+}/agent-notes", h_get)
    app.router.add_put("/api/sessions/{sid:\\d+}/agent-notes", h_put)
