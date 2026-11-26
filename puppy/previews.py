"""Previews: what an agent built, shown to the signed-in user through Puppy.

Agents kept starting ``python3 -m http.server`` on the network to show the user
a page they had made - a 3D model viewer, a wiring diagram, a mockup - and
left those servers running for weeks, readable by anybody on the network. A
preview is the same thing done properly: the turn-bound ``show_preview`` tool
(``puppy.preview_agent``) names a folder, this module records it, and the
console shows it in a tab beside the chat, served by the node that has the
files and relayed by the controller like everything else.

What is served, and how:

* One folder (the preview's root), live: every load reads the files as they
  are on disk, so an agent that changed them calls ``show_preview`` again and
  the user's tab reloads. The folder lies inside the session's working folder
  or the system temporary folder; hidden names are never served, nor Puppy's
  own data folder (unless the session works there), an engine CLI's home or a
  system location, and a link leading out of the root leads nowhere. Every
  touch goes through ``places``, so a drive that stops answering costs a 503,
  never a frozen Puppy.
* The pages run as a sandbox of their own: every answer carries
  ``Content-Security-Policy: sandbox`` without ``allow-same-origin``, so a
  page - and whatever it loads from the internet - never runs as the
  console's origin, never sees its cookies and cannot call its API. That is
  also why the files cannot simply sit behind the sign-in cookie: a sandboxed
  page's own requests carry none. Each preview therefore has a secret token of
  its own (192 random bits) that its URLs carry - ``/preview/<backend>/<token>/
  <path>`` on the controller - handed only to the signed-in console by
  ``GET /api/sessions/{sid}/previews``; the transcript and the agent only ever
  see the four-character ID. A wrong token is an ordinary 404 that says
  nothing about this Puppy.
* A preview lasts until the agent or the user closes it or its session is
  deleted, across restarts: the exact ``session_previews.<sid>`` meta record
  (``{format:1, items:[...]}``) holds them, is validated at startup and in a
  backup, and is never repaired.

The headless backend serves the files only behind its API token
(``/api/previews/<token>/<path>``); the public ``/preview/`` route exists on
the full runtime alone, which answers for its own previews and relays a
backend's (``backends.relay_preview``).
"""
from __future__ import annotations

import errno
import gzip
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import stat
import tempfile
import time
from urllib.parse import quote

from aiohttp import web

from puppy import config, db, places, shared_files, workers

log = logging.getLogger("puppy.previews")

PREFIX = "session_previews."
FORMAT = 1
PREVIEW_ID = re.compile(r"^[A-Z0-9]{4}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
LINK_SCHEME = "puppy-preview:"
ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BASES = ("cwd", "temp")
MAX_PREVIEWS = 20
MAX_NAME = 120
MAX_PATH = 4096
MAX_FILE_BYTES = 256 * 1024 * 1024
CHUNK = 2 * 1024 * 1024
GZIP_MAX = 16 * 1024 * 1024
MAX_LISTED = 2000
INDEX = "index.html"
PUBLIC_PREFIX = "/preview/"

# Every answer a preview gives, here and through a controller. The page is a
# sandbox with an origin of its own: its scripts run, but never as the
# console's origin, and its own fetches (an opaque origin's, so CORS) are let
# through. Only Puppy's own page may frame it, and nothing it loads learns the
# URL - the token - it came from.
PREVIEW_HEADERS = {
    "Content-Security-Policy": (
        "sandbox allow-scripts allow-forms allow-popups allow-modals "
        "allow-downloads allow-pointer-lock allow-presentation; "
        "frame-ancestors 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "private, no-cache",
}

# The types an agent's pages are made of, where the system table is silent or
# out of date; anything unknown is served as bytes to download.
TYPES = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
    ".map": "application/json", ".txt": "text/plain", ".md": "text/plain",
    ".log": "text/plain", ".csv": "text/csv", ".xml": "application/xml",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".avif": "image/avif", ".ico": "image/x-icon", ".bmp": "image/bmp",
    ".wasm": "application/wasm", ".pdf": "application/pdf",
    ".stl": "model/stl", ".obj": "model/obj", ".3mf": "model/3mf",
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".otf": "font/otf", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".zip": "application/zip", ".gz": "application/gzip",
}
TEXT_TYPES = ("application/javascript", "application/json", "application/xml",
              "image/svg+xml", "model/obj", "model/gltf+json")
COMPRESSIBLE = TEXT_TYPES + ("model/stl", "application/wasm")


class PreviewError(RuntimeError):
    """A refusal worded for the agent or the console: what was wrong."""


# ---- records ----

def _key(session_id) -> str:
    return PREFIX + str(int(session_id))


def _clean_name(value) -> str:
    value = " ".join(str(value or "").split())
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)
    return value[:MAX_NAME].strip()


