"""Multi-backend support. Backend id 0 = this instance. Remote backends are other
puppy instances; the browser stays single-origin and this instance proxies both
HTTP and websocket traffic to them, authenticated with their api_token."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import WSMsgType, web

from puppy import (__version__, config, db, live_websockets, protocol, runner,
                   tls, upgrade_contract)

log = logging.getLogger("puppy.backends")

_client = None
_upgrades_in_progress = set()
_fingerprints = {}
_proxy_websockets = set()
_auto_upgrade_task = None
_auto_upgrade_wake = None
_auto_upgrade_retry_after = {}
_auto_upgrade_checked_at = {}
_auto_upgrade_last_errors = {}
_health_task = None
_health_wake = None
_health = {}
_health_retry_after = {}
_health_retry_delay = {}
_active_urls = {}
_url_cursors = {}
_state_stream_tasks = {}  # backend id -> one controller-owned upstream socket

FAILOVER_CONNECT_TIMEOUT = 2.5
STATE_STREAM_HANDSHAKE_TIMEOUT = 5.0
PROXY_TOTAL_TIMEOUT = 60.0
PROXY_UPLOAD_TIMEOUT = 15 * 60.0
MAX_BACKEND_URLS = 8
UPLOAD_PROXY_PATH = re.compile(r"^sessions/\d+/upload$")
AUTO_UPGRADE_INTERVAL = 8.0
AUTO_UPGRADE_FAILURE_RETRY = 30.0
AUTO_UPGRADE_CURRENT_RECHECK = 5 * 60.0
HEALTH_SCAN_INTERVAL = 2.0
HEALTH_PROBE_TIMEOUT = 3.0
HEALTH_ONLINE_RECHECK = 30.0
HEALTH_OFFLINE_RETRY_MIN = 2.0
HEALTH_OFFLINE_RETRY_MAX = 8.0

# Remote settings are node-owned, but an offline node cannot answer the Settings
# page.  Keep the last authenticated values on the controller so every browser
# (and a fresh browser after a controller restart) can render them read-only.
# This is a rebuildable observation cache in the existing meta namespace, not a
# second source of truth.  The exact version is rejected rather than migrated.
LAST_KNOWN_VERSION = 1
LAST_KNOWN_PREFIX = "backend_last_known."
LAST_KNOWN_LIMITS = {
    "engines": (list, 2 * 1024 * 1024),
    "usage_refresh": (dict, 64 * 1024),
    "auto_upgrade": (dict, 256 * 1024),
    "uploads": (dict, 8 * 1024),
    "browser": (dict, 8 * 1024),
    "system_prompt": (dict, 1024 * 1024),
}
# Session observations have their own exact shape so adding them does not widen
# the established settings-cache format.  list_backends combines both only in
# its transient response for the browser.
LAST_SESSIONS_VERSION = 1
LAST_SESSIONS_PREFIX = "backend_last_sessions."
LAST_SESSIONS_MAX_BYTES = 4 * 1024 * 1024

HOP_HEADERS = {"host", "connection", "upgrade", "sec-websocket-key", "sec-websocket-version",
               "sec-websocket-extensions", "sec-websocket-protocol", "cookie", "x-puppy-token",
               "content-length", "transfer-encoding", "accept-encoding"}


async def _reject_redirect(_session, _context, _params) -> None:
    raise aiohttp.ClientConnectionError("backend redirects are not allowed")


def client() -> aiohttp.ClientSession:
    global _client
    if _client is None or _client.closed:
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(_reject_redirect)
        _client = aiohttp.ClientSession(trace_configs=[trace])
    return _client


def _ssl_pin(fingerprint: str):
    if not fingerprint:
        return True  # aiohttp default: normal CA verification for HTTPS
    pinned = _fingerprints.get(fingerprint)
    if pinned is None:
        pinned = aiohttp.Fingerprint(bytes.fromhex(fingerprint))
        _fingerprints[fingerprint] = pinned
    return pinned


def _connection_error(exc: Exception) -> str:
    if isinstance(exc, aiohttp.ServerFingerprintMismatch):
        return "TLS certificate fingerprint mismatch; verify and re-pair this backend"
    if isinstance(exc, (aiohttp.ClientConnectorCertificateError,
                        aiohttp.ClientConnectorSSLError)):
        return "TLS certificate verification failed: {}".format(exc)
    return str(exc)


def _failed_before_request(exc: Exception) -> bool:
    """True only when replay cannot duplicate a state-changing request."""
    return isinstance(exc, (
        aiohttp.ClientConnectorError,
        aiohttp.ClientConnectorCertificateError,
        aiohttp.ClientConnectorSSLError,
        aiohttp.ServerFingerprintMismatch,
        aiohttp.ConnectionTimeoutError,
    ))


def _base_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("backend URL is invalid") from exc
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("backend URL must start with http:// or https://")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("backend URL must not contain credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("backend URL must be an origin without a path, query, or fragment")
    return "{}://{}".format(parsed.scheme, parsed.netloc)


def _normalize_urls(value) -> list:
    if not isinstance(value, list):
        raise ValueError("backend URLs must be a list")
    if not value:
        raise ValueError("at least one backend URL is required")
    if len(value) > MAX_BACKEND_URLS:
        raise ValueError("at most {} backend URLs are allowed".format(MAX_BACKEND_URLS))
    urls = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("each backend URL must be text")
        url = _base_url(item)
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ValueError("at least one backend URL is required")
    return urls


def _backend_urls(backend: dict) -> list:
    value = backend.get("urls")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    try:
        return _normalize_urls(value)
    except ValueError:
        return []


def _request_urls(body: dict, backend=None) -> list:
    if "urls" in body:
        urls = _normalize_urls(body["urls"])
        if "url" in body and _base_url(body["url"]) != urls[0]:
            raise ValueError("backend url must match the first URL in urls")
        return urls
    if "url" in body:
        return [_base_url(body["url"])]
    if backend is not None:
        urls = _backend_urls(backend)
        if urls:
            return urls
    raise ValueError("at least one backend URL is required")


def _urls_json(urls: list) -> str:
    return json.dumps(urls, separators=(",", ":"))


def _ordered_backend_urls(backend: dict) -> list:
    urls = _backend_urls(backend)
    if not urls:
        return []
    bid = int(backend.get("id") or 0)
    active = _active_urls.get(bid)
    if active in urls:
        return [active] + [url for url in urls if url != active]
    start = _url_cursors.get(bid, 0) % len(urls)
    return urls[start:] + urls[:start]


def _remember_active_url(bid: int, url: str, urls=None) -> bool:
    previous = _active_urls.get(bid)
    _active_urls[bid] = url
    candidates = urls or []
    if url in candidates:
        _url_cursors[bid] = candidates.index(url)
    return previous != url


def _publish_active_url(backend: dict, url: str) -> None:
    bid = int(backend["id"])
    changed = _remember_active_url(bid, url, _backend_urls(backend))
    changed = _mark_backend_online(bid) or changed
    if changed:
        _broadcast_backends()


def _advance_url_cursor(bid: int, count: int) -> None:
    if count > 1 and _active_urls.get(bid) is None:
        _url_cursors[bid] = (_url_cursors.get(bid, 0) + 1) % count


def _validate_url_security(urls: list, tls_fingerprint: str) -> None:
    if tls_fingerprint and any(urlsplit(url).scheme != "https" for url in urls):
        raise ValueError(
            "a TLS certificate fingerprint requires every backend URL to use https://")


def _availability(bid: int) -> dict:
    """Return the controller's transient reachability verdict for one backend."""
    current = _health.get(int(bid))
    if current is None:
        return {
            "state": "checking",
            "reason": "The controller is checking this backend",
            "checked_at": None,
        }
    return {
        "state": current["state"],
        "reason": current["reason"],
        "checked_at": current["checked_at"],
    }


def backend_is_online(bid: int) -> bool:
    return _availability(bid)["state"] == "online"


def _set_health(bid: int, state: str, reason: str = "") -> bool:
    """Store a verdict and report whether its user-visible meaning changed."""
    bid = int(bid)
    reason = str(reason or "")[:500]
    previous = _health.get(bid)
    _health[bid] = {
        "state": state,
        "reason": reason,
        "checked_at": time.time(),
    }
    return previous is None or previous.get("state") != state or \
        previous.get("reason") != reason


def _mark_backend_online(bid: int) -> bool:
    bid = int(bid)
    _health_retry_after[bid] = time.monotonic() + HEALTH_ONLINE_RECHECK
    _health_retry_delay.pop(bid, None)
    return _set_health(bid, "online")


def _mark_backend_offline(bid: int, reason: str) -> bool:
    bid = int(bid)
    now = time.monotonic()
    current = _health.get(bid)
    # Several HTTP/WebSocket requests can already be in flight when a node
    # disappears. They all confirm the same outage, but must not multiply the
    # recovery backoff as though several scheduled probes had failed.
    if current is not None and current.get("state") == "offline" and \
            _health_retry_after.get(bid, 0) > now:
        return _set_health(bid, "offline", reason or "Backend unavailable")
    delay = _health_retry_delay.get(bid, HEALTH_OFFLINE_RETRY_MIN)
    _health_retry_after[bid] = now + delay
    _health_retry_delay[bid] = min(delay * 2, HEALTH_OFFLINE_RETRY_MAX)
    return _set_health(bid, "offline", reason or "Backend unavailable")


def _clear_backend_health(bid: int) -> None:
    bid = int(bid)
    _health.pop(bid, None)
    _health_retry_after.pop(bid, None)
    _health_retry_delay.pop(bid, None)
    task = _state_stream_tasks.pop(bid, None)
    if task is not None:
        task.cancel()
    runner.clear_published_state("remote:{}:".format(bid))


