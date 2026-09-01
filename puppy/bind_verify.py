"""Browser-reachable, one-use verification for full-WebUI bind changes.

Changing a listener blindly can lock an administrator out. A proposed literal
IP and port are therefore exposed briefly (or proved through the current
listener), and the originating browser must fetch a nonce there before the
endpoint can be committed to config.json.
"""
from __future__ import annotations

import asyncio
import errno
import ipaddress
import json
import logging
import math
import secrets
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from aiohttp import web

from puppy import config, listener_handoff, web_tls

VERIFY_TTL = 60
VERIFY_PREFIX = "/api/settings/bind/verify/"
MAX_PENDING = 16

log = logging.getLogger("puppy.bind_verify")


class BindVerificationError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_bind_ip(value) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 64 or "%" in raw:
        raise BindVerificationError("enter an IPv4 or IPv6 address")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError as exc:
        raise BindVerificationError(
            "bind address must be a literal IPv4 or IPv6 address") from exc


def normalize_bind_port(value) -> int:
    if isinstance(value, bool):
        raise BindVerificationError(
            "bind port must be a whole number between 1 and 65535")
    if isinstance(value, str):
        raw = value.strip()
        if not raw or len(raw) > 5 or not raw.isascii() or not raw.isdecimal():
            raise BindVerificationError(
                "bind port must be a whole number between 1 and 65535")
        port = int(raw)
    elif isinstance(value, int):
        port = value
    elif isinstance(value, float) and math.isfinite(value) and value == int(value):
        # JSON/API numeric inputs may represent a whole port as 10888.0.
        port = int(value)
    else:
        raise BindVerificationError(
            "bind port must be a whole number between 1 and 65535")
    if not 1 <= port <= 65535:
        raise BindVerificationError(
            "bind port must be a whole number between 1 and 65535")
    return port


