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


def payload() -> dict:
    return {
        "custom": custom_prompt(),
        "browser": browser_prompt(),
        "browser_default": config.DEFAULT_BROWSER_SYSTEM_PROMPT,
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
    unknown = set(body) - {"custom", "browser"}
    if unknown:
        return web.json_response(
            {"error": "unknown system prompt field: {}".format(sorted(unknown)[0])},
            status=400)
    if not body:
        return web.json_response({"error": "no system prompt fields supplied"}, status=400)
    try:
        custom = body.get("custom", custom_prompt())
        browser = body.get("browser", browser_prompt())
        config.set_system_prompts(custom, browser)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "system_prompt": payload()})


def register(app: web.Application) -> None:
    app.router.add_get("/api/system-prompt", h_get)
    app.router.add_patch("/api/system-prompt", h_patch)