def _relative_ok(value, directory=False) -> bool:
    """A path inside a root: names joined by "/", never empty, ".", ".." or
    hidden; a folder's entry ends in "/"."""
    if not isinstance(value, str) or len(value) > MAX_PATH or "\x00" in value:
        return False
    if value == "":
        return True
    if directory and value.endswith("/"):
        value = value[:-1]
    parts = value.split("/")
    return all(part and not part.startswith(".") for part in parts)


def _valid_item(item) -> bool:
    if not isinstance(item, dict) or set(item) != {
            "id", "token", "base", "root", "entry", "name", "created_at", "shown_at"}:
        return False
    if not isinstance(item["id"], str) or not PREVIEW_ID.fullmatch(item["id"]):
        return False
    if not isinstance(item["token"], str) or not TOKEN.fullmatch(item["token"]):
        return False
    if item["base"] not in BASES or not _relative_ok(item["root"]) or \
            item["root"].endswith("/") or (item["base"] == "temp" and not item["root"]):
        return False
    if not _relative_ok(item["entry"], directory=True):
        return False
    if not isinstance(item["name"], str) or item["name"] != _clean_name(item["name"]) or \
            not item["name"]:
        return False
    for field in ("created_at", "shown_at"):
        value = item[field]
        if type(value) not in (int, float) or value != value or value <= 0 or \
                value == float("inf"):
            return False
    return item["shown_at"] >= item["created_at"]


def _items(value) -> list:
    """The previews of one record, or ValueError: exact shape, never repaired."""
    if not isinstance(value, dict) or set(value) != {"format", "items"} or \
            type(value["format"]) is not int or value["format"] != FORMAT or \
            not isinstance(value["items"], list) or \
            not 0 < len(value["items"]) <= MAX_PREVIEWS:
        raise ValueError("invalid preview record")
    items = value["items"]
    if not all(_valid_item(item) for item in items):
        raise ValueError("invalid preview")
    if [item["created_at"] for item in items] != sorted(
            item["created_at"] for item in items):
        raise ValueError("previews out of order")
    return items


def _scan(connection=None) -> dict:
    """{session id: previews} for every record, read in one query."""
    sql = "SELECT key, value FROM meta WHERE key >= ? AND key < ?"
    args = (PREFIX, PREFIX[:-1] + "/")
    rows = connection.execute(sql, args).fetchall() if connection is not None \
        else db.query(sql, args)
    found = {}
    for row in rows:
        suffix = row[0][len(PREFIX):]
        if not suffix.isdigit() or str(int(suffix)) != suffix or int(suffix) <= 0:
            raise ValueError("invalid preview record key")
        found[int(suffix)] = _items(json.loads(row[1]))
    return found


def validate_persisted(connection) -> None:
    """Every record exact, its session there, ids and tokens unique on the node."""
    found = _scan(connection)
    ids, tokens = set(), set()
    for session_id, items in found.items():
        if connection.execute("SELECT 1 FROM sessions WHERE id=?",
                              (session_id,)).fetchone() is None:
            raise ValueError("previews of a session that does not exist")
        for item in items:
            if item["id"] in ids or item["token"] in tokens:
                raise ValueError("a preview id or token is used twice")
            ids.add(item["id"])
            tokens.add(item["token"])


def previews_of(session_id: int) -> list:
    value = db.meta_get(_key(session_id))
    return [] if value is None else _items(value)


def _write(session_id: int, items: list) -> None:
    if items:
        db.meta_set(_key(session_id), {"format": FORMAT, "items": items})
    else:
        db.meta_del(_key(session_id))


