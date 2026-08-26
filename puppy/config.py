"""Paths and config.json handling for Puppy's durable private state."""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import secrets
import socket
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("PUPPY_DATA") or os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "puppy.db")
LOG_PATH = os.path.join(DATA_DIR, "puppy.log")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

log = logging.getLogger("puppy.config")

DEFAULT_USAGE_REFRESH_MINUTES = 15
MAX_USAGE_REFRESH_MINUTES = 24 * 60
DEFAULT_UPLOAD_LIMIT_MB = 8
MAX_UPLOAD_LIMIT_MB = 1024

DEFAULTS = {
    "instance_name": socket.gethostname() or "puppy",
    "web": {"host": "0.0.0.0", "port": 10888},
    "auth": {"api_token": ""},  # generated on first run; used by remote puppy instances
    "backend": {
        "host": "127.0.0.1",
        "port": 10888,
        "advertise_url": "",
        "terminal_enabled": True,
        "remote_upgrade_enabled": False,
        "tls_mode": "disabled",
        "tls_cert": "",
        "tls_key": "",
    },
    "engines": {"usage_refresh_minutes": DEFAULT_USAGE_REFRESH_MINUTES},
    "uploads": {"max_file_size_mb": DEFAULT_UPLOAD_LIMIT_MB},
    "terminal": {"command": "/bin/bash -l"},
    # node-owned managed headless browser; enabling requires the availability
    # probe (binary + version) to pass at toggle time
    "browser": {"enabled": False},
    "sessions": {"default_cwd": "/etc/scripts", "turn_timeout": 7200,
                 "shutdown_grace": 60},
    # prompt-completion command: run `command` on backend id `backend` (0 =
    # this instance) when a session finishes its work; `enabled` is the bell
    "notify": {"enabled": False, "backend": 0, "command": ""},
}

_lock = threading.Lock()
_config = None


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass


def load() -> dict:
    global _config
    with _lock:
        if _config is not None:
            return _config
        ensure_dirs()
        raw = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                log.error("config.json unreadable (%s), using defaults", e)
        cfg = _merge(DEFAULTS, raw if isinstance(raw, dict) else {})
        if not cfg["auth"].get("api_token"):
            cfg["auth"]["api_token"] = secrets.token_urlsafe(32)
        _config = cfg
        _save_locked()
        return _config


def _save_locked() -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_config, f, indent=2, sort_keys=True)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def save() -> None:
    with _lock:
        _save_locked()


def get(path: str, default=None):
    cfg = load()
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_value(path: str, value) -> None:
    cfg = load()
    with _lock:
        cur = cfg
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        _save_locked()


def export_data() -> dict:
    """Return an isolated copy of every persisted configuration value."""
    load()
    with _lock:
        return copy.deepcopy(_config)


def _validate_shape(reference, value, path: str = "config") -> None:
    """Reject type-confused backup data while permitting future unknown keys."""
    if isinstance(reference, dict):
        if not isinstance(value, dict):
            raise ValueError("{} must be an object".format(path))
        for key, child in value.items():
            if key in reference:
                _validate_shape(reference[key], child, "{}.{}".format(path, key))
        return
    if isinstance(reference, bool):
        valid = isinstance(value, bool)
    elif isinstance(reference, int):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, type(reference))
    if not valid:
        raise ValueError("{} has the wrong type".format(path))


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        (isinstance(value, int) or math.isfinite(value))


def normalize_usage_refresh_minutes(value) -> int:
    """Validate the persisted/API interval. Zero explicitly disables refresh."""
    if not _finite_number(value) or value != int(value):
        raise ValueError("usage refresh interval must be a whole number of minutes")
    minutes = int(value)
    if minutes != 0 and not 1 <= minutes <= MAX_USAGE_REFRESH_MINUTES:
        raise ValueError("usage refresh interval must be 0 or between 1 and {} minutes".format(
            MAX_USAGE_REFRESH_MINUTES))
    return minutes


def normalize_upload_limit_mb(value) -> int:
    """Validate the per-file MiB limit. Zero deliberately disables uploads."""
    if not _finite_number(value) or value != int(value):
        raise ValueError("maximum upload size must be a whole number of MiB")
    megabytes = int(value)
    if not 0 <= megabytes <= MAX_UPLOAD_LIMIT_MB:
        raise ValueError("maximum upload size must be between 0 and {} MiB".format(
            MAX_UPLOAD_LIMIT_MB))
    return megabytes


def normalize_import(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("config must be an object")
    _validate_shape(DEFAULTS, data)
    merged = _merge(DEFAULTS, data)
    for section in ("web", "backend"):
        port = merged.get(section, {}).get("port")
        if not _finite_number(port) or not 1 <= port <= 65535 or port != int(port):
            raise ValueError("config.{}.port must be between 1 and 65535".format(section))
        merged[section]["port"] = int(port)
    for key, allow_zero in (("turn_timeout", False), ("shutdown_grace", True)):
        value = merged.get("sessions", {}).get(key)
        if not _finite_number(value) or abs(value) > 1e308 or \
                (value < 0 if allow_zero else value <= 0):
            raise ValueError("config.sessions.{} must be {}".format(
                key, "non-negative" if allow_zero else "positive"))
    merged["engines"]["usage_refresh_minutes"] = normalize_usage_refresh_minutes(
        merged.get("engines", {}).get("usage_refresh_minutes"))
    merged["uploads"]["max_file_size_mb"] = normalize_upload_limit_mb(
        merged.get("uploads", {}).get("max_file_size_mb"))
    token = merged.get("auth", {}).get("api_token")
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError("config.auth.api_token is missing")
    if not merged.get("web", {}).get("host") or not merged.get("backend", {}).get("host"):
        raise ValueError("configured bind hosts must not be empty")
    if merged.get("backend", {}).get("tls_mode") not in ("disabled", "auto", "files"):
        raise ValueError("config.backend.tls_mode is invalid")
    return merged


def replace_all(data: dict) -> None:
    """Atomically replace persisted configuration and its in-process cache."""
    global _config
    normalized = normalize_import(data)
    with _lock:
        previous = _config
        _config = normalized
        try:
            _save_locked()
        except Exception:
            _config = previous
            raise
