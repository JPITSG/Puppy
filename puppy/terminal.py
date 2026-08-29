"""Interactive PTY terminals bridged over websockets (xterm.js on the client).
Default command comes from config terminal.command; a per-tab override lets the
user open e.g. `ssh root@host` directly. Terminals are ephemeral by design."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import shlex
import signal
import struct
import termios

from aiohttp import WSMsgType, web

from puppy import config
from puppy.drivers.base import clean_env
from puppy.user_paths import service_home

log = logging.getLogger("puppy.terminal")

_active_terminals = 0


def active_count() -> int:
    return _active_terminals


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


async def ws_terminal(request: web.Request) -> web.WebSocketResponse:
    global _active_terminals
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
    await ws.prepare(request)
    if request.app.get("puppy_snapshot_busy"):
        await ws.close(code=1013, message=b"Puppy backup or restore in progress")
        return ws

    cmd_str = request.query.get("cmd", "").strip() or config.get("terminal.command", "/bin/bash -l")
    cwd = request.query.get("cwd", "").strip() or service_home()
    try:
        cols = max(10, min(500, int(request.query.get("cols", "80"))))
        rows = max(4, min(300, int(request.query.get("rows", "24"))))
    except ValueError:
        cols, rows = 80, 24
    try:
        argv = shlex.split(cmd_str)
        assert argv
    except Exception:
        await ws.close(message=b"bad command")
        return ws

    if not os.path.isdir(cwd):
        cwd = "/"

    env = clean_env(dict(os.environ))
    env["TERM"] = "xterm-256color"
    if not env.get("HOME"):
        env["HOME"] = service_home()

    pid, master = pty.fork()
    if pid == 0:  # child
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except Exception:
            os._exit(127)

    _active_terminals += 1
    log.info("terminal spawned pid=%s cmd=%r for %s", pid, cmd_str, request.remote)
    _set_winsize(master, cols, rows)
    os.set_blocking(master, False)

    loop = asyncio.get_event_loop()
    outq: asyncio.Queue = asyncio.Queue(maxsize=2000)

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        try:
            outq.put_nowait(data if data else None)
        except asyncio.QueueFull:
            pass
        if not data:
            loop.remove_reader(master)

    loop.add_reader(master, on_readable)

    async def sender() -> None:
        while True:
            data = await outq.get()
            if data is None:
                break
            try:
                await ws.send_bytes(data)
            except Exception:
                break
        try:
            await ws.close()
        except Exception:
            pass

    sender_task = asyncio.ensure_future(sender())

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                try:
                    os.write(master, msg.data)
                except OSError:
                    break
            elif msg.type == WSMsgType.TEXT:
                try:
                    import json
                    ctl = json.loads(msg.data)
                    if ctl.get("type") == "resize":
                        _set_winsize(master, int(ctl.get("cols", 80)), int(ctl.get("rows", 24)))
                        try:
                            os.kill(pid, signal.SIGWINCH)
                        except ProcessLookupError:
                            pass
                except Exception:
                    pass
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        try:
            loop.remove_reader(master)
        except Exception:
            pass
        sender_task.cancel()
        try:
            os.close(master)
        except OSError:
            pass
        await _reap(pid)
        _active_terminals = max(0, _active_terminals - 1)
        log.info("terminal pid=%s closed", pid)
    return ws


async def _reap(pid: int) -> None:
    for sig, wait in ((signal.SIGHUP, 0.5), (signal.SIGTERM, 1.0), (signal.SIGKILL, 2.0)):
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return
        except ChildProcessError:
            return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        await asyncio.sleep(wait)
    try:
        await asyncio.get_event_loop().run_in_executor(None, os.waitpid, pid, 0)
    except Exception:
        pass