def find(token: str):
    """(session id, preview) for a token, or None."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    for session_id, items in _scan().items():
        for item in items:
            if secrets.compare_digest(item["token"], token):
                return session_id, item
    return None


def _new_identity() -> tuple:
    used = _scan()
    ids = {item["id"] for items in used.values() for item in items}
    tokens = {item["token"] for items in used.values() for item in items}
    for _attempt in range(64):
        preview_id = "".join(secrets.choice(ID_ALPHABET) for _ in range(4))
        if preview_id not in ids:
            break
    else:
        raise PreviewError("could not choose an unused preview ID; try again")
    token = secrets.token_urlsafe(24)
    while token in tokens:
        token = secrets.token_urlsafe(24)
    return preview_id, token


# ---- where a preview's files are ----

def base_dir(session: dict, base: str) -> str:
    if base == "cwd":
        return str(session.get("cwd") or "") or "/"
    return tempfile.gettempdir()


def folder(session: dict, item: dict) -> str:
    """The preview's folder as the agent would write it."""
    root = base_dir(session, item["base"])
    return os.path.join(root, item["root"]) if item["root"] else root


def where(session: dict, item: dict) -> str:
    """The folder and what opens in it, as a person reads it: relative to the
    session's working folder, absolute in the temporary folder."""
    shown = (item["root"] + "/" if item["root"] else "./") if item["base"] == "cwd" \
        else folder(session, item) + "/"
    return shown + item["entry"]


def _hidden(relative: str) -> bool:
    return any(part.startswith(".") for part in relative.split(os.sep) if part)


def _never(real: str, real_root: str) -> str:
    """Why a resolved path inside a preview is never served, or ""."""
    data = os.path.realpath(config.DATA_DIR)
    if shared_files._inside(real, data) and not shared_files._inside(real_root, data):
        return "Puppy's own data folder is never served"
    if shared_files._inside(real, shared_files._cli_homes()):
        return "an engine CLI's own home folder is never served"
    home = shared_files._home()
    if home and home != "/" and shared_files._inside(real, home) and real != home:
        first = os.path.relpath(real, home).split(os.sep)[0]
        if first.startswith(".") and not shared_files._inside(
                real_root, os.path.join(home, first)):
            return ("hidden files and folders directly in the home folder are never "
                    "served: that is where sign-ins, keys and tokens live")
    for location in shared_files.SYSTEM_LOCATIONS:
        if shared_files._inside(real, location):
            return "system files under {} are never served".format(location)
    return ""


def _lead(path: str, base: str):
    """Where a path leads from a thread standing at ``base``: (real, folder?)
    or PreviewError when it leads to another drive."""
    found = places.nearby(path, base)
    if found is None:
        raise PreviewError("that path leads to another drive")
    return found


def named(cwd: str, value: str) -> str:
    """A path the agent wrote, made absolute against the working folder."""
    value = value.strip()
    value = os.path.expanduser(value) if value.startswith("~") else value
    return os.path.normpath(value if os.path.isabs(value) else os.path.join(cwd or "/", value))


def resolve(real_cwd: str, target: str, real_root=None) -> dict:
    """What ``show_preview`` names, checked, from the real paths
    ``places.visit`` found; in a thread that has arrived at the target.
    {base, root, entry, real_root, folder} or PreviewError."""
    temp = shared_files.temp_root()
    try:
        info = os.stat(target)
    except FileNotFoundError:
        raise PreviewError("there is nothing at that path")
    except OSError as exc:
        raise PreviewError("that path cannot be read: {}".format(exc.strerror or exc))
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise PreviewError("only a folder or a regular file can be shown")
    is_dir = stat.S_ISDIR(info.st_mode)
    if real_root is None:
        real_root = target if is_dir else os.path.dirname(target)
    elif not os.path.isdir(real_root):
        raise PreviewError("root must be a folder")
    if not shared_files._inside(target, real_root):
        raise PreviewError("the page must be inside the root folder it is served from")
    home = shared_files._home()
    if shared_files._inside(real_root, real_cwd):
        if real_root == real_cwd and real_cwd in ("/", home):
            raise PreviewError("that would serve the whole home or system folder; show "
                               "the folder holding the page instead")
        base, relative = "cwd", os.path.relpath(real_root, real_cwd)
    elif shared_files._inside(real_root, temp) and real_root != temp:
        base, relative = "temp", os.path.relpath(real_root, temp)
    else:
        raise PreviewError(
            "only folders inside this session's working folder ({}) or the temporary "
            "folder ({}) can be shown; copy the files into one of them first".format(
                real_cwd, temp))
    relative = "" if relative == os.curdir else relative
    never = _never(real_root, real_root if base == "temp" else real_cwd)
    if never:
        raise PreviewError(never)
    entry = os.path.relpath(target, real_root)
    entry = "" if entry == os.curdir else entry
    if _hidden(relative) or _hidden(entry):
        raise PreviewError("hidden files and folders are never served; move the page "
                           "out of the hidden folder")
    if len(relative) > MAX_PATH or len(entry) > MAX_PATH:
        raise PreviewError("that path is too long")
    if is_dir and entry:
        entry += "/"
    return {"base": base, "root": relative.replace(os.sep, "/"),
            "entry": entry.replace(os.sep, "/"), "real_root": real_root,
            "folder": is_dir}


