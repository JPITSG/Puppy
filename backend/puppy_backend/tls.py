"""Persistent TLS identity management for the headless backend."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import time
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from puppy import config
from puppy import tls as tls_contract

IDENTITY_FORMAT = 1


class Identity(NamedTuple):
    mode: str
    context: Optional[ssl.SSLContext]
    certificate: str
    private_key: str
    fingerprint: str

    @property
    def enabled(self) -> bool:
        return self.context is not None


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
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    _fsync_dir(path.parent)


def _context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError("configured TLS certificate or private key is missing")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(str(cert_path), str(key_path))
    except Exception as exc:
        raise RuntimeError("TLS certificate and private key could not be loaded: {}".format(exc)) \
            from exc
    return context


def _identity(mode: str, cert_path: Path, key_path: Path,
              expected_fingerprint: str = "") -> Identity:
    context = _context(cert_path, key_path)
    try:
        fingerprint = tls_contract.certificate_sha256(cert_path)
    except Exception as exc:
        raise RuntimeError("TLS certificate fingerprint failed: {}".format(exc)) from exc
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise RuntimeError("automatic TLS identity fingerprint does not match its manifest")
    return Identity(mode, context, str(cert_path), str(key_path), fingerprint)


def _safe_leaf(value, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or \
            value in (".", ".."):
        raise RuntimeError("automatic TLS identity has an invalid {} path".format(field))
    return value


def _load_auto_manifest(tls_dir: Path, manifest_path: Path) -> Optional[Identity]:
    if not manifest_path.exists():
        return None
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("automatic TLS identity manifest is not a regular file")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("automatic TLS identity manifest is unreadable") from exc
    if not isinstance(value, dict) or value.get("format") != IDENTITY_FORMAT:
        raise RuntimeError("automatic TLS identity manifest has an unsupported format")
    cert_name = _safe_leaf(value.get("certificate"), "certificate")
    key_name = _safe_leaf(value.get("private_key"), "private key")
    try:
        fingerprint = tls_contract.normalize_fingerprint(
            value.get("sha256"), allow_empty=False)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    cert_path = tls_dir / cert_name
    key_path = tls_dir / key_name
    if cert_path.is_symlink() or key_path.is_symlink():
        raise RuntimeError("automatic TLS identity files may not be symlinks")
    return _identity("auto", cert_path, key_path, fingerprint)


def _subject_alt_names() -> str:
    dns_names = {"localhost"}
    ip_addresses = {"127.0.0.1", "::1"}
    candidates = [str(config.get("backend.host", "") or "")]
    advertised = str(config.get("backend.advertise_url", "") or "")
    if advertised:
        try:
            candidates.append(urlsplit(advertised).hostname or "")
        except ValueError:
            pass
    for raw in candidates:
        value = raw.strip().strip("[]")
        if not value or value in ("0.0.0.0", "::"):
            continue
        try:
            ip_addresses.add(str(ipaddress.ip_address(value)))
            continue
        except ValueError:
            pass
        if len(value) <= 253 and re.fullmatch(r"[A-Za-z0-9.-]+", value):
            dns_names.add(value)
    entries = ["DNS:" + name for name in sorted(dns_names)]
    entries.extend("IP:" + address for address in sorted(ip_addresses))
    return ",".join(entries)


def _generate_auto_identity(tls_dir: Path, manifest_path: Path) -> Identity:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError(
            "automatic TLS needs the openssl command; install it or use --tls-cert/--tls-key")
    identity_id = secrets.token_hex(12)
    cert_path = tls_dir / ("identity-" + identity_id + ".crt")
    key_path = tls_dir / ("identity-" + identity_id + ".key")
    openssl_config = tls_dir / (".openssl-" + identity_id + ".cnf")
    name = str(config.get("instance_name", "Puppy Backend") or "Puppy Backend")
    common_name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name).strip()[:64] or "Puppy Backend"
    config_text = "\n".join([
        "[req]",
        "distinguished_name = subject",
        "prompt = no",
        "x509_extensions = puppy_server",
        "[subject]",
        "CN = " + common_name,
        "[puppy_server]",
        "basicConstraints = critical,CA:FALSE",
        "keyUsage = critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectAltName = " + _subject_alt_names(),
        "",
    ])
    command = [
        openssl, "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
        "-days", "3650", "-keyout", str(key_path), "-out", str(cert_path),
        "-config", str(openssl_config),
    ]
    env = dict(os.environ)
    env.pop("OPENSSL_CONF", None)
    try:
        fd = os.open(str(openssl_config), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(config_text)
            handle.flush()
            os.fsync(handle.fileno())
        generated = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=30, env=env)
        if generated.returncode != 0:
            raise RuntimeError("openssl failed to create the TLS identity: " +
                               generated.stdout[-500:].strip())
        os.chmod(str(cert_path), 0o600)
        os.chmod(str(key_path), 0o600)
        identity = _identity("auto", cert_path, key_path)
        for path in (cert_path, key_path):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        _atomic_json(manifest_path, {
            "format": IDENTITY_FORMAT,
            "certificate": cert_path.name,
            "private_key": key_path.name,
            "sha256": identity.fingerprint,
            "created_at": int(time.time()),
        })
        return identity
    except Exception:
        for path in (cert_path, key_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        try:
            openssl_config.unlink()
        except FileNotFoundError:
            pass


def _auto_identity() -> Identity:
    tls_dir = Path(config.DATA_DIR).resolve() / "tls"
    if tls_dir.is_symlink():
        raise RuntimeError("automatic TLS directory may not be a symlink")
    tls_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(tls_dir), 0o700)
    manifest_path = tls_dir / "identity.json"
    lock_path = tls_dir / ".identity.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "r+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            current = _load_auto_manifest(tls_dir, manifest_path)
            if current is not None:
                return current
            return _generate_auto_identity(tls_dir, manifest_path)
    except Exception:
        # os.fdopen owns fd once entered; close it only if setup itself failed.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def load_identity() -> Identity:
    mode = str(config.get("backend.tls_mode", "disabled") or "disabled")
    if mode == "disabled":
        return Identity("disabled", None, "", "", "")
    if mode == "auto":
        return _auto_identity()
    if mode == "files":
        cert_path = Path(str(config.get("backend.tls_cert", "") or "")).expanduser().resolve()
        key_path = Path(str(config.get("backend.tls_key", "") or "")).expanduser().resolve()
        return _identity("files", cert_path, key_path)
    raise RuntimeError("unknown backend TLS mode '{}'".format(mode))
