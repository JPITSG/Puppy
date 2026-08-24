"""Small shared helpers for pinned backend TLS identities."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import ssl

SHA256_HEX_LENGTH = 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PEM_CERT_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)


def normalize_fingerprint(value, *, allow_empty: bool = True) -> str:
    """Return a canonical lowercase SHA-256 certificate fingerprint."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError("TLS certificate fingerprint must be text")
    normalized = value.strip().lower().replace(":", "")
    if not normalized and allow_empty:
        return ""
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("TLS certificate fingerprint must be 64 hexadecimal characters")
    return normalized


def certificate_sha256(cert_path: Path) -> str:
    """Hash the leaf certificate's DER encoding, as aiohttp.Fingerprint does."""
    path = Path(cert_path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("TLS certificate file is unexpectedly large")
    text = path.read_text(encoding="ascii")
    match = _PEM_CERT_RE.search(text)
    if match is None:
        raise ValueError("TLS certificate file contains no PEM certificate")
    try:
        der = ssl.PEM_cert_to_DER_cert(match.group(0))
    except Exception as exc:
        raise ValueError("TLS certificate PEM is invalid") from exc
    return hashlib.sha256(der).hexdigest()
