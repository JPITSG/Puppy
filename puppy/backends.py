"""Multi-backend support. Backend id 0 = this instance. Remote backends are other
puppy instances; the browser stays single-origin and this instance proxies both
HTTP and websocket traffic to them, authenticated with their api_token."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import WSMsgType, web

from puppy import __version__, config, db, protocol, runner, tls, upgrade_contract

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

PROXY_CONNECT_TIMEOUT = 8.0
PROXY_TOTAL_TIMEOUT = 60.0
AUTO_UPGRADE_INTERVAL = 8.0
AUTO_UPGRADE_FAILURE_RETRY = 30.0
AUTO_UPGRADE_CURRENT_RECHECK = 5 * 60.0

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


def list_backends() -> list:
    rows = db.query(
        "SELECT id,name,url,protocol,capabilities,remote_version,role,tls_fingerprint,"
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
        item["auto_upgrade"] = bool(item.get("auto_upgrade"))
        item["upgrade_in_progress"] = item["id"] in _upgrades_in_progress
        out.append(item)
    return out


def get_backend(bid: int):
    row = db.query_one("SELECT * FROM backends WHERE id=?", (bid,))
    return dict(row) if row else None


def _backend_capabilities(backend: dict) -> list:
    value = backend.get("capabilities") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    return value if isinstance(value, list) else []


def _broadcast_backends() -> None:
    runner.broadcast_update({"type": "backends", "backends": list_backends()})


def _wake_auto_upgrade() -> None:
    if _auto_upgrade_wake is not None:
        _auto_upgrade_wake.set()


def reset_auto_upgrade_schedule() -> None:
    """Forget timing state after a database restore and evaluate its policies."""
    _auto_upgrade_retry_after.clear()
    _auto_upgrade_checked_at.clear()
    _auto_upgrade_last_errors.clear()
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
    return changed


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

async def h_list(request: web.Request):
    return web.json_response({"backends": list_backends()})


async def h_add(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid backend request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid backend request"}, status=400)
    name = (body.get("name") or "").strip()
    token = (body.get("token") or "").strip()
    auto_upgrade = body.get("auto_upgrade", False)
    if type(auto_upgrade) is not bool:
        return web.json_response({"error": "auto-upgrade must be on or off"}, status=400)
    try:
        url = _base_url(body.get("url"))
        tls_fingerprint = tls.normalize_fingerprint(
            body.get("tls_fingerprint") or body.get("tls_sha256"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not token:
        return web.json_response({"error": "API token is required"}, status=400)
    if tls_fingerprint and urlsplit(url).scheme != "https":
        return web.json_response(
            {"error": "a TLS certificate fingerprint requires an https:// URL"}, status=400)
    result = await probe_backend(url, token, tls_fingerprint)
    if not result["ok"]:
        return web.json_response({"error": result.get("error", "backend test failed"),
                                  "status": result.get("status")}, status=400)
    remote = result["remote"]
    name = name or str(remote.get("name") or "").strip() or "backend"
    if auto_upgrade and (remote.get("role") != "backend" or
                         protocol.UPGRADE_CAPABILITY not in (remote.get("capabilities") or [])):
        return web.json_response({
            "error": "automatic upgrades require an upgrade-capable headless backend"
        }, status=409)
    api_protocol, capabilities, remote_version, role = _metadata(remote)
    bid = db.execute(
        "INSERT INTO backends(name,url,token,protocol,capabilities,remote_version,role,"
        "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name[:80], url, token, api_protocol, capabilities, remote_version, role,
         tls_fingerprint, int(auto_upgrade), time.time()))
    _broadcast_backends()
    if auto_upgrade:
        _wake_auto_upgrade()
    return web.json_response({"ok": True, "id": bid, "remote": remote,
                              "auto_upgrade": auto_upgrade})


async def h_patch(request: web.Request):
    bid = int(request.match_info["bid"])
    backend = get_backend(bid)
    if backend is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid backend request"}, status=400)
    if not isinstance(body, dict) or type(body.get("auto_upgrade")) is not bool:
        return web.json_response({"error": "auto-upgrade must be on or off"}, status=400)
    enabled = body["auto_upgrade"]
    if enabled and (backend.get("role") != "backend" or
                    protocol.UPGRADE_CAPABILITY not in _backend_capabilities(backend)):
        return web.json_response({
            "error": "automatic upgrades require an upgrade-capable headless backend"
        }, status=409)
    db.execute("UPDATE backends SET auto_upgrade=? WHERE id=?", (int(enabled), bid))
    _auto_upgrade_retry_after.pop(bid, None)
    _auto_upgrade_checked_at.pop(bid, None)
    _auto_upgrade_last_errors.pop(bid, None)
    _broadcast_backends()
    if enabled:
        _wake_auto_upgrade()
    updated = next(item for item in list_backends() if item["id"] == bid)
    return web.json_response({"ok": True, "backend": updated})


async def h_delete(request: web.Request):
    bid = int(request.match_info["bid"])
    db.execute("DELETE FROM backends WHERE id=?", (bid,))
    _auto_upgrade_retry_after.pop(bid, None)
    _auto_upgrade_checked_at.pop(bid, None)
    _auto_upgrade_last_errors.pop(bid, None)
    _broadcast_backends()
    return web.json_response({"ok": True})


async def h_test(request: web.Request):
    bid = int(request.match_info["bid"])
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    result = await probe_backend(be["url"], be["token"], be["tls_fingerprint"])
    if result["ok"]:
        _store_metadata(bid, result["remote"])
        _broadcast_backends()
    return web.json_response(result)


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
    if bid in _upgrades_in_progress:
        raise BackendUpgradeError("an upgrade is already in progress for this backend", 409)
    _upgrades_in_progress.add(bid)
    _broadcast_backends()
    try:
        remote = remote_hint
        if remote is None:
            current = await probe_backend(be["url"], be["token"], be["tls_fingerprint"])
            if not current["ok"]:
                raise BackendUpgradeError(
                    current.get("error", "backend is unavailable"), 502)
            remote = current["remote"]
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
        target = be["url"].rstrip("/") + protocol.UPGRADE_API_PATH
        try:
            async with client().post(
                    target, data=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False,
                    ssl=_ssl_pin(be["tls_fingerprint"])) as response:
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
        except BackendUpgradeError:
            raise
        except asyncio.TimeoutError as exc:
            raise BackendUpgradeError(
                "backend timed out while staging the upgrade", 504) from exc
        except Exception as exc:
            raise BackendUpgradeError(
                "backend upgrade request failed: {}".format(_connection_error(exc)), 502) from exc

        deadline = time.monotonic() + 90
        last_error = "backend did not return after its upgrade restart"
        while time.monotonic() < deadline:
            await asyncio.sleep(0.75)
            checked = await probe_backend(be["url"], be["token"], be["tls_fingerprint"],
                                          timeout=2.5)
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
        raise BackendUpgradeError(last_error, 504)
    finally:
        _upgrades_in_progress.discard(bid)
        _broadcast_backends()


async def h_upgrade(request: web.Request):
    try:
        result = await upgrade_backend(int(request.match_info["bid"]))
        return web.json_response(result)
    except BackendUpgradeError as exc:
        return web.json_response(exc.payload(), status=exc.status)


async def _legacy_auto_readiness(backend: dict) -> dict:
    """Conservatively infer idleness for the first upgrade of older nodes.

    Their POST remains authoritative for terminal activity and last-moment
    races. This check prevents artifact work while a reported session is busy.
    """
    target = backend["url"].rstrip("/") + "/api/sessions"
    try:
        async with client().get(
                target, headers={"X-Puppy-Token": backend["token"]},
                timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False,
                ssl=_ssl_pin(backend["tls_fingerprint"])) as response:
            try:
                payload = await response.json()
            except Exception:
                payload = None
            if response.status != 200 or not isinstance(payload, dict) or \
                    not isinstance(payload.get("sessions"), list):
                return {
                    "ready": False, "state": "checking",
                    "reason": "could not verify legacy backend session activity",
                }
    except Exception as exc:
        return {
            "ready": False, "state": "checking",
            "reason": "could not verify legacy backend idleness: {}".format(
                _connection_error(exc)),
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
        probe_backend(item["url"], item["token"], item["tls_fingerprint"], timeout=5)
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
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, message)
            continue
        if not current["ok"]:
            message = current.get("error", "backend is unavailable")
            _auto_upgrade_retry_after[bid] = now + AUTO_UPGRADE_FAILURE_RETRY
            _auto_note_error(backend, message)
            continue
        remote = current["remote"]
        metadata_changed = _store_metadata(bid, remote) or metadata_changed
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


async def proxy(request: web.Request):
    bid = int(request.match_info["bid"])
    tail = request.match_info["tail"]
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    target = f"{be['url']}/api/{tail}"
    if request.query_string:
        target += "?" + request.query_string
    headers = {"X-Puppy-Token": be["token"]}
    for k, v in request.headers.items():
        if k.lower() not in HOP_HEADERS:
            headers.setdefault(k, v)

    if _is_ws(request):
        return await _proxy_ws(request, target, headers, be["tls_fingerprint"])

    try:
        body = await request.read()
        async with client().request(request.method, target, headers=headers,
                                    data=body if body else None,
                                    timeout=aiohttp.ClientTimeout(
                                        total=PROXY_TOTAL_TIMEOUT,
                                        connect=PROXY_CONNECT_TIMEOUT,
                                        sock_connect=PROXY_CONNECT_TIMEOUT),
                                    allow_redirects=False,
                                    ssl=_ssl_pin(be["tls_fingerprint"])) as r:
            payload = await r.read()
            if request.method == "GET" and tail in ("ping", "node") and r.status == 200:
                try:
                    remote = _normalize_peer(json.loads(payload.decode("utf-8")))
                    if remote.get("ok") is True and _store_metadata(bid, remote):
                        _broadcast_backends()
                except Exception:
                    pass  # proxy the authoritative response; metadata caching is best-effort
            resp = web.Response(status=r.status, body=payload,
                                content_type=r.content_type or "application/json")
            return resp
    except asyncio.TimeoutError:
        return web.json_response({"error": f"backend '{be['name']}' timeout"}, status=504)
    except Exception as e:
        return web.json_response(
            {"error": f"backend '{be['name']}' unreachable: {_connection_error(e)}"}, status=502)


async def _proxy_ws(request: web.Request, target: str, headers: dict,
                    tls_fingerprint: str):
    ws_url = "ws" + target[4:] if target.startswith("http") else target
    ws_client = None
    try:
        ws_client = await asyncio.wait_for(
            client().ws_connect(
                ws_url, headers={"X-Puppy-Token": headers["X-Puppy-Token"]},
                heartbeat=30, max_msg_size=1 << 22,
                ssl=_ssl_pin(tls_fingerprint)),
            timeout=PROXY_CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("ws proxy to %s timed out before handshake", ws_url)
        return web.json_response({"error": "backend websocket connection timed out"}, status=504)
    except Exception as exc:
        error = _connection_error(exc)
        log.warning("ws proxy to %s failed before handshake: %s", ws_url, error)
        return web.json_response(
            {"error": "backend websocket unreachable: {}".format(error)}, status=502)

    if request.app.get("puppy_snapshot_busy") == "restore":
        await ws_client.close()
        return web.json_response({"error": "Puppy restore in progress"}, status=503)

    ws_server = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 22)
    try:
        await ws_server.prepare(request)
        _proxy_websockets.add(ws_server)

        async def pump(src, dst):
            try:
                async for msg in src:
                    if msg.type == WSMsgType.TEXT:
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

        await asyncio.gather(pump(ws_server, ws_client), pump(ws_client, ws_server),
                             return_exceptions=True)
    except Exception as exc:
        log.warning("ws proxy to %s failed after handshake: %s", ws_url, _connection_error(exc))
    finally:
        _proxy_websockets.discard(ws_server)
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


async def close_proxy_websockets() -> None:
    """Revoke live proxied channels before imported auth/backend state takes effect."""
    sockets = list(_proxy_websockets)
    if not sockets:
        return

    async def close_socket(ws) -> None:
        try:
            await ws.close(code=1012, message=b"Puppy state restored")
        except Exception:
            pass

    try:
        await asyncio.wait_for(
            asyncio.gather(*(close_socket(ws) for ws in sockets)), timeout=3)
    except asyncio.TimeoutError:
        pass


async def close_client() -> None:
    global _client
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
