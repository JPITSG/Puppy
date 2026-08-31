"""Track long-lived HTTP sockets so aiohttp can shut down promptly.

aiohttp gives an in-progress request one timeout to finish, then another after
request cancellation.  A WebSocket handler normally remains in progress for
the life of its peer, so leaving those handlers to the generic server drain
turns a five-second shutdown timeout into a ten-second idle stop.  Puppy owns
these channels and closes them explicitly after its application-level drain.
"""
from __future__ import annotations

import asyncio
import weakref

from aiohttp import WSCloseCode, web


_APP_KEY = "puppy_live_websockets"
CLOSE_TIMEOUT_SECONDS = 1.0


def initialize(app: web.Application) -> None:
    """Give one application a weak registry of its prepared WebSockets."""
    app[_APP_KEY] = weakref.WeakSet()


def track(request: web.Request, websocket: web.WebSocketResponse) -> None:
    """Remember a prepared, potentially long-lived server WebSocket."""
    sockets = request.app.get(_APP_KEY)
    if sockets is not None:
        sockets.add(websocket)


async def close_all(app: web.Application,
                    reason: str = "Puppy is shutting down") -> None:
    """Close every live channel, bounding peers that omit a close reply."""
    sockets = [socket for socket in list(app.get(_APP_KEY) or ())
               if not socket.closed]
    if not sockets:
        return
    message = str(reason or "Puppy is shutting down").encode("utf-8")[:123]

    async def close_socket(socket) -> None:
        try:
            # Application state has already drained.  Avoid waiting for an
            # output-flow drain here; the close handshake itself is bounded
            # below and cancellation force-closes aiohttp's transport.
            await socket.close(code=WSCloseCode.SERVICE_RESTART,
                               message=message, drain=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    tasks = [asyncio.create_task(close_socket(socket)) for socket in sockets]
    _done, pending = await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Let handlers woken by the close frame run their ``finally`` blocks
    # before aiohttp inspects its remaining in-progress requests.
    await asyncio.sleep(0)
