"""Stable seam for the future remote backend upgrade feature.

No mutation route is registered yet. A safe implementation belongs here and
must, at minimum: authenticate through the node middleware, validate a signed
release manifest, stage on the artifact's filesystem, smoke-test the staged
zipapp, retain a rollback artifact, atomically replace the live artifact, and
ask the external service manager to restart only after the HTTP response is
flushed. Until that exists the remote-upgrade capability remains absent.
"""
from __future__ import annotations

from puppy import protocol

from . import build_info


def descriptor() -> dict:
    """Metadata exposed by /api/ping and /api/node without enabling upgrades."""
    return {
        "supported": False,
        "api": protocol.UPGRADE_API_PATH,
        "artifact": build_info.ARTIFACT_KIND,
        "restart": "external-service-manager",
    }


def build_descriptor() -> dict:
    out = {"artifact": build_info.ARTIFACT_KIND}
    if build_info.BUILD_COMMIT:
        out["commit"] = build_info.BUILD_COMMIT
    return out