def _origin(value: str) -> Tuple[str, str, int, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 512:
        raise BindVerificationError("browser origin is missing")
    try:
        encoded = raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BindVerificationError("browser origin is invalid") from exc
    if any(byte < 0x21 or byte > 0x7e for byte in encoded):
        raise BindVerificationError("browser origin is invalid")
    try:
        parsed = urlsplit(raw)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise BindVerificationError("browser origin is invalid") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or \
            parsed.username is not None or parsed.password is not None or \
            parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise BindVerificationError("browser origin is invalid")
    canonical_host = _canonical_hostname(parsed.hostname)
    return parsed.scheme, canonical_host, port, raw.rstrip("/")


def _canonical_hostname(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return str(value).lower().rstrip(".")


def _authority(host: str, port: int) -> str:
    display = "[{}]".format(host) if ":" in host else host
    return "{}:{}".format(display, port)


def _wildcard_probe_host(target, origin_host: str,
                         connected_host: Optional[str]) -> str:
    """Choose a concrete browser target with the wildcard's IP family.

    A hostname could resolve to the opposite family and falsely prove a bind
    that will disappear after restart. Prefer a literal origin when possible;
    otherwise use the local address that accepted the prepare request.
    """
    for candidate in (origin_host, connected_host):
        try:
            parsed = ipaddress.ip_address(str(candidate or "").split("%", 1)[0])
        except ValueError:
            continue
        if parsed.version == target.version and not parsed.is_unspecified and \
                not parsed.is_link_local:
            return str(parsed)
    raise BindVerificationError(
        "cannot safely verify an IPv{} wildcard from this connection; "
        "enter a concrete IPv{} address instead".format(
            target.version, target.version))


def _request_authority(value: str, scheme: str = "http") -> Optional[Tuple[str, int]]:
    try:
        parsed = urlsplit("http://" + str(value or ""))
        if not parsed.hostname or parsed.username is not None or \
                parsed.password is not None or parsed.path not in ("", "/") or \
                parsed.query or parsed.fragment:
            return None
        return _canonical_hostname(parsed.hostname), parsed.port or (
            443 if scheme == "https" else 80)
    except ValueError:
        return None


def _runtime_can_conflict(app: web.Application, target, port: int) -> bool:
    """Whether EADDRINUSE can plausibly be caused by Puppy's live listener."""
    runtime = app.get("puppy_runtime_web") or {}
    try:
        active = ipaddress.ip_address(str(runtime.get("host", "")))
        active_port = int(runtime.get("port"))
    except (TypeError, ValueError):
        return False
    if active_port != port or active.version != target.version:
        return False
    if target.is_unspecified:
        return True
    return active.is_unspecified or active == target


def _entries(app: web.Application) -> Dict[str, dict]:
    return app.setdefault("puppy_bind_verifications", {})


def _close_entry(entry: Optional[dict]) -> None:
    if not entry:
        return
    timer = entry.get("timer")
    if timer is not None:
        timer.cancel()
    server = entry.get("server")
    if server is not None:
        server.close()


def _discard(app: web.Application, token: str) -> Optional[dict]:
    entry = _entries(app).pop(token, None)
    _close_entry(entry)
    return entry


def _expire(app: web.Application, token: str) -> None:
    _discard(app, token)


def _cors(entry: dict) -> dict:
    return {
        "Access-Control-Allow-Origin": entry["origin"],
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Private-Network": "true",
        "Cache-Control": "no-store",
        "Content-Type": "application/json",
        "Vary": "Origin",
        "X-Content-Type-Options": "nosniff",
    }


def _origin_matches(entry: dict, supplied: str) -> bool:
    if supplied:
        try:
            scheme, host, port, _raw = _origin(supplied)
        except BindVerificationError:
            return False
        return (scheme, host, port) == entry["origin_key"]
    # Browsers commonly omit Origin on a same-origin GET. That is safe only
    # when the probe authority itself is the origin that prepared the nonce.
    _scheme, host, port = entry["origin_key"]
    return (entry["probe_host"], entry["port"]) == (host, port)


def probe_response(app: web.Application, token: str, method: str,
                   host_header: str, origin_header: str) -> Tuple[int, dict, bytes]:
    entry = _entries(app).get(token)
    if entry is None or time.monotonic() >= entry["expires"]:
        _discard(app, token)
        return 404, {"Cache-Control": "no-store"}, b'{"error":"verification expired"}'
    if _request_authority(host_header, entry["origin_key"][0]) != \
            (entry["probe_host"], entry["port"]) or \
            not _origin_matches(entry, origin_header):
        return 403, {"Cache-Control": "no-store"}, b'{"error":"verification target mismatch"}'
    headers = _cors(entry)
    if method == "OPTIONS":
        return 204, headers, b""
    if method != "GET":
        return 405, headers, b'{"error":"method not allowed"}'
    entry["verified"] = True
    body = json.dumps({"ok": True, "token": token}, separators=(",", ":")).encode("utf-8")
    return 200, headers, body


async def _raw_probe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     app: web.Application, token: str) -> None:
    status = 400
    headers = {"Cache-Control": "no-store"}
    body = b'{"error":"bad request"}'
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
        if len(raw) > 16 * 1024:
            raise ValueError("request headers are too large")
        lines = raw.decode("latin-1").split("\r\n")
        method, target, _version = lines[0].split(" ", 2)
        request_headers = {}
        for line in lines[1:]:
            if not line:
                break
            key, separator, value = line.partition(":")
            if not separator:
                raise ValueError("malformed header")
            request_headers[key.strip().lower()] = value.strip()
        if urlsplit(target).path == VERIFY_PREFIX + token:
            status, headers, body = probe_response(
                app, token, method.upper(), request_headers.get("host", ""),
                request_headers.get("origin", ""))
        else:
            status, body = 404, b'{"error":"not found"}'
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            asyncio.TimeoutError, OSError, UnicodeError, ValueError):
        pass
    reason = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed"}.get(status, "Error")
    response_headers = {
        **headers,
        "Connection": "close",
        "Content-Length": str(len(body)),
    }
    response = "HTTP/1.1 {} {}\r\n{}\r\n\r\n".format(
        status, reason, "\r\n".join(
            "{}: {}".format(key, value) for key, value in response_headers.items()))
    try:
        writer.write(response.encode("latin-1") + body)
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def prepare(app: web.Application, user: str, bind_ip, origin: str,
                  port, connected_host: Optional[str] = None,
                  target_scheme=None, https_source=None,
                  certificate_path=None, private_key_path=None) -> dict:
    host = normalize_bind_ip(bind_ip)
    port = normalize_bind_port(port)
    scheme, origin_host, origin_port, canonical_origin = _origin(origin)
    if listener_handoff.queued_by(app):
        raise BindVerificationError(
            "a listener restart is already queued; wait for Puppy to reconnect",
            status=409)
    runtime = app.get("puppy_runtime_web") or {}
    runtime_scheme = str(runtime.get("scheme") or "http")
    if scheme != runtime_scheme:
        raise BindVerificationError(
            "direct bind verification is unavailable through a protocol-changing proxy; "
            "the bind endpoint was not changed")
    entries = _entries(app)
    for old_token, old_entry in list(entries.items()):
        if old_entry.get("user") == user or time.monotonic() >= old_entry.get("expires", 0):
            _discard(app, old_token)
    if len(entries) >= MAX_PENDING:
        raise BindVerificationError("too many bind verifications are pending", status=429)

    parsed_ip = ipaddress.ip_address(host)
    probe_host = _wildcard_probe_host(
        parsed_ip, origin_host, connected_host) if parsed_ip.is_unspecified else host
    try:
        current_transport = web_tls.load_state()
        transport = web_tls.prepare_change(
            target_scheme if target_scheme is not None else current_transport["scheme"],
            https_source if https_source is not None else
            current_transport["https_source"],
            certificate_path, private_key_path,
            (origin_host, probe_host, host))
    except web_tls.WebTLSError as exc:
        raise BindVerificationError(str(exc)) from exc
    token = secrets.token_urlsafe(24)
    expires = time.monotonic() + VERIFY_TTL
    entry = {
        "user": user,
        "host": host,
        "port": port,
        "expected_host": str(config.get("web.host", "0.0.0.0")),
        "expected_port": int(config.get("web.port", port)),
        "probe_host": probe_host,
        "origin": canonical_origin,
        "origin_key": (scheme, origin_host, origin_port),
        "expires": expires,
        "verified": False,
        "server": None,
        "timer": None,
        "transport": transport,
    }
    entries[token] = entry

    async def connected(reader, writer):
        await _raw_probe(reader, writer, app, token)

    try:
        ssl_context = app.get("puppy_runtime_ssl_context") if scheme == "https" else None
        if scheme == "https" and ssl_context is None:
            raise BindVerificationError("the active HTTPS listener has no TLS context")
        entry["server"] = await asyncio.start_server(
            connected, host=host, port=port, limit=16 * 1024,
            ssl=ssl_context, ssl_handshake_timeout=5 if ssl_context else None)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE or not _runtime_can_conflict(
                app, parsed_ip, port):
            _discard(app, token)
            message = exc.strerror or str(exc)
            raise BindVerificationError(
                "cannot bind {}: {}".format(_authority(host, port), message)) from exc
        # The current wildcard/same-address listener commonly owns this socket.
        # Its public nonce route will answer only if the proposed address really
        # reaches this Puppy process; an unrelated listener cannot forge it.
        entry["server"] = None
    except BaseException:
        _discard(app, token)
        raise

    entry["timer"] = asyncio.get_running_loop().call_later(
        VERIFY_TTL, _expire, app, token)
    base_url = scheme + "://" + _authority(probe_host, port)
    next_url = transport.scheme + "://" + _authority(probe_host, port)
    return {
        "ok": True,
        "token": token,
        "host": host,
        "port": port,
        "scheme": transport.scheme,
        "https_source": transport.https_source,
        "verify_url": base_url + VERIFY_PREFIX + token,
        "next_url": next_url + "/",
        "expires_in": VERIFY_TTL,
    }


