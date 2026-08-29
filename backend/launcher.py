#!/usr/bin/env python3
"""External lifecycle/rollback launcher for an upgradeable Puppy backend.

A host service starts this stable stdlib-only process. It runs the zipapp as a
child, recognizes the reserved upgrade exit code, health-checks the replacement,
and restores the retained artifact before restarting if validation fails.
Pending state survives launcher crashes and host reboots.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

LAUNCHER_PROTOCOL = 1
UPGRADE_EXIT_CODE = 75
TLS_PIN_FEATURE = "tls-pin-health"

_child: Optional[subprocess.Popen] = None
_stopping = False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
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


def _status(path: Path, state: str, pending: Dict[str, Any], error: str = "") -> None:
    value = {
        "state": state,
        "from_version": pending.get("previous_version", ""),
        "target_version": pending.get("target_version", ""),
        "sha256": pending.get("target_sha256", ""),
        "updated_at": int(time.time()),
    }
    if error:
        value["error"] = error[:500]
    _atomic_json(path, value)


def _signal_child(signum, _frame) -> None:
    global _stopping
    _stopping = True
    child = _child
    if child is not None and child.poll() is None:
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass


def _start(artifact: Path, backend_args: list, env: Dict[str, str]) -> subprocess.Popen:
    global _child
    command = [sys.executable, str(artifact)] + backend_args
    print("puppy-backend-launcher: starting " + " ".join(command), flush=True)
    _child = subprocess.Popen(command, env=env)
    return _child


def _pin_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        context.options |= ssl.OP_NO_COMPRESSION
    return context


def _stop(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _health(pending: Dict[str, Any], child: subprocess.Popen,
            timeout: float) -> Tuple[bool, str]:
    health = pending.get("health")
    if not isinstance(health, dict):
        return False, "pending state has no health configuration"
    host = str(health.get("host") or "127.0.0.1")
    if host == "::":
        host = "::1"
    elif host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    port = int(health.get("port") or 10888)
    use_tls = bool(health.get("tls"))
    fingerprint = str(health.get("certificate_sha256") or "").strip().lower().replace(":", "")
    if use_tls and (len(fingerprint) != 64 or
                    any(char not in "0123456789abcdef" for char in fingerprint)):
        return False, "pending TLS health configuration has no valid certificate pin"
    data_dir = Path(str(pending.get("data_dir") or ""))
    try:
        config = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
        token = str(config["auth"]["api_token"])
    except Exception as exc:
        return False, "cannot read health token: {}".format(exc)
    expected = str(pending.get("target_version") or "")
    deadline = time.monotonic() + timeout
    last_error = "health check timed out"
    while time.monotonic() < deadline:
        if _stopping:
            return False, "launcher is stopping"
        code = child.poll()
        if code is not None:
            return False, "candidate exited with status {}".format(code)
        connection = None
        try:
            if use_tls:
                # Complete the handshake and authenticate the peer certificate
                # before placing the API token on the connection.
                context = _pin_context()
                connection = http.client.HTTPSConnection(
                    host, port, timeout=1.5, context=context)
                connection.connect()
                peer = connection.sock.getpeercert(binary_form=True) if connection.sock else b""
                actual = hashlib.sha256(peer).hexdigest() if peer else ""
                if not actual or not hmac.compare_digest(actual, fingerprint):
                    raise RuntimeError("backend TLS certificate pin mismatch")
            else:
                connection = http.client.HTTPConnection(host, port, timeout=1.5)
                connection.connect()
            connection.request("GET", "/api/ping", headers={"X-Puppy-Token": token})
            response = connection.getresponse()
            body = response.read(1024 * 1024)
            if response.status != 200:
                raise RuntimeError("health endpoint returned HTTP {}".format(response.status))
            payload = json.loads(body.decode("utf-8"))
            caps = payload.get("capabilities") or []
            upgrade = payload.get("upgrade") or {}
            if payload.get("version") == expected and "remote-upgrade" in caps and \
                    upgrade.get("supported") is True:
                return True, ""
            last_error = "candidate health metadata did not match the staged release"
        except Exception as exc:
            last_error = str(exc)
        finally:
            if connection is not None:
                connection.close()
        time.sleep(0.25)
    return False, last_error


def _load_pending(marker: Path, artifact: Path, state_dir: Path) -> Dict[str, Any]:
    value = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format") != LAUNCHER_PROTOCOL:
        raise ValueError("unsupported pending-state format")
    target = Path(str(value.get("artifact") or "")).resolve()
    backup = Path(str(value.get("backup") or "")).resolve()
    status = Path(str(value.get("status_path") or "")).resolve()
    if target != artifact:
        raise ValueError("pending artifact does not match launch target")
    if backup.parent != artifact.parent or backup == artifact:
        raise ValueError("rollback artifact is outside the artifact directory")
    if status.parent != state_dir:
        raise ValueError("upgrade status is outside the launcher state directory")
    value["backup"] = str(backup)
    value["status_path"] = str(status)
    return value


def _restore(artifact: Path, backup: Path, expected_sha: str) -> None:
    if not backup.is_file() or _sha256(backup) != expected_sha:
        raise RuntimeError("rollback artifact is missing or has the wrong checksum")
    temporary = artifact.with_name("." + artifact.name + ".rollback")
    shutil.copy2(str(backup), str(temporary))
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(artifact))
    _fsync_dir(artifact.parent)


def _resolve_pending(marker: Path, artifact: Path, state_dir: Path,
                     backend_args: list, env: Dict[str, str],
                     health_timeout: float) -> Optional[subprocess.Popen]:
    try:
        pending = _load_pending(marker, artifact, state_dir)
    except Exception as exc:
        print("puppy-backend-launcher: invalid pending state: {}".format(exc),
              file=sys.stderr, flush=True)
        return None
    status_path = Path(pending["status_path"])
    backup = Path(pending["backup"])
    try:
        if _sha256(artifact) != pending.get("target_sha256"):
            raise RuntimeError("staged artifact checksum changed before restart")
        _status(status_path, "validating", pending)
        candidate = _start(artifact, backend_args, env)
        healthy, error = _health(pending, candidate, health_timeout)
        if _stopping:
            _stop(candidate)
            return None
        if healthy:
            _status(status_path, "succeeded", pending)
            marker.unlink(missing_ok=True)
            _fsync_dir(marker.parent)
            print("puppy-backend-launcher: upgrade to {} is healthy".format(
                pending.get("target_version")), flush=True)
            return candidate
        _stop(candidate)
        _restore(artifact, backup, str(pending.get("previous_sha256") or ""))
        _status(status_path, "rolled-back", pending, error)
        marker.unlink(missing_ok=True)
        _fsync_dir(marker.parent)
        print("puppy-backend-launcher: candidate failed; rollback restored ({})".format(error),
              file=sys.stderr, flush=True)
        return _start(artifact, backend_args, env)
    except Exception as exc:
        try:
            if _child is not None:
                _stop(_child)
            _restore(artifact, backup, str(pending.get("previous_sha256") or ""))
            _status(status_path, "rolled-back", pending, str(exc))
            marker.unlink(missing_ok=True)
            _fsync_dir(marker.parent)
            print("puppy-backend-launcher: recovery error; rollback restored ({})".format(exc),
                  file=sys.stderr, flush=True)
            return _start(artifact, backend_args, env)
        except Exception as rollback_exc:
            _status(status_path, "failed", pending,
                    "{}; rollback failed: {}".format(exc, rollback_exc))
            print("puppy-backend-launcher: upgrade recovery failed: {}; rollback failed: {}".format(
                exc, rollback_exc), file=sys.stderr, flush=True)
            return None


def main() -> int:
    global _child
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--health-timeout", type=float, default=20.0)
    parser.add_argument("backend_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    artifact_arg = Path(args.artifact)
    artifact = artifact_arg.resolve()
    state_dir = Path(args.state_dir).resolve()
    backend_args = list(args.backend_args)
    if backend_args[:1] == ["--"]:
        backend_args.pop(0)
    if not backend_args:
        backend_args = ["serve"]
    if not artifact.is_file() or artifact_arg.is_symlink():
        parser.error("--artifact must be a regular, non-symlink file")
    if args.health_timeout <= 0:
        parser.error("--health-timeout must be positive")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(state_dir), 0o700)
    marker = state_dir / "pending.json"
    status = state_dir / "status.json"
    env = dict(os.environ)
    env.update({
        "PUPPY_BACKEND_LAUNCHER_PROTOCOL": str(LAUNCHER_PROTOCOL),
        "PUPPY_BACKEND_LAUNCHER_FEATURES": TLS_PIN_FEATURE,
        "PUPPY_BACKEND_MANAGED_ARTIFACT": str(artifact),
        "PUPPY_BACKEND_UPGRADE_MARKER": str(marker),
        "PUPPY_BACKEND_UPGRADE_STATUS": str(status),
    })
    signal.signal(signal.SIGTERM, _signal_child)
    signal.signal(signal.SIGINT, _signal_child)

    while not _stopping:
        if marker.exists():
            child = _resolve_pending(marker, artifact, state_dir, backend_args, env,
                                     args.health_timeout)
            if child is None:
                return 0 if _stopping else 1
        else:
            child = _start(artifact, backend_args, env)
        code = child.wait()
        _child = None
        if _stopping:
            return 0 if code < 0 else code
        if code == UPGRADE_EXIT_CODE and marker.exists():
            print("puppy-backend-launcher: child requested staged upgrade restart", flush=True)
            continue
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