def _wake_health() -> None:
    if _health_wake is not None:
        _health_wake.set()


def _last_known_key(bid: int) -> str:
    return LAST_KNOWN_PREFIX + str(int(bid))


def _last_sessions_key(bid: int) -> str:
    return LAST_SESSIONS_PREFIX + str(int(bid))


def _validated_sessions(value):
    if not isinstance(value, list):
        return None
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > LAST_SESSIONS_MAX_BYTES:
            return None
        return json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        return None


def _load_last_sessions(bid: int):
    value = db.meta_get(_last_sessions_key(bid))
    if not isinstance(value, dict) or set(value) != {"version", "sessions"} or \
            value.get("version") != LAST_SESSIONS_VERSION:
        return None
    return _validated_sessions(value.get("sessions"))


def _cache_remote_sessions(bid: int, sessions) -> bool:
    normalized = _validated_sessions(sessions)
    if normalized is None:
        return False
    key = _last_sessions_key(bid)
    row = db.query_one("SELECT value FROM meta WHERE key=?", (key,))
    current = _load_last_sessions(bid)
    # A malformed or differently versioned value is rejected, never rewritten
    # as an implicit persisted-format migration.
    if row is not None and current is None:
        return False
    if current == normalized:
        return False
    db.meta_set(key, {"version": LAST_SESSIONS_VERSION, "sessions": normalized})
    return True


def _validated_last_known_value(name: str, value):
    spec = LAST_KNOWN_LIMITS.get(name)
    if spec is None or not isinstance(value, spec[0]):
        return None
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > spec[1]:
            return None
        # A detached JSON copy prevents callers from mutating cached objects.
        return json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        return None


def _load_last_known(bid: int):
    value = db.meta_get(_last_known_key(bid))
    if not isinstance(value, dict) or value.get("version") != LAST_KNOWN_VERSION:
        return None
    allowed = {"version", "node_uuid"}.union(LAST_KNOWN_LIMITS)
    if set(value) - allowed:
        return None
    result = {"version": LAST_KNOWN_VERSION}
    node_uuid = value.get("node_uuid")
    if isinstance(node_uuid, str) and len(node_uuid) == 32:
        result["node_uuid"] = node_uuid
    for name in LAST_KNOWN_LIMITS:
        if name not in value:
            continue
        normalized = _validated_last_known_value(name, value[name])
        if normalized is None:
            return None
        result[name] = normalized
    return result if len(result) > 1 else None


def _clear_last_known(bid: int) -> None:
    db.execute("DELETE FROM meta WHERE key=?", (_last_known_key(bid),))
    db.execute("DELETE FROM meta WHERE key=?", (_last_sessions_key(bid),))


def _merge_last_known(bid: int, updates: dict) -> bool:
    if not updates:
        return False
    row = db.query_one("SELECT value FROM meta WHERE key=?", (_last_known_key(bid),))
    current = _load_last_known(bid)
    # A present cache must match exactly.  Do not turn an authenticated poll
    # into an implicit persisted-format migration; an operator upgrading a
    # future cache version must replace it deliberately.
    if row is not None and current is None:
        return False
    current = current or {"version": LAST_KNOWN_VERSION}
    merged = dict(current)
    node_uuid = updates.get("node_uuid")
    if isinstance(node_uuid, str) and len(node_uuid) == 32:
        merged["node_uuid"] = node_uuid
    for name, value in updates.items():
        if name not in LAST_KNOWN_LIMITS:
            continue
        normalized = _validated_last_known_value(name, value)
        if normalized is not None:
            merged[name] = normalized
    if merged == current:
        return False
    db.meta_set(_last_known_key(bid), merged)
    return True


def _cache_remote_payload(bid: int, tail: str, payload: dict) -> bool:
    """Remember settings/status learned through one authenticated node reply."""
    if not isinstance(payload, dict):
        return False
    updates = {}
    if tail == "sessions" and isinstance(payload.get("sessions"), list):
        return _cache_remote_sessions(bid, payload["sessions"])
    elif tail in ("ping", "node"):
        for name in ("uploads", "browser"):
            if name in payload:
                updates[name] = payload[name]
        if "node_uuid" in payload:
            updates["node_uuid"] = payload["node_uuid"]
    elif tail == "engines" or tail.startswith("engines/"):
        for name in ("engines", "usage_refresh", "auto_upgrade"):
            if name in payload:
                updates[name] = payload[name]
    elif tail == "uploads/settings" and "uploads" in payload:
        updates["uploads"] = payload["uploads"]
    elif tail in ("browser/status", "browser/enabled", "browser_status") and \
            type(payload.get("enabled")) is bool:
        updates["browser"] = {"enabled": payload["enabled"]}
    elif tail == "system-prompt" and "system_prompt" in payload:
        updates["system_prompt"] = payload["system_prompt"]
    return _merge_last_known(bid, updates)


def _cacheable_remote_tail(tail: str) -> bool:
    return tail in (
        "sessions", "ping", "node", "uploads/settings", "browser/status",
        "browser/enabled", "system-prompt") or \
        tail == "engines" or tail.startswith("engines/")


def list_backends() -> list:
    rows = db.query(
        "SELECT id,name,url,urls,protocol,capabilities,remote_version,role,tls_fingerprint,"
        "auto_upgrade,created_at "
        "FROM backends ORDER BY id")
    out = []
    for row in rows:
        item = dict(row)
        try:
            caps = json.loads(item.get("capabilities") or "[]")
        except Exception:
            caps = []
        item["capabilities"] = caps if isinstance(caps, list) else []
        item["urls"] = _backend_urls(item)
        item["url"] = item["urls"][0] if item["urls"] else ""
        active = _active_urls.get(int(item["id"]))
        item["active_url"] = active if active in item["urls"] else ""
        item["auto_upgrade"] = bool(item.get("auto_upgrade"))
        item["upgrade_in_progress"] = item["id"] in _upgrades_in_progress
        item["availability"] = _availability(int(item["id"]))
        bid = int(item["id"])
        last_known = _load_last_known(bid)
        last_sessions = _load_last_sessions(bid)
        if last_sessions is not None:
            last_known = dict(last_known or {"version": LAST_KNOWN_VERSION})
            last_known["sessions"] = last_sessions
        item["last_known"] = last_known
        out.append(item)
    return out


def get_backend(bid: int):
    row = db.query_one("SELECT * FROM backends WHERE id=?", (bid,))
    if not row:
        return None
    backend = dict(row)
    backend["urls"] = _backend_urls(backend)
    return backend


def node_channel(bid: int):
    """Transport facts for one node: ordered URLs, token, TLS pin, or None.

    Node 0 is this controller itself, reached over loopback with its own API
    token, so brokered features (the workspace-link relay) drive local and
    remote nodes through one identical code path."""
    if not bid:
        host = str(config.get("web.host", "127.0.0.1") or "127.0.0.1")
        if host in ("0.0.0.0", "::", "*", ""):
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = "[{}]".format(host)
        port = int(config.get("web.port", 10888))
        return {"bid": 0, "name": str(config.get("instance_name") or "this backend"),
                "urls": ["http://{}:{}".format(host, port)],
                "token": str(config.get("auth.api_token") or ""),
                "ssl": True,
                "capabilities": list(protocol.execution_capabilities())}
    backend = get_backend(bid)
    if backend is None:
        return None
    if not backend_is_online(bid):
        return None
    return {"bid": bid, "name": str(backend.get("name") or str(bid)),
            "urls": _ordered_backend_urls(backend),
            "token": backend["token"],
            "ssl": _ssl_pin(backend.get("tls_fingerprint") or ""),
            "capabilities": _backend_capabilities(backend)}


def _backend_capabilities(backend: dict) -> list:
    value = backend.get("capabilities") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    return value if isinstance(value, list) else []


def backend_supports(bid: int, capability: str) -> bool:
    backend = get_backend(int(bid))
    return bool(backend and capability in _backend_capabilities(backend))


def _broadcast_backends() -> None:
    runner.publish_state({"type": "backends", "backends": list_backends()})


def _publish_remote_stream(bid: int, connected: bool,
                           node_runtime_id: str = "") -> None:
    runner.publish_state({
        "type": "remote_stream", "backend_id": int(bid),
        "connected": bool(connected),
        "node_runtime_id": str(node_runtime_id or ""),
    }, topic="remote:{}:stream".format(int(bid)))


def _publish_remote_state(bid: int, message: dict) -> None:
    runner.publish_state({
        "type": "remote_state", "backend_id": int(bid),
        "event": dict(message),
    }, topic="remote:{}:{}".format(int(bid), message.get("type") or "unknown"))


