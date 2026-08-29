"""One-use browser handoff for verified WebUI listener restarts.

The bind verifier proves that a browser can reach a proposed endpoint before
it is saved. This module carries that proof through a deployment-owned restart:
a short-lived capability survives in Puppy's private data directory, the old
process calls a verified restart adapter, and only the newly started configured
listener may claim it.

The capability is deliberately transient and excluded from state snapshots.
It contains a hash of the browser token, not the token itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import subprocess
import threading
import time
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from aiohttp import web

from puppy import config


HANDOFF_TTL = 40 * 60
HANDOFF_PREFIX = "/api/settings/bind/handoff/"
MAX_RECORD_BYTES = 3 * 1024 * 1024
RESTART_HOOK_ENV = "PUPPY_RESTART_HOOK"
RESTART_PROBE_TIMEOUT = 4.0

_lock = threading.Lock()


class ListenerHandoffError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _authority(host: str, port: int) -> str:
    display = "[{}]".format(host) if ":" in host else host
    return "{}:{}".format(display, port)


def _request_authority(value: str) -> Optional[Tuple[str, int]]:
    try:
        parsed = urlsplit("http://" + str(value or ""))
        if not parsed.hostname or parsed.username is not None or \
                parsed.password is not None or parsed.path not in ("", "/") or \
                parsed.query or parsed.fragment:
            return None
        return parsed.hostname.lower().rstrip("."), parsed.port or 80
    except ValueError:
        return None


def _runtime_dir() -> Path:
    root = Path(config.DATA_DIR).resolve() / "runtime"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != int(uid_getter()):
        raise ListenerHandoffError(
            "private listener handoff storage is not service-owned", status=500)
    root.chmod(0o700)
    return root


def _record_path() -> Path:
    return _runtime_dir() / "listener-handoff.json"


def _unlink(path: Optional[Path] = None) -> None:
    try:
        (path or _record_path()).unlink()
    except FileNotFoundError:
        pass


def _load_locked() -> Optional[dict]:
    path = _record_path()
    try:
        info = path.lstat()
        uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
                info.st_uid != int(uid_getter()) or info.st_size > MAX_RECORD_BYTES:
            _unlink(path)
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(path), flags)
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except Exception:
            _unlink(path)
            return None
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        port = record.get("port")
        created_at = float(record.get("created_at"))
        expires_at = float(record.get("expires_at"))
        token_hash = record.get("token_hash")
        browser_state = record.get("browser_state")
        valid = isinstance(record, dict) and record.get("format") == 1 and \
            isinstance(token_hash, str) and len(token_hash) == 64 and \
            all(character in "0123456789abcdef" for character in token_hash) and \
            isinstance(record.get("user"), str) and 0 < len(record["user"]) <= 256 and \
            isinstance(record.get("host"), str) and 0 < len(record["host"]) <= 64 and \
            isinstance(record.get("probe_host"), str) and \
            0 < len(record["probe_host"]) <= 255 and \
            isinstance(record.get("origin"), str) and 0 < len(record["origin"]) <= 512 and \
            isinstance(record.get("source_runtime"), str) and \
            0 < len(record["source_runtime"]) <= 128 and \
            isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535 and \
            record.get("status") in ("prepared", "queued") and \
            isinstance(browser_state, dict) and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in browser_state.items()) and \
            math.isfinite(created_at) and math.isfinite(expires_at) and \
            created_at <= expires_at <= created_at + HANDOFF_TTL + 1
    except (AttributeError, TypeError, ValueError):
        valid = False
        expires_at = 0
    if not valid or time.time() >= expires_at:
        _unlink(path)
        return None
    return record


def _save_locked(record: dict) -> None:
    root = _runtime_dir()
    path = root / "listener-handoff.json"
    temporary = root / (".listener-handoff-{}.tmp".format(secrets.token_hex(8)))
    payload = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise ListenerHandoffError("listener handoff state is too large", status=413)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _matching_locked(token: str) -> Optional[dict]:
    record = _load_locked()
    supplied_token = str(token or "")
    if len(supplied_token) > 128:
        return None
    supplied = hashlib.sha256(supplied_token.encode("utf-8")).hexdigest()
    expected = str(record.get("token_hash", "")) if record else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        return None
    return record


def _public_payload(token: str, record: dict) -> dict:
    base = "http://" + _authority(record["probe_host"], int(record["port"]))
    route = HANDOFF_PREFIX + token
    return {
        "token": token,
        "host": record["host"],
        "port": int(record["port"]),
        "next_url": base + "/",
        "ready_url": base + route + "/ready",
        "claim_url": base + route,
        "expires_in": max(0, int(float(record["expires_at"]) - time.time())),
    }


def create(app: web.Application, user: str, host: str, port: int,
           probe_host: str, origin: str) -> dict:
    """Replace any older pending handoff and return its browser capability."""
    if not user or user == "@token":
        raise ListenerHandoffError(
            "automatic listener activation requires a browser login", status=403)
    token = secrets.token_urlsafe(32)
    record = {
        "format": 1,
        "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "user": user,
        "host": str(host),
        "port": int(port),
        "probe_host": str(probe_host),
        "origin": str(origin).rstrip("/"),
        "source_runtime": str(app.get("puppy_runtime_id") or ""),
        "created_at": time.time(),
        "expires_at": time.time() + HANDOFF_TTL,
        "status": "prepared",
        "browser_state": {},
    }
    with _lock:
        _save_locked(record)
    return _public_payload(token, record)


def discard() -> None:
    """Invalidate any prepared/queued handoff (for superseding state changes)."""
    with _lock:
        try:
            _unlink()
        except (ListenerHandoffError, OSError):
            pass


def cleanup() -> None:
    """Remove an expired or malformed transient record at application startup."""
    with _lock:
        try:
            _load_locked()
        except (ListenerHandoffError, OSError):
            pass


def _restart_hook() -> Path:
    """Return a deployment-owned executable safe for this service to invoke."""
    raw = os.environ.get(RESTART_HOOK_ENV, "")
    if not raw:
        raise ListenerHandoffError(
            "automatic restart requires a deployment restart hook", status=503)
    if "\x00" in raw:
        raise ListenerHandoffError("the deployment restart hook is invalid", status=503)
    path = Path(raw)
    if not path.is_absolute():
        raise ListenerHandoffError(
            "the deployment restart hook must be an absolute path", status=503)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ListenerHandoffError(
            "the deployment restart hook is unavailable", status=503) from exc
    uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    allowed_owners = {0, int(uid_getter())}
    unsafe_write_bits = stat.S_IWGRP | stat.S_IWOTH
    execute_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid not in allowed_owners or \
            (info.st_mode & unsafe_write_bits) or not (info.st_mode & execute_bits):
        raise ListenerHandoffError(
            "the deployment restart hook has unsafe ownership or permissions", status=503)
    return path


def _restart_environment() -> dict:
    environment = dict(os.environ)
    environment["PUPPY_DATA"] = os.path.abspath(config.DATA_DIR)
    return environment


def queue_restart() -> int:
    """Verify and launch the deployment's restart adapter without a shell.

    The adapter contract is two fixed commands: ``probe PID`` must prove it can
    target this exact runtime, and ``restart PID`` must safely queue its restart.
    Deployment-specific process-manager knowledge stays outside Puppy.
    """
    hook = _restart_hook()
    pid = str(os.getpid())
    environment = _restart_environment()
    try:
        probe = subprocess.run(
            [str(hook), "probe", pid], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd="/", env=environment, timeout=RESTART_PROBE_TIMEOUT,
            check=False)
    except subprocess.TimeoutExpired as exc:
        raise ListenerHandoffError(
            "the deployment restart hook probe timed out", status=503) from exc
    except OSError as exc:
        raise ListenerHandoffError(
            "could not run the deployment restart hook probe", status=503) from exc
    if probe.returncode != 0:
        raise ListenerHandoffError(
            "the deployment restart hook could not verify this Puppy process",
            status=503)
    # Re-check the file after the external probe before starting its mutating
    # command. This catches accidental removal or permission changes cleanly.
    hook = _restart_hook()
    try:
        process = subprocess.Popen(
            [str(hook), "restart", pid], cwd="/", env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    except OSError as exc:
        raise ListenerHandoffError(
            "could not queue Puppy's graceful restart", status=503) from exc
    return int(process.pid)


def activate(app: web.Application, user: str, token: str, browser_state: dict,
             restart: Optional[Callable[[], object]] = None) -> dict:
    """Arm a prepared ticket and queue exactly one graceful service restart."""
    with _lock:
        record = _matching_locked(token)
        if record is None or record.get("user") != user:
            raise ListenerHandoffError(
                "listener activation expired; verify the endpoint again", status=409)
        if (str(config.get("web.host")) != record.get("host") or
                int(config.get("web.port", 0)) != int(record.get("port", 0))):
            _unlink()
            raise ListenerHandoffError(
                "the configured listener changed before activation", status=409)
        runtime = app.get("puppy_runtime_web") or {}
        if (str(runtime.get("host")) == record.get("host") and
                int(runtime.get("port", 0)) == int(record.get("port", 0))):
            _unlink()
            raise ListenerHandoffError("the configured listener is already active", status=409)
        if record.get("status") == "queued":
            return {"ok": True, "queued": True, **_public_payload(token, record)}
        if record.get("status") != "prepared":
            raise ListenerHandoffError("listener activation is already in progress", status=409)

        record["browser_state"] = browser_state
        record["status"] = "queued"
        record["queued_at"] = time.time()
        _save_locked(record)
        try:
            (restart or queue_restart)()
        except Exception:
            record["status"] = "prepared"
            record.pop("queued_at", None)
            _save_locked(record)
            raise
        return {"ok": True, "queued": True, **_public_payload(token, record)}


def cors_headers(record: dict) -> dict:
    return {
        "Access-Control-Allow-Origin": record["origin"],
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Private-Network": "true",
        "Cache-Control": "no-store",
        "Vary": "Origin",
        "X-Content-Type-Options": "nosniff",
    }


def lookup(token: str) -> Optional[dict]:
    with _lock:
        return _matching_locked(token)


def queued_by(app: web.Application) -> bool:
    """Whether this live process already launched the pending restart."""
    with _lock:
        record = _load_locked()
        return bool(record and record.get("status") == "queued" and
                    record.get("source_runtime") == str(
                        app.get("puppy_runtime_id") or ""))


def target_matches(record: dict, host_header: str) -> bool:
    return _request_authority(host_header) == (
        str(record.get("probe_host", "")).lower().rstrip("."),
        int(record.get("port", 0)))


def is_ready(app: web.Application, record: dict) -> bool:
    runtime = app.get("puppy_runtime_web") or {}
    return record.get("status") == "queued" and \
        str(app.get("puppy_runtime_id") or "") != record.get("source_runtime") and \
        str(runtime.get("host")) == record.get("host") and \
        int(runtime.get("port", 0)) == int(record.get("port", 0)) and \
        str(config.get("web.host")) == record.get("host") and \
        int(config.get("web.port", 0)) == int(record.get("port", 0))


def claim(app: web.Application, token: str, host_header: str) -> Optional[dict]:
    """Atomically consume a ready ticket at the newly active endpoint."""
    with _lock:
        record = _matching_locked(token)
        if record is None or not target_matches(record, host_header) or \
                not is_ready(app, record):
            return None
        _unlink()
        return record
