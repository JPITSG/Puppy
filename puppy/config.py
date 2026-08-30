"""Paths and config.json handling for Puppy's durable private state."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import secrets
import socket
import threading

from puppy.user_paths import service_home

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("PUPPY_DATA") or os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "puppy.db")
LOG_PATH = os.path.join(DATA_DIR, "puppy.log")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DEFAULT_USAGE_REFRESH_MINUTES = 15
MAX_USAGE_REFRESH_MINUTES = 24 * 60
DEFAULT_UPLOAD_LIMIT_MB = 8
MAX_UPLOAD_LIMIT_MB = 1024
MAX_SYSTEM_PROMPT_CHARS = 32768
MAX_MODEL_ID_CHARS = 256

# This is model-visible only for turns where the node-owned managed browser is
# enabled and available.  Keep the default beside the persisted setting rather
# than in browser_agent.py so Settings, backup validation, and both runtimes all
# agree on what "Reset to default" means.
DEFAULT_BROWSER_SYSTEM_PROMPT = (
    "When the Puppy browser tools are available, use the shared, user-visible "
    "Puppy browser as the default for interactive web navigation, authenticated "
    "flows, screenshots, page inspection, form interaction, and user-visible UI "
    "verification. The user sees and can interact with the same browser; Puppy "
    "owns its lifecycle and preserves this session's browser across turns. Do not "
    "launch or install Chrome, Chromium, Playwright, Selenium, or another "
    "standalone browser when the Puppy browser can complete the task equivalently. "
    "Standalone browser automation remains appropriate when the user explicitly "
    "requests it, when running a repository's own browser test suite, for bulk or "
    "multi-context automation, when a required capability is not offered by these "
    "tools, or after a managed-browser attempt fails and retrying would not help. "
    "If you fall back, briefly state the concrete reason. Command-line HTTP "
    "clients remain appropriate for API-only and other non-rendered checks."
)

# This is model-visible only when the execution node is working in its private
# mirror of a project owned by another node. Keep it generic rather than
# embedding the authoritative absolute path: the latter is already rewritten
# at the execution boundary, and project-relative paths are portable in chat.
DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT = (
    "This session uses a remote workspace. The coding engine runs on this node "
    "against a Puppy-managed mirror, while the authoritative project is stored "
    "on another node. Work normally in the current working directory; Puppy "
    "synchronizes it between turns, so do not access or synchronize the storage "
    "node yourself. Prefer project-relative paths when referring to files "
    "because the mirror's absolute path is an implementation detail."
)

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
    # auto_upgrade schedules the vendor-delegated engine CLI updater this node
    # already runs by hand: off, immediately, or in the window after a local time
    "engines": {
        "usage_refresh_minutes": DEFAULT_USAGE_REFRESH_MINUTES,
        "auto_upgrade": {"enabled": False, "mode": "now", "at": "03:30"},
    },
    "uploads": {"max_file_size_mb": DEFAULT_UPLOAD_LIMIT_MB},
    "terminal": {"command": "/bin/bash -l"},
    # node-owned managed headless browser; enabling requires the availability
    # probe (binary + version) to pass at toggle time. color_scheme is the
    # prefers-color-scheme its pages render with, synced from the WebUI theme.
    "browser": {"enabled": False, "color_scheme": "dark"},
    # The custom text is added to every engine turn on this node. Conditional
    # fields are independently editable instructions added only while a turn
    # uses a cross-node workspace or this node actually offers browser tools.
    "system_prompt": {
        "custom": "",
        "remote_workspace": DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT,
        "browser": DEFAULT_BROWSER_SYSTEM_PROMPT,
    },
    "sessions": {"default_cwd": service_home(), "turn_timeout": 7200,
                 "shutdown_grace": 60},
    # prompt-completion command: run `command` on backend id `backend` (0 =
    # this instance) when a session finishes its work; `enabled` is the bell
    "notify": {"enabled": False, "backend": 0, "command": ""},
}

_lock = threading.Lock()
_config = None


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
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                raise RuntimeError("config.json is unreadable: {}".format(e)) from e
            cfg = normalize_import(raw)
        else:
            cfg = copy.deepcopy(DEFAULTS)
            cfg["auth"]["api_token"] = secrets.token_urlsafe(32)
            cfg = normalize_import(cfg)
        _config = cfg
        if not os.path.exists(CONFIG_PATH):
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
    """Require the complete current config shape and reject unknown fields."""
    if isinstance(reference, dict):
        if not isinstance(value, dict):
            raise ValueError("{} must be an object".format(path))
        missing = sorted(set(reference) - set(value))
        unknown = sorted(set(value) - set(reference))
        if missing:
            raise ValueError("{} is missing {}".format(path, ", ".join(missing)))
        if unknown:
            raise ValueError("{} contains unknown fields: {}".format(
                path, ", ".join(unknown)))
        for key, child in reference.items():
            _validate_shape(child, value[key], "{}.{}".format(path, key))
        return
    if isinstance(reference, bool):
        valid = isinstance(value, bool)
    elif isinstance(reference, int):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, type(reference))
    if not valid:
        raise ValueError("{} has the wrong type".format(path))


ENGINE_AUTO_UPGRADE_MODES = ("now", "at")
_ENGINE_AT_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def normalize_engine_auto_upgrade(value) -> dict:
    """Validate the automatic engine-update schedule, whatever its source.

    Shared by the API and by backup import so a hand-edited archive cannot
    install a schedule the scheduler would then have to second-guess."""
    if not isinstance(value, dict):
        raise ValueError("config.engines.auto_upgrade must be an object")
    if set(value) != {"enabled", "mode", "at"}:
        raise ValueError(
            "config.engines.auto_upgrade must contain enabled, mode, and at")
    enabled = value["enabled"]
    if type(enabled) is not bool:
        raise ValueError("config.engines.auto_upgrade.enabled must be true or false")
    mode = value["mode"]
    if not isinstance(mode, str):
        raise ValueError("config.engines.auto_upgrade.mode must be text")
    if mode not in ENGINE_AUTO_UPGRADE_MODES:
        raise ValueError("config.engines.auto_upgrade.mode must be 'now' or 'at'")
    at = value["at"]
    if not isinstance(at, str):
        raise ValueError("config.engines.auto_upgrade.at must be text")
    if not _ENGINE_AT_RE.match(at):
        raise ValueError("config.engines.auto_upgrade.at must be HH:MM in 24-hour form")
    return {"enabled": enabled, "mode": mode, "at": at}


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


def normalize_system_prompt(value, path: str) -> str:
    """Validate model-visible text from the API, config, or a backup archive."""
    if not isinstance(value, str):
        raise ValueError("{} must be text".format(path))
    if "\x00" in value:
        raise ValueError("{} cannot contain a null character".format(path))
    if len(value) > MAX_SYSTEM_PROMPT_CHARS:
        raise ValueError("{} cannot exceed {} characters".format(
            path, MAX_SYSTEM_PROMPT_CHARS))
    return value.replace("\r\n", "\n").replace("\r", "\n")


def set_system_prompts(custom: str, remote_workspace: str, browser: str) -> None:
    """Validate and persist the node's prompt fields in one atomic write."""
    custom = normalize_system_prompt(custom, "custom system prompt")
    remote_workspace = normalize_system_prompt(
        remote_workspace, "remote workspace system prompt")
    browser = normalize_system_prompt(browser, "browser system prompt")
    cfg = load()
    with _lock:
        previous = cfg.get("system_prompt")
        cfg["system_prompt"] = {
            "custom": custom,
            "remote_workspace": remote_workspace,
            "browser": browser,
        }
        try:
            _save_locked()
        except Exception:
            cfg["system_prompt"] = previous
            raise