def _valid_stream_state(message: dict, runtime_id: str,
                        revisions: dict) -> bool:
    kind = message.get("type")
    if kind not in ("sessions", "node", "engines", "browser_status",
                    "terminal_instances"):
        return False
    if not isinstance(message.get("runtime_id"), str) or \
            message["runtime_id"] != runtime_id:
        return False
    if str(message.get("state_topic") or "") != kind:
        return False
    revision = message.get("state_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        return False
    if revision <= int(revisions.get(kind, 0)):
        return False
    valid = (
        (kind == "sessions" and isinstance(message.get("sessions"), list)) or
        (kind == "node" and message.get("ok") is True and
         isinstance(message.get("capabilities"), list)) or
        (kind == "engines" and isinstance(message.get("engines"), list) and
         isinstance(message.get("usage_refresh"), dict) and
         isinstance(message.get("auto_upgrade"), dict) and
         isinstance(message.get("timers"), dict)) or
        (kind == "browser_status" and type(message.get("enabled")) is bool and
         isinstance(message.get("instances"), list)) or
        (kind == "terminal_instances" and isinstance(message.get("instances"), list))
    )
    if valid:
        revisions[kind] = revision
    return bool(valid)


async def _backend_state_stream(bid: int) -> None:
    """Maintain one authenticated state subscription for one capable node."""
    retry = 0.8
    last_runtime = ""
    while True:
        backend = get_backend(bid)
        if backend is None or not backend_is_online(bid) or \
                protocol.NODE_STATE_STREAM_CAPABILITY not in \
                _backend_capabilities(backend):
            return
        socket = None
        selected_url = ""
        error = "backend state stream is unavailable"
        for url in _ordered_backend_urls(backend):
            target = "ws" + (url + "/api/ws/updates")[4:]
            try:
                socket = await asyncio.wait_for(client().ws_connect(
                    target,
                    headers={"X-Puppy-Token": backend["token"]},
                    heartbeat=30, max_msg_size=runner.STREAM_LIMIT,
                    ssl=_ssl_pin(backend.get("tls_fingerprint") or "")),
                    timeout=FAILOVER_CONNECT_TIMEOUT)
                selected_url = url
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = _connection_error(exc)
        if socket is None:
            _publish_remote_stream(bid, False)
            log.debug("backend %s state stream unavailable: %s",
                      backend.get("name") or bid, error)
        else:
            if _remember_active_url(bid, selected_url, _backend_urls(backend)):
                _broadcast_backends()
            runtime_id = ""
            revisions = {}
            required = {"sessions", "node", "engines", "browser_status"}
            seen = set()
            stream_ready = False
            try:
                # Capability negotiation promises this exact first frame.
                # Bound it independently from the long-lived heartbeat so a
                # mislabelled/future-incompatible peer cannot retain a dead
                # controller subscription forever.
                frame = await asyncio.wait_for(
                    socket.receive(), timeout=STATE_STREAM_HANDSHAKE_TIMEOUT)
                if frame.type != WSMsgType.TEXT:
                    raise RuntimeError("backend state stream sent no handshake")
                try:
                    ready = json.loads(frame.data)
                except Exception as exc:
                    raise RuntimeError(
                        "backend state stream sent invalid JSON") from exc
                candidate = ready.get("runtime_id", "") \
                    if isinstance(ready, dict) and \
                    isinstance(ready.get("runtime_id"), str) else ""
                if not isinstance(ready, dict) or \
                        ready.get("type") != "updates_ready" or \
                        type(ready.get("stream_version")) is not int or \
                        ready.get("stream_version") != 1 or not candidate or \
                        len(candidate) > 128:
                    raise RuntimeError("backend state stream handshake is incompatible")
                if last_runtime and candidate != last_runtime:
                    runner.clear_published_state("remote:{}:".format(bid))
                last_runtime = runtime_id = candidate
                retry = 0.8
                async for frame in socket:
                    if frame.type != WSMsgType.TEXT:
                        if frame.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break
                        continue
                    try:
                        message = json.loads(frame.data)
                    except Exception:
                        continue
                    if not isinstance(message, dict):
                        continue
                    kind = message.get("type")
                    if kind == "updates_ready":
                        raise RuntimeError(
                            "backend state stream repeated its handshake")
                    if kind == "node_stopping":
                        action = "restarting" if message.get("reason") == "restart" \
                            else "shutting down"
                        if _mark_backend_offline(
                                bid, "Backend {}".format(action)):
                            _broadcast_backends()
                        runner.broadcast_update({
                            "type": "remote_state", "backend_id": bid,
                            "event": message,
                        })
                        break
                    if not runtime_id or not _valid_stream_state(
                            message, runtime_id, revisions):
                        continue
                    changed = False
                    if kind == "sessions":
                        changed = _cache_remote_payload(bid, "sessions", message)
                    elif kind == "engines":
                        changed = _cache_remote_payload(bid, "engines", message)
                    elif kind == "node":
                        changed = _store_metadata(bid, message)
                    elif kind == "browser_status":
                        changed = _cache_remote_payload(
                            bid, "browser/status", message)
                    _publish_remote_state(bid, message)
                    seen.add(kind)
                    if kind == "node" and \
                            protocol.TERMINAL_INSTANCES_CAPABILITY in \
                            (message.get("capabilities") or []):
                        required.add("terminal_instances")
                    if not stream_ready and required.issubset(seen):
                        stream_ready = True
                        _publish_remote_stream(bid, True, runtime_id)
                    if kind == "node" and changed:
                        _broadcast_backends()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("backend %s state stream ended: %s",
                          backend.get("name") or bid, _connection_error(exc))
            finally:
                await socket.close()
            # This replaceable verdict must also supersede the cached true
            # state after an orderly node_stopping edge. Otherwise a console
            # attaching during the restart can see health=offline followed by
            # an obsolete connected snapshot and suppress its fallback path.
            _publish_remote_stream(bid, False, runtime_id)
        await asyncio.sleep(retry)
        retry = min(retry * 1.7, 15.0)


def _sync_state_streams() -> None:
    desired = {
        int(item["id"]) for item in list_backends()
        if item.get("availability", {}).get("state") == "online" and
        protocol.NODE_STATE_STREAM_CAPABILITY in (item.get("capabilities") or [])
    }
    for bid, task in list(_state_stream_tasks.items()):
        if bid not in desired:
            _state_stream_tasks.pop(bid, None)
            task.cancel()
            _publish_remote_stream(bid, False)
    for bid in sorted(desired):
        task = _state_stream_tasks.get(bid)
        if task is not None and not task.done():
            continue
        task = asyncio.create_task(
            _backend_state_stream(bid),
            name="puppy-backend-state-{}".format(bid))
        _state_stream_tasks[bid] = task

        def finished(done, selected=bid):
            if _state_stream_tasks.get(selected) is done:
                _state_stream_tasks.pop(selected, None)

        task.add_done_callback(finished)


def _wake_auto_upgrade() -> None:
    if _auto_upgrade_wake is not None:
        _auto_upgrade_wake.set()


def reset_auto_upgrade_schedule() -> None:
    """Forget transient controller state after a database restore.

    The restored database can reuse a backend id for a different connection.
    Tear down every pre-restore upstream socket before health discovery reads
    the replacement rows; no bytes learned under an old token/endpoint may be
    published into the restored controller epoch.
    """
    old_streams = list(_state_stream_tasks.items())
    _state_stream_tasks.clear()
    for _bid, task in old_streams:
        task.cancel()
    # Disconnected nodes retain their last replaceable snapshots even though
    # they have no live task.  A restored database may remove or reuse any id,
    # so clear the complete controller fanout namespace, not just active ids.
    runner.clear_published_state("remote:")
    _auto_upgrade_retry_after.clear()
    _auto_upgrade_checked_at.clear()
    _auto_upgrade_last_errors.clear()
    _active_urls.clear()
    _url_cursors.clear()
    _health.clear()
    _health_retry_after.clear()
    _health_retry_delay.clear()
    _wake_health()
    _wake_auto_upgrade()


# ---- CRUD handlers ----

def _normalize_peer(data: dict) -> dict:
    try:
        api_protocol = int(data.get("protocol", protocol.LEGACY_PROTOCOL))
    except (TypeError, ValueError):
        raise ValueError("backend returned an invalid protocol")
    if api_protocol not in protocol.SUPPORTED_BACKEND_PROTOCOLS:
        supported = ", ".join(str(v) for v in protocol.SUPPORTED_BACKEND_PROTOCOLS)
        raise ValueError(f"backend protocol {api_protocol} is unsupported (supported: {supported})")
    role = str(data.get("role") or ("legacy-full" if api_protocol == 0 else "")).strip()
    if api_protocol > 0 and role not in ("backend", "full"):
        raise ValueError(f"remote role '{role or '?'}' is not a Puppy backend")
    caps = data.get("capabilities") or []
    if not isinstance(caps, list) or not all(isinstance(v, str) for v in caps):
        raise ValueError("backend returned invalid capabilities")
    return {
        **data,
        "protocol": api_protocol,
        "role": role,
        "capabilities": sorted(set(caps)),
    }


async def notify_exec(bid: int, command: str, info: dict) -> dict:
    """Run an expanded completion command on a paired backend. Same transport
    rules as every other backend call: token auth, no redirects, pinned TLS."""
    be = get_backend(bid)
    if be is None:
        return {"ok": False, "error": "backend %s is not paired" % bid}
    if not backend_is_online(bid):
        return {"ok": False, "error": "%s is offline" % be["name"]}
    urls = _ordered_backend_urls(be)
    last_error = "backend is unavailable"
    for index, url in enumerate(urls):
        try:
            async with client().post(
                    url + "/api/notify/exec",
                    json={"command": command, "info": info},
                    headers={"X-Puppy-Token": be["token"]},
                    timeout=aiohttp.ClientTimeout(
                        total=45, connect=FAILOVER_CONNECT_TIMEOUT,
                        sock_connect=FAILOVER_CONNECT_TIMEOUT),
                    allow_redirects=False,
                    ssl=_ssl_pin(be.get("tls_fingerprint") or "")) as response:
                _publish_active_url(be, url)
                if response.status == 404:
                    return {"ok": False,
                            "error": "%s cannot run commands (upgrade it, or its shell "
                                     "surface is disabled)" % be["name"]}
                if response.status != 200:
                    return {"ok": False,
                            "error": "%s returned %s" % (be["name"], response.status)}
                data = await response.json()
                return data if isinstance(data, dict) else {
                    "ok": False, "error": "invalid reply"}
        except Exception as exc:
            last_error = _connection_error(exc)
            if index + 1 < len(urls) and _failed_before_request(exc):
                continue
            break
    _advance_url_cursor(bid, len(urls))
    if _mark_backend_offline(bid, last_error):
        _broadcast_backends()
        await close_proxy_websockets(bid, "Backend unavailable")
    return {"ok": False, "error": "%s: %s" % (be["name"], last_error)}


async def fetch_completion_events(bid: int, after: int):
    """Read one node's authoritative completion sequence directly.

    This deliberately bypasses the browser proxy: controller health is the
    reachability authority, and token/TLS/redirect rules match every other
    controller-owned backend request.
    """
    be = get_backend(bid)
    if be is None or not backend_is_online(bid):
        return None
    urls = _ordered_backend_urls(be)
    last_error = "backend is unavailable"
    for index, url in enumerate(urls):
        try:
            async with client().get(
                    url + "/api/completions", params={"after": str(int(after))},
                    headers={"X-Puppy-Token": be["token"]},
                    timeout=aiohttp.ClientTimeout(
                        total=10, connect=FAILOVER_CONNECT_TIMEOUT,
                        sock_connect=FAILOVER_CONNECT_TIMEOUT),
                    allow_redirects=False,
                    ssl=_ssl_pin(be.get("tls_fingerprint") or "")) as response:
                _publish_active_url(be, url)
                if response.status == 404:
                    return None
                if response.status != 200:
                    return None
                data = await response.json()
                return data if isinstance(data, dict) else None
        except Exception as exc:
            last_error = _connection_error(exc)
            if index + 1 < len(urls) and _failed_before_request(exc):
                continue
            break
    _advance_url_cursor(bid, len(urls))
    if _mark_backend_offline(bid, last_error):
        _broadcast_backends()
        await close_proxy_websockets(bid, "Backend unavailable")
    return None


async def probe_backend(url: str, token: str, tls_fingerprint: str = "",
                        timeout: float = 8.0) -> dict:
    """Authenticate and negotiate metadata with a prospective backend."""
    try:
        async with client().get(
                f"{url.rstrip('/')}/api/ping",
                headers={"X-Puppy-Token": token},
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False, ssl=_ssl_pin(tls_fingerprint)) as response:
            try:
                data = await response.json()
            except Exception:
                return {"ok": False, "status": response.status,
                        "error": "backend did not return JSON"}
            if not isinstance(data, dict):
                return {"ok": False, "status": response.status,
                        "error": "backend returned an invalid response"}
            if response.status != 200:
                return {"ok": False, "status": response.status,
                        "error": data.get("error") or f"HTTP {response.status}"}
            if data.get("ok") is not True:
                return {"ok": False, "status": response.status,
                        "error": "endpoint is not a Puppy backend"}
            try:
                remote = _normalize_peer(data)
            except ValueError as e:
                return {"ok": False, "status": response.status, "error": str(e)}
            reported_transport = remote.get("transport")
            if reported_transport is not None and not isinstance(reported_transport, dict):
                return {"ok": False, "status": response.status,
                        "error": "backend returned invalid transport metadata"}
            reported_upgrade = remote.get("upgrade")
            if reported_upgrade is not None and not isinstance(reported_upgrade, dict):
                return {"ok": False, "status": response.status,
                        "error": "backend returned invalid upgrade metadata"}
            if isinstance(reported_upgrade, dict) and \
                    reported_upgrade.get("readiness") is not None:
                readiness = reported_upgrade["readiness"]
                if not isinstance(readiness, dict) or \
                        type(readiness.get("ready")) is not bool or \
                        not isinstance(readiness.get("state"), str) or \
                        not isinstance(readiness.get("reason"), str):
                    return {"ok": False, "status": response.status,
                            "error": "backend returned invalid upgrade readiness"}
            if tls_fingerprint and isinstance(reported_transport, dict):
                try:
                    reported_pin = tls.normalize_fingerprint(
                        reported_transport.get("certificate_sha256"))
                except ValueError:
                    return {"ok": False, "status": response.status,
                            "error": "backend returned an invalid TLS certificate fingerprint"}
                if reported_pin and reported_pin != tls_fingerprint:
                    return {"ok": False, "status": response.status,
                            "error": "backend TLS metadata does not match the verified certificate"}
            return {"ok": True, "status": response.status, "remote": remote}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "connection timed out"}
    except Exception as e:
        return {"ok": False, "error": _connection_error(e)}


