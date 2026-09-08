"""Explicit cancellation of user-requested preparation, never partial commits.

The optional operation header opts an audited handler into this transient
protocol. Cancellation is cooperative, including in worker threads. Ownership
and the handler's locks live until cleanup finishes, even if HTTP disconnects.
"""
import asyncio
from contextlib import asynccontextmanager
import contextvars
import functools
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from aiohttp import web

HEADER = "X-Puppy-Operation"
CAPABILITY = "operation-cancel-v1"
TTL = 600
MAX_RECORDS = 256
MAX_RUNNING = 16
_current = contextvars.ContextVar("puppy_operation", default=None)


class Cancelled(BaseException):
    """Not an ordinary failure; must pass through handlers' error formatting."""


class Operation:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = False
        self.committed = False
        self.done = False
        self.task = None
        self.expires = time.monotonic() + TTL

    def cancel(self):
        with self.lock:
            if not self.done and not self.committed:
                self.cancelled = True
            return self.state()

    def state(self):
        return "finished" if self.done else "finishing" if self.committed else \
            "cancelling" if self.cancelled else "running"


def active():
    return _current.get() is not None


def checkpoint():
    operation = _current.get()
    if operation and operation.cancelled:
        raise Cancelled()


def commit():
    """Last cancellable boundary; call before changing authoritative state."""
    operation = _current.get()
    if operation:
        with operation.lock:
            checkpoint()
            operation.committed = True


async def wait(awaitable):
    """Interrupt an audited, cancellation-safe async wait; await its cleanup."""
    if _current.get() is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            checkpoint()
            await asyncio.wait({task}, timeout=0.1)
        checkpoint()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def to_thread(function, *args, **kwargs):
    """Never release a lock or delete staging while its worker still writes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


@asynccontextmanager
async def lock(mutex):
    """Cancel while waiting for ownership without leaking an acquired lock."""
    if not active():
        async with mutex:
            yield
        return
    acquired = asyncio.create_task(mutex.acquire())
    try:
        await wait(asyncio.shield(acquired))
        yield
    finally:
        if not acquired.done():
            acquired.cancel()
        await asyncio.gather(acquired, return_exceptions=True)
        if not acquired.cancelled() and acquired.exception() is None and acquired.result():
            mutex.release()


class CheckedIO:
    """Checkpoint compressed archive reads/writes too, even within one huge file."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size=-1):
        checkpoint()
        return self.stream.read(size)

    def write(self, data):
        checkpoint()
        return self.stream.write(data)


def copyfileobj(source, destination, length=256 * 1024):
    while True:
        checkpoint()
        chunk = source.read(length)
        if not chunk:
            break
        destination.write(chunk)


def copy2(source, destination, *, follow_symlinks=True):
    checkpoint()
    if not follow_symlinks and os.path.islink(source):
        os.symlink(os.readlink(source), destination)
    else:
        with open(source, "rb") as reader, open(destination, "wb") as writer:
            copyfileobj(reader, writer)
    shutil.copystat(source, destination, follow_symlinks=follow_symlinks)
    return destination


def run_process(argv, *, input=None, timeout=60, **kwargs):
    """Bounded Git/build preparation with process-group cleanup on Cancel."""
    checkpoint()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          start_new_session=True, **kwargs) as process:
        try:
            first = True
            while True:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    out, err = process.communicate(input if first else None,
                                                   timeout=min(0.1, remaining))
                    return subprocess.CompletedProcess(argv, process.returncode, out, err)
                except subprocess.TimeoutExpired:
                    first = False
        finally:
            # Descendants may outlive their parent or hold its output pipes.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def _records(app):
    records = app.setdefault("puppy_operations", {})
    now = time.monotonic()
    for key, operation in list(records.items()):
        if (operation.done or operation.task is None) and operation.expires < now:
            del records[key]
    return records


def _key(request, identity):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", identity):
        raise web.HTTPBadRequest(text="Invalid operation identity")
    return (str(request.get("user", "@token")), identity)


def cancellable(handler):
    @functools.wraps(handler)
    async def wrapped(request):
        identity = request.headers.get(HEADER)
        if not identity:
            return await handler(request)
        records = _records(request.app)
        key = _key(request, identity)
        operation = records.get(key)
        if operation and (operation.task is not None or operation.done):
            return web.json_response({"error": "Operation identity already used"}, status=409)
        if sum(not row.done and row.task is not None for row in records.values()) >= MAX_RUNNING or \
                (operation is None and len(records) >= MAX_RECORDS):
            return web.json_response({"error": "Too many operations; retry shortly"}, status=429)
        operation = records.setdefault(key, Operation())

        async def run():
            token = _current.set(operation)
            try:
                checkpoint()  # Cancel may have arrived before the POST.
                response = await handler(request)
                checkpoint()
                return response
            except Cancelled:
                return web.json_response({"error": "Operation cancelled", "cancelled": True}, status=409)
            finally:
                operation.done = True
                operation.expires = time.monotonic() + TTL
                _current.reset(token)

        task = operation.task = asyncio.create_task(run())
        # Keep the outer mutation guard alive through disconnected callers.
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            # Keep only the small tombstone, not a completed task's potentially
            # large diff/response and request body, for the ten-minute TTL.
            operation.task = None
    return wrapped


async def h_control(request):
    records = _records(request.app)
    key = _key(request, request.match_info["operation_id"])
    operation = records.get(key)
    if operation is None:
        if request.method == "GET":
            return web.json_response({"state": "pending"})
        if len(records) >= MAX_RECORDS:
            return web.json_response({"error": "Too many operations; retry shortly"}, status=429)
        operation = records.setdefault(key, Operation())
    state = operation.cancel() if request.method == "DELETE" else operation.state()
    return web.json_response({"ok": True, "state": state})


def register(app):
    app.router.add_get("/api/operations/{operation_id}", h_control)
    app.router.add_delete("/api/operations/{operation_id}", h_control)

    async def cleanup(_app):
        await asyncio.gather(*(row.task for row in _records(app).values()
                               if row.task is not None), return_exceptions=True)
    app.on_cleanup.append(cleanup)
