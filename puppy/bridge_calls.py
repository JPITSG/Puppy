"""How long one blocking MCP tool call of a Puppy bridge may last, and
keeping it alive while it does.

Spawned-agent and task waits block for minutes, so that an agent asks once
instead of forty times. Every engine bounds a tool call its own way, and its
driver says how in each Puppy bridge's environment (``Driver.mcp_env``):
``PUPPY_MCP_CALL_LIMIT`` - seconds, 0 for a limit too long to matter - and
``PUPPY_MCP_PROGRESS_RESETS`` when an MCP progress notification restarts
that limit (OpenCode's client ends a call at 60 s otherwise). A bridge that
waits sends progress while it does to a client that asked for it with a
``progressToken``, and never asks for longer than the client will wait.

This module runs in the stdio bridge children, so it uses the standard
library alone.
"""
from __future__ import annotations

import os
import socket
import time

# A call ends this long before the engine's own limit would end it.
CALL_MARGIN = 10.0
# What a bridge assumes when its environment does not say: the shortest
# limit any engine is known to have.
UNKNOWN_LIMIT = 60.0
SHORTEST_CALL = 5.0
PROGRESS_EVERY = 15.0


class BridgeResponseTooLarge(ValueError):
    pass


def progress_token(params):
    """The progressToken a tools/call request carries, or None."""
    meta = params.get("_meta") if isinstance(params, dict) else None
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        return None
    return token


def call_budget(longest: float, token=None) -> float:
    """How long this call may block: ``longest`` when the engine puts no
    limit on it, or one progress keeps restarting, else its limit less a
    margin."""
    raw = os.environ.get("PUPPY_MCP_CALL_LIMIT")
    try:
        limit = UNKNOWN_LIMIT if raw is None else float(raw)
    except ValueError:
        limit = UNKNOWN_LIMIT
    resets = os.environ.get("PUPPY_MCP_PROGRESS_RESETS") == "1"
    if limit <= 0 or (resets and token is not None):
        return float(longest)
    return max(SHORTEST_CALL, min(float(longest), limit - CALL_MARGIN))


def exchange(path: str, request: bytes, timeout: float, max_response: int,
             token=None, write=None, label: str = "") -> bytes:
    """Send one request over a bridge's node socket and read its one-line
    answer, telling a client that asked for progress, every PROGRESS_EVERY
    seconds, that the call is alive. Raises OSError (socket.timeout
    included) or BridgeResponseTooLarge."""
    deadline = time.monotonic() + max(1.0, float(timeout))
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sent = 0
    try:
        client.settimeout(min(10.0, max(1.0, float(timeout))))
        client.connect(path)
        client.sendall(request)
        chunks = bytearray()
        while b"\n" not in chunks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("timed out")
            client.settimeout(min(PROGRESS_EVERY, remaining))
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                if time.monotonic() >= deadline:
                    raise
                if token is not None and write is not None:
                    sent += 1
                    params = {"progressToken": token, "progress": sent}
                    if label:
                        params["message"] = label
                    write({"jsonrpc": "2.0", "method": "notifications/progress",
                           "params": params})
                continue
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > max_response:
                raise BridgeResponseTooLarge("bridge response is too large")
        return bytes(chunks)
    finally:
        client.close()
