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
    if request.app.get("puppy_upgrade_draining") and request.path != protocol.UPGRADE_API_PATH and \
            (request.method not in ("GET", "HEAD", "OPTIONS") or
             request.path.startswith("/api/ws/")):
        return web.json_response({"error": "backend is restarting for an upgrade"}, status=503)
    request["user"] = "@token"
    response = await handler(request)
    if request.path.startswith("/api/") and not response.prepared:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def build_app(include_terminal: bool = True, transport=None,
              upgrade_health=None) -> web.Application:
    app = web.Application(middlewares=[token_middleware],
                          client_max_size=8 * 1024 * 1024)
    app["puppy_role"] = "backend"
    transport = dict(transport or {"encrypted": False})
    tls_enabled = bool(transport.get("encrypted"))
    app["puppy_capabilities"] = upgrade.capabilities(include_terminal, tls_enabled)
    app["puppy_upgrade"] = lambda: upgrade.descriptor(tls_enabled)
    app["puppy_transport"] = transport
    app["puppy_build"] = upgrade.build_descriptor()
    app["puppy_upgrade_health"] = dict(upgrade_health or {
        "host": "127.0.0.1", "port": 10888, "tls": False,
    })
    app["puppy_upgrade_draining"] = False
    register_execution_api(app, include_terminal=include_terminal)
    upgrade.register(app)

    async def on_shutdown(_app):
        await runner.shutdown()

    app.on_shutdown.append(on_shutdown)
    return app