def remember(session_id: int, found: dict, name=None) -> tuple:
    """Record a preview, or bring an existing one of the same page up to
    date: (preview, whether it is new). On the loop, with no await."""
    items = previews_of(session_id)
    now = time.time()
    label = _clean_name(name) if name is not None else ""
    for index, item in enumerate(items):
        if (item["base"], item["root"], item["entry"]) == \
                (found["base"], found["root"], found["entry"]):
            item = dict(item, shown_at=max(now, item["created_at"]),
                        name=label or item["name"])
            items[index] = item
            _write(session_id, items)
            return item, False
    if len(items) >= MAX_PREVIEWS:
        raise PreviewError(
            "this session already has {} previews; close one the user no longer needs "
            "(previews lists them) and show this again".format(MAX_PREVIEWS))
    preview_id, token = _new_identity()
    default = found["entry"].rstrip("/").rsplit("/", 1)[-1]
    if not default or default == INDEX:
        default = os.path.basename(found["real_root"].rstrip(os.sep))
    item = {"id": preview_id, "token": token, "base": found["base"],
            "root": found["root"], "entry": found["entry"],
            "name": label or _clean_name(default) or "Preview",
            "created_at": now, "shown_at": now}
    _write(session_id, items + [item])
    return item, True


def forget(session_id: int, preview_id: str) -> bool:
    items = previews_of(session_id)
    kept = [item for item in items if item["id"] != preview_id]
    if len(kept) == len(items):
        return False
    _write(session_id, kept)
    return True


def markdown_link(item: dict) -> str:
    return "[{}]({}{})".format(shared_files.link_text(item["name"]), LINK_SCHEME,
                               item["id"])


def public(session: dict, item: dict) -> dict:
    """A preview as the signed-in console reads it, token included."""
    return {"id": item["id"], "name": item["name"], "token": item["token"],
            "entry": item["entry"], "folder": folder(session, item),
            "where": where(session, item), "created_at": item["created_at"],
            "shown_at": item["shown_at"]}


# ---- serving ----

def content_type(name: str) -> str:
    extension = os.path.splitext(name)[1].lower()
    kind = TYPES.get(extension) or mimetypes.guess_type(name)[0] or \
        "application/octet-stream"
    return kind


def _texty(kind: str) -> bool:
    return kind.startswith("text/") or kind in TEXT_TYPES


def _type_header(kind: str) -> str:
    return kind + "; charset=utf-8" if _texty(kind) else kind