async def probe_backend_urls(urls: list, token: str, tls_fingerprint: str = "",
                             timeout: float = 8.0) -> dict:
    """Try each configured origin in order and return the first authenticated peer."""
    attempts = []
    for url in urls:
        result = await probe_backend(url, token, tls_fingerprint, timeout=timeout)
        if result.get("ok"):
            result["active_url"] = url
            result["attempts"] = attempts
            return result
        attempts.append({"url": url, "error": result.get("error", "backend test failed"),
                         "status": result.get("status")})
    last = attempts[-1] if attempts else {"error": "no backend URLs are configured"}
    error = str(last.get("error") or "backend test failed")
    if len(attempts) > 1:
        errors = []
        for attempt in attempts:
            detail = str(attempt.get("error") or "backend test failed")
            if detail not in errors:
                errors.append(detail)
        error = "none of the configured backend URLs could be reached; " + "; ".join(errors)
    failed = {"ok": False, "error": error, "attempts": attempts}
    if last.get("status") is not None:
        failed["status"] = last["status"]
    return failed


async def probe_configured_backend(backend: dict, timeout: float = 8.0) -> dict:
    urls = _ordered_backend_urls(backend)
    result = await probe_backend_urls(
        urls, backend["token"], backend.get("tls_fingerprint") or "", timeout=timeout)
    bid = int(backend["id"])
    if result.get("ok"):
        result["active_changed"] = _remember_active_url(
            bid, result["active_url"], _backend_urls(backend))
    else:
        result["active_changed"] = False
        _advance_url_cursor(bid, len(urls))
    return result


def _metadata(remote: dict) -> tuple:
    return (
        int(remote.get("protocol", 0)),
        json.dumps(remote.get("capabilities") or [], separators=(",", ":")),
        str(remote.get("version") or "")[:40],
        str(remote.get("role") or "")[:24],
    )


def _store_metadata(bid: int, remote: dict) -> bool:
    api_protocol, capabilities, remote_version, role = _metadata(remote)
    previous = db.query_one(
        "SELECT protocol,capabilities,remote_version,role FROM backends WHERE id=?", (bid,))
    changed = previous is not None and (
        int(previous["protocol"]) != api_protocol or
        str(previous["capabilities"]) != capabilities or
        str(previous["remote_version"]) != remote_version or
        str(previous["role"]) != role)
    if changed:
        db.execute(
            "UPDATE backends SET protocol=?,capabilities=?,remote_version=?,role=? WHERE id=?",
            (api_protocol, capabilities, remote_version, role, bid))
    return _cache_remote_payload(bid, "ping", remote) or changed


def _same_backend_connection(first: dict, second: dict) -> bool:
    return _backend_urls(first) == _backend_urls(second) and \
        str(first.get("token") or "") == str(second.get("token") or "") and \
        str(first.get("tls_fingerprint") or "") == \
        str(second.get("tls_fingerprint") or "")


