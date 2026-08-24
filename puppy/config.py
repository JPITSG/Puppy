"""Paths, config.json handling. All user config / private data lives in data/."""
from __future__ import annotations

import copy
import json
import logging
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

DEFAULTS = {
    "instance_name": socket.gethostname() or "puppy",
    "web": {"host": "0.0.0.0", "port": 10888},
    "auth": {"api_token": ""},  # generated on first run; used by remote puppy instances
    "terminal": {"command": "/bin/bash -l"},
    "sessions": {"default_cwd": "/etc/scripts", "turn_timeout": 7200},
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