def _open_inside(real_root: str, relative: str):
    """(fd, stat) of a file inside the root, reached without following a link."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    parts = [part for part in relative.split(os.sep) if part]
    folder_fd = os.open(real_root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    try:
        for part in parts[:-1]:
            step = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=folder_fd)
            os.close(folder_fd)
            folder_fd = step
        if not parts:
            raise IsADirectoryError(real_root)
        fd = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
                     dir_fd=folder_fd)
    finally:
        os.close(folder_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise IsADirectoryError(relative)
    return fd, info


def _listing(real_dir: str, real_root: str, title: str, at_root: bool) -> bytes:
    """A folder without an index.html, as a page of links: what a plain web
    server shows, never a hidden name nor a link leading out of the preview
    (followed with ``places.nearby``, so one to a drive that does not answer
    is never touched)."""
    rows = []
    more = 0
    with os.scandir(real_dir) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if len(rows) >= MAX_LISTED:
                more += 1
                continue
            try:
                if entry.is_symlink():
                    lead = places.nearby(entry.path, real_root)
                    if lead is None or not shared_files._inside(lead[0], real_root) or \
                            _hidden(os.path.relpath(lead[0], real_root)) or \
                            _never(lead[0], real_root):
                        continue
                    info = os.stat(lead[0])
                else:
                    info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(info.st_mode)
            if not is_dir and not stat.S_ISREG(info.st_mode):
                continue
            rows.append((not is_dir, entry.name.lower(), entry.name, is_dir,
                         0 if is_dir else info.st_size))
    rows.sort()
    lines = [] if at_root else ['<li class="up"><a href="../">../</a></li>']
    for _file, _key_name, name, is_dir, size in rows:
        href = quote(name, safe="") + ("/" if is_dir else "")
        label = html.escape(name + ("/" if is_dir else ""))
        size_text = "" if is_dir else shared_files._size(size)
        lines.append('<li><a href="{}">{}</a><span>{}</span></li>'.format(
            href, label, html.escape(size_text)))
    if more:
        lines.append("<li class=\"more\">and {} more</li>".format(more))
    if not rows and at_root:
        lines.append("<li class=\"more\">This folder is empty</li>")
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>{title}</title><style>"
        ":root{{color-scheme:light dark;font:14px/1.5 system-ui,-apple-system,"
        "\"Segoe UI\",Roboto,sans-serif}}body{{margin:0;padding:20px 24px}}"
        "h1{{font-size:15px;margin:0 0 12px;font-weight:600}}ul{{list-style:none;"
        "margin:0;padding:0;max-width:720px}}li{{display:flex;gap:16px;"
        "justify-content:space-between;padding:4px 0;border-bottom:1px solid "
        "rgba(128,128,128,.2)}}a{{color:inherit;overflow-wrap:anywhere}}"
        "span,.more{{opacity:.65;white-space:nowrap}}</style></head><body>"
        "<h1>{title}</h1><ul>{rows}</ul></body></html>").format(
            title=html.escape(title), rows="".join(lines))
    return page.encode("utf-8")


def _look(real_root: str, request_parts: list, trailing: bool, title: str):
    """In a thread that has arrived at the root: what one request names.

    ("missing", why) | ("redirect", location) | ("listing", bytes) |
    ("file", fd, stat, name)."""
    for part in request_parts:
        if not part or part in (".", "..") or part.startswith(".") or "\x00" in part:
            return ("missing", "hidden or invalid name")
    real, _is_dir = _lead(os.path.join(real_root, *request_parts), real_root)
    if not shared_files._inside(real, real_root):
        return ("missing", "leads out of the preview")
    relative = os.path.relpath(real, real_root)
    relative = "" if relative == os.curdir else relative
    if _hidden(relative) or _never(real, real_root):
        return ("missing", "never served")
    try:
        info = os.stat(real)
    except OSError:
        return ("missing", "not there")
    if stat.S_ISDIR(info.st_mode):
        if request_parts and not trailing:
            return ("redirect", quote(request_parts[-1], safe="") + "/")
        index = os.path.join(relative, INDEX) if relative else INDEX
        try:
            fd, opened = _open_inside(real_root, index)
            return ("file", fd, opened, INDEX)
        except (FileNotFoundError, IsADirectoryError):
            pass
        except OSError as exc:
            if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
                raise
        shown = title + ("/" + relative.replace(os.sep, "/") if relative else "")
        return ("listing", _listing(real, real_root, shown, not relative))
    if trailing or not stat.S_ISREG(info.st_mode):
        return ("missing", "not a file")
    try:
        fd, opened = _open_inside(real_root, relative)
    except OSError:
        return ("missing", "could not be opened")
    return ("file", fd, opened, os.path.basename(real))


def request_parts(path):
    """(names, ends in "/") of a request's path within a preview, or None
    for one no preview answers: empty, ".", ".." or hidden names."""
    if not isinstance(path, str) or len(path) > MAX_PATH or "\x00" in path:
        return None
    trailing = path.endswith("/") or path == ""
    parts = path.split("/")
    if trailing:
        parts = parts[:-1]
    if any(not part or part in (".", "..") or part.startswith(".") for part in parts):
        return None
    return parts, trailing


def _read_all(fd: int, size: int) -> bytes:
    chunks, total = [], 0
    while total < size:
        chunk = os.pread(fd, min(CHUNK, size - total), total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _etag(info) -> str:
    return '"{:x}-{:x}-{:x}"'.format(info.st_size, info.st_mtime_ns, info.st_ino)


def _matches(request: web.Request, etag: str) -> bool:
    wanted = request.headers.get("If-None-Match", "")
    if not wanted:
        return False
    tags = [tag.strip() for tag in wanted.split(",")]
    plain = etag.strip('"')
    return "*" in tags or any(tag.replace("W/", "").strip('"') in (plain, plain + "-gz")
                              for tag in tags)


def _text(status: int, text: str, extra=None) -> web.Response:
    headers = dict(PREVIEW_HEADERS)
    headers["Cache-Control"] = "no-store"
    headers.update(extra or {})
    return web.Response(status=status, text=text, content_type="text/plain",
                        headers=headers)


def unavailable() -> web.Response:
    """A preview whose backend cannot be reached now, naming no backend."""
    return _text(503, "This preview is not available right now", {"Retry-After": "8"})


def not_found() -> web.Response:
    """The one answer for anything a preview does not have - a wrong token
    included, which must say nothing about this Puppy."""
    return _text(404, "Not found")


async def _close(real_root: str, fd: int) -> None:
    try:
        await places.ask(real_root, os.close, fd)
    except Exception:
        pass    # a drive that has gone keeps the descriptor; nothing else to do


async def serve(request: web.Request, token: str, path: str) -> web.StreamResponse:
    """One file of a preview, by its token, as a sandboxed page's resource."""
    if request.app.get("puppy_snapshot_busy") == "restore":
        return _text(503, "Puppy restore in progress")
    try:
        found = find(token)
    except ValueError:
        log.error("a preview record failed validation")
        return _text(500, "Preview records are invalid")
    if found is None:
        return not_found()
    session_id, item = found
    session = db.get_session(session_id)
    if session is None:
        return not_found()
    named = request_parts(path)
    if named is None:
        return not_found()
    parts, trailing = named
    root_path = folder(session, item)
    try:
        place = await places.visit(root_path)
        if not place.directory:
            return _text(404, "This preview's folder is no longer there")
        real_root = place.real
        looked = await places.ask(real_root, _look, real_root, parts, trailing, item["name"])
    except places.NotAnswering as exc:
        return _text(503, "This preview's folder is not answering · {}".format(exc))
    except PreviewError:
        return not_found()
    if looked[0] == "missing":
        return not_found()
    if looked[0] == "redirect":
        headers = dict(PREVIEW_HEADERS, Location=looked[1])
        headers["Cache-Control"] = "no-store"
        return web.Response(status=301, headers=headers)
    if looked[0] == "listing":
        headers = dict(PREVIEW_HEADERS)
        headers["Cache-Control"] = "no-store"
        return web.Response(body=looked[1], headers=headers,
                            content_type="text/html", charset="utf-8")
    _kind, fd, info, name = looked
    try:
        return await _send(request, real_root, fd, info, name)
    finally:
        await _close(real_root, fd)


