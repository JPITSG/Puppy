"""Signed wire contract for controller-to-backend artifact upgrades.

The backend API token is already the controller/backend shared secret. Upgrade
signatures derive a purpose-specific HMAC key from it so an uploaded body is
bound to its canonical manifest without reusing the raw token as a signing key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any, Dict, Tuple

FORMAT_VERSION = 1
LAUNCHER_PROTOCOL = 1
UPGRADE_EXIT_CODE = 75
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MANIFEST_HEADER = "X-Puppy-Upgrade-Manifest"
SIGNATURE_HEADER = "X-Puppy-Upgrade-Signature"
SIGNING_CONTEXT = b"puppy-backend-upgrade-v1"

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def version_key(value: str) -> Tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(str(value or ""))
    if match is None:
        raise ValueError("version must use numeric major.minor.patch form")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def canonical_manifest(manifest: Dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def encode_manifest(manifest: Dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(canonical_manifest(manifest)).decode("ascii").rstrip("=")


def decode_manifest(encoded: str) -> Dict[str, Any]:
    if not encoded or len(encoded) > 4096:
        raise ValueError("missing or oversized upgrade manifest")
    try:
        padding = "=" * (-len(encoded) % 4)
        value = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid upgrade manifest") from exc
    if not isinstance(value, dict):
        raise ValueError("upgrade manifest must be an object")
    return value


def artifact_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sign(token: str, manifest: Dict[str, Any], payload: bytes) -> str:
    key = hmac.new(token.encode("utf-8"), SIGNING_CONTEXT, hashlib.sha256).digest()
    signed = canonical_manifest(manifest) + b"\0" + payload
    return hmac.new(key, signed, hashlib.sha256).hexdigest()


def signature_ok(token: str, manifest: Dict[str, Any], payload: bytes,
                 supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(sign(token, manifest, payload), supplied.lower())
