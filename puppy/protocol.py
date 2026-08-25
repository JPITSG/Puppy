"""Wire contract shared by the full console and headless backend nodes.

The application version may move independently on controllers and backends.
Only bump API_PROTOCOL when an incompatible change makes an older peer unsafe
or impossible to support; additive fields and capabilities do not require it.
"""
from __future__ import annotations

API_PROTOCOL = 1
LEGACY_PROTOCOL = 0
SUPPORTED_BACKEND_PROTOCOLS = (LEGACY_PROTOCOL, API_PROTOCOL)

# The node owns create/reset/delete/reboot-expiry semantics for scratch cwd
# paths; old protocol-1 nodes simply omit this additive capability.
TEMPORARY_WORKSPACE_CAPABILITY = "temporary-workspaces"
USAGE_REFRESH_CAPABILITY = "engine-usage-refresh"
MANUAL_USAGE_REFRESH_CAPABILITY = "engine-usage-refresh-manual"
FILE_UPLOAD_CAPABILITY = "file-uploads"

BASE_CAPABILITIES = (
    "sessions",
    "session-stream",
    "approvals",
    "message-queue",
    # model/effort changes made while work is pending hold their place in the
    # message queue ({kind:"config"} items) instead of applying immediately
    "queued-config",
    "uploads",
    FILE_UPLOAD_CAPABILITY,
    "filesystem",
    "engine-switch",
    TEMPORARY_WORKSPACE_CAPABILITY,
    USAGE_REFRESH_CAPABILITY,
    MANUAL_USAGE_REFRESH_CAPABILITY,
)
TERMINAL_CAPABILITY = "terminal"
# POST /api/notify/exec runs a controller-supplied completion command. It is
# part of the node's shell surface, so it exists exactly when terminal does.
NOTIFY_EXEC_CAPABILITY = "notify-exec"

# An HTTPS backend advertises this when its pairing data includes a stable
# SHA-256 leaf-certificate pin. The API token remains application-layer auth;
# TLS provides confidentiality and peer identity on the same port.
TLS_PIN_CAPABILITY = "pinned-tls"

# Signed/staged remote upgrades are additive to protocol 1. A backend advertises
# this only while its external health-check/rollback launcher is active. The
# GET descriptor's readiness object is additive: older nodes omit it and still
# enforce their original POST-time idle gate.
UPGRADE_CAPABILITY = "remote-upgrade"
UPGRADE_API_PATH = "/api/node/upgrade"


def execution_capabilities(include_terminal: bool = True) -> list:
    caps = list(BASE_CAPABILITIES)
    if include_terminal:
        caps.append(TERMINAL_CAPABILITY)
        caps.append(NOTIFY_EXEC_CAPABILITY)
    return caps
