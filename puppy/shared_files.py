"""Files an agent shares into its conversation for the user to download.

An agent asks for this explicitly: the turn-bound ``share_file`` tool
(``puppy.files_agent``) names one file, and this module copies that file into
the session's own upload storage under an unguessable id, beside its
attachments and tool images.  The agent then writes the Markdown link the tool
hands back - ``[model.stl](puppy-file:f…)`` - and the console turns it into a
download of ``GET /api/sessions/{sid}/files/{id}``.

Least privilege, in order:

* No path ever appears in a URL.  An id resolves only inside the session that
  shared it, to a one-file directory this module created, so a forged or
  copied link reaches nothing else; the route answers only the authenticated
  console or controller, like every other route.
* What can be shared is narrow: a regular file inside the session's own
  working folder or the system temporary folder, never Puppy's own data
  folder (unless the working folder is one Puppy manages there), never an
  engine's CLI home, never the hidden entries directly in the home folder
  where sign-ins, keys and tokens live, never system locations.  The path is
  resolved once, then opened by walking the resolved path with ``O_NOFOLLOW``
  at every step, so a symlink swapped in afterwards cannot redirect the read.
* The copy is taken at that moment - the link always serves exactly what was
  shared - with byte caps per file, per session and per turn.
* The bytes only ever leave as a download: ``application/octet-stream``,
  ``attachment``, sniffing off and a sandbox policy, so a shared HTML or SVG
  file can never run on the console's origin.  A controller relaying a
  backend's file imposes the same headers itself (``backends``).

A share lives and dies with its session - deleted with it, carried in its
backups like any upload, moved to Main when a task is folded into it - and the
orphan sweep never takes one, because its id is not an upload id.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
import threading
import unicodedata
from urllib.parse import quote

from aiohttp import web

from puppy import config, db, uploads

log = logging.getLogger("puppy.shared_files")

FILE_ID = re.compile(r"^f[0-9a-f]{32}$")
LINK_SCHEME = "puppy-file:"
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_SESSION_BYTES = 1024 * 1024 * 1024
MAX_TURN_SHARES = 20
MAX_PATH_CHARS = 4096
MAX_LISTED = 500
COPY_CHUNK = 1024 * 1024
# Never shared even from a session working there: the kernel's views and the
# system's own secrets.
SYSTEM_LOCATIONS = ("/proc", "/sys", "/dev", "/run", "/etc/shadow", "/etc/gshadow",
                    "/etc/ssh")

# Every answer that carries shared bytes, here and through a controller.
DOWNLOAD_HEADERS = {
    "Content-Type": "application/octet-stream",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
}

_turn_lock = threading.Lock()
_turn_counts = {}
_session_locks_guard = threading.Lock()
_session_locks = {}


class ShareError(RuntimeError):
    """A refusal worded for the agent: what was wrong and what to do instead."""


def content_disposition(name: str) -> str:
    """``attachment`` with an ASCII fallback and the exact UTF-8 name."""
    fallback = "".join(char if 0x20 <= ord(char) < 0x7F and char not in '"\\' else "_"
                       for char in name).strip() or "download"
    return "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
        fallback, quote(name, safe=""))


def download_headers(name: str) -> dict:
    headers = dict(DOWNLOAD_HEADERS)
    headers["Content-Disposition"] = content_disposition(name)
    return headers


def file_name(value) -> str:
    """The name a download gets: one printable path component, bounded.

    Taken as written - never URL-decoded like a browser upload's name - with
    control and formatting characters (a right-to-left override could dress
    ``report.exe`` up as ``report.pdf``) made visible as ``_``.
    """
    value = unicodedata.normalize("NFC", str(value or ""))
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join("_" if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") else char
                    for char in value).strip()
    if value in ("", ".", ".."):
        value = "file"
    limit = uploads.MAX_FILENAME_BYTES
    if len(value.encode("utf-8")) > limit:
        # a long name loses the end of its stem, never its extension
        stem, dot, extension = value.rpartition(".")
        extension = dot + extension if stem and len(extension.encode("utf-8")) <= 16 else ""
        stem = value[:len(value) - len(extension)]
        while stem and len((stem + extension).encode("utf-8")) > limit:
            stem = stem[:-1]
        value = stem.rstrip() + extension
    return value.strip() or "file"


def link_text(name: str) -> str:
    """A Markdown link label that cannot close or break the link."""
    escaped = []
    for char in name:
        if char in "\\[]`*_<>":
            escaped.append("\\" + char)
        elif ord(char) < 32:
            escaped.append(" ")
        else:
            escaped.append(char)
    return "".join(escaped)


def markdown_link(file_id: str, name: str) -> str:
    return "[{}]({}{})".format(link_text(name), LINK_SCHEME, file_id)


# ---- what may be shared ----

def _inside(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _home() -> str:
    home = os.path.expanduser("~")
    return os.path.realpath(home) if home and home != "~" else ""


def _cli_homes() -> str:
    from puppy import terminal
    return os.path.realpath(terminal.cli_home_root())


def temp_root() -> str:
    return os.path.realpath(tempfile.gettempdir())


def resolve_source(session: dict, path) -> tuple:
    """(root, relative path, resolved path) of a file this session may share.

    ``session["cwd"]`` is the raw execution folder - a linked workspace's
    mirror, a task's copy, a scratch workspace - which is where the agent
    works and what its paths are relative to.
    """
    if not isinstance(path, str) or not path.strip() or "\x00" in path or \
            len(path) > MAX_PATH_CHARS:
        raise ShareError("give the path of the one file to share")
    cwd = os.path.realpath(str(session.get("cwd") or "") or "/")
    raw = os.path.expanduser(path.strip()) if path.strip().startswith("~") else path.strip()
    resolved = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
    data = os.path.realpath(config.DATA_DIR)
    if _inside(resolved, data) and not (_inside(cwd, data) and _inside(resolved, cwd)):
        raise ShareError("files in Puppy's own data folder are never shared")
    if _inside(resolved, _cli_homes()):
        raise ShareError("an engine CLI's own home folder is never shared")
    home = _home()
    if home and home != "/" and _inside(resolved, home) and resolved != home:
        first = os.path.relpath(resolved, home).split(os.sep)[0]
        if first.startswith(".") and not _inside(cwd, os.path.join(home, first)):
            raise ShareError(
                "hidden files and folders directly in the home folder are never shared: "
                "that is where sign-ins, keys and tokens live")
    for location in SYSTEM_LOCATIONS:
        if _inside(resolved, location):
            raise ShareError("system files under {} are never shared".format(location))
    temp = temp_root()
    for root in (cwd, temp):
        if _inside(resolved, root) and resolved != root:
            return root, os.path.relpath(resolved, root), resolved
    raise ShareError(
        "only files inside this session's working folder ({}) or the temporary folder ({}) "
        "can be shared; copy the file into one of them first".format(cwd, temp))


def _open_source(root: str, relative: str) -> tuple:
    """(fd, stat) of the file, reached without following a symlink anywhere."""
    parts = relative.split(os.sep)
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ShareError("give the path of the one file to share")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        folder = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError:
        raise ShareError("the folder holding that file cannot be read")
    try:
        for part in parts[:-1]:
            step = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=folder)
            os.close(folder)
            folder = step
        info = os.stat(parts[-1], dir_fd=folder, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ShareError("only a regular file can be shared, not a folder, link or "
                             "device; to share a folder, make an archive of it first")
        fd = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
                     dir_fd=folder)
    except ShareError:
        raise
    except FileNotFoundError:
        raise ShareError("there is no file at that path")
    except PermissionError:
        raise ShareError("Puppy cannot read that file")
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ShareError("the path changed while it was being shared; try again")
        raise ShareError("the file could not be opened: {}".format(exc.strerror or exc))
    finally:
        os.close(folder)
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or \
            (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(fd)
        raise ShareError("the file changed while it was being shared; try again")
    return fd, opened


# ---- storage ----

def _session_root(session_id: int) -> Path:
    return Path(config.DATA_DIR).resolve() / "uploads" / str(int(session_id))


def shared(session_id: int) -> list:
    """Every share of a session, newest first: [{id, name, size, created_at}]."""
    root = _session_root(session_id)
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    found = []
    for child in children:
        if not FILE_ID.fullmatch(child.name):
            continue
        try:
            target = uploads._validated_upload_file(int(session_id), child.name)
            info = target.lstat()
            created = child.lstat().st_mtime
        except OSError:
            continue
        found.append({"id": child.name, "name": target.name, "size": info.st_size,
                      "created_at": created})
    found.sort(key=lambda item: (-item["created_at"], item["id"]))
    return found


def _new_directory(session_id: int) -> Path:
    root = Path(config.DATA_DIR).resolve() / "uploads"
    uploads._private_directory(root)
    session_root = root / str(int(session_id))
    uploads._private_directory(session_root)
    for _attempt in range(4):
        candidate = session_root / ("f" + secrets.token_hex(16))
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise OSError("could not allocate private storage for a shared file")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(COPY_CHUNK)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    total = 0
    while True:
        block = os.read(fd, COPY_CHUNK)
        if not block:
            return digest.hexdigest()
        total += len(block)
        if total > MAX_FILE_BYTES:
            raise ShareError("that file grew past {} while it was being shared".format(
                _size(MAX_FILE_BYTES)))
        digest.update(block)


def _session_lock(session_id: int) -> threading.Lock:
    """One share at a time per session, so its caps and reuse hold."""
    with _session_locks_guard:
        lock = _session_locks.get(int(session_id))
        if lock is None:
            lock = _session_locks[int(session_id)] = threading.Lock()
        return lock


def _count_turn(turn: str) -> None:
    with _turn_lock:
        count = _turn_counts.get(turn, 0)
        if count >= MAX_TURN_SHARES:
            raise ShareError("this turn has already shared {} files, the most one turn may; "
                             "make an archive of the rest and share that".format(MAX_TURN_SHARES))
        _turn_counts[turn] = count + 1
        while len(_turn_counts) > 256:
            _turn_counts.pop(next(iter(_turn_counts)))


def _uncount_turn(turn: str) -> None:
    with _turn_lock:
        if _turn_counts.get(turn):
            _turn_counts[turn] -= 1


def share(session_id: int, path, name=None, turn: str = "") -> dict:
    """Copy one file into the session's shares; blocking, so run off the loop.

    Returns {id, name, size, reused, link}. Sharing the same unchanged file
    under the same name again answers the earlier share instead of a copy.
    """
    session = db.get_session(int(session_id))
    if session is None:
        raise ShareError("the session no longer exists")
    root, relative, resolved = resolve_source(session, path)
    if name is not None and not isinstance(name, str):
        raise ShareError("name must be text")
    # the name the agent gave the file, never a symlink target's
    label = file_name(name if (name or "").strip() else
                      os.path.basename(os.path.normpath(path.strip())))
    fd, info = _open_source(root, relative)
    directory = None
    counted = False
    try:
        if info.st_size > MAX_FILE_BYTES:
            raise ShareError("that file is {}; files up to {} can be shared".format(
                _size(info.st_size), _size(MAX_FILE_BYTES)))
        with _session_lock(session_id):
            existing = shared(session_id)
            alike = [item for item in existing
                     if item["name"] == label and item["size"] == info.st_size]
            if alike:
                fingerprint = _digest_fd(fd)
                for item in alike:
                    try:
                        same = _digest(uploads._validated_upload_file(int(session_id), item["id"]))
                    except OSError:
                        continue
                    if same == fingerprint:
                        return {"id": item["id"], "name": label, "size": item["size"],
                                "reused": True, "link": markdown_link(item["id"], label)}
                os.lseek(fd, 0, os.SEEK_SET)
            held = sum(item["size"] for item in existing)
            if held + info.st_size > MAX_SESSION_BYTES:
                raise ShareError(_session_full(held))
            if turn:
                _count_turn(turn)
                counted = True
            directory = _new_directory(session_id)
            total = 0
            out = os.open(str(directory / label), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(out, "wb") as sink:
                while True:
                    block = os.read(fd, COPY_CHUNK)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_FILE_BYTES:
                        raise ShareError("that file grew past {} while it was being shared".format(
                            _size(MAX_FILE_BYTES)))
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
            if held + total > MAX_SESSION_BYTES:
                raise ShareError(_session_full(held))
            dirfd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
            file_id = directory.name
            directory = None
        log.info("session %s shared %s (%s bytes) as %s", session_id, resolved, total, file_id)
        return {"id": file_id, "name": label, "size": total, "reused": False,
                "link": markdown_link(file_id, label)}
    except BaseException:
        if counted:
            _uncount_turn(turn)
        raise
    finally:
        os.close(fd)
        if directory is not None:
            shutil.rmtree(str(directory), ignore_errors=True)


def _session_full(held: int) -> str:
    return ("this conversation already holds {} of shared files, and one conversation may "
            "hold {}; share a smaller file, or continue in a new session".format(
                _size(held), _size(MAX_SESSION_BYTES)))


def adopt(source_id: int, target_id: int) -> int:
    """Move a task's shares to its Main as the task is folded into it.

    The folded archive keeps the task's answers word for word, links and all,
    and renders them in Main's view: the files move with the words, under the
    same ids, so those links keep working. Answers how many moved.
    """
    source, target = _session_root(source_id), _session_root(target_id)
    moved = 0
    try:
        children = sorted(source.iterdir())
    except OSError:
        return 0
    for child in children:
        if not FILE_ID.fullmatch(child.name):
            continue
        try:
            uploads._validated_upload_file(int(source_id), child.name)
            uploads._private_directory(target.parent)
            uploads._private_directory(target)
            os.rename(str(child), str(target / child.name))
            moved += 1
        except OSError as exc:
            log.warning("session %s shared file %s stayed with its task: %s",
                        target_id, child.name, exc)
    if moved:
        log.info("session %s took %d shared file(s) from task %s", target_id, moved, source_id)
    return moved


def _size(count: int) -> str:
    value = float(count)
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return "{} {}".format(int(value), unit) if unit == "bytes" else \
                "{:.1f} {}".format(value, unit)
        value /= 1024
    return "{} bytes".format(count)


# ---- routes ----

async def h_download(request: web.Request):
    """One shared file, only ever as a download (HEAD answers its headers)."""
    if request.app.get("puppy_snapshot_busy") == "restore":
        return web.json_response({"error": "Puppy restore in progress"}, status=503)
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    file_id = request.match_info.get("file_id", "")
    if not FILE_ID.fullmatch(file_id):
        return web.json_response({"error": "invalid shared file id"}, status=400)
    try:
        target = uploads._validated_upload_file(session_id, file_id)
    except FileNotFoundError:
        return web.json_response({"error": "this file is no longer available"}, status=404,
                                 headers={"Cache-Control": "no-store"})
    except OSError as exc:
        log.warning("session %s shared file %s failed validation: %s", session_id, file_id, exc)
        return web.json_response({"error": "shared file storage failed safety validation"},
                                 status=409)
    return web.FileResponse(target, headers=download_headers(target.name))


async def h_list(request: web.Request):
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    files = await asyncio.get_running_loop().run_in_executor(None, shared, session_id)
    return web.json_response({"files": files[:MAX_LISTED], "more": len(files) > MAX_LISTED},
                             headers={"Cache-Control": "no-store"})


def register(app: web.Application) -> None:
    app.router.add_get("/api/sessions/{sid:\\d+}/files", h_list)
    app.router.add_get("/api/sessions/{sid:\\d+}/files/{file_id:f[0-9a-f]{32}}", h_download)

    async def on_startup(_app):
        from puppy import files_agent
        await files_agent.start(_app)

    async def on_cleanup(_app):
        from puppy import files_agent
        await files_agent.stop(_app)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
