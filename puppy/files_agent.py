"""Turn-scoped MCP tool that shares a file into the conversation for download.

The stdio MCP child receives a session/turn identity and a private Unix
socket into the running node, exactly like the browser, terminal, remote
screen and session bridges; the node does the work in ``shared_files``. One
tool, ``share_file``: it copies one file the agent names into the session's
own storage and hands back the Markdown link the agent writes in its answer,
which the console turns into a download for the signed-in user.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import stat
import struct
import sys

from puppy import bridge_calls, config, workers
from puppy.spawn_agent import _tool

log = logging.getLogger("puppy.files.agent")

SERVER_NAME = "puppy_files"
SOCKET_NAME = "agent.sock"
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 64 * 1024
REQUEST_TIMEOUT = 60.0

TOOL_INSTRUCTIONS = (
    "share_file gives the user a file from this machine as a link in this "
    "conversation that downloads it with one click. Use it when the user asks "
    "for a file, or when you made or changed a file they will want to keep "
    "(a model, an export, an archive, a report, a log). Pass the file's path; "
    "the file must be inside this session's working folder or the system "
    "temporary folder, so copy anything else into one of them first, and "
    "make an archive to share a folder. The result gives a Markdown link such "
    "as [model.stl](puppy-file:f0123...): put that link in your answer where "
    "the user should click, with any words you like as its text, for example "
    "\"The latest STL file is [here](puppy-file:f0123...)\". Only use links "
    "share_file returned in this conversation; never write a puppy-file link "
    "yourself, and never share credentials, keys or tokens. The link works only "
    "in this conversation, for the signed-in user. The file is copied when you "
    "share it: share it again after changing it."
)
ENGINE_GUIDANCE = (
    "Puppy's share_file tool turns a file into a download link in this "
    "conversation: share a file the user asks for, or one you made for them, "
    "and put the exact puppy-file link it returns in your answer. Never write "
    "a puppy-file link yourself."
)

def instructions() -> str:
    """The MCP server's instructions; built in, with no setting."""
    return TOOL_INSTRUCTIONS


TOOLS = [
    _tool(
        "share_file",
        "Share one file with the user as a download link in this conversation. "
        "Copies the file now and returns the Markdown link to put in your answer. "
        "The file must be inside this session's working folder or the system "
        "temporary folder; at most 256 MiB.",
        {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096,
                     "description": ("The file's absolute path, or a path relative "
                                     "to the session's working folder.")},
            "name": {"type": "string", "maxLength": 180,
                     "description": ("The file name the user's download gets; the "
                                     "file's own name when omitted.")},
        }, required=["path"]),
]

_server = None
_app = None


class FilesAgentError(RuntimeError):
    pass