async def _send(request, real_root, fd, info, name) -> web.StreamResponse:
    if info.st_size > MAX_FILE_BYTES:
        return _text(413, "This file is larger than a preview serves ({})".format(
            shared_files._size(MAX_FILE_BYTES)))
    kind = content_type(name)
    etag = _etag(info)
    headers = dict(PREVIEW_HEADERS)
    headers["ETag"] = etag
    headers["Vary"] = "Accept-Encoding"
    if _matches(request, etag):
        return web.Response(status=304, headers=headers)
    headers["Content-Type"] = _type_header(kind)
    accepts = "gzip" in request.headers.get("Accept-Encoding", "").lower()
    if accepts and (_texty(kind) or kind in COMPRESSIBLE) and \
            1024 <= info.st_size <= GZIP_MAX:
        body = await places.ask(real_root, _read_all, fd, info.st_size)
        packed = await workers.jobs.run(gzip.compress, body, 6)
        if len(packed) < len(body):
            headers["Content-Encoding"] = "gzip"
            headers["ETag"] = etag[:-1] + '-gz"'
            body = packed
        return web.Response(body=body, headers=headers)
    headers["Content-Length"] = str(info.st_size)
    response = web.StreamResponse(headers=headers)
    await response.prepare(request)
    if request.method != "HEAD":
        offset = 0
        while offset < info.st_size:
            chunk = await places.ask(real_root, os.pread, fd,
                                     min(CHUNK, info.st_size - offset), offset)
            if not chunk:
                break
            await response.write(chunk)
            offset += len(chunk)
        if offset < info.st_size:
            # the file shrank while it was sent: end without the response's
            # own end, so the browser sees a failure, never a short file
            if request.transport is not None:
                request.transport.close()
            return response
    await response.write_eof()
    return response