async def _probe_backend_health(backend: dict) -> None:
    """Probe and publish one node without waiting for slower peers."""
    bid = int(backend["id"])
    try:
        result = await probe_configured_backend(
            backend, timeout=HEALTH_PROBE_TIMEOUT)
        current = get_backend(bid)
        if current is None or not _same_backend_connection(backend, current):
            return
        if not result.get("ok"):
            error = str(result.get("error") or "Backend unavailable")
            if _mark_backend_offline(bid, error):
                _broadcast_backends()
                await close_proxy_websockets(bid, "Backend unavailable")
                log.info("backend %s is offline: %s", backend["name"], error)
            return

        remote = result["remote"]
        changed = bool(result.get("active_changed")) or \
            _store_metadata(bid, remote)
        became_online = _mark_backend_online(bid)
        if changed or became_online:
            _broadcast_backends()
        if became_online:
            _wake_auto_upgrade()
            log.info("backend %s is online", backend["name"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        current = get_backend(bid)
        if current is None or not _same_backend_connection(backend, current):
            return
        error = _connection_error(exc)
        if _mark_backend_offline(bid, error):
            _broadcast_backends()
            await close_proxy_websockets(bid, "Backend unavailable")
            log.info("backend %s is offline: %s", backend["name"], error)


async def health_cycle(app: web.Application) -> None:
    """Refresh controller-owned backend availability without involving browsers.

    This is the sole recovery traffic for an offline node. Normal proxy and UI
    traffic is admitted only after one of these authenticated probes succeeds.
    Each result is published independently, so a timed-out peer cannot delay a
    healthy node's session list.
    """
    if app.get("puppy_snapshot_busy"):
        return
    rows = [dict(row) for row in db.query("SELECT * FROM backends ORDER BY id")]
    live_ids = {int(row["id"]) for row in rows}
    for bucket in (_health, _health_retry_after, _health_retry_delay):
        for bid in list(bucket):
            if bid not in live_ids:
                bucket.pop(bid, None)
    now = time.monotonic()
    due = [row for row in rows
           if int(row["id"]) not in _upgrades_in_progress and
           _health_retry_after.get(int(row["id"]), 0) <= now]
    await asyncio.gather(*(_probe_backend_health(backend) for backend in due))
    _sync_state_streams()


async def _health_loop(app: web.Application) -> None:
    while True:
        if _health_wake is not None:
            _health_wake.clear()
        try:
            await health_cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("backend health scan failed")
        try:
            if _health_wake is None:
                await asyncio.sleep(HEALTH_SCAN_INTERVAL)
            else:
                await asyncio.wait_for(
                    _health_wake.wait(), timeout=HEALTH_SCAN_INTERVAL)
        except asyncio.TimeoutError:
            pass


async def start_health_worker(app: web.Application) -> None:
    global _health_task, _health_wake
    if _health_task is not None and not _health_task.done():
        return
    _health.clear()
    _health_retry_after.clear()
    _health_retry_delay.clear()
    _health_wake = asyncio.Event()
    _health_task = asyncio.create_task(_health_loop(app))


async def stop_health_worker(_app: web.Application = None) -> None:
    global _health_task, _health_wake
    task = _health_task
    _health_task = None
    _health_wake = None
    state_tasks = list(_state_stream_tasks.values())
    _state_stream_tasks.clear()
    for state_task in state_tasks:
        state_task.cancel()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if state_tasks:
        await asyncio.gather(*state_tasks, return_exceptions=True)


def _sweep_build_leftovers(work_root: Path) -> None:
    """Drop build directories a killed process never unwound.

    TemporaryDirectory removes its own on any ordinary exit, so anything left
    here outlived the process that made it. Only this function's own prefix is
    considered, and only after a day, so a build running beside this one is
    never touched.
    """
    cutoff = time.time() - 24 * 60 * 60
    try:
        children = sorted(work_root.iterdir())
    except OSError:
        return
    for child in children:
        if not child.name.startswith("build-"):
            continue
        try:
            if child.is_symlink() or not child.is_dir() or \
                    child.lstat().st_mtime > cutoff:
                continue
            shutil.rmtree(str(child))
            log.info("removed abandoned backend build directory %s", child.name)
        except OSError as exc:
            log.warning("abandoned backend build directory retained: %s", exc)


def _build_upgrade_payload() -> tuple:
    """Build and independently self-test the current backend in private data."""
    root = Path(__file__).resolve().parent.parent
    build_script = root / "backend" / "build.py"
    if not build_script.is_file():
        raise RuntimeError("backend build source is not installed on this controller")
    work_root = Path(config.DATA_DIR) / "upgrades"
    work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os_mode = work_root.stat().st_mode & 0o777
    if os_mode != 0o700:
        work_root.chmod(0o700)
    _sweep_build_leftovers(work_root)
    with tempfile.TemporaryDirectory(prefix="build-", dir=str(work_root)) as temporary:
        temp_dir = Path(temporary)
        artifact = temp_dir / "puppy-backend.pyz"
        built = subprocess.run(
            [sys.executable, str(build_script), "--output", str(artifact)],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        if built.returncode != 0:
            raise RuntimeError("backend build failed: " + built.stdout[-800:].strip())
        smoke_data = temp_dir / "smoke-data"
        checked = subprocess.run(
            [sys.executable, str(artifact), "self-test", "--data-dir", str(smoke_data)],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=30)
        if checked.returncode != 0:
            raise RuntimeError("backend self-test failed: " + checked.stdout[-800:].strip())
        try:
            self_test = json.loads([line for line in checked.stdout.splitlines() if line.strip()][-1])
        except Exception as exc:
            raise RuntimeError("backend self-test returned invalid output") from exc
        payload = artifact.read_bytes()
    if self_test.get("ok") is not True or self_test.get("role") != "backend" or \
            self_test.get("version") != __version__ or \
            self_test.get("protocol") != protocol.API_PROTOCOL or \
            self_test.get("artifact") != "zipapp":
        raise RuntimeError("built backend metadata does not match this controller")
    if not 0 < len(payload) <= upgrade_contract.MAX_ARTIFACT_BYTES:
        raise RuntimeError("built backend artifact exceeds the upgrade size limit")
    manifest = {
        "format": upgrade_contract.FORMAT_VERSION,
        "artifact": "zipapp",
        "version": __version__,
        "protocol": protocol.API_PROTOCOL,
        "size": len(payload),
        "sha256": upgrade_contract.artifact_sha256(payload),
        "nonce": secrets.token_urlsafe(18),
        "created_at": int(time.time()),
        "build_commit": str(self_test.get("build_commit") or ""),
    }
    return payload, manifest

# Aliases the spawn bridge resolves to this controller's own node, so no
# backend may carry them; "#<id>" is reserved for id references.
RESERVED_NODE_NAMES = ("local", "this node", "this backend")


def node_key(name) -> str:
    """Case- and whitespace-insensitive form of a node display name: spawn
    directives are typed by people (and models) who do not reproduce case
    reliably, so names differing only that way are the same name."""
    return " ".join(str(name or "").strip().lower().split())


def node_name_conflict(name: str, exclude_bid=None) -> str:
    """Why this display name cannot identify a backend unambiguously ("" if
    it can): empty, an id reference, a reserved alias, this controller's own
    instance name, or already another backend's name."""
    name = str(name or "").strip()
    if not name:
        return "backend name is required"
    if name.startswith("#"):
        return "backend names cannot start with '#' (that form refers to a node id)"
    key = node_key(name)
    if key in RESERVED_NODE_NAMES:
        return "'{}' is reserved for this controller's own node".format(name)
    if key == node_key(config.get("instance_name")):
        return "'{}' is this controller's own instance name".format(name)
    for item in list_backends():
        if exclude_bid is not None and int(item["id"]) == int(exclude_bid):
            continue
        if node_key(item.get("name")) == key:
            return "'{}' is already the name of backend #{}".format(
                name, item["id"])
    return ""


def instance_name_conflict(name: str) -> str:
    """Why this controller cannot take this instance name ("" if it can): it
    would shadow a backend or a reserved alias in node resolution."""
    name = str(name or "").strip()
    if name.startswith("#") or node_key(name) in RESERVED_NODE_NAMES:
        return "'{}' is reserved and cannot be the instance name".format(name)
    for item in list_backends():
        if node_key(item.get("name")) == node_key(name):
            return "'{}' is already the name of backend #{}".format(
                name, item["id"])
    return ""


async def h_list(request: web.Request):
    return web.json_response({"backends": list_backends()})


async def h_add(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid backend request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid backend request"}, status=400)
    name = (body.get("name") or "").strip()[:80]
    token = (body.get("token") or "").strip()
    auto_upgrade = body.get("auto_upgrade", False)
    if type(auto_upgrade) is not bool:
        return web.json_response({"error": "auto-upgrade must be on or off"}, status=400)
    conflict = node_name_conflict(name) if name else ""
    if conflict:
        return web.json_response({"error": conflict}, status=409)
    try:
        urls = _request_urls(body)
        tls_fingerprint = tls.normalize_fingerprint(
            body.get("tls_fingerprint") or body.get("tls_sha256"))
        _validate_url_security(urls, tls_fingerprint)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not token:
        return web.json_response({"error": "API token is required"}, status=400)
    result = await probe_backend_urls(urls, token, tls_fingerprint)
    if not result["ok"]:
        return web.json_response({"error": result.get("error", "backend test failed"),
                                  "status": result.get("status"),
                                  "attempts": result.get("attempts", [])}, status=400)
    remote = result["remote"]
    name = name or str(remote.get("name") or "").strip()[:80] or "backend"
    conflict = node_name_conflict(name)
    if conflict:
        # the node's own instance name is only a default; a second node
        # reporting the same one must be told apart by the person pairing it
        return web.json_response(
            {"error": conflict + "; pass a different name"}, status=409)
    if auto_upgrade and (remote.get("role") != "backend" or
                         protocol.UPGRADE_CAPABILITY not in (remote.get("capabilities") or [])):
        return web.json_response({
            "error": "automatic upgrades require an upgrade-capable headless backend"
        }, status=409)
    api_protocol, capabilities, remote_version, role = _metadata(remote)
    bid = db.execute(
        "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,role,"
        "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (name[:80], urls[0], _urls_json(urls), token, api_protocol, capabilities,
         remote_version, role, tls_fingerprint, int(auto_upgrade), time.time()))
    _cache_remote_payload(bid, "ping", remote)
    _remember_active_url(bid, result["active_url"], urls)
    _mark_backend_online(bid)
    _broadcast_backends()
    _wake_health()
    if auto_upgrade:
        _wake_auto_upgrade()
    added_backend = next(item for item in list_backends() if item["id"] == bid)
    return web.json_response({"ok": True, "id": bid, "remote": remote,
                              "active_url": result["active_url"], "urls": urls,
                              "auto_upgrade": auto_upgrade,
                              "backend": added_backend})


async def h_patch(request: web.Request):
    bid = int(request.match_info["bid"])
    backend = get_backend(bid)
    if backend is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid backend request"}, status=400)
    allowed = {"name", "url", "urls", "token", "tls_fingerprint", "tls_sha256",
               "auto_upgrade"}
    if not isinstance(body, dict) or not body or set(body) - allowed:
        return web.json_response({"error": "invalid backend request"}, status=400)
    if "auto_upgrade" in body and type(body["auto_upgrade"]) is not bool:
        return web.json_response({"error": "auto-upgrade must be on or off"}, status=400)

    name = str(backend["name"])
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            return web.json_response({"error": "backend name is required"}, status=400)
        name = body["name"].strip()[:80]
        conflict = node_name_conflict(name, exclude_bid=bid)
        if conflict:
            return web.json_response({"error": conflict}, status=409)

    try:
        urls = _request_urls(body, backend)
        if "token" in body:
            if not isinstance(body["token"], str) or not body["token"].strip():
                return web.json_response({"error": "API token is required"}, status=400)
            token = body["token"].strip()
        else:
            token = str(backend["token"])
        fingerprint_values = [body[key] for key in ("tls_fingerprint", "tls_sha256")
                              if key in body]
        if len(fingerprint_values) > 1:
            normalized = {tls.normalize_fingerprint(value)
                          for value in fingerprint_values}
            if len(normalized) != 1:
                raise ValueError("TLS certificate fingerprints do not match")
            tls_fingerprint = normalized.pop()
        elif fingerprint_values:
            tls_fingerprint = tls.normalize_fingerprint(fingerprint_values[0])
        else:
            tls_fingerprint = str(backend.get("tls_fingerprint") or "")
        _validate_url_security(urls, tls_fingerprint)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    connection_changed = (urls != _backend_urls(backend) or token != backend["token"] or
                          tls_fingerprint != (backend.get("tls_fingerprint") or ""))
    if connection_changed and bid in _upgrades_in_progress:
        return web.json_response(
            {"error": "backend connection cannot change while an upgrade is in progress"},
            status=409)

    remote = None
    active_url = _active_urls.get(bid, "")
    if connection_changed:
        probe_urls = list(urls)
        if active_url in probe_urls:
            probe_urls.remove(active_url)
            probe_urls.insert(0, active_url)
        result = await probe_backend_urls(probe_urls, token, tls_fingerprint)
        if not result["ok"]:
            return web.json_response({
                "error": result.get("error", "backend test failed"),
                "status": result.get("status"),
                "attempts": result.get("attempts", []),
            }, status=400)
        remote = result["remote"]
        active_url = result["active_url"]
        # The probe yields to the upgrade worker. Re-check after it so an
        # upgrade that began in that window cannot have its live connection
        # replaced underneath the signed restart/rollback pipeline.
        if bid in _upgrades_in_progress:
            return web.json_response(
                {"error": "backend connection cannot change while an upgrade is in progress"},
                status=409)

    enabled = body.get("auto_upgrade", bool(backend.get("auto_upgrade")))
    candidate_role = remote.get("role") if remote else backend.get("role")
    candidate_capabilities = ((remote.get("capabilities") or []) if remote else
                              _backend_capabilities(backend))
    if enabled and (candidate_role != "backend" or
                    protocol.UPGRADE_CAPABILITY not in candidate_capabilities):
        return web.json_response({
            "error": "automatic upgrades require an upgrade-capable headless backend"
        }, status=409)

    previous_last_known = _load_last_known(bid) if connection_changed else None
    if remote:
        api_protocol, capabilities, remote_version, role = _metadata(remote)
    else:
        api_protocol = int(backend.get("protocol") or 0)
        capabilities = str(backend.get("capabilities") or "[]")
        remote_version = str(backend.get("remote_version") or "")
        role = str(backend.get("role") or "")
    db.execute(
        "UPDATE backends SET name=?,url=?,urls=?,token=?,protocol=?,capabilities=?,"
        "remote_version=?,role=?,tls_fingerprint=?,auto_upgrade=? WHERE id=?",
        (name, urls[0], _urls_json(urls), token, api_protocol, capabilities, remote_version, role,
         tls_fingerprint, int(enabled), bid))
    if connection_changed:
        # A backend id survives an endpoint/token/pin edit, but its upstream
        # state socket must not.  Cancel it before installing observations from
        # the newly authenticated connection; the health worker creates one
        # fresh subscription using the new transport tuple.
        _clear_backend_health(bid)
        _publish_remote_stream(bid, False)
        same_node = bool(
            previous_last_known and
            isinstance(previous_last_known.get("node_uuid"), str) and
            previous_last_known.get("node_uuid") == remote.get("node_uuid"))
        if not same_node:
            _clear_last_known(bid)
            from puppy import notify
            notify.forget_backend(bid)
        _cache_remote_payload(bid, "ping", remote)
        _remember_active_url(bid, active_url, urls)
        _mark_backend_online(bid)
    if connection_changed or enabled != bool(backend.get("auto_upgrade")):
        _auto_upgrade_retry_after.pop(bid, None)
        _auto_upgrade_checked_at.pop(bid, None)
        _auto_upgrade_last_errors.pop(bid, None)
    _broadcast_backends()
    if enabled:
        _wake_auto_upgrade()
    if connection_changed:
        await close_proxy_websockets(bid, "Backend connection changed")
        _wake_health()
    updated = next(item for item in list_backends() if item["id"] == bid)
    return web.json_response({"ok": True, "backend": updated,
                              "remote": remote,
                              "connection_changed": connection_changed})


async def h_delete(request: web.Request):
    bid = int(request.match_info["bid"])
    db.execute("DELETE FROM backends WHERE id=?", (bid,))
    from puppy import notify
    notify.forget_backend(bid)
    _clear_last_known(bid)
    _active_urls.pop(bid, None)
    _url_cursors.pop(bid, None)
    _auto_upgrade_retry_after.pop(bid, None)
    _auto_upgrade_checked_at.pop(bid, None)
    _auto_upgrade_last_errors.pop(bid, None)
    _clear_backend_health(bid)
    _broadcast_backends()
    _wake_health()
    return web.json_response({"ok": True})


async def h_test(request: web.Request):
    bid = int(request.match_info["bid"])
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    result = await probe_configured_backend(be)
    changed = bool(result.get("active_changed"))
    if result["ok"]:
        changed = _store_metadata(bid, result["remote"]) or changed
        changed = _mark_backend_online(bid) or changed
        _wake_auto_upgrade()
    else:
        changed = _mark_backend_offline(
            bid, result.get("error", "Backend unavailable")) or changed
        await close_proxy_websockets(bid, "Backend unavailable")
    if changed:
        _broadcast_backends()
    _wake_health()
    current = next((item for item in list_backends() if item["id"] == bid), None)
    return web.json_response({**result, "backend": current})


class BackendUpgradeError(RuntimeError):
    def __init__(self, message: str, status: int = 500, readiness=None):
        super().__init__(message)
        self.status = status
        self.readiness = readiness if isinstance(readiness, dict) else None

    def payload(self) -> dict:
        value = {"error": str(self)}
        if self.readiness is not None:
            value["readiness"] = self.readiness
        return value


async def upgrade_backend(bid: int, remote_hint=None) -> dict:
    """Run the one safe upgrade pipeline used by manual and automatic requests."""
    be = get_backend(bid)
    if be is None:
        raise BackendUpgradeError("unknown backend", 404)
    if not backend_is_online(bid):
        raise BackendUpgradeError("backend is offline; test its connection to retry now", 503)
    if bid in _upgrades_in_progress:
        raise BackendUpgradeError("an upgrade is already in progress for this backend", 409)
    _upgrades_in_progress.add(bid)
    _broadcast_backends()
    try:
        remote = remote_hint
        if remote is None:
            current = await probe_configured_backend(be)
            if not current["ok"]:
                _mark_backend_offline(
                    bid, current.get("error", "backend is unavailable"))
                _broadcast_backends()
                raise BackendUpgradeError(
                    current.get("error", "backend is unavailable"), 503)
            remote = current["remote"]
            _mark_backend_online(bid)
            if current.get("active_changed"):
                _broadcast_backends()
        _store_metadata(bid, remote)
        upgrade_descriptor = remote.get("upgrade") or {}
        if protocol.UPGRADE_CAPABILITY not in (remote.get("capabilities") or []) or \
                not upgrade_descriptor.get("supported"):
            raise BackendUpgradeError("backend does not support safe remote upgrades", 409)
        remote_readiness = upgrade_descriptor.get("readiness")
        if isinstance(remote_readiness, dict) and remote_readiness.get("ready") is not True:
            raise BackendUpgradeError(
                remote_readiness.get("reason") or "backend is not ready to upgrade",
                409, remote_readiness)
        try:
            if upgrade_contract.version_key(__version__) <= \
                    upgrade_contract.version_key(str(remote.get("version") or "")):
                raise BackendUpgradeError("backend is already at this version or newer", 409)
        except ValueError as exc:
            raise BackendUpgradeError(str(exc), 409) from exc

        loop = asyncio.get_running_loop()
        try:
            payload, manifest = await loop.run_in_executor(None, _build_upgrade_payload)
        except Exception as exc:
            log.exception("backend release build failed")
            raise BackendUpgradeError(str(exc), 500) from exc
        headers = {
            "X-Puppy-Token": be["token"],
            "Content-Type": "application/octet-stream",
            upgrade_contract.MANIFEST_HEADER: upgrade_contract.encode_manifest(manifest),
            upgrade_contract.SIGNATURE_HEADER: upgrade_contract.sign(be["token"], manifest, payload),
        }
        urls = _ordered_backend_urls(be)
        for index, url in enumerate(urls):
            target = url.rstrip("/") + protocol.UPGRADE_API_PATH
            try:
                async with client().post(
                        target, data=payload, headers=headers,
                        timeout=aiohttp.ClientTimeout(
                            total=45, connect=FAILOVER_CONNECT_TIMEOUT,
                            sock_connect=FAILOVER_CONNECT_TIMEOUT),
                        allow_redirects=False,
                        ssl=_ssl_pin(be["tls_fingerprint"])) as response:
                    _publish_active_url(be, url)
                    try:
                        accepted = await response.json()
                    except Exception:
                        accepted = {"error": "backend returned a non-JSON upgrade response"}
                    if response.status != 202 or accepted.get("accepted") is not True:
                        status = (response.status if 400 <= response.status < 600 and
                                  response.status not in (401, 403) else 502)
                        raise BackendUpgradeError(
                            accepted.get("error") or "backend rejected the upgrade", status,
                            accepted.get("readiness"))
                break
            except BackendUpgradeError:
                raise
            except Exception as exc:
                if index + 1 < len(urls) and _failed_before_request(exc):
                    continue
                if isinstance(exc, asyncio.TimeoutError):
                    raise BackendUpgradeError(
                        "backend timed out while staging the upgrade", 503) from exc
                raise BackendUpgradeError(
                    "backend upgrade request failed: {}".format(
                        _connection_error(exc)), 502) from exc
        else:
            raise BackendUpgradeError("backend is unavailable", 502)

        deadline = time.monotonic() + 90
        last_error = "backend did not return after its upgrade restart"
        while time.monotonic() < deadline:
            await asyncio.sleep(0.75)
            checked = await probe_configured_backend(be, timeout=2.5)
            if not checked["ok"]:
                last_error = checked.get("error", last_error)
                continue
            upgraded = checked["remote"]
            last = (upgraded.get("upgrade") or {}).get("last") or {}
            if upgraded.get("version") == manifest["version"] and \
                    protocol.UPGRADE_CAPABILITY in (upgraded.get("capabilities") or []) and \
                    last.get("state") == "succeeded" and \
                    last.get("target_version") == manifest["version"] and \
                    last.get("sha256") == manifest["sha256"]:
                _store_metadata(bid, upgraded)
                _mark_backend_online(bid)
                log.info("backend %s upgraded %s -> %s", be["name"],
                         remote.get("version"), upgraded.get("version"))
                return {
                    "ok": True, "from_version": remote.get("version"),
                    "to_version": upgraded.get("version"), "sha256": manifest["sha256"],
                    "remote": upgraded,
                }
            if last.get("target_version") == manifest["version"] and \
                    last.get("state") in ("rolled-back", "failed"):
                _store_metadata(bid, upgraded)
                raise BackendUpgradeError(
                    "backend rolled back the upgrade: {}".format(
                        last.get("error") or "candidate health check failed"), 502)
            last_error = "backend returned version {} instead of {}".format(
                upgraded.get("version", "?"), manifest["version"])
        _mark_backend_offline(bid, last_error)
        raise BackendUpgradeError(last_error, 503)
    finally:
        _upgrades_in_progress.discard(bid)
        _broadcast_backends()


async def h_upgrade(request: web.Request):
    try:
        bid = int(request.match_info["bid"])
        result = await upgrade_backend(bid)
        current = next((item for item in list_backends() if item["id"] == bid), None)
        return web.json_response({**result, "backend": current})
    except BackendUpgradeError as exc:
        return web.json_response(exc.payload(), status=exc.status)


async def _legacy_auto_readiness(backend: dict) -> dict:
    """Conservatively infer idleness for the first upgrade of older nodes.

    Their POST remains authoritative for terminal activity and last-moment
    races. This check prevents artifact work while a reported session is busy.
    """
    payload = None
    last_error = "backend is unavailable"
    for index, url in enumerate(_ordered_backend_urls(backend)):
        target = url.rstrip("/") + "/api/sessions"
        try:
            async with client().get(
                    target, headers={"X-Puppy-Token": backend["token"]},
                    timeout=aiohttp.ClientTimeout(
                        total=5, connect=FAILOVER_CONNECT_TIMEOUT,
                        sock_connect=FAILOVER_CONNECT_TIMEOUT), allow_redirects=False,
                    ssl=_ssl_pin(backend["tls_fingerprint"])) as response:
                try:
                    payload = await response.json()
                except Exception:
                    payload = None
                if response.status == 200 and isinstance(payload, dict) and \
                        isinstance(payload.get("sessions"), list):
                    _publish_active_url(backend, url)
                    break
                return {
                    "ready": False, "state": "checking",
                    "reason": "could not verify legacy backend session activity",
                }
        except Exception as exc:
            last_error = _connection_error(exc)
            if index + 1 < len(_backend_urls(backend)):
                continue
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        return {
            "ready": False, "state": "checking",
            "reason": "could not verify legacy backend idleness: {}".format(
                last_error),
        }
    busy = [item for item in payload["sessions"] if not isinstance(item, dict) or
            item.get("status") != "idle"]
    return {
        "ready": not busy,
        "state": "ready" if not busy else "busy",
        "reason": ("legacy backend reports no active sessions" if not busy else
                   "legacy backend has an active session"),
        "legacy": True,
    }


def _auto_note_error(backend: dict, message: str) -> None:
    bid = int(backend["id"])
    if _auto_upgrade_last_errors.get(bid) != message:
        log.warning("automatic upgrade for backend %s deferred: %s", backend["name"], message)
        _auto_upgrade_last_errors[bid] = message


async def auto_upgrade_cycle(app: web.Application) -> None:
    """Probe opted-in headless nodes and upgrade at most one at a time."""
    if app.get("puppy_snapshot_busy"):
        return
    rows = [dict(row) for row in db.query(
        "SELECT * FROM backends WHERE auto_upgrade=1 ORDER BY id")]
    live_ids = {int(row["id"]) for row in rows}
    for bucket in (_auto_upgrade_retry_after, _auto_upgrade_checked_at,
                   _auto_upgrade_last_errors):
        for bid in list(bucket):
            if bid not in live_ids:
                bucket.pop(bid, None)
    now = time.monotonic()
    due = []
    controller_version = upgrade_contract.version_key(__version__)
    for backend in rows:
        bid = int(backend["id"])
        if not backend_is_online(bid):
            continue
        if _auto_upgrade_retry_after.get(bid, 0) > now:
            continue
        try:
            stored_version = upgrade_contract.version_key(
                str(backend.get("remote_version") or ""))
        except ValueError:
            stored_version = None
        if stored_version is not None and stored_version >= controller_version and \
                now - _auto_upgrade_checked_at.get(bid, 0) < AUTO_UPGRADE_CURRENT_RECHECK:
            continue
        due.append(backend)
    if not due:
        return

    probes = await asyncio.gather(*(
        probe_configured_backend(item, timeout=5)
        for item in due), return_exceptions=True)
    metadata_changed = False
    for backend, current in zip(due, probes):
        if app.get("puppy_snapshot_busy"):
            break
        bid = int(backend["id"])
        now = time.monotonic()
        _auto_upgrade_checked_at[bid] = now
        if isinstance(current, Exception):
            message = _connection_error(current)
            if _mark_backend_offline(bid, message):
                _broadcast_backends()
                await close_proxy_websockets(bid, "Backend unavailable")
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, message)
            continue
        if not current["ok"]:
            message = current.get("error", "backend is unavailable")
            if _mark_backend_offline(bid, message):
                _broadcast_backends()
                await close_proxy_websockets(bid, "Backend unavailable")
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, message)
            continue
        remote = current["remote"]
        _mark_backend_online(bid)
        stored_changed = _store_metadata(bid, remote)
        metadata_changed = bool(current.get("active_changed")) or \
            stored_changed or metadata_changed
        try:
            remote_version = upgrade_contract.version_key(str(remote.get("version") or ""))
        except ValueError as exc:
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, str(exc))
            continue
        if remote_version >= controller_version:
            _auto_upgrade_retry_after.pop(bid, None)
            _auto_upgrade_last_errors.pop(bid, None)
            continue
        descriptor = remote.get("upgrade") or {}
        if remote.get("role") != "backend" or \
                protocol.UPGRADE_CAPABILITY not in (remote.get("capabilities") or []) or \
                descriptor.get("supported") is not True:
            message = descriptor.get("reason") or \
                "backend does not support safe remote upgrades"
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, message)
            continue
        readiness = descriptor.get("readiness")
        if not isinstance(readiness, dict):
            readiness = await _legacy_auto_readiness(backend)
        if readiness.get("ready") is not True:
            state = readiness.get("state")
            delay = AUTO_UPGRADE_INTERVAL if state in ("busy", "upgrading", "checking") \
                else AUTO_UPGRADE_FAILURE_RETRY
            _auto_upgrade_retry_after[bid] = time.monotonic() + delay
            if state in ("blocked", "unsupported"):
                _auto_note_error(
                    backend, readiness.get("reason") or "backend is not ready to upgrade")
            continue
        fresh = get_backend(bid)
        if fresh is None or not bool(fresh.get("auto_upgrade")) or \
                app.get("puppy_snapshot_busy"):
            continue
        try:
            result = await upgrade_backend(bid, remote_hint=remote)
        except BackendUpgradeError as exc:
            state = (exc.readiness or {}).get("state")
            delay = AUTO_UPGRADE_INTERVAL if state in ("busy", "upgrading") or \
                "already in progress" in str(exc) else AUTO_UPGRADE_FAILURE_RETRY
            _auto_upgrade_retry_after[bid] = time.monotonic() + delay
            if state not in ("busy", "upgrading") and "already in progress" not in str(exc):
                _auto_note_error(backend, str(exc))
            continue
        _auto_upgrade_retry_after.pop(bid, None)
        _auto_upgrade_last_errors.pop(bid, None)
        _auto_upgrade_checked_at[bid] = time.monotonic()
        log.info("automatic backend upgrade completed for %s at v%s",
                 backend["name"], result["to_version"])
    if metadata_changed:
        _broadcast_backends()