def _root() -> str:
    root = os.path.join(config.DATA_DIR, "files")
    os.makedirs(root, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def socket_path() -> str:
    return os.path.join(_root(), SOCKET_NAME)


def _package_search_path() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def turn_mcp(session_id: int, turn_id: str):
    """Return the stdio MCP descriptor only while this node's bridge is up."""
    if _server is None:
        return None
    return {
        "name": SERVER_NAME,
        "command": sys.executable,
        "args": ["-m", "puppy.files_agent"],
        "engine_guidance": ENGINE_GUIDANCE,
        # the server's own instructions already say this, and Claude Code shows
        # them in its system prompt: only engines that do not need it again
        "guidance_in_instructions": True,
        # Claude Code's allow list: these only look, so its auto mode runs
        # them without first asking its safety check (drivers/claude.py)
        "read_only_tools": [tool["name"] for tool in TOOLS
                            if tool["annotations"]["readOnlyHint"]],
        "env": {
            "PYTHONPATH": _package_search_path(),
            # MCP clients may filter inherited env; the child also changes cwd.
            "PUPPY_DATA": os.path.abspath(config.DATA_DIR),
            "PUPPY_FILES_SOCKET": socket_path(),
            "PUPPY_FILES_SESSION_ID": str(int(session_id)),
            "PUPPY_FILES_TURN_ID": str(turn_id),
        },
    }


def _safe_unlink_socket(path: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise FilesAgentError("refusing to replace non-owned file agent socket path")
    os.unlink(path)


async def start(app=None) -> None:
    global _server, _app
    if _server is not None:
        return
    _app = app
    path = socket_path()
    if len(path.encode("utf-8")) >= 104:
        log.error("file agent socket path is too long: %s", path)
        return
    try:
        _safe_unlink_socket(path)
        _server = await asyncio.start_unix_server(
            _handle_connection, path=path, limit=MAX_REQUEST)
        os.chmod(path, 0o600)
    except Exception as exc:
        _server = None
        log.error("could not start file agent bridge: %s", exc)


async def stop(_app_unused=None) -> None:
    global _server, _app
    server, _server = _server, None
    _app = None
    if server is not None:
        server.close()
        await server.wait_closed()
    try:
        _safe_unlink_socket(socket_path())
    except FilesAgentError as exc:
        log.warning("could not clean file agent socket: %s", exc)


def _peer_is_owner(writer) -> bool:
    raw_socket = writer.get_extra_info("socket")
    if raw_socket is None or not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        _pid, uid, _gid = struct.unpack(
            "3i", raw_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                        struct.calcsize("3i")))
        return uid == os.geteuid()
    except (OSError, struct.error):
        return False


async def _dispatch(request: dict) -> dict:
    try:
        session_id = int(request.get("session_id"))
    except (TypeError, ValueError):
        raise FilesAgentError("invalid originating session identity")
    turn_id = str(request.get("turn_id") or "")
    method = str(request.get("method") or "")
    params = request.get("params") or {}
    if session_id <= 0 or not turn_id or len(turn_id) > 100:
        raise FilesAgentError("invalid file tool turn identity")
    if not isinstance(params, dict):
        raise FilesAgentError("file tool arguments must be an object")
    if method not in {item["name"] for item in TOOLS}:
        raise FilesAgentError("unknown file tool: " + method[:80])
    unknown = set(params) - {"path", "name"}
    if unknown:
        raise FilesAgentError("unknown share_file field: " + sorted(unknown)[0])

    from puppy import db, places, runner, shared_files, uploads
    session = db.get_session(session_id)
    if session is None:
        raise FilesAgentError("the originating session no longer exists")
    try:
        for place in shared_files.named(session, params.get("path")):
            await places.visit(place)
    except places.NotAnswering as exc:
        raise FilesAgentError(str(exc))
    if not runner.hub(session_id).tool_turn_active(turn_id):
        raise FilesAgentError("this tool belongs to a turn that is no longer running")
    if _app is not None and _app.get("puppy_snapshot_busy"):
        raise FilesAgentError("a Puppy backup or restore is in progress; share the file "
                              "again in a moment")
    # Counted as an upload so a backup never starts beside a half-made copy;
    # the check above and this count happen with no await between them, and
    # the count is the copy's own until its thread ends, even when this
    # request has stopped waiting for it.
    loop = asyncio.get_running_loop()

    def release():
        uploads._active_uploads = max(0, uploads._active_uploads - 1)
        uploads._state_changed()

    def work():
        try:
            return shared_files.share(session_id, params.get("path"), params.get("name"),
                                      turn_id)
        finally:
            try:
                loop.call_soon_threadsafe(release)
            except RuntimeError:
                pass   # the loop has closed: nothing is left to count

    uploads._active_uploads += 1
    uploads._state_changed()
    try:
        future = workers.jobs.run(work)
    except BaseException:
        release()
        raise
    try:
        result = await future
    except shared_files.ShareError as exc:
        raise FilesAgentError(str(exc))
    size = shared_files._size(result["size"])
    lines = [
        ("Already shared, unchanged: {} ({})." if result["reused"] else
         "Shared {} ({}) for download in this conversation.").format(result["name"], size),
        "Markdown link: " + result["link"],
        "Put that link in your answer where the user should click; any link text works.",
    ]
    return {"text": "\n".join(lines)}


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8") + b"\n"


async def _handle_connection(reader, writer) -> None:
    try:
        if not _peer_is_owner(writer):
            raise FilesAgentError("file agent peer is not the Puppy service account")
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw or len(raw) > MAX_REQUEST:
            raise FilesAgentError("invalid file agent request size")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise FilesAgentError("invalid file agent request")
        result = await asyncio.wait_for(_dispatch(request), timeout=REQUEST_TIMEOUT)
        response = {"ok": True, "result": result}
    except FilesAgentError as exc:
        response = {"ok": False, "error": str(exc)}
    except asyncio.TimeoutError:
        response = {"ok": False, "error": (
            "sharing the file is taking too long; share it again in a moment, and a "
            "copy that finished meanwhile is answered at once")}
    except Exception:
        log.exception("file agent request failed")
        response = {"ok": False, "error": "sharing the file failed internally"}
    try:
        encoded = _encode(response)
        if len(encoded) > MAX_RESPONSE:
            encoded = _encode({"ok": False, "error": "the file tool's answer was too large"})
        writer.write(encoded)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---- stdio MCP subprocess ----

def _bridge_call(method: str, params: dict) -> dict:
    path = os.environ.get("PUPPY_FILES_SOCKET", "")
    session_id = os.environ.get("PUPPY_FILES_SESSION_ID", "")
    turn_id = os.environ.get("PUPPY_FILES_TURN_ID", "")
    if not path or not session_id or not turn_id:
        raise FilesAgentError("Puppy file bridge environment is incomplete")
    request = _encode({"session_id": session_id, "turn_id": turn_id,
                       "method": method, "params": params})
    if len(request) > MAX_REQUEST:
        raise FilesAgentError("file tool request is too large")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(REQUEST_TIMEOUT + 5)
    try:
        client.connect(path)
        client.sendall(request)
        chunks = []
        total = 0
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            cut = chunk.find(b"\n")
            piece = chunk if cut < 0 else chunk[:cut]
            chunks.append(piece)
            total += len(piece)
            if total > MAX_RESPONSE:
                raise FilesAgentError("file bridge response is too large")
            if cut >= 0:
                break
    except (OSError, socket.timeout) as exc:
        raise FilesAgentError("could not reach Puppy's file bridge: {}".format(exc))
    finally:
        client.close()
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except Exception:
        raise FilesAgentError("Puppy's file bridge returned an invalid response")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise FilesAgentError(str((payload or {}).get("error") or "file tool failed"))
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise FilesAgentError("Puppy's file bridge returned an invalid result")
    return result


def _write_mcp(payload: dict) -> None:
    bridge_calls.write_message(payload, ensure_ascii=False)


def mcp_main() -> None:
    """The stdio MCP server; stdout is reserved for JSON-RPC."""
    bridge_calls.serve(
        info={"name": "Puppy files", "version": "1"}, instructions=instructions(),
        tools=TOOLS, max_request=MAX_REQUEST, write=_write_mcp,
        call=lambda name, arguments, token: _bridge_call(name, arguments),
        failure=FilesAgentError, fallback="",
        refusals_are_results=True)


if __name__ == "__main__":
    mcp_main()
