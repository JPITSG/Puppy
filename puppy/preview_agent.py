"""Turn-scoped MCP tools that show the user what the agent built.

The stdio MCP child receives a session/turn identity and a private Unix
socket into the running node, exactly like the browser, terminal, remote
screen, session and file bridges; the node does the work in ``previews``.
``show_preview`` names a folder (or one file in it) the console then shows in
a tab beside the chat - served through Puppy, never by a web server of the
agent's own on the network - and calling it again after a change reloads the
user's tab. ``previews`` lists the session's previews and ``close_preview``
ends one.
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
import time

from puppy import bridge_calls, config, system_prompts
from puppy.spawn_agent import _tool

log = logging.getLogger("puppy.previews.agent")

SERVER_NAME = "puppy_preview"
SOCKET_NAME = "agent.sock"
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 64 * 1024
REQUEST_TIMEOUT = 40.0

TOOL_INSTRUCTIONS = (
    "Puppy previews show the user something you built in this session - a web "
    "page or mockup, a 3D model viewer, a diagram, images, a folder of files - "
    "in a tab beside this chat, served through the signed-in Puppy console. "
    "Use show_preview for that instead of starting a web server: never run "
    "python -m http.server, npx serve or the like just to show files. "
    "show_preview takes a folder, whose index.html opens (or a listing when it "
    "has none), or one file inside the folder it serves; pass root to serve a "
    "larger folder when the page loads files outside its own. The folder must "
    "be inside this session's working folder or the system temporary folder, "
    "and hidden files and folders are never served. A preview serves files "
    "only, with no code running on the server, and its pages must use relative "
    "links: a link starting with / leaves the preview. Each page runs in a "
    "sandbox with an origin of its own: scripts, fetching its own files, "
    "WebGL, canvases, forms and downloads work; cookies, localStorage, "
    "sessionStorage, IndexedDB, service workers and web workers do not, so a "
    "page keeps its state in memory. Files are read from disk at every load: "
    "after changing them, call show_preview again with the same path and the "
    "user's tab reloads, keeping the preview's ID and link. The result gives a "
    "Markdown link such as [Plate viewer](puppy-preview:K3QZ): put it in your "
    "answer where the user should click. Only use links show_preview returned "
    "in this conversation; never write a puppy-preview link yourself. A "
    "preview stays until it is closed, across restarts: previews lists this "
    "session's previews and close_preview ends one the user no longer needs."
)


def instructions() -> str:
    """The MCP server's instructions: built in; the editable policy rides the
    engine's system prompt as the descriptor's engine_guidance."""
    return TOOL_INSTRUCTIONS


_PREVIEW_ID = {"type": "string", "pattern": "^[A-Za-z0-9]{4}$",
               "description": "The preview's four-character ID, as previews lists it."}

TOOLS = [
    _tool(
        "show_preview",
        "Show a folder or file to the user in a tab beside this chat, served through "
        "Puppy, or reload it after a change (same path: same preview, same link). "
        "Returns the Markdown link to put in your answer.",
        {
            "path": {"type": "string", "minLength": 1, "maxLength": 4096,
                     "description": ("A folder (its index.html opens, else a listing) or "
                                     "a file in it; absolute, or relative to the "
                                     "session's working folder.")},
            "name": {"type": "string", "maxLength": 120,
                     "description": ("What the tab and link are called; the folder's "
                                     "or file's name when omitted.")},
            "root": {"type": "string", "maxLength": 4096,
                     "description": ("The folder to serve when the page loads files "
                                     "outside its own folder; it must hold path.")},
        }, required=["path"]),
    _tool("previews", "List this session's previews: ID, name and what each shows.",
          read_only=True),
    _tool("close_preview",
          "Stop serving one of this session's previews; its link and tab stop working.",
          {"preview_id": _PREVIEW_ID}, required=["preview_id"], destructive=True),
]
_FIELDS = {"show_preview": {"path", "name", "root"}, "previews": set(),
           "close_preview": {"preview_id"}}

_server = None
_app = None


class PreviewAgentError(RuntimeError):
    pass


def _root() -> str:
    root = os.path.join(config.DATA_DIR, "previews")
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


def _guidance() -> str:
    try:
        return system_prompts.preview_prompt()
    except ValueError:      # refused at startup; a hand edit since is not a turn's end
        return config.DEFAULT_PREVIEW_SYSTEM_PROMPT


def turn_mcp(session_id: int, turn_id: str):
    """Return the stdio MCP descriptor only while this node's bridge is up."""
    if _server is None:
        return None
    return {
        "name": SERVER_NAME,
        "command": sys.executable,
        "args": ["-m", "puppy.preview_agent"],
        "engine_guidance": _guidance(),
        # Claude Code's allow list: these only look, so its auto mode runs
        # them without first asking its safety check (drivers/claude.py)
        "read_only_tools": [tool["name"] for tool in TOOLS
                            if tool["annotations"]["readOnlyHint"]],
        "env": {
            "PYTHONPATH": _package_search_path(),
            # MCP clients may filter inherited env; the child also changes cwd.
            "PUPPY_DATA": os.path.abspath(config.DATA_DIR),
            "PUPPY_PREVIEW_SOCKET": socket_path(),
            "PUPPY_PREVIEW_SESSION_ID": str(int(session_id)),
            "PUPPY_PREVIEW_TURN_ID": str(turn_id),
        },
    }


