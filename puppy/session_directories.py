"""Node-owned working-directory availability, pushed with session lists.

One cached answer per cwd - whether it is there, which agent notes it holds,
where it really is - so payload builders do no filesystem work and browsers
never poll. Linux inotify watches the path and its ancestors (including
symlink targets), so removing and recreating even a missing parent wakes the
worker. Only relevant names and directory metadata matter, not file edits.
A short periodic pass also covers network mounts, watch limits and platforms
without inotify.

Every folder is looked at by a check of its own (``places``), off the event
loop: a drive that has stopped answering makes its own folders unavailable,
for as long as that lasts, and keeps no other folder's answer back. Nothing is
persisted.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import stat
import struct
import sys
import threading
import time
from typing import Optional

from puppy import db, places

log = logging.getLogger("puppy.session_directories")
CHECK_SECONDS = 15.0
DEBOUNCE_SECONDS = 0.15
# How long one pass waits for its answers; a later one is taken in by itself.
PROMPT_SECONDS = 1.0
_records = {}
_notes = {}     # cwd -> the agent notes there, as last seen
_real = {}      # cwd -> where it is with every link followed, as last seen
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
    if key not in _records and monitor is not None and not monitor.asked(key):
        # Search also builds session payloads, in its own threads.
        monitor.loop.call_soon_threadsafe(monitor.again)
    return _records.get(key)


def notes(cwd) -> list:
    """The agent notes last seen in a folder; none before anybody looked."""
    record(cwd)
    return list(_notes.get(str(cwd or ""), ()))


def noted(cwd, names) -> None:
    """Somebody has just seen the notes there: no need to look again first."""
    if _monitor is not None:
        _notes[str(cwd or "")] = list(names)


def real(cwd) -> str:
    """Where a folder really is, as last seen; its own path until then. For
    telling projects apart without going to either of them."""
    key = str(cwd or "")
    return _real.get(key) or os.path.abspath(key)


def reached(cwd, path) -> None:
    """Somebody has just been to this folder and followed its links."""
    if _monitor is not None:
        _real[str(cwd or "")] = path


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
        self.lock = threading.Lock()
        self.users = 0          # threads adding or removing watches
        self.leftover = -1      # closed for everybody but them
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

    def _borrow(self):
        if self.fd < 0:
            return -1
        with self.lock:
            if self.fd >= 0:
                self.users += 1
            return self.fd

    def _return(self):
        with self.lock:
            self.users -= 1
            if not self.users and self.leftover >= 0:
                os.close(self.leftover)
                self.leftover = -1

    def add(self, cwd, real, inside=()):
        """Watch a folder, in a thread: {watch: the names in it that matter}."""
        names = {}
        fd = self._borrow() if cwd else -1
        if fd < 0:
            return names
        try:
            paths = {}
            # Watch both sides of symlinks: changing the link or its target
            # changes the availability of the session's original path.
            for path in (os.path.abspath(cwd), real):
                paths.setdefault(path, set()).update(os.fsencode(name) for name in inside)
                while path != os.path.dirname(path):
                    parent = os.path.dirname(path)
                    paths.setdefault(parent, set()).add(os.fsencode(os.path.basename(path)))
                    path = parent
            for path, children in paths.items():
                wd = self.libc.inotify_add_watch(fd, os.fsencode(path), self.MASK)
                if wd >= 0:
                    # Aliases of the same inode share a descriptor.
                    names.setdefault(wd, set()).update(children)
        finally:
            self._return()
        return names

    def keep(self, watches) -> set:
        """Listen to these alone; the rest are to be dropped."""
        names = {}
        for watched in watches:
            for wd, children in watched.items():
                names.setdefault(wd, set()).update(children)
        old, self.names = self.names, names
        return old.keys() - names.keys()

    def drop(self, watches) -> None:
        fd = self._borrow()
        if fd < 0:
            return
        try:
            for wd in watches:
                self.libc.inotify_rm_watch(fd, wd)
        finally:
            self._return()

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
            # Never close/reuse an fd while a thread is adding watches: one
            # that waits for a drive may do so for longer than Puppy runs.
            with self.lock:
                fd, self.fd = self.fd, -1
                if self.users:
                    self.leftover = fd
                else:
                    os.close(fd)
        self.names = {}


class Monitor:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.wanted = True      # a pass, not only to take in what has come
        self.watch = _Inotify()
        self.directories = set()
        self.checking = {}      # cwd -> the check now looking at it
        self.answers = {}       # cwd -> what one found, not taken in yet
        self.watches = {}       # cwd -> what watches it
        self.unused = set()     # watches nobody needs, to be dropped

    def asked(self, cwd):
        """Whether a folder's answer is on its way already."""
        return cwd in self.checking or cwd in self.answers

    def again(self):
        """Something may have changed: every folder is looked at anew."""
        self.wanted = True
        self.wake.set()

    def readable(self):
        if self.watch.changed():
            self.again()

    def returned(self):
        """A drive answers again (said in a thread of its own)."""
        try:
            self.loop.call_soon_threadsafe(self.again)
        except RuntimeError:
            pass

    def inspect(self, cwd):
        """Everything there is to know about one folder, in its own thread."""
        from puppy import agent_notes
        path = os.path.realpath(cwd) if cwd else ""
        # Install watches before reading so a change during a pass requests
        # another pass. Watch failures never change a directory's verdict.
        watches = {}
        try:
            watches = self.watch.add(cwd, path, agent_notes.NOTE_FILES)
        except (OSError, ValueError):
            log.debug("directory watches unavailable", exc_info=True)
        elsewhere = []
        names = agent_notes.names(cwd, elsewhere)
        return inspect(cwd), names, path, watches, elsewhere

    async def check(self, cwd):
        from puppy import agent_notes
        try:
            available, names, path, watches, elsewhere = await places.ask(cwd, self.inspect, cwd)
            known = _notes.get(cwd, ())
            answer = [name for name in agent_notes.NOTE_FILES
                      if name in names or (name in elsewhere and name in known)]
            self.answers[cwd] = (available, answer, path, watches)
            if elsewhere:
                found = [name for name in elsewhere if await self.note_there(cwd, name, name in known)]
                answer = [name for name in agent_notes.NOTE_FILES if name in names or name in found]
                self.answers[cwd] = (available, answer, path, watches)
        except places.NotAnswering as exc:
            if not exc.queued:  # else it was not looked at: nothing is known
                self.answers[cwd] = (False, None, None, None)
        except Exception:   # no name a folder could have, say
            log.debug("a folder could not be looked at", exc_info=True)
            self.answers[cwd] = (False, [], "", {})
        finally:
            self.checking.pop(cwd, None)

    async def note_there(self, cwd, name, known):
        """Whether a note that is a link to another drive is there. A drive that
        does not answer changes nothing: not the note, and not the folder."""
        from puppy import agent_notes
        path = os.path.join(cwd, name)
        try:
            return await places.ask(path, agent_notes.is_note, path)
        except places.NotAnswering:
            return known

    def take_in(self):
        """The answers that have come, published when they change anything."""
        answers, self.answers = self.answers, {}
        changed, returned = False, set()
        for known in (_records, _notes, _real, self.watches):
            for cwd in [cwd for cwd in known if cwd not in self.directories]:
                del known[cwd]
        for cwd, (available, names, path, watches) in answers.items():
            if cwd not in self.directories:
                continue
            # a folder that was gone and is back: work waiting for it moves on
            if available and _records.get(cwd) is False:
                returned.add(cwd)
            changed = changed or _records.get(cwd) != available
            _records[cwd] = available
            if names is None:   # not answering: what was known of it stands
                continue
            changed = changed or _notes.get(cwd, []) != names
            _notes[cwd], _real[cwd], self.watches[cwd] = names, path, watches
        self.unused |= self.watch.keep(self.watches.values())
        if changed:
            from puppy import runner, session_git
            runner.broadcast_sessions()
            runner.folders_returned(returned)
            for cwd in returned:    # its Git mark says it could not be checked
                session_git.request(cwd)

    async def look(self):
        """One pass over every session's folder."""
        self.directories = {str(s["cwd"] or "") for s in db.list_sessions(include_archived=True)}
        asked = []
        for cwd in self.directories - self.checking.keys():
            asked.append(asyncio.ensure_future(self.check(cwd)))
            self.checking[cwd] = asked[-1]
        if asked:
            _done, late = await asyncio.wait(asked, timeout=PROMPT_SECONDS)
            for task in late:
                task.add_done_callback(lambda _task: self.wake.set())
        self.take_in()
        unused, self.unused = self.unused - self.watch.names.keys(), set()
        if unused:
            try:
                await places.ask(os.sep, self.watch.drop, unused)
            except OSError:
                pass

    async def run(self, app):
        loop = asyncio.get_running_loop()
        if self.watch.fd >= 0:
            loop.add_reader(self.watch.fd, self.readable)
        places.on_return(self.returned)
        due = 0.0
        try:
            while True:
                if app.get("puppy_snapshot_busy"):
                    await asyncio.sleep(1)
                    continue
                self.wake.clear()
                try:
                    if self.wanted or time.monotonic() >= due:
                        self.wanted = False
                        due = time.monotonic() + CHECK_SECONDS
                        await self.look()
                    else:
                        self.take_in()  # one that had been late
                except Exception:
                    log.exception("looking at the sessions' folders failed")
                try:
                    await asyncio.wait_for(self.wake.wait(), max(0.0, due - time.monotonic()))
                    # Coalesce a rename/recreate burst without delaying an
                    # eventual full check when events keep arriving.
                    await asyncio.sleep(DEBOUNCE_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            places.off_return(self.returned)
            for task in list(self.checking.values()):
                task.cancel()
            if self.watch.fd >= 0:
                loop.remove_reader(self.watch.fd)
            self.watch.close()


async def _lifecycle(app):
    global _monitor
    for known in (_records, _notes, _real):
        known.clear()
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
