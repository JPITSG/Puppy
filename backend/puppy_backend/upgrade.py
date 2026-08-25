"""Signed, staged, launcher-managed upgrades for the headless zipapp."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
import zipfile

from aiohttp import web

from puppy import __version__, config, protocol, runner, terminal, upgrade_contract

from . import build_info

log = logging.getLogger("puppy.backend.upgrade")

_upgrade_lock = asyncio.Lock()
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _runtime(tls_enabled=None) -> dict:
    configured = bool(config.get("backend.remote_upgrade_enabled", False))
    if tls_enabled is None:
        tls_enabled = str(config.get("backend.tls_mode", "disabled")) != "disabled"
    launcher = os.environ.get("PUPPY_BACKEND_LAUNCHER_PROTOCOL")
    launcher_features = {
        item.strip() for item in
        os.environ.get("PUPPY_BACKEND_LAUNCHER_FEATURES", "").split(",") if item.strip()
    }
    artifact_raw = os.environ.get("PUPPY_BACKEND_MANAGED_ARTIFACT", "")
    marker_raw = os.environ.get("PUPPY_BACKEND_UPGRADE_MARKER", "")
    status_raw = os.environ.get("PUPPY_BACKEND_UPGRADE_STATUS", "")
    reason = ""
    artifact = marker = status_path = None
    try:
        if not configured:
            reason = "disabled by configuration"
        elif build_info.ARTIFACT_KIND != "zipapp":
            reason = "only zipapp deployments can upgrade remotely"
        elif launcher != str(upgrade_contract.LAUNCHER_PROTOCOL):
            reason = "external upgrade launcher is not active"
        elif tls_enabled and upgrade_contract.LAUNCHER_TLS_PIN_FEATURE not in launcher_features:
            reason = "external upgrade launcher cannot validate pinned TLS health"
        elif not artifact_raw or not marker_raw or not status_raw:
            reason = "launcher paths are incomplete"
        else:
            artifact_input = Path(artifact_raw)
            artifact = artifact_input.resolve()
            marker = Path(marker_raw).resolve()
            status_path = Path(status_raw).resolve()
            data_dir = Path(config.DATA_DIR).resolve()
            if artifact != Path(sys.argv[0]).resolve() or not artifact.is_file():
                reason = "managed artifact does not match the running zipapp"
            elif artifact_input.is_symlink() or not os.access(str(artifact.parent), os.W_OK):
                reason = "managed artifact is not atomically replaceable"
            elif marker.name != "pending.json" or status_path.name != "status.json" or \
                    marker.parent != status_path.parent or not _under(marker.parent, data_dir):
                reason = "launcher state must live inside the private data directory"
    except Exception as exc:
        reason = str(exc)
    return {
        "enabled": not reason,
        "reason": reason,
        "artifact": artifact,
        "marker": marker,
        "status": status_path,
    }


def enabled(tls_enabled=None) -> bool:
    return bool(_runtime(tls_enabled)["enabled"])


def capabilities(include_terminal: bool, tls_enabled: bool = False) -> list:
    caps = protocol.execution_capabilities(include_terminal)
    if tls_enabled:
        caps.append(protocol.TLS_PIN_CAPABILITY)
    if enabled(tls_enabled):
        caps.append(protocol.UPGRADE_CAPABILITY)
    return caps


def _last_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        allowed = ("state", "from_version", "target_version", "sha256", "updated_at", "error")
        return {key: value[key] for key in allowed if key in value}
    except Exception:
        return {}


def _readiness_payload(state: str, reason: str = "", sessions=None,
                       active_terminals: int = 0) -> dict:
    return {
        "ready": state == "ready",
        "state": state,
        "reason": reason,
        "sessions": list(sessions or []),
        "active_terminals": int(active_terminals),
        "checked_at": time.time(),
    }


def _busy_reason(blockers: list, terminals: int) -> str:
    running = sum(1 for item in blockers if item.get("running"))
    queued = sum(max(0, int(item.get("queued") or 0)) for item in blockers)
    parts = []
    if running:
        parts.append("{} running turn{}".format(running, "" if running == 1 else "s"))
    if queued:
        parts.append("{} queued message{}".format(queued, "" if queued == 1 else "s"))
    if terminals:
        parts.append("{} active terminal{}".format(
            terminals, "" if terminals == 1 else "s"))
    return "backend is busy: " + ", ".join(parts or ["session activity"])


def _workload_readiness() -> dict:
    blockers = runner.upgrade_blockers()
    terminals = terminal.active_count()
    if blockers or terminals:
        return _readiness_payload("busy", _busy_reason(blockers, terminals),
                                  blockers, terminals)
    return _readiness_payload("ready", "backend is idle and ready to upgrade")


def _readiness(runtime: dict, app=None) -> dict:
    if not runtime["enabled"]:
        return _readiness_payload(
            "unsupported", runtime["reason"] or "remote upgrade is disabled")
    if app is not None and app.get("puppy_upgrade_draining"):
        return _readiness_payload("upgrading", "backend is restarting for an upgrade")
    if _upgrade_lock.locked():
        return _readiness_payload("upgrading", "another upgrade is being staged")
    marker = runtime.get("marker")
    try:
        if isinstance(marker, Path) and marker.exists():
            return _readiness_payload(
                "blocked", "an earlier upgrade is awaiting launcher validation")
    except OSError as exc:
        return _readiness_payload("blocked", "upgrade state cannot be read: {}".format(exc))
    return _workload_readiness()


def readiness(tls_enabled=None, app=None) -> dict:
    """Report whether a new upgrade can be accepted at this instant."""
    return _readiness(_runtime(tls_enabled), app)


def descriptor(tls_enabled=None, app=None) -> dict:
    runtime = _runtime(tls_enabled)
    out = {
        "supported": bool(runtime["enabled"]),
        "api": protocol.UPGRADE_API_PATH,
        "artifact": build_info.ARTIFACT_KIND,
        "signing": "hmac-sha256",
        "restart": "external-launcher",
        "launcher_protocol": upgrade_contract.LAUNCHER_PROTOCOL,
        "readiness": _readiness(runtime, app),
    }
    if runtime["reason"]:
        out["reason"] = runtime["reason"]
    if runtime["status"] is not None:
        last = _last_status(runtime["status"])
        if last:
            out["last"] = last
    return out


def build_descriptor() -> dict:
    out = {"artifact": build_info.ARTIFACT_KIND}
    if build_info.BUILD_COMMIT:
        out["commit"] = build_info.BUILD_COMMIT
    return out


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    _fsync_dir(path.parent)


def _copy_atomic(source: Path, target: Path) -> None:
    temporary = target.with_name("." + target.name + ".tmp")
    shutil.copy2(str(source), str(temporary))
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(target))
    _fsync_dir(target.parent)


def _validate_manifest(manifest: dict, payload: bytes) -> None:
    required = {
        "format": int, "artifact": str, "version": str, "protocol": int,
        "size": int, "sha256": str, "nonce": str, "created_at": int,
    }
    for key, kind in required.items():
        if type(manifest.get(key)) is not kind:
            raise ValueError("manifest field '{}' is missing or invalid".format(key))
    if manifest["format"] != upgrade_contract.FORMAT_VERSION:
        raise ValueError("unsupported upgrade format")
    if manifest["artifact"] != "zipapp":
        raise ValueError("unsupported upgrade artifact")
    if manifest["protocol"] != protocol.API_PROTOCOL:
        raise ValueError("candidate protocol is incompatible with this controller contract")
    if manifest["size"] != len(payload) or not 0 < len(payload) <= upgrade_contract.MAX_ARTIFACT_BYTES:
        raise ValueError("artifact size does not match the signed manifest")
    if not _HEX_SHA256.fullmatch(manifest["sha256"]) or \
            upgrade_contract.artifact_sha256(payload) != manifest["sha256"]:
        raise ValueError("artifact checksum does not match the signed manifest")
    if not 12 <= len(manifest["nonce"]) <= 128:
        raise ValueError("manifest nonce is invalid")
    if abs(int(time.time()) - manifest["created_at"]) > 3600:
        raise ValueError("upgrade manifest has expired")
    if upgrade_contract.version_key(manifest["version"]) <= upgrade_contract.version_key(__version__):
        raise ValueError("candidate version must be newer than {}".format(__version__))


def _validate_zipapp(stage: Path) -> None:
    try:
        with zipfile.ZipFile(str(stage)) as archive:
            names = set(archive.namelist())
            if "__main__.py" not in names or "puppy_backend/cli.py" not in names:
                raise ValueError("artifact is not a Puppy backend zipapp")
            if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                raise ValueError("artifact expands beyond the safety limit")
            if archive.testzip():
                raise ValueError("artifact contains a corrupt member")
    except zipfile.BadZipFile as exc:
        raise ValueError("artifact is not a valid zipapp") from exc


async def _smoke_test(stage: Path, state_dir: Path, expected: dict) -> dict:
    smoke_dir = Path(tempfile.mkdtemp(prefix="smoke-", dir=str(state_dir)))
    env = dict(os.environ)
    for key in ("PUPPY_DATA", "PUPPY_BACKEND_LAUNCHER_PROTOCOL",
                "PUPPY_BACKEND_LAUNCHER_FEATURES",
                "PUPPY_BACKEND_MANAGED_ARTIFACT", "PUPPY_BACKEND_UPGRADE_MARKER",
                "PUPPY_BACKEND_UPGRADE_STATUS"):
        env.pop(key, None)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(stage), "self-test", "--data-dir", str(smoke_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError("candidate smoke test timed out")
        output = stdout.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise ValueError("candidate smoke test failed: {}".format(output[-500:].strip()))
        try:
            result = json.loads([line for line in output.splitlines() if line.strip()][-1])
        except Exception as exc:
            raise ValueError("candidate smoke test returned invalid output") from exc
        if result.get("ok") is not True or result.get("role") != "backend" or \
                result.get("artifact") != "zipapp" or \
                result.get("version") != expected["version"] or \
                result.get("protocol") != expected["protocol"]:
            raise ValueError("candidate smoke-test metadata does not match its manifest")
        return result
    finally:
        shutil.rmtree(str(smoke_dir), ignore_errors=True)


async def _exit_for_launcher(app: web.Application) -> None:
    await asyncio.sleep(0.8)
    try:
        await app.shutdown()
        await app.cleanup()
    finally:
        os._exit(upgrade_contract.UPGRADE_EXIT_CODE)


async def h_status(request: web.Request) -> web.Response:
    return web.json_response(descriptor(app=request.app))


async def h_upgrade(request: web.Request) -> web.Response:
    runtime = _runtime()
    current_readiness = _readiness(runtime, request.app)
    if not current_readiness["ready"]:
        return web.json_response({
            "error": current_readiness["reason"],
            "readiness": current_readiness,
            "sessions": current_readiness["sessions"],
            "active_terminals": current_readiness["active_terminals"],
        }, status=409)
    if request.content_length is not None and request.content_length > upgrade_contract.MAX_ARTIFACT_BYTES:
        return web.json_response({"error": "upgrade artifact is too large"}, status=413)

    async with _upgrade_lock:
        accepted_upgrade = False
        request.app["puppy_upgrade_draining"] = True
        try:
            payload = await request.read()
            manifest = upgrade_contract.decode_manifest(
                request.headers.get(upgrade_contract.MANIFEST_HEADER, ""))
            _validate_manifest(manifest, payload)
            token = str(config.get("auth.api_token", ""))
            if not upgrade_contract.signature_ok(
                    token, manifest, payload,
                    request.headers.get(upgrade_contract.SIGNATURE_HEADER, "")):
                return web.json_response({"error": "upgrade signature is invalid"}, status=403)

            artifact = runtime["artifact"]
            marker = runtime["marker"]
            status_path = runtime["status"]
            assert isinstance(artifact, Path) and isinstance(marker, Path) and isinstance(status_path, Path)
            state_dir = marker.parent
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(str(state_dir), 0o700)
            if marker.exists():
                raise ValueError("an earlier upgrade is still pending launcher validation")

            stage = artifact.with_name(".{}.upgrade-{}.pyz".format(artifact.stem, uuid.uuid4().hex))
            previous_sha = upgrade_contract.artifact_sha256(artifact.read_bytes())
            backup = artifact.with_name(artifact.stem + ".previous" + artifact.suffix)
            try:
                fd = os.open(str(stage), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             stat.S_IMODE(artifact.stat().st_mode) or 0o755)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(str(stage), stat.S_IMODE(artifact.stat().st_mode) or 0o755)
                _validate_zipapp(stage)
                smoke = await _smoke_test(stage, state_dir, manifest)
                current_readiness = _workload_readiness()
                if not current_readiness["ready"]:
                    return web.json_response({
                        "error": "backend became busy while the candidate was being validated",
                        "readiness": current_readiness,
                        "sessions": current_readiness["sessions"],
                        "active_terminals": current_readiness["active_terminals"],
                    }, status=409)
                _copy_atomic(artifact, backup)
                pending = {
                    "format": upgrade_contract.LAUNCHER_PROTOCOL,
                    "artifact": str(artifact),
                    "backup": str(backup),
                    "status_path": str(status_path),
                    "data_dir": str(Path(config.DATA_DIR).resolve()),
                    "previous_version": __version__,
                    "previous_sha256": previous_sha,
                    "target_version": manifest["version"],
                    "target_sha256": manifest["sha256"],
                    "manifest_nonce": manifest["nonce"],
                    "health": dict(request.app["puppy_upgrade_health"]),
                    "created_at": int(time.time()),
                }
                _atomic_json(status_path, {
                    "state": "restart-pending", "from_version": __version__,
                    "target_version": manifest["version"], "sha256": manifest["sha256"],
                    "updated_at": int(time.time()),
                })
                _atomic_json(marker, pending)
                try:
                    os.replace(str(stage), str(artifact))
                except Exception:
                    marker.unlink(missing_ok=True)
                    _fsync_dir(marker.parent)
                    raise
                _fsync_dir(artifact.parent)
            finally:
                try:
                    stage.unlink()
                except FileNotFoundError:
                    pass

            log.warning("accepted signed backend upgrade %s -> %s (%s)",
                        __version__, manifest["version"], manifest["sha256"][:12])
            accepted_upgrade = True
            asyncio.create_task(_exit_for_launcher(request.app))
            return web.json_response({
                "ok": True, "accepted": True, "from_version": __version__,
                "target_version": manifest["version"], "sha256": manifest["sha256"],
                "smoke": smoke, "restart": "launcher",
            }, status=202)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            log.exception("backend upgrade staging failed")
            return web.json_response({"error": "upgrade staging failed: {}".format(exc)}, status=500)
        finally:
            if not accepted_upgrade:
                request.app["puppy_upgrade_draining"] = False


def register(app: web.Application) -> None:
    app.router.add_get(protocol.UPGRADE_API_PATH, h_status)
    app.router.add_post(protocol.UPGRADE_API_PATH, h_upgrade)