def _safe_unlink_socket(path: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise PreviewAgentError("refusing to replace non-owned preview agent socket path")
    os.unlink(path)


async def start(app=None) -> None:
    global _server, _app
    if _server is not None:
        return
    _app = app
    path = socket_path()
    if len(path.encode("utf-8")) >= 104:
        log.error("preview agent socket path is too long: %s", path)
        return
    try:
        _safe_unlink_socket(path)
        _server = await asyncio.start_unix_server(
            _handle_connection, path=path, limit=MAX_REQUEST)
        os.chmod(path, 0o600)
    except Exception as exc:
        _server = None
        log.error("could not start preview agent bridge: %s", exc)


async def stop(_app_unused=None) -> None:
    global _server, _app
    server, _server = _server, None
    _app = None
    if server is not None:
        server.close()
        await server.wait_closed()
    try:
        _safe_unlink_socket(socket_path())
    except PreviewAgentError as exc:
        log.warning("could not clean preview agent socket: %s", exc)


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


def _ago(when: float) -> str:
    seconds = max(0, int(time.time() - when))
    if seconds < 90:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size * (2 if unit != "minute" else 1.5):
            count = round(seconds / size)
            return "{} {}{} ago".format(count, unit, "" if count == 1 else "s")
    return "just now"


async def _show(session_id: int, turn_id: str, session: dict, params: dict) -> dict:
    from puppy import places, previews, runner
    path, root, name = params.get("path"), params.get("root"), params.get("name")
    if not isinstance(path, str) or not path.strip() or "\x00" in path or \
            len(path) > previews.MAX_PATH:
        raise PreviewAgentError("give the path of the folder or file to show")
    if root is not None and (not isinstance(root, str) or not root.strip() or
                             "\x00" in root or len(root) > previews.MAX_PATH):
        raise PreviewAgentError("root must be the path of a folder holding the page")
    if name is not None and not isinstance(name, str):
        raise PreviewAgentError("name must be text")
    cwd = str(session.get("cwd") or "") or "/"
    try:
        here = await places.visit(cwd)
        target = await places.visit(previews.named(cwd, path))
        top = await places.visit(previews.named(cwd, root)) if root is not None else None
        found = await places.ask(target.real, previews.resolve, here.real, target.real,
                                 top.real if top is not None else None)
    except places.NotAnswering as exc:
        raise PreviewAgentError(str(exc))
    except previews.PreviewError as exc:
        raise PreviewAgentError(str(exc))
    # back on the loop: everything from here to the broadcast happens at once
    hub = runner.hub(session_id)
    if not hub.tool_turn_active(turn_id):
        raise PreviewAgentError("this tool belongs to a turn that is no longer running")
    if _app is not None and _app.get("puppy_snapshot_busy"):
        raise PreviewAgentError("a Puppy backup or restore is in progress; show the "
                                "preview again in a moment")
    try:
        item, new = previews.remember(session_id, found, name)
    except previews.PreviewError as exc:
        raise PreviewAgentError(str(exc))
    hub.preview_activity(turn_id, item["id"], name=item["name"])
    where = previews.where(session, item)
    folderish = item["entry"] == "" or item["entry"].endswith("/")
    lines = [
        ("Showing {} as Preview {} in a tab beside this chat." if new else
         "Reloaded Preview {1} ({0}) in the user's tab.").format(item["name"], item["id"]),
        "Serves: " + where + (" (its index.html, or a listing of the folder)"
                              if folderish else ""),
        "Markdown link: " + previews.markdown_link(item),
        "Put that link in your answer where the user should click; after changing the "
        "files, call show_preview with the same path to reload the user's tab.",
    ]
    return {"text": "\n".join(lines)}


async def _dispatch(request: dict) -> dict:
    try:
        session_id = int(request.get("session_id"))
    except (TypeError, ValueError):
        raise PreviewAgentError("invalid originating session identity")
    turn_id = str(request.get("turn_id") or "")
    method = str(request.get("method") or "")
    params = request.get("params") or {}
    if session_id <= 0 or not turn_id or len(turn_id) > 100:
        raise PreviewAgentError("invalid preview tool turn identity")
    if not isinstance(params, dict):
        raise PreviewAgentError("preview tool arguments must be an object")
    if method not in _FIELDS:
        raise PreviewAgentError("unknown preview tool: " + method[:80])
    unknown = set(params) - _FIELDS[method]
    if unknown:
        raise PreviewAgentError("{} does not take {}".format(method, sorted(unknown)[0]))

    from puppy import db, previews, runner
    session = db.get_session(session_id)
    if session is None:
        raise PreviewAgentError("the originating session no longer exists")
    if not runner.hub(session_id).tool_turn_active(turn_id):
        raise PreviewAgentError("this tool belongs to a turn that is no longer running")
    if method == "show_preview":
        return await _show(session_id, turn_id, session, params)
    try:
        items = previews.previews_of(session_id)
    except ValueError:
        raise PreviewAgentError("this session's preview record is invalid")
    if method == "previews":
        if not items:
            return {"text": "This session has no previews."}
        lines = ["{} preview{} (newest last):".format(len(items),
                                                       "" if len(items) == 1 else "s")]
        for item in items:
            lines.append("{}  {}  {}  shown {}  link: {}".format(
                item["id"], item["name"], previews.where(session, item),
                _ago(item["shown_at"]), previews.markdown_link(item)))
        return {"text": "\n".join(lines)}
    preview_id = str(params.get("preview_id") or "").upper()
    if not previews.PREVIEW_ID.fullmatch(preview_id):
        raise PreviewAgentError("preview_id is the four-character ID previews lists")
    if _app is not None and _app.get("puppy_snapshot_busy"):
        raise PreviewAgentError("a Puppy backup or restore is in progress; close the "
                                "preview again in a moment")
    if not previews.forget(session_id, preview_id):
        raise PreviewAgentError("this session has no Preview {}; previews lists the "
                                "open ones".format(preview_id))
    runner.hub(session_id).preview_activity(turn_id, preview_id, closed=True)
    return {"text": "Closed Preview {}: its link and tab no longer show it.".format(preview_id)}


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8") + b"\n"


async def _handle_connection(reader, writer) -> None:
    try:
        if not _peer_is_owner(writer):
            raise PreviewAgentError("preview agent peer is not the Puppy service account")
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw or len(raw) > MAX_REQUEST:
            raise PreviewAgentError("invalid preview agent request size")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise PreviewAgentError("invalid preview agent request")
        result = await asyncio.wait_for(_dispatch(request), timeout=REQUEST_TIMEOUT)
        response = {"ok": True, "result": result}
    except PreviewAgentError as exc:
        response = {"ok": False, "error": str(exc)}
    except asyncio.TimeoutError:
        response = {"ok": False, "error": (
            "the preview's folder is taking too long to answer; try again in a moment")}
    except Exception:
        log.exception("preview agent request failed")
        response = {"ok": False, "error": "the preview tool failed internally"}
    try:
        encoded = _encode(response)
        if len(encoded) > MAX_RESPONSE:
            encoded = _encode({"ok": False, "error": "the preview tool's answer was too large"})
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
    path = os.environ.get("PUPPY_PREVIEW_SOCKET", "")
    session_id = os.environ.get("PUPPY_PREVIEW_SESSION_ID", "")
    turn_id = os.environ.get("PUPPY_PREVIEW_TURN_ID", "")
    if not path or not session_id or not turn_id:
        raise PreviewAgentError("Puppy preview bridge environment is incomplete")
    request = _encode({"session_id": session_id, "turn_id": turn_id,
                       "method": method, "params": params})
    if len(request) > MAX_REQUEST:
        raise PreviewAgentError("preview tool request is too large")
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
                raise PreviewAgentError("preview bridge response is too large")
            if cut >= 0:
                break
    except (OSError, socket.timeout) as exc:
        raise PreviewAgentError("could not reach Puppy's preview bridge: {}".format(exc))
    finally:
        client.close()
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except Exception:
        raise PreviewAgentError("Puppy's preview bridge returned an invalid response")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PreviewAgentError(str((payload or {}).get("error") or "preview tool failed"))
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise PreviewAgentError("Puppy's preview bridge returned an invalid result")
    return result


def _write_mcp(payload: dict) -> None:
    bridge_calls.write_message(payload, ensure_ascii=False)


def mcp_main() -> None:
    """The stdio MCP server; stdout is reserved for JSON-RPC."""
    bridge_calls.serve(
        info={"name": "Puppy previews", "version": "1"}, instructions=instructions(),
        tools=TOOLS, max_request=MAX_REQUEST, write=_write_mcp,
        call=lambda name, arguments, token: _bridge_call(name, arguments),
        failure=PreviewAgentError, fallback="",
        refusals_are_results=True)


if __name__ == "__main__":
    mcp_main()
