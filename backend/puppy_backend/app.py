"""Token-authenticated aiohttp application for a headless Puppy backend."""
from __future__ import annotations

import hmac
import logging

from aiohttp import web

from puppy import config, live_websockets, protocol, runner
from puppy.web import register_execution_api

from . import upgrade

log = logging.getLogger("puppy_backend.app")


@web.middleware
async def token_middleware(request: web.Request, handler):
    supplied = request.headers.get("X-Puppy-Token", "")
    expected = config.get("auth.api_token", "")
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        return web.json_response({"error": "auth required"}, status=401,
                                 headers={"Cache-Control": "no-store"})
    draining = request.app.get("puppy_upgrade_draining") or \
        request.app.get("puppy_shutdown_draining")
    if draining and request.path != protocol.UPGRADE_API_PATH and \
            (request.method not in ("GET", "HEAD", "OPTIONS") or
             request.path.startswith("/api/ws/")):
        message = ("backend is restarting for an upgrade" if
                   request.app.get("puppy_upgrade_draining") else
                   "backend is shutting down")
        return web.json_response({"error": message}, status=503)
    request["user"] = "@token"
    response = await handler(request)
    if request.path.startswith("/api/") and not response.prepared:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def build_app(include_terminal: bool = True, transport=None,
              upgrade_health=None) -> web.Application:
    app = web.Application(middlewares=[token_middleware],
                          client_max_size=8 * 1024 * 1024)
    live_websockets.initialize(app)
    app["puppy_role"] = "backend"
    transport = dict(transport or {"encrypted": False})
    tls_enabled = bool(transport.get("encrypted"))
    app["puppy_capabilities"] = upgrade.capabilities(include_terminal, tls_enabled)
    app["puppy_upgrade"] = lambda: upgrade.descriptor(tls_enabled, app=app)
    app["puppy_transport"] = transport
    app["puppy_build"] = upgrade.build_descriptor()
    app["puppy_upgrade_health"] = dict(upgrade_health or {
        "host": "127.0.0.1", "port": 10888, "tls": False,
    })
    app["puppy_upgrade_draining"] = False
    app["puppy_shutdown_draining"] = False
    app["puppy_shutdown_notice_sent"] = False
    register_execution_api(app, include_terminal=include_terminal)
    upgrade.register(app)

    async def on_shutdown(_app):
        _app["puppy_shutdown_draining"] = True
        # Set the runner's drain latch before the bounded socket write: a turn
        # finishing during that small window must not start its queued successor.
        runner.begin_shutdown()
        reason = "restart" if _app.get("puppy_upgrade_draining") else "shutdown"
        if not _app["puppy_shutdown_notice_sent"]:
            _app["puppy_shutdown_notice_sent"] = True
            try:
                await runner.announce_node_stopping(reason)
            except Exception:
                # A lifecycle hint must never obstruct the shutdown it reports.
                log.warning("could not send graceful shutdown notice", exc_info=True)
        try:
            await runner.shutdown()
        finally:
            await live_websockets.close_all(
                _app, "Puppy backend {}".format(
                    "is restarting" if reason == "restart" else "is shutting down"))

    app.on_shutdown.append(on_shutdown)
    return app