# ---- routes ----

async def h_node_file(request: web.Request):
    """A preview's file behind the node's own authentication: what a
    controller relays for a backend's preview."""
    return await serve(request, request.match_info["token"],
                       request.match_info.get("path", ""))


async def h_public(request: web.Request):
    """The full runtime's ``/preview/<backend>/<token>/<path>``: no sign-in,
    only the token. This instance's own previews, or a backend's relayed."""
    try:
        bid = int(request.match_info["bid"])
    except (TypeError, ValueError):
        return not_found()
    token = request.match_info["token"]
    if not TOKEN.fullmatch(token):
        return not_found()
    path = request.match_info.get("path", "")
    if bid == 0:
        return await serve(request, token, path)
    from puppy import backends
    return await backends.relay_preview(request, bid, token, path)


async def h_public_root(request: web.Request):
    """``/preview/<backend>/<token>`` without its slash: the relative links
    of the page it opens need one."""
    if not TOKEN.fullmatch(request.match_info["token"]):
        return not_found()
    headers = dict(PREVIEW_HEADERS, Location=request.match_info["token"] + "/")
    headers["Cache-Control"] = "no-store"
    return web.Response(status=301, headers=headers)


async def h_list(request: web.Request):
    session_id = int(request.match_info["sid"])
    session = db.get_session(session_id)
    if session is None:
        return web.json_response({"error": "session not found"}, status=404)
    try:
        items = previews_of(session_id)
    except ValueError:
        return web.json_response({"error": "this session's preview record is invalid"},
                                 status=409)
    return web.json_response({"previews": [public(session, item) for item in items]},
                             headers={"Cache-Control": "no-store"})


async def h_delete(request: web.Request):
    session_id = int(request.match_info["sid"])
    preview_id = request.match_info["pid"]
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response({"error": "a Puppy backup or restore is in progress"},
                                 status=503)
    try:
        removed = forget(session_id, preview_id)
    except ValueError:
        return web.json_response({"error": "this session's preview record is invalid"},
                                 status=409)
    if not removed:
        return web.json_response({"error": "this preview is no longer open"}, status=404)
    from puppy import runner
    runner.hub(session_id).preview_activity("", preview_id, closed=True)
    return web.json_response({"ok": True, "id": preview_id})


def register(app: web.Application) -> None:
    """The node's half, on both runtimes."""
    app.router.add_get("/api/sessions/{sid:\\d+}/previews", h_list)
    app.router.add_delete("/api/sessions/{sid:\\d+}/previews/{pid:[A-Z0-9]{4}}", h_delete)
    app.router.add_get("/api/previews/{token:[A-Za-z0-9_-]{32}}/{path:.*}", h_node_file)

    async def on_startup(_app):
        # a malformed record refuses to start rather than being repaired
        validate_persisted(db.connect())
        from puppy import preview_agent
        await preview_agent.start(_app)

    async def on_cleanup(_app):
        from puppy import preview_agent
        await preview_agent.stop(_app)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)


def register_public(app: web.Application) -> None:
    """The full runtime's public route: the console's frames and windows."""
    app.router.add_get(PUBLIC_PREFIX + "{bid:\\d+}/{token:[A-Za-z0-9_-]+}", h_public_root)
    app.router.add_get(PUBLIC_PREFIX + "{bid:\\d+}/{token:[A-Za-z0-9_-]+}/{path:.*}",
                       h_public)