async def _auto_upgrade_loop(app: web.Application) -> None:
    while True:
        if _auto_upgrade_wake is not None:
            _auto_upgrade_wake.clear()
        try:
            await auto_upgrade_cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automatic backend upgrade scan failed")
        try:
            if _auto_upgrade_wake is None:
                await asyncio.sleep(AUTO_UPGRADE_INTERVAL)
            else:
                await asyncio.wait_for(
                    _auto_upgrade_wake.wait(), timeout=AUTO_UPGRADE_INTERVAL)
        except asyncio.TimeoutError:
            pass


async def start_auto_upgrade_worker(app: web.Application) -> None:
    global _auto_upgrade_task, _auto_upgrade_wake
    if _auto_upgrade_task is not None and not _auto_upgrade_task.done():
        return
    _auto_upgrade_wake = asyncio.Event()
    _auto_upgrade_task = asyncio.create_task(_auto_upgrade_loop(app))


async def stop_auto_upgrade_worker(_app: web.Application = None) -> None:
    global _auto_upgrade_task, _auto_upgrade_wake
    task = _auto_upgrade_task
    _auto_upgrade_task = None
    _auto_upgrade_wake = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def upgrade_blockers() -> list:
    if not _upgrades_in_progress:
        return []
    names = []
    for bid in sorted(_upgrades_in_progress):
        backend = get_backend(bid)
        names.append(backend["name"] if backend else str(bid))
    return ["backend upgrade in progress: {}".format(", ".join(names))]


