"""Frontend HTTP/HTTPS selection and private TLS identity management.

The headless backend has an independent pinned-TLS identity.  This module owns
only the primary WebUI listener: its exact transport choice lives in controller
database state, while imported/generated PEM material stays beneath data/tls so
the existing snapshot transaction covers both pieces together.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import tempfile
import threading
import time
from typing import NamedTuple, Optional, Sequence

from puppy import config, db
from puppy import tls as tls_contract


STATE_KEY = "web.transport"
STATE_FORMAT = 1
IDENTITY_FORMAT = 1
DEFAULT_STATE = {
    "format": STATE_FORMAT,
    "scheme": "http",
    "https_source": "auto",
}
SOURCES = ("auto", "custom")
MAX_PEM_BYTES = 1024 * 1024
MAX_SOURCE_PATH = 4096

_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_lock = threading.RLock()


class WebTLSError(RuntimeError):
    pass


class Identity(NamedTuple):
    source: str
    context: ssl.SSLContext
    certificate: str
    private_key: str
    fingerprint: str
    created_at: int
    sans: tuple


class Runtime(NamedTuple):
    state: dict
    identity: Optional[Identity]

    @property
    def scheme(self) -> str:
        return self.state["scheme"]

    @property
    def context(self) -> Optional[ssl.SSLContext]:
        return self.identity.context if self.identity is not None else None

    def listener(self, host: str, port: int) -> dict:
        return listener_payload(host, port, self.state, self.identity)


class PreparedChange(NamedTuple):
    expected_state: dict
    scheme: str
    https_source: str
    certificate: Optional[bytes]
    private_key: Optional[bytes]
    sans: tuple


class InstalledIdentity(NamedTuple):
    identity: Identity
    manifest_path: Path
    old_manifest: Optional[bytes]
    old_names: tuple
    new_paths: tuple


def normalize_state(value) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "format", "scheme", "https_source"}:
        raise WebTLSError("WebUI transport state has an unsupported shape")
    if value.get("format") != STATE_FORMAT:
        raise WebTLSError("WebUI transport state has an unsupported format")
    scheme = value.get("scheme")
    source = value.get("https_source")
    if scheme not in ("http", "https"):
        raise WebTLSError("WebUI transport scheme must be http or https")
    if source not in SOURCES:
        raise WebTLSError("WebUI HTTPS certificate source is invalid")
    return {"format": STATE_FORMAT, "scheme": scheme, "https_source": source}


def load_state() -> dict:
    row = db.query_one("SELECT value FROM meta WHERE key=?", (STATE_KEY,))
    if row is None:
        raise WebTLSError(
            "WebUI transport state is missing; initialize this database in the current shape")
    try:
        value = json.loads(row["value"])
    except Exception as exc:
        raise WebTLSError("WebUI transport state is unreadable") from exc
    return normalize_state(value)


def _tls_root(base: Optional[Path] = None, create: bool = False) -> Path:
    data_root = Path(config.DATA_DIR).resolve() if base is None else Path(base).resolve()
    root = data_root / "tls" / "web"
    if create:
        tls_parent = root.parent
        if tls_parent.exists() and tls_parent.is_symlink():
            raise WebTLSError("private TLS storage may not be a symbolic link")
        tls_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.mkdir(mode=0o700, exist_ok=True)
        for directory in (tls_parent, root):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise WebTLSError("private TLS storage is not a directory")
            directory.chmod(0o700)
    return root


def _manifest_path(source: str, base: Optional[Path] = None) -> Path:
    if source not in SOURCES:
        raise WebTLSError("unknown WebUI certificate source")
    return _tls_root(base) / (source + ".json")


def _safe_leaf(value, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or \
            value in (".", ".."):
        raise WebTLSError("WebUI TLS identity has an invalid {} path".format(field))
    return value


def _read_regular(path: Path, label: str, maximum: int = MAX_PEM_BYTES) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WebTLSError("{} is unavailable".format(label)) from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WebTLSError("{} must be a regular file".format(label))
    if info.st_size <= 0 or info.st_size > maximum:
        raise WebTLSError("{} must be between 1 byte and 1 MiB".format(label))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_size != info.st_size:
                raise WebTLSError("{} changed while it was being read".format(label))
            value = handle.read(maximum + 1)
    except WebTLSError:
        raise
    except OSError as exc:
        raise WebTLSError("{} could not be read".format(label)) from exc
    if len(value) != info.st_size or len(value) > maximum:
        raise WebTLSError("{} changed while it was being read".format(label))
    return value


def _source_file(value, label: str) -> bytes:
    raw = str(value or "").strip()
    if not raw or len(raw) > MAX_SOURCE_PATH or "\x00" in raw:
        raise WebTLSError("enter the server path to the {}".format(label))
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WebTLSError("{} path is unavailable".format(label)) from exc
    return _read_regular(path, label)


def _context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    key_prefix = _read_regular(key_path, "WebUI private key")
    if b"ENCRYPTED PRIVATE KEY" in key_prefix or b"Proc-Type: 4,ENCRYPTED" in key_prefix:
        raise WebTLSError("encrypted private keys are not supported")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(str(cert_path), str(key_path))
    except Exception as exc:
        raise WebTLSError(
            "WebUI certificate and private key do not form a usable PEM pair") from exc
    return context


def _certificate_dates(cert_path: Path) -> None:
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return
    try:
        decoded = decoder(str(cert_path))
        not_before = decoded.get("notBefore")
        not_after = decoded.get("notAfter")
        now = time.time()
        if not_before and ssl.cert_time_to_seconds(not_before) > now + 300:
            raise WebTLSError("WebUI certificate is not valid yet")
        if not_after and ssl.cert_time_to_seconds(not_after) <= now:
            raise WebTLSError("WebUI certificate has expired")
    except WebTLSError:
        raise
    except Exception as exc:
        raise WebTLSError("WebUI certificate could not be decoded") from exc


def _identity_from_paths(source: str, cert_path: Path, key_path: Path,
                         created_at: int, sans: Sequence[str],
                         expected_fingerprint: str = "",
                         validate_dates: bool = True) -> Identity:
    _read_regular(cert_path, "WebUI certificate")
    context = _context(cert_path, key_path)
    if validate_dates:
        _certificate_dates(cert_path)
    try:
        fingerprint = tls_contract.certificate_sha256(cert_path)
    except Exception as exc:
        raise WebTLSError("WebUI certificate fingerprint could not be read") from exc
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise WebTLSError("WebUI TLS identity fingerprint does not match its manifest")
    return Identity(source, context, str(cert_path), str(key_path), fingerprint,
                    int(created_at), tuple(sans))


def _load_manifest(source: str, base: Optional[Path] = None,
                   validate_dates: bool = True) -> Optional[Identity]:
    manifest_path = _manifest_path(source, base)
    if not manifest_path.exists():
        return None
    raw = _read_regular(manifest_path, "WebUI TLS identity manifest", 64 * 1024)
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise WebTLSError("WebUI TLS identity manifest is unreadable") from exc
    required = {"format", "source", "certificate", "private_key",
                "sha256", "created_at", "sans"}
    if not isinstance(value, dict) or set(value) != required or \
            value.get("format") != IDENTITY_FORMAT or value.get("source") != source:
        raise WebTLSError("WebUI TLS identity manifest has an unsupported shape")
    created_at = value.get("created_at")
    sans = value.get("sans")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at < 0 or \
            not isinstance(sans, list) or any(not isinstance(item, str) for item in sans) or \
            sans != sorted(set(sans)):
        raise WebTLSError("WebUI TLS identity manifest is invalid")
    if source == "custom" and sans:
        raise WebTLSError("custom WebUI TLS identity manifest has invalid names")
    if source == "auto" and any(_san(item.split(":", 1)[1]) != item
                                for item in sans if ":" in item) or \
            any(":" not in item for item in sans):
        raise WebTLSError("automatic WebUI TLS identity manifest has invalid names")
    try:
        fingerprint = tls_contract.normalize_fingerprint(
            value.get("sha256"), allow_empty=False)
    except ValueError as exc:
        raise WebTLSError(str(exc)) from exc
    root = _tls_root(base)
    certificate = root / _safe_leaf(value.get("certificate"), "certificate")
    private_key = root / _safe_leaf(value.get("private_key"), "private key")
    return _identity_from_paths(
        source, certificate, private_key, created_at, sans, fingerprint,
        validate_dates=validate_dates)


def _identity_validity_error(identity: Identity) -> str:
    try:
        _certificate_dates(Path(identity.certificate))
    except WebTLSError as exc:
        return str(exc)
    return ""


def _public_identity(identity: Optional[Identity]) -> dict:
    if identity is None:
        return {
            "available": False, "usable": False, "error": "",
            "sha256": "", "created_at": 0, "sans": [],
        }
    error = _identity_validity_error(identity)
    return {
        "available": True,
        "usable": not error,
        "error": error,
        "sha256": identity.fingerprint,
        "created_at": identity.created_at,
        "sans": list(identity.sans),
    }


def settings_payload() -> dict:
    state = load_state()
    # Settings must stay reachable so an operator can replace a certificate
    # that expired while this process was already running.  Runtime startup and
    # any HTTPS commit still enforce certificate validity strictly.
    auto = _load_manifest("auto", validate_dates=False)
    custom = _load_manifest("custom", validate_dates=False)
    selected = auto if state["https_source"] == "auto" else custom
    if state["scheme"] == "https" and selected is None:
        raise WebTLSError("configured WebUI HTTPS identity is missing")
    return {
        **state,
        "openssl_available": bool(shutil.which("openssl")),
        "identities": {
            "auto": _public_identity(auto),
            "custom": _public_identity(custom),
        },
    }


def load_runtime() -> Runtime:
    state = load_state()
    identity = None
    if state["scheme"] == "https":
        identity = _load_manifest(state["https_source"])
        if identity is None:
            raise WebTLSError("configured WebUI HTTPS identity is missing")
    return Runtime(state, identity)


def listener_payload(host: str, port: int, state: dict,
                     identity: Optional[Identity]) -> dict:
    normalized = normalize_state(state)
    fingerprint = identity.fingerprint if normalized["scheme"] == "https" and \
        identity is not None else ""
    return {
        "host": str(host),
        "port": int(port),
        "scheme": normalized["scheme"],
        "https_source": normalized["https_source"],
        "certificate_sha256": fingerprint,
    }


def configured_listener(host: str, port: int) -> dict:
    state = load_state()
    identity = None
    if state["scheme"] == "https":
        identity = _load_manifest(state["https_source"], validate_dates=False)
        if identity is None:
            raise WebTLSError("configured WebUI HTTPS identity is missing")
    return listener_payload(host, port, state, identity)


def listener_key(value: dict) -> tuple:
    scheme = str(value.get("scheme") or "http")
    return (
        str(value.get("host") or ""),
        int(value.get("port") or 0),
        scheme,
        str(value.get("https_source") or "auto") if scheme == "https" else "",
        str(value.get("certificate_sha256") or "") if scheme == "https" else "",
    )


def _san(value: str) -> Optional[str]:
    raw = str(value or "").strip().strip("[]").rstrip(".")
    if not raw or raw in ("0.0.0.0", "::"):
        return None
    try:
        return "IP:" + str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    if _DNS_NAME.fullmatch(raw):
        return "DNS:" + raw.lower()
    return None


def subject_alt_names(hosts: Sequence[str]) -> tuple:
    values = {"DNS:localhost", "IP:127.0.0.1", "IP:::1"}
    for host in hosts:
        value = _san(host)
        if value:
            values.add(value)
    return tuple(sorted(values))


def _validate_pair_bytes(certificate: bytes, private_key: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix=".web-tls-", dir=config.DATA_DIR) as temporary:
        root = Path(temporary)
        cert_path = root / "certificate.pem"
        key_path = root / "private-key.pem"
        cert_path.write_bytes(certificate)
        key_path.write_bytes(private_key)
        cert_path.chmod(0o600)
        key_path.chmod(0o600)
        return _identity_from_paths("custom", cert_path, key_path, int(time.time()), ()).fingerprint


def prepare_change(scheme, https_source, certificate_path, private_key_path,
                   hosts: Sequence[str]) -> PreparedChange:
    scheme = str(scheme or "").strip().lower()
    source = str(https_source or "").strip().lower()
    if scheme not in ("http", "https"):
        raise WebTLSError("select HTTP or HTTPS for the WebUI")
    if source not in SOURCES:
        raise WebTLSError("select a WebUI HTTPS certificate source")
    cert_raw = str(certificate_path or "").strip()
    key_raw = str(private_key_path or "").strip()
    if bool(cert_raw) != bool(key_raw):
        raise WebTLSError("certificate chain and private key paths are required together")
    if (cert_raw or key_raw) and source != "custom":
        raise WebTLSError("custom certificate paths require the custom certificate option")

    certificate = private_key = None
    sans = subject_alt_names(hosts)
    with _lock:
        expected = load_state()
        if source == "custom" and cert_raw:
            certificate = _source_file(cert_raw, "certificate chain")
            private_key = _source_file(key_raw, "private key")
            _validate_pair_bytes(certificate, private_key)
        elif scheme == "https" and source == "custom":
            if _load_manifest("custom") is None:
                raise WebTLSError(
                    "select both server files for the custom certificate and private key")
        elif scheme == "https" and source == "auto":
            current = _load_manifest("auto", validate_dates=False)
            if (current is None or _identity_validity_error(current) or
                    not set(sans).issubset(set(current.sans))) and \
                    not shutil.which("openssl"):
                raise WebTLSError(
                    "self-signed HTTPS generation needs the openssl command on this server")
    return PreparedChange(expected, scheme, source, certificate, private_key, sans)


def _atomic_manifest(path: Path, value: dict) -> None:
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        path.chmod(0o600)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_unique(path: Path, value: bytes) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _install_identity(source: str, certificate: bytes, private_key: bytes,
                      sans: Sequence[str]) -> InstalledIdentity:
    root = _tls_root(create=True)
    manifest_path = _manifest_path(source)
    # An expired identity is still structurally valid old state and must be
    # replaceable.  The newly installed pair is validated strictly below.
    old_identity = _load_manifest(source, validate_dates=False)
    old_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    old_names = () if old_identity is None else (
        Path(old_identity.certificate).name, Path(old_identity.private_key).name)
    identity_id = secrets.token_hex(12)
    cert_path = root / (source + "-" + identity_id + ".crt")
    key_path = root / (source + "-" + identity_id + ".key")
    created_at = int(time.time())
    try:
        _write_unique(cert_path, certificate)
        _write_unique(key_path, private_key)
        identity = _identity_from_paths(source, cert_path, key_path, created_at, sans)
        _atomic_manifest(manifest_path, {
            "format": IDENTITY_FORMAT,
            "source": source,
            "certificate": cert_path.name,
            "private_key": key_path.name,
            "sha256": identity.fingerprint,
            "created_at": created_at,
            "sans": list(sans),
        })
        return InstalledIdentity(
            identity, manifest_path, old_manifest, old_names, (cert_path, key_path))
    except Exception:
        if old_manifest is None:
            try:
                manifest_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_manifest(
                manifest_path, json.loads(old_manifest.decode("utf-8")))
        for path in (cert_path, key_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def _rollback_install(change: InstalledIdentity) -> None:
    if change.old_manifest is None:
        try:
            change.manifest_path.unlink()
        except FileNotFoundError:
            pass
    else:
        _atomic_manifest(change.manifest_path,
                         json.loads(change.old_manifest.decode("utf-8")))
    for path in change.new_paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _finalize_install(change: InstalledIdentity) -> None:
    root = change.manifest_path.parent
    current = {path.name for path in change.new_paths}
    for name in change.old_names:
        if name in current:
            continue
        try:
            (root / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Superseded unique files are harmless; the current manifest no
            # longer references them, so cleanup must not turn a committed
            # transport change into an ambiguous API failure.
            pass


def _generate_auto(sans: Sequence[str]) -> InstalledIdentity:
    openssl = shutil.which("openssl")
    if not openssl:
        raise WebTLSError(
            "self-signed HTTPS generation needs the openssl command on this server")
    with tempfile.TemporaryDirectory(prefix=".web-tls-generate-", dir=config.DATA_DIR) as temporary:
        root = Path(temporary)
        cert_path = root / "certificate.pem"
        key_path = root / "private-key.pem"
        openssl_path = root / "openssl.cnf"
        openssl_path.write_text("\n".join([
            "[req]",
            "distinguished_name = subject",
            "prompt = no",
            "x509_extensions = puppy_server",
            "[subject]",
            "CN = Puppy",
            "[puppy_server]",
            "basicConstraints = critical,CA:FALSE",
            "keyUsage = critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage = serverAuth",
            "subjectAltName = " + ",".join(sans),
            "",
        ]), encoding="ascii")
        openssl_path.chmod(0o600)
        environment = dict(os.environ)
        environment.pop("OPENSSL_CONF", None)
        try:
            generated = subprocess.run([
                openssl, "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
                "-days", "3650", "-keyout", str(key_path), "-out", str(cert_path),
                "-config", str(openssl_path),
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=30, env=environment, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WebTLSError("Puppy could not generate a self-signed certificate") from exc
        if generated.returncode != 0:
            raise WebTLSError("openssl could not generate a self-signed certificate")
        return _install_identity("auto", cert_path.read_bytes(), key_path.read_bytes(), sans)


def commit_change(prepared: PreparedChange) -> dict:
    with _lock:
        current = load_state()
        if current != prepared.expected_state:
            raise WebTLSError(
                "WebUI transport changed while it was being verified; try again")
        installed = None
        installation = None
        if prepared.https_source == "custom" and prepared.certificate is not None:
            installation = _install_identity(
                "custom", prepared.certificate, prepared.private_key or b"", ())
            installed = installation.identity
        elif prepared.scheme == "https" and prepared.https_source == "auto":
            installed = _load_manifest("auto", validate_dates=False)
            if installed is None or _identity_validity_error(installed) or \
                    not set(prepared.sans).issubset(set(installed.sans)):
                installation = _generate_auto(prepared.sans)
                installed = installation.identity
        elif prepared.scheme == "https" and prepared.https_source == "custom":
            installed = _load_manifest("custom")
        if prepared.scheme == "https" and installed is None:
            raise WebTLSError("configured WebUI HTTPS identity is missing")
        next_state = {
            "format": STATE_FORMAT,
            "scheme": prepared.scheme,
            "https_source": prepared.https_source,
        }
        try:
            db.meta_set(STATE_KEY, next_state)
        except Exception as exc:
            if installation is not None:
                _rollback_install(installation)
            raise WebTLSError("WebUI transport setting could not be saved") from exc
        if installation is not None:
            _finalize_install(installation)
        return next_state


def validate_snapshot(tls_parent: Path, state: dict) -> None:
    normalized = normalize_state(state)
    web_root = Path(tls_parent) / "web"
    if not web_root.exists():
        if normalized["scheme"] == "https":
            raise WebTLSError("snapshot is missing its configured WebUI TLS identity")
        return
    if not web_root.is_dir() or web_root.is_symlink():
        raise WebTLSError("snapshot WebUI TLS storage is invalid")
    allowed = set()
    identities = {}
    for source in SOURCES:
        identity = _load_manifest(
            source, base=Path(tls_parent).parent, validate_dates=False)
        identities[source] = identity
        if identity is not None:
            manifest = source + ".json"
            allowed.update({manifest, Path(identity.certificate).name,
                            Path(identity.private_key).name})
    actual = {item.name for item in web_root.iterdir()}
    if actual != allowed:
        raise WebTLSError("snapshot WebUI TLS storage contains unexpected files")
    if normalized["scheme"] == "https" and identities[normalized["https_source"]] is None:
        raise WebTLSError("snapshot is missing its configured WebUI TLS identity")
    if normalized["scheme"] == "https":
        selected = identities[normalized["https_source"]]
        error = _identity_validity_error(selected)
        if error:
            raise WebTLSError(error)
