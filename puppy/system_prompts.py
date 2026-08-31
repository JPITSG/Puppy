"""Node-owned model prompt settings and their shared execution API."""
from __future__ import annotations

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
    unknown = set(body) - {"custom", "remote_workspace", "browser", "terminal"}
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
        config.set_system_prompts(custom, remote_workspace, browser, terminal)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "system_prompt": payload()})


def register(app: web.Application) -> None:
    app.router.add_get("/api/system-prompt", h_get)
    app.router.add_patch("/api/system-prompt", h_patch)