# ---- proxy ----

def _is_ws(request: web.Request) -> bool:
    return request.headers.get("Upgrade", "").lower() == "websocket"


def _unavailable_response(backend: dict):
    availability = _availability(int(backend["id"]))
    reason = availability.get("reason") or "The controller has not verified this backend"
    return web.json_response({
        "error": "backend '{}' is unavailable: {}".format(backend["name"], reason),
        "availability": availability,
    }, status=503, headers={"Retry-After": "8"})


async def proxy(request: web.Request):
    bid = int(request.match_info["bid"])
    tail = request.match_info["tail"]
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    # Browsers never discover reachability by hammering a proxy route. The
    # controller health worker is the sole path that can move this gate back
    # to online after an outage (and the explicit Test button can force it).
    if not backend_is_online(bid):
        return _unavailable_response(be)
    urls = _ordered_backend_urls(be)
    if not urls:
        if _mark_backend_offline(bid, "backend has no configured URL"):
            _broadcast_backends()
        return _unavailable_response(be)
    headers = {"X-Puppy-Token": be["token"]}
    for k, v in request.headers.items():
        if k.lower() not in HOP_HEADERS:
            headers.setdefault(k, v)

    if _is_ws(request):
        return await _proxy_ws(request, be, urls, tail, headers)

    streaming_upload = request.method == "POST" and UPLOAD_PROXY_PATH.fullmatch(tail)
    if streaming_upload:
        body = request.content.iter_chunked(256 * 1024)
        timeout = aiohttp.ClientTimeout(
            total=PROXY_UPLOAD_TIMEOUT, connect=FAILOVER_CONNECT_TIMEOUT,
            sock_connect=FAILOVER_CONNECT_TIMEOUT)
    else:
        buffered = await request.read()
        body = buffered if buffered else None
        timeout = aiohttp.ClientTimeout(
            total=PROXY_TOTAL_TIMEOUT, connect=FAILOVER_CONNECT_TIMEOUT,
            sock_connect=FAILOVER_CONNECT_TIMEOUT)
    safe_replay = request.method in ("GET", "HEAD", "OPTIONS")
    last_error = None
    for index, url in enumerate(urls):
        target = f"{url}/api/{tail}"
        if request.query_string:
            target += "?" + request.query_string
        try:
            async with client().request(request.method, target, headers=headers,
                                        data=body, timeout=timeout,
                                        allow_redirects=False,
                                        ssl=_ssl_pin(be["tls_fingerprint"])) as r:
                payload = await r.read()
                changed = _remember_active_url(bid, url, _backend_urls(be))
                changed = _mark_backend_online(bid) or changed
                if request.method == "GET" and tail in ("ping", "node") and r.status == 200:
                    try:
                        remote = _normalize_peer(json.loads(payload.decode("utf-8")))
                        if remote.get("ok") is True:
                            changed = _store_metadata(bid, remote) or changed
                    except Exception:
                        pass  # proxy the authoritative response; metadata caching is best-effort
                if 200 <= r.status < 300 and _cacheable_remote_tail(tail):
                    try:
                        response_payload = json.loads(payload.decode("utf-8"))
                        changed = _cache_remote_payload(
                            bid, tail, response_payload) or changed
                    except Exception:
                        pass  # cache observation is best-effort; proxy reply remains authoritative
                if changed:
                    _broadcast_backends()
                return web.Response(status=r.status, body=payload,
                                    content_type=r.content_type or "application/json")
        except Exception as exc:
            last_error = exc
            can_retry = _failed_before_request(exc) or (
                safe_replay and isinstance(exc, (aiohttp.ClientConnectionError,
                                                 asyncio.TimeoutError)))
            if index + 1 < len(urls) and can_retry:
                continue
            break
    _advance_url_cursor(bid, len(urls))
    detail = _connection_error(last_error) if last_error else "no URL is available"
    if _mark_backend_offline(bid, detail):
        _broadcast_backends()
    await close_proxy_websockets(bid, "Backend unavailable")
    return _unavailable_response(be)