async def commit(app: web.Application, user: str, token: str) -> dict:
    entry = _entries(app).get(str(token or ""))
    if entry is None or time.monotonic() >= entry["expires"] or entry["user"] != user:
        _discard(app, str(token or ""))
        raise BindVerificationError("bind verification expired; try again", status=409)
    if not entry["verified"]:
        raise BindVerificationError(
            "the proposed bind endpoint was not reached by this browser", status=409)
    entry = _discard(app, str(token))
    server = entry.get("server") if entry else None
    if server is not None:
        try:
            await server.wait_closed()
        except OSError:
            pass
    old_host = str(config.get("web.host", "0.0.0.0"))
    old_port = int(config.get("web.port", entry["port"]))
    if listener_handoff.queued_by(app):
        raise BindVerificationError(
            "a listener restart is already queued; wait for Puppy to reconnect",
            status=409)
    if (old_host, old_port) != (entry["expected_host"], entry["expected_port"]):
        raise BindVerificationError(
            "the listener setting changed while it was being verified; try again",
            status=409)
    previous_config = config.export_data()
    try:
        updated = config.export_data()
        updated["web"]["host"] = entry["host"]
        updated["web"]["port"] = entry["port"]
        config.replace_all(updated)
        try:
            web_tls.commit_change(entry["transport"])
        except Exception:
            config.replace_all(previous_config)
            raise
    except web_tls.WebTLSError as exc:
        raise BindVerificationError(
            "verification succeeded but the listener setting could not be saved: {}".
            format(exc), status=500) from exc
    except (OSError, ValueError) as exc:
        raise BindVerificationError(
            "verification succeeded but the listener setting could not be saved", status=500) from exc
    configured = web_tls.configured_listener(entry["host"], entry["port"])
    old_transport = entry["transport"].expected_state
    old_scheme = old_transport["scheme"]
    log.info("verified WebUI listener changed from %s://%s to %s://%s by %r",
             old_scheme, _authority(old_host, old_port), configured["scheme"],
             _authority(entry["host"], entry["port"]), user)
    runtime_web = app.get("puppy_runtime_web") or {
        "host": old_host, "port": old_port, "scheme": old_scheme,
        "https_source": old_transport["https_source"], "certificate_sha256": ""}
    restart_required = web_tls.listener_key(configured) != web_tls.listener_key(runtime_web)
    result = {
        "ok": True,
        **configured,
        "previous_host": old_host,
        "previous_port": old_port,
        "restart_required": restart_required,
        "next_url": configured["scheme"] + "://" +
                    _authority(entry["probe_host"], entry["port"]) + "/",
    }
    # Any newer verified listener choice supersedes an older unclaimed
    # browser handoff. API-token callers retain the existing manual activation
    # contract; only a logged-in browser can receive a new-origin web session.
    if restart_required and user != "@token":
        try:
            result["handoff"] = listener_handoff.create(
                app, user, entry["host"], entry["port"], entry["probe_host"],
                entry["origin"], configured["scheme"],
                configured["https_source"], configured["certificate_sha256"])
        except (listener_handoff.ListenerHandoffError, OSError) as exc:
            # The listener setting is already durably committed. Return that
            # fact rather than turning a handoff-storage problem into an
            # ambiguous interrupted save.
            result["handoff_error"] = str(exc) if isinstance(
                exc, listener_handoff.ListenerHandoffError) else \
                "could not create private listener handoff state"
    else:
        listener_handoff.discard()
    return result


async def close_all(app: web.Application) -> None:
    servers = []
    for token in list(_entries(app)):
        entry = _discard(app, token)
        if entry and entry.get("server") is not None:
            servers.append(entry["server"])
    for server in servers:
        try:
            await server.wait_closed()
        except OSError:
            pass


async def h_probe(request: web.Request) -> web.Response:
    status, headers, body = probe_response(
        request.app, request.match_info["token"], request.method,
        request.host, request.headers.get("Origin", ""))
    return web.Response(status=status, headers=headers, body=body)
