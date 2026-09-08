"""Private, bounded session file uploads shared by full and headless nodes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import threading
import time
import unicodedata
from urllib.parse import unquote

from aiohttp import web

from puppy import config, db, operations

log = logging.getLogger("puppy.uploads")

FILENAME_HEADER = "X-Puppy-Filename"
SIZE_HEADER = "X-Puppy-Size"
STREAM_CHUNK_BYTES = 256 * 1024
MAX_FILENAME_BYTES = 180
UPLOAD_ID = re.compile(r"^\d{13}-[0-9a-f]{10}$")
# Previews are served for these raster types only. SVG is deliberately absent:
# it is scriptable, and this route hands bytes back on the console's own origin.
PREVIEW_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# Attachment markers are persisted as ordinary user-message text.  Keep the
# server-side spellings beside the storage lifecycle code so queue cancellation
# can recognize the uploads it made unreachable without treating arbitrary
# paths mentioned in prose as files to delete.
ATTACH_IMAGE_PREFIX = "[image attached: "
ATTACH_IMAGE_SUFFIX = " — view it with your image/file tools]"
ATTACH_FILE_PREFIX = "[file attached: "
ATTACH_FILE_SUFFIX = " — inspect it with your file tools]"

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


def _state_changed() -> None:
    try:
        from puppy import state_stream
        state_stream.wake("node")
    except Exception:
        pass


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


# Orphan sweep. An upload is stranded when the composer that staged it never
# sent and never discarded it - a closed tab, a lost connection. Age is what
# makes this safe to run beside live uploads with no locking against them: a
# file is legitimately unreferenced from its POST until the send that names
# it, and a staged attachment lives in the durable draft, so nothing younger
# than the gate is ever a candidate.
SWEEP_INTERVAL_SECONDS = 60.0
ORPHAN_AGE_SECONDS = 24 * 60 * 60
_sweep_lock = threading.Lock()
_last_sweep = 0.0


def _upload_session_ids(root: Path) -> list:
    ids = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return ids
    for child in children:
        if child.name.isdigit() and child.is_dir() and not child.is_symlink():
            ids.append(int(child.name))
    return ids


def sweep_orphans(session_id=None) -> int:
    """Remove upload directories no durable text names any more.

    ``session_id`` limits the pass to one session; without it every session
    that owns upload storage is swept, which is how a session nobody uploads
    to again is reached. Every uncertainty retains the file: keeping private
    bytes is cheaper than deleting one a message still names. A session whose
    row is gone has no referencing text at all, so this also recovers the
    storage of a delete that failed part way.
    """
    root = Path(config.DATA_DIR).resolve() / "uploads"
    if not root.is_dir():
        return 0
    targets = [int(session_id)] if session_id is not None else _upload_session_ids(root)
    now = time.time()
    removed = 0
    for sid in targets:
        session_root = root / str(sid)
        try:
            children = sorted(session_root.iterdir())
        except OSError:
            continue
        draft = ""
        candidates = []
        for child in children:
            if not UPLOAD_ID.fullmatch(child.name) or child.is_symlink() or \
                    not child.is_dir():
                continue
            try:
                if now - child.lstat().st_mtime < ORPHAN_AGE_SECONDS:
                    continue
            except OSError:
                continue
            candidates.append(child)
        if not candidates:
            continue
        try:
            draft = str(db.get_session_draft(sid).get("text") or "")
        except Exception as exc:
            log.warning("upload sweep skipped session %s: %s", sid, exc)
            continue
        for child in candidates:
            try:
                if upload_is_referenced(sid, child.name, (draft,)):
                    continue
                shutil.rmtree(str(child))
                removed += 1
            except Exception as exc:
                log.warning("stale upload retained: %s", exc)
    if removed:
        log.info("discarded %s stranded upload(s)", removed)
    return removed


def _sweep_due(session_id: int) -> None:
    """Throttled sweep of one session, from the path that already writes it."""
    global _last_sweep
    now = time.monotonic()
    with _sweep_lock:
        if now - _last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep = now
    try:
        sweep_orphans(session_id)
    except Exception as exc:
        log.warning("upload sweep failed: %s", exc)


def _new_upload_directory(session_id: int) -> Path:
    root = Path(config.DATA_DIR).resolve() / "uploads"
    _private_directory(root)
    session_root = root / str(int(session_id))
    _private_directory(session_root)
    _sweep_due(int(session_id))
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


def _validated_upload_file(session_id: int, upload_id: str) -> Path:
    """The one regular file inside an upload directory, or raise.

    Discarding and previewing share this so a single set of checks governs both:
    the directory and its file must be real, non-symlink, owned by this service,
    and the directory must hold exactly the one file the upload wrote.
    """
    directory = Path(config.DATA_DIR).resolve() / "uploads" / str(int(session_id)) / upload_id
    info = directory.lstat()   # FileNotFoundError is the caller's to interpret
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != int(uid_getter()):
        raise OSError("upload storage is not a private service-owned directory")
    children = list(directory.iterdir())
    if len(children) != 1:
        raise OSError("upload directory has unexpected contents")
    child_info = children[0].lstat()
    if not stat.S_ISREG(child_info.st_mode) or stat.S_ISLNK(child_info.st_mode) or \
            child_info.st_uid != int(uid_getter()):
        raise OSError("uploaded file failed safety validation")
    return children[0]


def _attachment_upload_ids(session_id: int, text: str) -> set:
    """Upload ids named by well-formed attachment marker lines in ``text``."""
    if not isinstance(text, str) or not text:
        return set()
    root = str(Path(config.DATA_DIR).resolve() / "uploads" / str(int(session_id))) + "/"
    pattern = re.compile(re.escape(root) + r"(\d{13}-[0-9a-f]{10})/")
    found = set()
    for line in text.splitlines():
        if line.startswith(ATTACH_IMAGE_PREFIX) and line.endswith(ATTACH_IMAGE_SUFFIX):
            marker = line[len(ATTACH_IMAGE_PREFIX):-len(ATTACH_IMAGE_SUFFIX)]
        elif line.startswith(ATTACH_FILE_PREFIX) and line.endswith(ATTACH_FILE_SUFFIX):
            marker = line[len(ATTACH_FILE_PREFIX):-len(ATTACH_FILE_SUFFIX)]
        else:
            continue
        found.update(match.group(1) for match in pattern.finditer(marker))
    return found


def upload_is_referenced(session_id: int, upload_id: str, retained=()) -> bool:
    """Whether durable or in-flight session text still owns one upload."""
    session_id = int(session_id)
    if any(upload_id in _attachment_upload_ids(session_id, text)
           for text in retained or () if isinstance(text, str)):
        return True
    referenced = db.query_one(
        "SELECT 1 FROM events WHERE session_id=? AND kind='user' "
        "AND instr(payload, ?) > 0 LIMIT 1",
        (session_id, upload_id))
    return referenced is not None


def discard_abandoned(session_id: int, abandoned, retained=()) -> int:
    """Remove queue-owned uploads which no remaining durable text references.

    Sent attachments are durable transcript data and must remain available for
    previews and history recall.  A prompt cancelled before it starts has no
    transcript event, however, so its newly uploaded files would otherwise be
    stranded until the whole session is deleted.  Fail closed on any database
    or path-validation uncertainty: retaining private bytes is safer than
    deleting a file which another message still names.
    """
    session_id = int(session_id)
    abandoned_ids = set()
    for text in abandoned or ():
        abandoned_ids.update(_attachment_upload_ids(session_id, text))
    if not abandoned_ids:
        return 0

    retained_ids = set()
    for text in retained or ():
        retained_ids.update(_attachment_upload_ids(session_id, text))
    removed = 0
    for upload_id in sorted(abandoned_ids - retained_ids):
        try:
            # The random id is JSON-safe and appears unchanged inside a user
            # event payload. A false positive only retains a file; it can never
            # authorize deletion of a different upload.
            referenced = upload_is_referenced(session_id, upload_id)
        except Exception as exc:
            log.warning("could not check session %s upload %s references: %s",
                        session_id, upload_id, exc)
            continue
        if referenced:
            continue
        try:
            target = _validated_upload_file(session_id, upload_id)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("abandoned session %s upload %s failed safety validation: %s",
                        session_id, upload_id, exc)
            continue
        try:
            target.unlink()
            target.parent.rmdir()
            removed += 1
            log.info("discarded abandoned session %s upload %s", session_id, upload_id)
        except OSError as exc:
            log.warning("abandoned session %s upload %s could not be discarded: %s",
                        session_id, upload_id, exc)
    return removed


class AttachmentError(ValueError):
    """A staged file a message names cannot be carried over as asked."""


def _upload_root(session_id: int) -> str:
    return str(Path(config.DATA_DIR).resolve() / "uploads" / str(int(session_id))) + "/"


def rewrite_attachment_paths(source_id: int, target_id: int, text: str) -> str:
    """``text`` with its marker lines' ``source_id`` upload paths pointing at
    the same upload ids under ``target_id``.

    Pure: nothing is copied or checked, and prose that merely mentions such a
    path is left alone. This is also how a retried task creation is compared
    against the record it made: the retry still carries Main's paths.
    """
    if not isinstance(text, str) or not text:
        return text
    source_root = _upload_root(source_id)
    if source_root not in text:
        return text
    target_root = _upload_root(target_id)
    pattern = re.compile(re.escape(source_root) + r"(\d{13}-[0-9a-f]{10})/")
    lines = []
    for line in text.split("\n"):
        if (line.startswith(ATTACH_IMAGE_PREFIX) and line.endswith(ATTACH_IMAGE_SUFFIX)) or \
                (line.startswith(ATTACH_FILE_PREFIX) and line.endswith(ATTACH_FILE_SUFFIX)):
            line = pattern.sub(lambda match: target_root + match.group(1) + "/", line)
        lines.append(line)
    return "\n".join(lines)


def _staged_file(session_id: int, upload_id: str) -> Path:
    """A staged upload another conversation is about to take over, or raise
    AttachmentError in the words the dialog should show."""
    try:
        return _validated_upload_file(session_id, upload_id)
    except FileNotFoundError:
        raise AttachmentError(
            "An attached file is no longer available; remove it and attach it again")
    except OSError as exc:
        log.warning("session %s upload %s failed adoption validation: %s",
                    session_id, upload_id, exc)
        raise AttachmentError("An attached file failed safety validation")


def verify_attachments(source_id: int, text: str) -> None:
    """Raise AttachmentError unless every upload ``text``'s marker lines stage
    under ``source_id`` is still present and valid. Cheap, so a task can refuse
    before it allocates a working copy; adoption re-checks each file anyway."""
    for upload_id in sorted(_attachment_upload_ids(int(source_id), text)):
        _staged_file(int(source_id), upload_id)


def adopt_attachments(source_id: int, target_id: int, text: str) -> str:
    """Copy the uploads ``text``'s marker lines stage under ``source_id`` into
    ``target_id``'s private storage and return the text naming the copies.

    A task's first prompt is written in Main's dialog, so its files were
    uploaded under Main; from then on the task's transcript is what names
    them, and bytes must live with the conversation that names them - Main's
    orphan sweep and deletion would otherwise take them from under the task.
    Each copy keeps its upload id, unique by construction, so previews and
    history recall find it under the task. Fails closed: a file that is gone
    or fails validation refuses the whole adoption and removes the copies made
    so far, and the caller keeps the prompt it was given.
    """
    source_id, target_id = int(source_id), int(target_id)
    upload_ids = _attachment_upload_ids(source_id, text)
    if not upload_ids:
        return text
    root = Path(config.DATA_DIR).resolve() / "uploads"
    target_root = root / str(target_id)
    copied = []
    try:
        for upload_id in sorted(upload_ids):
            source = _staged_file(source_id, upload_id)
            _private_directory(root)
            _private_directory(target_root)
            destination = target_root / upload_id
            destination.mkdir(mode=0o700)
            copied.append(destination)
            target = destination / source.name
            with source.open("rb") as reader, target.open("xb") as writer:
                os.chmod(str(target), 0o600)
                operations.copyfileobj(reader, writer, STREAM_CHUNK_BYTES)
                writer.flush()
                os.fsync(writer.fileno())
    except (AttachmentError, operations.Cancelled):
        for directory in copied:
            shutil.rmtree(str(directory), ignore_errors=True)
        raise
    except OSError as exc:
        for directory in copied:
            shutil.rmtree(str(directory), ignore_errors=True)
        log.warning("session %s could not adopt session %s uploads: %s",
                    target_id, source_id, exc)
        raise AttachmentError("An attached file could not be copied into the task")
    log.info("session %s adopted %d upload(s) from session %s",
             target_id, len(copied), source_id)
    return rewrite_attachment_paths(source_id, target_id, text)


def remove_session_storage(session_id: int) -> None:
    """Drop a session's entire private upload storage.

    Only for a session that no longer exists: one just deleted, or one whose
    creation failed after files had been copied in for it.
    """
    # Keep the path free of a trailing slash so rmtree refuses a symlink
    # instead of traversing its target.
    shutil.rmtree(Path(_upload_root(session_id)), ignore_errors=True)


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
    _state_changed()
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
    _state_changed()
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
        _state_changed()


async def h_session_upload_delete(request: web.Request):
    """Discard an uploaded file that the composer has not sent."""
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    upload_id = request.match_info.get("upload_id", "")
    if not UPLOAD_ID.fullmatch(upload_id):
        return web.json_response({"error": "invalid upload id"}, status=400)
    # A browser may request deletion just before its draft-update frame reaches
    # this coroutine, and another browser may concurrently keep or re-add the
    # same chip. Never remove bytes while any shared/in-flight text still owns
    # them; an unambiguous later cleanup or session deletion owns the bytes.
    from puppy import runner
    try:
        hub = runner.hub(session_id)
        retained = [db.get_session_draft(session_id)["text"]]
        retained.extend(item for item in hub.queue + hub.held if isinstance(item, str))
        if hub._active_prompt_text:
            retained.append(hub._active_prompt_text)
        if upload_is_referenced(session_id, upload_id, retained):
            return web.json_response({"ok": True, "removed": False, "referenced": True})
    except Exception as exc:
        log.warning("session %s upload %s reference check failed: %s",
                    session_id, upload_id, exc)
        return web.json_response({"error": "could not verify upload references"}, status=409)
    try:
        target = _validated_upload_file(session_id, upload_id)
    except FileNotFoundError:
        return web.json_response({"ok": True, "removed": False})
    except OSError:
        return web.json_response({"error": "upload storage failed safety validation"},
                                 status=409)
    try:
        target.unlink()
        target.parent.rmdir()
    except OSError as exc:
        log.warning("session %s upload %s could not be discarded: %s",
                    session_id, upload_id, exc)
        return web.json_response({"error": "could not discard uploaded file"}, status=409)
    return web.json_response({"ok": True, "removed": True})


async def h_session_upload_preview(request: web.Request):
    """Serve one uploaded image back so a preview survives a page reload.

    Deliberately narrow rather than a general file route: only the raster types
    the composer previews, always with an explicit content type and sniffing
    off, and never a caller-supplied filename - the upload directory holds
    exactly one file, so there is no path for a traversal to take.
    """
    session_id = int(request.match_info["sid"])
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    upload_id = request.match_info.get("upload_id", "")
    if not UPLOAD_ID.fullmatch(upload_id):
        return web.json_response({"error": "invalid upload id"}, status=400)
    try:
        target = _validated_upload_file(session_id, upload_id)
    except FileNotFoundError:
        return web.json_response({"error": "upload not found"}, status=404)
    except OSError as exc:
        log.warning("session %s upload %s failed preview validation: %s",
                    session_id, upload_id, exc)
        return web.json_response({"error": "upload storage failed safety validation"},
                                 status=409)
    content_type = PREVIEW_CONTENT_TYPES.get(target.suffix.lower())
    if content_type is None:
        return web.json_response({"error": "this upload has no image preview"}, status=415)
    return web.FileResponse(target, headers={
        "Content-Type": content_type,
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        # an upload id names immutable bytes, so a reload can reuse them
        "Cache-Control": "private, max-age=86400, immutable",
    })


def register(app: web.Application) -> None:
    app.router.add_get("/api/uploads/settings", h_settings_get)
    app.router.add_patch("/api/uploads/settings", h_settings_patch)
    app.router.add_post("/api/sessions/{sid:\\d+}/upload", h_session_upload)
    app.router.add_get(
        "/api/sessions/{sid:\\d+}/upload/{upload_id:[0-9]{13}-[0-9a-f]{10}}",
        h_session_upload_preview)
    app.router.add_delete(
        "/api/sessions/{sid:\\d+}/upload/{upload_id:[0-9]{13}-[0-9a-f]{10}}",
        h_session_upload_delete)
