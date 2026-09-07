#!/usr/bin/env python3
"""Local PTY/WebSocket latency probe; no engines, remote services or quota.

Run with python3 tests/terminal_latency_bench.py. Measures transport delivery,
not browser paint or network latency to a remote backend.
"""
import os
import sys

BURST = (b"terminal output 0123456789 abcdefghijklmnopqrstuvwxyz\r\n" * 128)
BURST_COUNT = 512

if "--child" in sys.argv:
    import signal
    import tty
    tty.setraw(0)
    screen = "--screen" in sys.argv
    if screen:
        def resized(_signum, _frame):
            size = os.get_terminal_size(0)
            os.write(1, ("\r\nRESIZE %sx%s\r\n" % size).encode())
        signal.signal(signal.SIGWINCH, resized)
    os.write(1, b"ready")
    while True:
        command = os.read(0, 1)
        if command == b"p":
            os.write(1, b"p")
        elif command == b"b":
            for _ in range(BURST_COUNT):
                os.write(1, BURST)
            if screen:
                os.write(1, "\r\nBURST DONE: café € 終\r\n".encode())
        else:
            break
    sys.exit(0)

import asyncio
import json
from pathlib import Path
import shlex
import shutil
import statistics
import time

from aiohttp import ClientSession, WSMsgType, web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("terminal-latency-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from puppy import config, terminal


async def main():
    config.load()
    instance = terminal.TerminalInstance("T1ST", shlex.join([
        sys.executable, str(Path(__file__).resolve()), "--child"]), str(ROOT), 80, 24)
    app = web.Application()
    app.router.add_get("/terminal", lambda request: terminal._serve_viewer(request, instance))
    server = web.AppRunner(app)
    try:
        await server.setup()
        site = web.TCPSite(server, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        await instance.start()
        async with ClientSession() as client:
            async with client.ws_connect("http://127.0.0.1:%s/terminal" % port) as ws:
                async def receive(size):
                    total = frames = 0
                    while total < size:
                        msg = await ws.receive(timeout=10)
                        if msg.type == WSMsgType.BINARY:
                            total += len(msg.data)
                            frames += 1
                        else:
                            assert msg.type == WSMsgType.TEXT, msg
                    assert total == size, (total, size)
                    return frames

                await receive(5)
                latencies = []
                for _ in range(200):
                    start = time.perf_counter()
                    await ws.send_bytes(b"p")
                    await receive(1)
                    latencies.append((time.perf_counter() - start) * 1000)
                bursts = []
                for _ in range(5):
                    start = time.perf_counter()
                    await ws.send_bytes(b"b")
                    frames = await receive(len(BURST) * BURST_COUNT)
                    bursts.append({"ms": round((time.perf_counter() - start) * 1000, 2),
                                   "frames": frames})
                snapshots = []
                for _ in range(3):
                    start = time.perf_counter()
                    instance.transcript()
                    snapshots.append((time.perf_counter() - start) * 1000)
                print(json.dumps({"echo_median_ms": round(statistics.median(latencies), 3),
                    "echo_p95_ms": round(sorted(latencies)[189], 3),
                    "snapshot_median_ms": round(statistics.median(snapshots), 2),
                    "burst_bytes": len(BURST) * BURST_COUNT, "bursts": bursts}, indent=2))
    finally:
        await instance.stop("benchmark complete")
        await server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