def normalize_import(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("config must be an object")
    _validate_shape(DEFAULTS, data)
    merged = copy.deepcopy(data)
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
    merged["engines"]["auto_upgrade"] = normalize_engine_auto_upgrade(
        merged.get("engines", {}).get("auto_upgrade"))
    merged["uploads"]["max_file_size_mb"] = normalize_upload_limit_mb(
        merged.get("uploads", {}).get("max_file_size_mb"))
    merged["system_prompt"]["custom"] = normalize_system_prompt(
        merged.get("system_prompt", {}).get("custom"),
        "config.system_prompt.custom")
    merged["system_prompt"]["remote_workspace"] = normalize_system_prompt(
        merged.get("system_prompt", {}).get("remote_workspace"),
        "config.system_prompt.remote_workspace")
    merged["system_prompt"]["browser"] = normalize_system_prompt(
        merged.get("system_prompt", {}).get("browser"),
        "config.system_prompt.browser")
    token = merged.get("auth", {}).get("api_token")
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError("config.auth.api_token is missing")
    if not merged.get("web", {}).get("host") or not merged.get("backend", {}).get("host"):
        raise ValueError("configured bind hosts must not be empty")
    if merged.get("backend", {}).get("tls_mode") not in ("disabled", "auto", "files"):
        raise ValueError("config.backend.tls_mode is invalid")
    if merged.get("browser", {}).get("color_scheme") not in ("dark", "light"):
        raise ValueError("config.browser.color_scheme is invalid")
    if not _same_shape_and_values(data, merged):
        raise ValueError(
            "config values are not in the current canonical form; update them manually")
    return merged


def _same_shape_and_values(left, right) -> bool:
    """Compare JSON-compatible values without treating integers and floats alike."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _same_shape_and_values(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_shape_and_values(a, b) for a, b in zip(left, right))
    return left == right


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
