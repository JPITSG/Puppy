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

import aiohttp
from aiohttp import WSMsgType, web

from puppy import __version__, config, db, protocol, upgrade_contract

log = logging.getLogger("puppy.backends")

_client = None
_upgrades_in_progress = set()

HOP_HEADERS = {"host", "connection", "upgrade", "sec-websocket-key", "sec-websocket-version",
               "sec-websocket-extensions", "sec-websocket-protocol", "cookie", "x-puppy-token",
               "content-length", "transfer-encoding", "accept-encoding"}


def client() -> aiohttp.ClientSession:
    global _client
    if _client is None or _client.closed:
        _client = aiohttp.ClientSession()
    return _client


def list_backends() -> list:
    rows = db.query(
        "SELECT id,name,url,protocol,capabilities,remote_version,role,created_at "
        "FROM backends ORDER BY id")
    out = []
    for row in rows:
        item = dict(row)
        try:
            caps = json.loads(item.get("capabilities") or "[]")
        except Exception:
            caps = []
        item["capabilities"] = caps if isinstance(caps, list) else []
        out.append(item)
    return out


def get_backend(bid: int):
    row = db.query_one("SELECT * FROM backends WHERE id=?", (bid,))
    return dict(row) if row else None


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


async def probe_backend(url: str, token: str, timeout: float = 8.0) -> dict:
    """Authenticate and negotiate metadata with a prospective backend."""
    try:
        async with client().get(
                f"{url.rstrip('/')}/api/ping",
                headers={"X-Puppy-Token": token},
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False) as response:
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
            return {"ok": True, "status": response.status, "remote": remote}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "connection timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _metadata(remote: dict) -> tuple:
    return (
        int(remote.get("protocol", 0)),
        json.dumps(remote.get("capabilities") or [], separators=(",", ":")),
        str(remote.get("version") or "")[:40],
        str(remote.get("role") or "")[:24],
    )


