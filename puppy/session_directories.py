"""Node-owned working-directory availability, pushed with session lists.

One cached answer per cwd; no filesystem work in payload builders and no
browser polling. Linux inotify watches the path and its ancestors (including
symlink targets), so removing and recreating even a missing parent wakes the
worker. Only relevant names and directory metadata matter, not file edits.
A short periodic pass also covers network mounts, watch limits and platforms
without inotify. All filesystem checks and watch setup run off the event loop.
Nothing is persisted.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import stat
import struct
import sys
from typing import Optional

from puppy import db

log = logging.getLogger("puppy.session_directories")
CHECK_SECONDS = 15.0
DEBOUNCE_SECONDS = 0.15
_records = {}
_monitor = None


def inspect(cwd: str) -> bool:
    """A directory the node's current account can read and traverse."""
    try:
        return stat.S_ISDIR(os.stat(cwd).st_mode) and os.access(cwd, os.R_OK | os.X_OK)
    except (OSError, ValueError):
        return False


def record(cwd) -> Optional[bool]:
    key = str(cwd or "")
    monitor = _monitor
    if key not in _records and monitor is not None:
        # Search also builds session payloads in an executor.
        monitor.loop.call_soon_threadsafe(monitor.wake.set)
    return _records.get(key)


class _Inotify:
    # Attributes, child create/delete/move, self delete/move and unmount.
    # Exclude file modification/open events: ordinary work needs no checks.
    MASK = 0x00000004 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | \
        0x00000400 | 0x00000800 | 0x00002000
    OVERFLOW = 0x00004000
    IGNORED = 0x00008000
    HEADER = struct.Struct("iIII")

    def __init__(self):
        self.fd = -1
        self.names = {}
        if not sys.platform.startswith("linux"):
            return
        try:
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.inotify_init1.argtypes = [ctypes.c_int]
            self.libc.inotify_init1.restype = ctypes.c_int
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.libc.inotify_add_watch.restype = ctypes.c_int
            self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
            self.libc.inotify_rm_watch.restype = ctypes.c_int
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (AttributeError, OSError):
            pass  # the periodic pass works without watches

    def sync(self, directories):
        if self.fd < 0:
            return
        paths = {}
        for cwd in directories:
            if not cwd:
                continue
            # Watch both sides of symlinks: changing the link or its target
            # changes the availability of the session's original path.
            for path in (os.path.abspath(cwd), os.path.realpath(cwd)):
                paths.setdefault(path, set())
                while path != os.path.dirname(path):
                    parent = os.path.dirname(path)
                    paths.setdefault(parent, set()).add(os.fsencode(os.path.basename(path)))
                    path = parent
        names = {}
        for path, children in paths.items():
            wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), self.MASK)
            if wd >= 0:
                # Aliases of the same inode share a descriptor.
                names.setdefault(wd, set()).update(children)
        old, self.names = self.names, names
        for wd in old.keys() - names.keys():
            self.libc.inotify_rm_watch(self.fd, wd)

    def changed(self):
        changed = False
        # A busy ancestor such as /tmp must not monopolise the event loop.
        for _ in range(4):
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return changed
            if not data:
                return changed
            offset = 0
            while offset < len(data):
                wd, mask, _, size = self.HEADER.unpack_from(data, offset)
                offset += self.HEADER.size
                name = data[offset:offset + size].split(b"\0", 1)[0]
                offset += size
                if mask & self.OVERFLOW:
                    changed = True
                elif wd in self.names and (not name or name in self.names[wd]):
                    changed = True
                    if mask & self.IGNORED:
                        self.names.pop(wd, None)
        return changed

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        self.names.clear()


class Monitor:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.watch = _Inotify()

    def readable(self):
        if self.watch.changed():
            self.wake.set()

    def inspect_all(self, directories):
        # Install watches before reading so a change during a pass requests
        # another pass. Watch failures never change a directory's verdict.
        try:
            self.watch.sync(directories)
        except (OSError, ValueError):
            log.debug("directory watches unavailable", exc_info=True)
        return {cwd: inspect(cwd) for cwd in directories}

    async def run(self, app):
        loop = asyncio.get_running_loop()
        pending = None
        if self.watch.fd >= 0:
            loop.add_reader(self.watch.fd, self.readable)
        try:
            while True:
                if app.get("puppy_snapshot_busy"):
                    await asyncio.sleep(1)
                    continue
                self.wake.clear()
                directories = {str(s["cwd"] or "") for s in db.list_sessions(include_archived=True)}
                pending = loop.run_in_executor(None, self.inspect_all, directories)
                answers = await asyncio.shield(pending)
                pending = None
                changed = any(_records.get(cwd) != value for cwd, value in answers.items())
                _records.clear()
                _records.update(answers)
                if changed:
                    from puppy import runner
                    runner.broadcast_sessions()
                try:
                    await asyncio.wait_for(self.wake.wait(), CHECK_SECONDS)
                    # Coalesce a rename/recreate burst without delaying an
                    # eventual full check when events keep arriving.
                    await asyncio.sleep(DEBOUNCE_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.watch.fd >= 0:
                loop.remove_reader(self.watch.fd)
            # Never close/reuse an fd while its executor is adding watches.
            if pending is not None:
                await asyncio.shield(pending)
            self.watch.close()


async def _lifecycle(app):
    global _monitor
    _records.clear()
    monitor = _monitor = Monitor()
    task = asyncio.create_task(monitor.run(app), name="puppy-session-directories")
    try:
        yield
    finally:
        _monitor = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            # Startup can be cancelled before run() gets its first turn.
            monitor.watch.close()


def register(app):
    if not app.get("puppy_session_directories_registered"):
        app["puppy_session_directories_registered"] = True
        app.cleanup_ctx.append(_lifecycle)
