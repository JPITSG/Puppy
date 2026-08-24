"""Token-authenticated aiohttp application for a headless Puppy backend."""
from __future__ import annotations

import hmac

from aiohttp import web

from puppy import config, protocol, runner
from puppy.web import register_execution_api

from . import upgrade


@web.middleware
async def token_middleware(request: web.Request, handler):
    supplied = request.headers.get("X-Puppy-Token", "")
    expected = config.get("auth.api_token", "")
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        return web.json_response({"error": "auth required"}, status=401,
                                 headers={"Cache-Control": "no-store"})
    request["user"] = "@token"
    response = await handler(request)
    if request.path.startswith("/api/") and not response.prepared:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def build_app(include_terminal: bool = True) -> web.Application:
    app = web.Application(middlewares=[token_middleware],
                          client_max_size=8 * 1024 * 1024)
    app["puppy_role"] = "backend"
    app["puppy_capabilities"] = protocol.execution_capabilities(include_terminal)
    app["puppy_upgrade"] = upgrade.descriptor()
    app["puppy_build"] = upgrade.build_descriptor()
    register_execution_api(app, include_terminal=include_terminal)

    async def on_shutdown(_app):
        await runner.shutdown()

    app.on_shutdown.append(on_shutdown)
    return app