async def _proxy_ws(request: web.Request, backend: dict, urls: list,
                    tail: str, headers: dict):
    bid = int(backend["id"])
    tls_fingerprint = backend.get("tls_fingerprint") or ""
    ws_client = None
    selected_url = ""
    last_error = None
    ws_url = ""
    for url in urls:
        target = f"{url}/api/{tail}"
        if request.query_string:
            target += "?" + request.query_string
        ws_url = "ws" + target[4:] if target.startswith("http") else target
        try:
            ws_client = await asyncio.wait_for(
                client().ws_connect(
                    ws_url, headers={"X-Puppy-Token": headers["X-Puppy-Token"]},
                    heartbeat=30, max_msg_size=1 << 22,
                    ssl=_ssl_pin(tls_fingerprint)),
                timeout=FAILOVER_CONNECT_TIMEOUT)
            selected_url = url
            break
        except Exception as exc:
            last_error = exc
            log.warning("ws proxy to %s failed before handshake: %s",
                        ws_url, _connection_error(exc))
    if ws_client is None:
        error = _connection_error(last_error) if last_error else "no URL is available"
        if _mark_backend_offline(bid, error):
            _broadcast_backends()
        await close_proxy_websockets(bid, "Backend unavailable")
        return _unavailable_response(backend)

    # A connection edit can finish while this remote handshake is in flight,
    # before the viewer socket is registered for scoped revocation. Refuse to
    # attach that just-opened old channel after its pairing stopped being
    # authoritative; the browser will reconnect through the new settings.
    current = get_backend(bid)
    if current is None or selected_url not in _backend_urls(current) or \
            headers["X-Puppy-Token"] != current.get("token") or \
            tls_fingerprint != (current.get("tls_fingerprint") or ""):
        await ws_client.close()
        return web.json_response({"error": "backend connection changed; reconnect"}, status=409)

    _publish_active_url(current, selected_url)

    if request.app.get("puppy_snapshot_busy") == "restore":
        await ws_client.close()
        return web.json_response({"error": "Puppy restore in progress"}, status=503)

    ws_server = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 22)
    try:
        await ws_server.prepare(request)
        live_websockets.track(request, ws_server)
        socket_record = (bid, ws_server)
        _proxy_websockets.add(socket_record)

        async def pump(src, dst, from_backend=False):
            try:
                async for msg in src:
                    if msg.type == WSMsgType.TEXT:
                        if from_backend and tail == "ws/updates":
                            try:
                                update = json.loads(msg.data)
                            except Exception:
                                update = None
                            if isinstance(update, dict) and \
                                    update.get("type") == "node_stopping":
                                action = "restarting" if update.get("reason") == "restart" \
                                    else "shutting down"
                                if _mark_backend_offline(
                                        bid, "Backend {}".format(action)):
                                    _broadcast_backends()
                            elif isinstance(update, dict) and \
                                    update.get("type") == "sessions" and \
                                    _cache_remote_payload(bid, "sessions", update):
                                _broadcast_backends()
                        await dst.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
            finally:
                try:
                    await dst.close()
                except Exception:
                    pass

        await asyncio.gather(pump(ws_server, ws_client),
                             pump(ws_client, ws_server, from_backend=True),
                             return_exceptions=True)
    except Exception as exc:
        log.warning("ws proxy to %s failed after handshake: %s", ws_url, _connection_error(exc))
    finally:
        _proxy_websockets.discard((bid, ws_server))
        if not ws_server.closed:
            try:
                await ws_server.close()
            except Exception:
                pass
        if ws_client is not None and not ws_client.closed:
            try:
                await ws_client.close()
            except Exception:
                pass
    return ws_server


async def close_proxy_websockets(bid=None, reason: str = "Puppy state restored") -> None:
    """Revoke proxied channels globally for restore, or for one edited backend."""
    sockets = [ws for socket_bid, ws in list(_proxy_websockets)
               if bid is None or socket_bid == bid]
    if not sockets:
        return

    close_message = str(reason or "Backend connection changed").encode("utf-8")[:123]

    async def close_socket(ws) -> None:
        try:
            await ws.close(code=1012, message=close_message)
        except Exception:
            pass

    try:
        await asyncio.wait_for(
            asyncio.gather(*(close_socket(ws) for ws in sockets)), timeout=3)
    except asyncio.TimeoutError:
        pass


async def close_client() -> None:
    if _client is not None and not _client.closed:
        await _client.close()


def register(app: web.Application) -> None:
    app.router.add_get("/api/backends", h_list)
    app.router.add_post("/api/backends", h_add)
    app.router.add_patch("/api/backends/{bid:\\d+}", h_patch)
    app.router.add_delete("/api/backends/{bid:\\d+}", h_delete)
    app.router.add_post("/api/backends/{bid:\\d+}/test", h_test)
    app.router.add_post("/api/backends/{bid:\\d+}/upgrade", h_upgrade)
    app.router.add_route("*", "/api/b/{bid:\\d+}/{tail:.+}", proxy)