def _store_metadata(bid: int, remote: dict) -> None:
    api_protocol, capabilities, remote_version, role = _metadata(remote)
    db.execute(
        "UPDATE backends SET protocol=?,capabilities=?,remote_version=?,role=? WHERE id=?",
        (api_protocol, capabilities, remote_version, role, bid))


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
    body = await request.json()
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip().rstrip("/")
    token = (body.get("token") or "").strip()
    if not url.startswith(("http://", "https://")) or not token:
        return web.json_response({"error": "http(s) url and token required"}, status=400)
    result = await probe_backend(url, token)
    if not result["ok"]:
        return web.json_response({"error": result.get("error", "backend test failed"),
                                  "status": result.get("status")}, status=400)
    remote = result["remote"]
    name = name or str(remote.get("name") or "").strip() or "backend"
    api_protocol, capabilities, remote_version, role = _metadata(remote)
    bid = db.execute(
        "INSERT INTO backends(name,url,token,protocol,capabilities,remote_version,role,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (name[:80], url, token, api_protocol, capabilities, remote_version, role, time.time()))
    return web.json_response({"ok": True, "id": bid, "remote": remote})


async def h_delete(request: web.Request):
    bid = int(request.match_info["bid"])
    db.execute("DELETE FROM backends WHERE id=?", (bid,))
    return web.json_response({"ok": True})


async def h_test(request: web.Request):
    bid = int(request.match_info["bid"])
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    result = await probe_backend(be["url"], be["token"])
    if result["ok"]:
        _store_metadata(bid, result["remote"])
    return web.json_response(result)


async def h_upgrade(request: web.Request):
    bid = int(request.match_info["bid"])
    be = get_backend(bid)
    if be is None:
        return web.json_response({"error": "unknown backend"}, status=404)
    if bid in _upgrades_in_progress:
        return web.json_response({"error": "an upgrade is already in progress for this backend"},
                                 status=409)
    _upgrades_in_progress.add(bid)
    try:
        current = await probe_backend(be["url"], be["token"])
        if not current["ok"]:
            return web.json_response({"error": current.get("error", "backend is unavailable")},
                                     status=502)
        remote = current["remote"]
        _store_metadata(bid, remote)
        if protocol.UPGRADE_CAPABILITY not in (remote.get("capabilities") or []) or \
                not (remote.get("upgrade") or {}).get("supported"):
            return web.json_response({"error": "backend does not support safe remote upgrades"},
                                     status=409)
        try:
            if upgrade_contract.version_key(__version__) <= \
                    upgrade_contract.version_key(str(remote.get("version") or "")):
                return web.json_response({"error": "backend is already at this version or newer"},
                                         status=409)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=409)

        loop = asyncio.get_running_loop()
        try:
            payload, manifest = await loop.run_in_executor(None, _build_upgrade_payload)
        except Exception as exc:
            log.exception("backend release build failed")
            return web.json_response({"error": str(exc)}, status=500)
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
                    timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False) as response:
                try:
                    accepted = await response.json()
                except Exception:
                    accepted = {"error": "backend returned a non-JSON upgrade response"}
                if response.status != 202 or accepted.get("accepted") is not True:
                    return web.json_response(
                        {"error": accepted.get("error") or "backend rejected the upgrade"},
                        status=(response.status if 400 <= response.status < 600 and
                                response.status not in (401, 403) else 502))
        except asyncio.TimeoutError:
            return web.json_response({"error": "backend timed out while staging the upgrade"}, status=504)
        except Exception as exc:
            return web.json_response({"error": "backend upgrade request failed: {}".format(exc)},
                                     status=502)

        deadline = time.monotonic() + 90
        last_error = "backend did not return after its upgrade restart"
        while time.monotonic() < deadline:
            await asyncio.sleep(0.75)
            checked = await probe_backend(be["url"], be["token"], timeout=2.5)
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
                return web.json_response({
                    "ok": True, "from_version": remote.get("version"),
                    "to_version": upgraded.get("version"), "sha256": manifest["sha256"],
                    "remote": upgraded,
                })
            if last.get("target_version") == manifest["version"] and \
                    last.get("state") in ("rolled-back", "failed"):
                _store_metadata(bid, upgraded)
                return web.json_response({
                    "error": "backend rolled back the upgrade: {}".format(
                        last.get("error") or "candidate health check failed")
                }, status=502)
            last_error = "backend returned version {} instead of {}".format(
                upgraded.get("version", "?"), manifest["version"])
        return web.json_response({"error": last_error}, status=504)
    finally:
        _upgrades_in_progress.discard(bid)


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
        return await _proxy_ws(request, target, headers)

    try:
        body = await request.read()
        async with client().request(request.method, target, headers=headers,
                                    data=body if body else None,
                                    timeout=aiohttp.ClientTimeout(total=60)) as r:
            payload = await r.read()
            resp = web.Response(status=r.status, body=payload,
                                content_type=r.content_type or "application/json")
            return resp
    except asyncio.TimeoutError:
        return web.json_response({"error": f"backend '{be['name']}' timeout"}, status=504)
    except Exception as e:
        return web.json_response({"error": f"backend '{be['name']}' unreachable: {e}"}, status=502)


async def _proxy_ws(request: web.Request, target: str, headers: dict):
    ws_server = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 22)
    await ws_server.prepare(request)
    ws_url = "ws" + target[4:] if target.startswith("http") else target
    try:
        async with client().ws_connect(ws_url, headers={"X-Puppy-Token": headers["X-Puppy-Token"]},
                                       heartbeat=30, max_msg_size=1 << 22) as ws_client:
            async def pump(src, dst):
                async for msg in src:
                    if msg.type == WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
                try:
                    await dst.close()
                except Exception:
                    pass

            await asyncio.gather(pump(ws_server, ws_client), pump(ws_client, ws_server),
                                 return_exceptions=True)
    except Exception as e:
        log.warning("ws proxy to %s failed: %s", ws_url, e)
        if not ws_server.closed:
            try:
                await ws_server.close(message=str(e).encode()[:120])
            except Exception:
                pass
    return ws_server


async def close_client() -> None:
    global _client
    if _client is not None and not _client.closed:
        await _client.close()


def register(app: web.Application) -> None:
    app.router.add_get("/api/backends", h_list)
    app.router.add_post("/api/backends", h_add)
    app.router.add_delete("/api/backends/{bid:\\d+}", h_delete)
    app.router.add_post("/api/backends/{bid:\\d+}/test", h_test)
    app.router.add_post("/api/backends/{bid:\\d+}/upgrade", h_upgrade)
    app.router.add_route("*", "/api/b/{bid:\\d+}/{tail:.+}", proxy)
