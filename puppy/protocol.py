"""Wire contract shared by the full console and headless backend nodes.

The application version may move independently on controllers and backends.
Only bump API_PROTOCOL when an incompatible change makes an older peer unsafe
or impossible to support; additive fields and capabilities do not require it.
"""
from __future__ import annotations

API_PROTOCOL = 1
LEGACY_PROTOCOL = 0
SUPPORTED_BACKEND_PROTOCOLS = (LEGACY_PROTOCOL, API_PROTOCOL)

BASE_CAPABILITIES = (
    "sessions",
    "session-stream",
    "approvals",
    "message-queue",
    "uploads",
    "filesystem",
    "engine-switch",
)
TERMINAL_CAPABILITY = "terminal"

# Reserved for the future signed/staged remote-upgrade API. A backend must not
# advertise this capability until the mutation endpoint is actually available.
UPGRADE_CAPABILITY = "remote-upgrade"
UPGRADE_API_PATH = "/api/node/upgrade"


def execution_capabilities(include_terminal: bool = True) -> list:
    caps = list(BASE_CAPABILITIES)
    if include_terminal:
        caps.append(TERMINAL_CAPABILITY)
    return caps
