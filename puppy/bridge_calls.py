"""The stdio side of Puppy's MCP bridges: their one JSON-RPC server, how
long one blocking tool call may last, and keeping it alive while it does.

Spawned-agent and task waits block for minutes, so that an agent asks once
instead of forty times. Every engine bounds a tool call its own way, and its
driver says how in each Puppy bridge's environment (``Driver.mcp_env``):
``PUPPY_MCP_CALL_LIMIT`` - seconds, 0 for a limit too long to matter - and
``PUPPY_MCP_PROGRESS_RESETS`` when an MCP progress notification restarts
that limit (OpenCode's client ends a call at 60 s otherwise). A bridge that
waits sends progress while it does to a client that asked for it with a
``progressToken``, and never asks for longer than the client will wait.

Calls of tools that only look (``readOnlyHint``) are answered side by side:
Claude Code sends the look-only calls of one message together and waits for
each action alone, so three reads or screenshots take as long as the
slowest of them rather than the three in a row. Every other call waits for
those to end and runs alone, in the order it came, as every call once did.

This module runs in the stdio bridge children, so it uses the standard
library alone.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

# A call ends this long before the engine's own limit would end it.
CALL_MARGIN = 10.0
# What a bridge assumes when its environment does not say: the shortest
# limit any engine is known to have.
UNKNOWN_LIMIT = 60.0
SHORTEST_CALL = 5.0
PROGRESS_EVERY = 15.0
# Look-only calls answered at once; another waits for a place.
CONCURRENT_CALLS = 8
# The protocol version answered to a client that names none.
PROTOCOL_VERSION = "2024-11-05"

# One message on stdout at a time: answers and progress come from several
# threads.
_output = threading.Lock()


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


def write_message(payload: dict, ensure_ascii: bool = True) -> None:
    """Write one JSON-RPC message on stdout, which is reserved for them."""
    line = json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=ensure_ascii).encode("utf-8") + b"\n"
    with _output:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


def image_block(data: str, mime_type: str) -> dict:
    """An MCP image content block. ``PUPPY_IMAGE_DETAIL`` names the detail an
    engine should read it at (Codex's ``codex/imageDetail``: "original" keeps
    a screenshot at its own size, which the coordinates it states rely on)."""
    block = {"type": "image", "data": str(data), "mimeType": str(mime_type)}
    detail = os.environ.get("PUPPY_IMAGE_DETAIL", "")
    if detail in ("original", "high", "low", "auto"):
        block["_meta"] = {"codex/imageDetail": detail}
    return block


def content(result: dict, fallback: str, image_type: str = "image/png") -> list:
    """A bridge's answer as MCP content: its text, then its picture if any."""
    blocks = []
    text = result.get("text")
    if text:
        blocks.append({"type": "text", "text": str(text)})
    image = result.get("image")
    if isinstance(image, dict) and image.get("data"):
        blocks.append(image_block(image["data"], image.get("mime_type") or image_type))
    if not blocks:
        blocks.append({"type": "text", "text": fallback})
    return blocks


def picture_limits() -> list:
    """What this engine's model reads at its own size, as the driver put it
    in the environment (``Driver.mcp_env``): [longest side, pixels, shortest
    side or 0], or None when it did not say."""
    raw = os.environ.get("PUPPY_IMAGE_LIMITS", "").split()
    try:
        values = [int(part) for part in raw]
    except ValueError:
        return None
    return values if len(values) == 3 else None


class _Calls:
    """Look-only calls in threads of their own, at most CONCURRENT_CALLS at
    once; any other call first waits for them to end."""

    def __init__(self):
        self._places = threading.BoundedSemaphore(CONCURRENT_CALLS)
        self._quiet = threading.Condition()
        self._running = 0

    def run(self, answer, side_by_side: bool) -> None:
        if not side_by_side:
            self.settle()
            answer()
            return
        self._places.acquire()
        with self._quiet:
            self._running += 1
        threading.Thread(target=self._answer, args=(answer,), daemon=True,
                         name="puppy-bridge-call").start()

    def _answer(self, answer) -> None:
        try:
            answer()
        finally:
            self._places.release()
            with self._quiet:
                self._running -= 1
                self._quiet.notify_all()

    def settle(self) -> None:
        with self._quiet:
            while self._running:
                self._quiet.wait()


def serve(*, info: dict, instructions: str, tools: list, call, failure,
          max_request: int, write=None, fallback: str = "",
          image_type: str = "image/png", refusals_are_results: bool = False,
          stream=None) -> None:
    """A bridge's MCP stdio server: stdout is reserved for JSON-RPC.

    ``call(name, arguments, token)`` answers one tool call with the node's
    result (``text`` and, for a picture, ``image``); a ``failure`` it raises
    is the call's error text. An unknown tool or arguments that are not an
    object are a protocol error, or with ``refusals_are_results`` an error
    result like a failed call.
    """
    write = write or write_message
    names = {tool["name"] for tool in tools}
    looks = {tool["name"] for tool in tools
             if (tool.get("annotations") or {}).get("readOnlyHint")}
    calls = _Calls()

    def respond(request_id, result=None, error=None) -> None:
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result if result is not None else {}
        write(message)

    def failed(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": True}

    def answer(request_id, name, arguments, token) -> None:
        try:
            try:
                result = {"content": content(call(name, arguments, token),
                                             fallback, image_type),
                          "isError": False}
            except failure as exc:
                result = failed(str(exc))
            respond(request_id, result=result)
        except Exception:
            respond(request_id, error={"code": -32603,
                                       "message": "internal MCP server error"})

    for raw in stream if stream is not None else sys.stdin.buffer:
        if len(raw) > max_request:
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(message, dict) or message.get("id") is None:
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            respond(request_id, result={
                "protocolVersion": requested if isinstance(requested, str)
                else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": info,
                "instructions": instructions,
            })
        elif method == "ping":
            respond(request_id, result={})
        elif method == "tools/list":
            respond(request_id, result={"tools": tools})
        elif method == "tools/call":
            name = str(params.get("name") or "") if isinstance(params, dict) else ""
            arguments = (params.get("arguments") or {}) if isinstance(params, dict) else {}
            if name not in names or not isinstance(arguments, dict):
                if refusals_are_results:
                    respond(request_id, result=failed(
                        "unknown tool: " + name[:80] if name not in names
                        else "tool arguments must be an object"))
                else:
                    respond(request_id, error={
                        "code": -32603, "message": "internal MCP server error"})
                continue
            token = progress_token(params)
            calls.run(lambda request_id=request_id, name=name, arguments=arguments,
                      token=token: answer(request_id, name, arguments, token),
                      name in looks)
        else:
            respond(request_id, error={"code": -32601, "message": "method not found"})
    calls.settle()
