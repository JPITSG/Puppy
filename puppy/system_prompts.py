"""Node-owned model prompt settings and their shared execution API."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from puppy import config

# Browser MCP children import the value accessors below on every turn. Keep the
# comparatively heavy WebUI package off that startup path; handlers import it
# only after the full aiohttp application is already running.
if TYPE_CHECKING:
    from aiohttp import web


def custom_prompt() -> str:
    """Return safe custom guidance even if a hand-edited config is malformed."""
    value = config.get("system_prompt.custom", "")
    try:
        return config.normalize_system_prompt(value, "custom system prompt")
    except ValueError:
        return ""


def browser_prompt() -> str:
    """Return the editable browser policy, falling back to Puppy's default."""
    value = config.get("system_prompt.browser", config.DEFAULT_BROWSER_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(value, "browser system prompt")
    except ValueError:
        return config.DEFAULT_BROWSER_SYSTEM_PROMPT


def terminal_prompt() -> str:
    """Return the editable shared-terminal policy with a safe fallback."""
    value = config.get("system_prompt.terminal", config.DEFAULT_TERMINAL_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(value, "terminal system prompt")
    except ValueError:
        return config.DEFAULT_TERMINAL_SYSTEM_PROMPT


def vnc_prompt() -> str:
    """Return the editable remote-screen policy with a safe fallback."""
    value = config.get("system_prompt.vnc", config.DEFAULT_VNC_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(value, "VNC system prompt")
    except ValueError:
        return config.DEFAULT_VNC_SYSTEM_PROMPT


def spawn_prompt() -> str:
    """Return the editable spawned-agent policy with a safe fallback."""
    value = config.get("system_prompt.spawn", config.DEFAULT_SPAWN_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(value, "spawn system prompt")
    except ValueError:
        return config.DEFAULT_SPAWN_SYSTEM_PROMPT


def tasks_prompt() -> str:
    """Return the editable task-tool policy with a safe fallback."""
    value = config.get("system_prompt.tasks", config.DEFAULT_TASKS_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(value, "task system prompt")
    except ValueError:
        return config.DEFAULT_TASKS_SYSTEM_PROMPT


PREVIEW_KEY = "preview_guidance"
PREVIEW_FORMAT = 1


def _preview_record(value) -> str:
    """The text of a ``preview_guidance`` record, or ValueError: exact shape,
    never repaired."""
    if not isinstance(value, dict) or set(value) != {"format", "text"} or \
            type(value["format"]) is not int or value["format"] != PREVIEW_FORMAT:
        raise ValueError("invalid preview guidance record")
    text = config.normalize_system_prompt(value["text"], "preview system prompt")
    if text != value["text"]:
        raise ValueError("invalid preview guidance record")
    return text


def preview_prompt(connection=None) -> str:
    """The editable preview policy: the optional exact ``preview_guidance``
    meta record (``{format:1, text}``), Puppy's default while there is none.
    A meta record rather than a config.json key, so adding it changed no
    node's config shape; one of any other shape is refused at startup and in
    a backup."""
    sql = "SELECT value FROM meta WHERE key=?"
    if connection is not None:
        row = connection.execute(sql, (PREVIEW_KEY,)).fetchone()
    else:
        from puppy import db
        row = db.query_one(sql, (PREVIEW_KEY,))
    if row is None:
        return config.DEFAULT_PREVIEW_SYSTEM_PROMPT
    return _preview_record(json.loads(row[0]))


def validate_persisted(connection) -> None:
    preview_prompt(connection)


def _set_preview_prompt(text: str) -> None:
    from puppy import db
    if text == config.DEFAULT_PREVIEW_SYSTEM_PROMPT:
        db.meta_apply(delete_keys=(PREVIEW_KEY,))
    else:
        db.meta_apply({PREVIEW_KEY: {"format": PREVIEW_FORMAT, "text": text}})


def remote_workspace_prompt() -> str:
    """Return remote-workspace guidance, falling back to Puppy's default."""
    value = config.get(
        "system_prompt.remote_workspace",
        config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT)
    try:
        return config.normalize_system_prompt(
            value, "remote workspace system prompt")
    except ValueError:
        return config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT


def turn_prompt(remote_workspace: bool = False) -> str:
    """Compose node guidance for one turn without leaking conditional text."""
    parts = [custom_prompt()]
    if remote_workspace:
        parts.append(remote_workspace_prompt())
    return "\n\n".join(part.strip() for part in parts if part.strip())


def payload() -> dict:
    return {
        "custom": custom_prompt(),
        "remote_workspace": remote_workspace_prompt(),
        "remote_workspace_default": config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT,
        "browser": browser_prompt(),
        "browser_default": config.DEFAULT_BROWSER_SYSTEM_PROMPT,
        "terminal": terminal_prompt(),
        "terminal_default": config.DEFAULT_TERMINAL_SYSTEM_PROMPT,
        "vnc": vnc_prompt(),
        "vnc_default": config.DEFAULT_VNC_SYSTEM_PROMPT,
        "spawn": spawn_prompt(),
        "spawn_default": config.DEFAULT_SPAWN_SYSTEM_PROMPT,
        "tasks": tasks_prompt(),
        "tasks_default": config.DEFAULT_TASKS_SYSTEM_PROMPT,
        "previews": preview_prompt(),
        "previews_default": config.DEFAULT_PREVIEW_SYSTEM_PROMPT,
        "max_chars": config.MAX_SYSTEM_PROMPT_CHARS,
    }


async def h_get(request: web.Request):
    from aiohttp import web
    return web.json_response({"system_prompt": payload()})


async def h_patch(request: web.Request):
    from aiohttp import web
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid system prompt request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "system prompt request must be an object"}, status=400)
    unknown = set(body) - {"custom", "remote_workspace", "browser", "terminal",
                           "vnc", "spawn", "tasks", "previews"}
    if unknown:
        return web.json_response(
            {"error": "unknown system prompt field: {}".format(sorted(unknown)[0])},
            status=400)
    if not body:
        return web.json_response({"error": "no system prompt fields supplied"}, status=400)
    try:
        custom = body.get("custom", custom_prompt())
        remote_workspace = body.get(
            "remote_workspace", remote_workspace_prompt())
        browser = body.get("browser", browser_prompt())
        terminal = body.get("terminal", terminal_prompt())
        vnc = body.get("vnc", vnc_prompt())
        spawn = body.get("spawn", spawn_prompt())
        tasks = body.get("tasks", tasks_prompt())
        if not isinstance(tasks, str):
            raise ValueError("task system prompt must be text")
        previous = preview_prompt()
        preview = config.normalize_system_prompt(
            body.get("previews", previous), "preview system prompt")
        # every field is checked before either store is written, and the
        # preview record is put back when config.json refuses the rest
        for value, path in ((custom, "custom system prompt"),
                            (remote_workspace, "remote workspace system prompt"),
                            (browser, "browser system prompt"),
                            (terminal, "terminal system prompt"),
                            (vnc, "VNC system prompt"), (spawn, "spawn system prompt"),
                            (tasks, "task system prompt")):
            config.normalize_system_prompt(value, path)
        if preview != previous:
            _set_preview_prompt(preview)
        try:
            config.set_system_prompts(custom, remote_workspace, browser, terminal,
                                      vnc, spawn, tasks)
        except Exception:
            if preview != previous:
                _set_preview_prompt(previous)
            raise
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "system_prompt": payload()})


def register(app: web.Application) -> None:
    app.router.add_get("/api/system-prompt", h_get)
    app.router.add_patch("/api/system-prompt", h_patch)
