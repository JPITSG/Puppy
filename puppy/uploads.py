"""Private, bounded session file uploads shared by full and headless nodes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import time
import unicodedata
from urllib.parse import unquote

from aiohttp import web

from puppy import config, db

log = logging.getLogger("puppy.uploads")

FILENAME_HEADER = "X-Puppy-Filename"
SIZE_HEADER = "X-Puppy-Size"
STREAM_CHUNK_BYTES = 256 * 1024
MAX_FILENAME_BYTES = 180
UPLOAD_ID = re.compile(r"^\d{13}-[0-9a-f]{10}$")

_active_uploads = 0


def settings_payload() -> dict:
    megabytes = config.normalize_upload_limit_mb(
        config.get("uploads.max_file_size_mb", config.DEFAULT_UPLOAD_LIMIT_MB))
    return {
        "enabled": megabytes > 0,
        "max_file_size_mb": megabytes,
        "max_file_size_bytes": megabytes * 1024 * 1024,
    }


def active_count() -> int:
    return max(0, int(_active_uploads))


def _safe_filename(value: str) -> str:
    try:
        value = unquote(str(value or ""), errors="strict")
    except (UnicodeDecodeError, ValueError):
        value = ""
    value = unicodedata.normalize("NFKC", value).replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join("_" if ord(char) < 32 or ord(char) == 127 else char for char in value)
    value = value.strip()
    if value in ("", ".", ".."):
        value = "file"
    while len(value.encode("utf-8", "replace")) > MAX_FILENAME_BYTES:
        value = value[:-1]
    return value or "file"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != int(uid_getter()):
        raise OSError("upload storage is not a private service-owned directory")
    path.chmod(0o700)


def _new_upload_directory(session_id: int) -> Path:
    root = Path(config.DATA_DIR).resolve() / "uploads"
    _private_directory(root)
    session_root = root / str(int(session_id))
    _private_directory(session_root)
    for _attempt in range(8):
        candidate = session_root / ("{}-{}".format(
            int(time.time() * 1000), secrets.token_hex(5)))
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise OSError("could not allocate private upload storage")


def _discard_partial(directory: Path, partial: Path) -> None:
    try:
        partial.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("partial upload retained at %s", partial)
        return
    try:
        directory.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("empty partial upload directory retained at %s", directory)


async def h_settings_get(_request: web.Request):
    return web.json_response({"uploads": settings_payload()})


async def h_settings_patch(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid upload settings request"}, status=400)
    if not isinstance(body, dict) or "max_file_size_mb" not in body:
        return web.json_response({"error": "maximum upload size is required"}, status=400)
    try:
        value = config.normalize_upload_limit_mb(body["max_file_size_mb"])
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    config.set_value("uploads.max_file_size_mb", value)
    return web.json_response({"ok": True, "uploads": settings_payload()})


async def h_session_upload(request: web.Request):
    """Stream one arbitrary file into private session-owned upload storage."""
    global _active_uploads
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)

    policy = settings_payload()
    if not policy["enabled"]:
        return web.json_response({
            "error": "file uploads are disabled on this backend", "uploads": policy,
        }, status=403)
    limit = int(policy["max_file_size_bytes"])
    declared_values = [request.content_length]
    supplied_size = request.headers.get(SIZE_HEADER)
    if supplied_size is not None:
        try:
            supplied_size_value = int(supplied_size)
            if supplied_size_value < 0:
                raise ValueError
            declared_values.append(supplied_size_value)
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid declared file size"}, status=400)
    if any(value is not None and value > limit for value in declared_values):
        return web.json_response({
            "error": "file exceeds the {} MiB upload limit".format(
                policy["max_file_size_mb"]), "uploads": policy,
        }, status=413)

    filename = _safe_filename(request.headers.get(FILENAME_HEADER, "file"))
    content_type = (request.headers.get("Content-Type") or "application/octet-stream")
    content_type = content_type.split(";", 1)[0].strip().lower()[:120] or \
        "application/octet-stream"
    directory = None
    partial = None
    _active_uploads += 1
    try:
        directory = _new_upload_directory(session_id)
        final_path = directory / filename
        partial = directory / ("." + filename + ".part")
        size = 0
        with partial.open("xb") as output:
            os.chmod(str(partial), 0o600)
            async for chunk in request.content.iter_chunked(STREAM_CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=limit, actual_size=size,
                        text=json.dumps({
                            "error": "file exceeds the {} MiB upload limit".format(
                                policy["max_file_size_mb"]),
                            "uploads": policy,
                        }, separators=(",", ":")),
                        content_type="application/json")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(partial), str(final_path))
        partial = None
        log.info("session %s file uploaded: %s (%d bytes, %s)",
                 session_id, final_path, size, content_type)
        return web.json_response({
            "ok": True,
            "upload_id": directory.name,
            "path": str(final_path),
            "name": filename,
            "size": size,
            "content_type": content_type,
            "uploads": policy,
        })
    except web.HTTPException:
        if directory is not None and partial is not None:
            _discard_partial(directory, partial)
        raise
    except (asyncio.CancelledError, ConnectionError):
        if directory is not None and partial is not None:
            _discard_partial(directory, partial)
        raise
    except OSError as exc:
        if directory is not None and partial is not None:
            _discard_partial(directory, partial)
        log.warning("session %s upload failed: %s", session_id, exc)
        return web.json_response({"error": "could not store uploaded file"}, status=500)
    except Exception as exc:
        if directory is not None and partial is not None:
            _discard_partial(directory, partial)
        log.warning("session %s upload stream failed: %s", session_id, exc)
        return web.json_response({"error": "file upload was interrupted"}, status=500)
    finally:
        _active_uploads = max(0, _active_uploads - 1)


async def h_session_upload_delete(request: web.Request):
    """Discard an uploaded file that the composer has not sent."""
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    upload_id = request.match_info.get("upload_id", "")
    if not UPLOAD_ID.fullmatch(upload_id):
        return web.json_response({"error": "invalid upload id"}, status=400)
    directory = Path(config.DATA_DIR).resolve() / "uploads" / str(session_id) / upload_id
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return web.json_response({"ok": True, "removed": False})
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != int(uid_getter()):
        return web.json_response({"error": "upload storage failed safety validation"},
                                 status=409)
    try:
        children = list(directory.iterdir())
        if len(children) != 1:
            raise OSError("upload directory has unexpected contents")
        child_info = children[0].lstat()
        if not stat.S_ISREG(child_info.st_mode) or stat.S_ISLNK(child_info.st_mode) or \
                child_info.st_uid != int(uid_getter()):
            raise OSError("uploaded file failed safety validation")
        children[0].unlink()
        directory.rmdir()
    except OSError as exc:
        log.warning("session %s upload %s could not be discarded: %s",
                    session_id, upload_id, exc)
        return web.json_response({"error": "could not discard uploaded file"}, status=409)
    return web.json_response({"ok": True, "removed": True})


def register(app: web.Application) -> None:
    app.router.add_get("/api/uploads/settings", h_settings_get)
    app.router.add_patch("/api/uploads/settings", h_settings_patch)
    app.router.add_post("/api/sessions/{sid:\\d+}/upload", h_session_upload)
    app.router.add_delete(
        "/api/sessions/{sid:\\d+}/upload/{upload_id:[0-9]{13}-[0-9a-f]{10}}",
        h_session_upload_delete)
