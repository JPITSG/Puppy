"""Threads, kept apart by the kind of work they do.

Anything that would block the event loop runs in a thread, and the loop's own
default pool is not Puppy's alone: aiohttp serves every script and style sheet
through it, answers are compressed in it and so are large WebSocket frames.
Work that fills that pool therefore locks people out of the console - as
session searches did when several agents ran them at once, each holding a
thread for as long as it took while the pages waited behind them.

So the default pool (``io``) is kept for short work on Puppy's own files, its
size does not depend on the host, and everything that may take a while - or
may never return, such as a file on a network mount that went away - has a
pool of its own below. A full pool delays only its own kind of work. Nothing
outside this module uses an executor directly: a call names the pool it runs
in, ``tests/workers_test.py`` holds that, and it holds the list of what may
run in ``io``.

Two more pieces keep the pools honest. A ``Lane`` admits callers to a pool a
few at a time, in order: the rest wait on the event loop, where they cost
nothing and leave when their own caller does, instead of in a thread that
nobody can get back. And a ``Watchdog`` - a thread of its own, so that it can
speak while everything else is stuck - notices when the default pool or the
event loop has stopped answering, writes every thread's stack to the log, and
tells the console.
"""
from __future__ import annotations

import asyncio
import collections
from concurrent.futures import ThreadPoolExecutor
import contextvars
import functools
import itertools
import logging
import os
import sys
import threading
import time
import traceback
import weakref

log = logging.getLogger("puppy.workers")

# The default pool's size. Python's own is the CPU count plus four, which is
# five on the small machines that need the room most.
IO_THREADS = 32
# A pool whose work has waited this long for a thread is worth a log line.
BACKLOG_SECONDS = 60.0
WATCH_SECONDS = 5.0
STALL_SECONDS = 10.0
REPEAT_SECONDS = 300.0
MAX_REPORTS = 6         # stack dumps per stall: the first, then the repeats
STACK_FRAMES = 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep


def describe(function) -> str:
    """What a piece of work is called in the log: never its arguments."""
    while isinstance(function, functools.partial):
        # a context's ``run`` and the like are only the envelope
        inner = function.args[0] if function.args and callable(function.args[0]) else None
        function = inner if inner is not None else function.func
    name = getattr(function, "__qualname__", "") or type(function).__name__
    module = getattr(function, "__module__", "") or ""
    return "{}.{}".format(module, name) if module else name


class Executor(ThreadPoolExecutor):
    """A thread pool that can say what it is doing, whoever gave it the work."""

    def __init__(self, name: str, threads: int):
        super().__init__(max_workers=threads, thread_name_prefix="puppy-" + name)
        self.name = name
        self.size = threads
        self._keys = itertools.count()
        self._doing = {}    # key -> [what, waiting since, running since or None]

    def submit(self, fn, /, *args, **kwargs):
        key = next(self._keys)
        entry = [describe(fn), time.monotonic(), None]
        self._doing[key] = entry

        def run():
            entry[2] = time.monotonic()
            return fn(*args, **kwargs)
        try:
            future = super().submit(run)
        except BaseException:
            self._doing.pop(key, None)
            raise
        # also the way out of work cancelled before it had a thread
        future.add_done_callback(lambda _future: self._doing.pop(key, None))
        return future

    def state(self) -> dict:
        """How much is running and waiting, and the longest of each."""
        now = time.monotonic()
        entries = list(self._doing.values())
        running = sorted((entry for entry in entries if entry[2] is not None),
                         key=lambda entry: entry[2])
        waiting = sorted((entry for entry in entries if entry[2] is None),
                         key=lambda entry: entry[1])
        return {
            "name": self.name, "threads": self.size,
            "running": len(running), "waiting": len(waiting),
            "longest": (running[0][0], now - running[0][2]) if running else None,
            "oldest": (waiting[0][0], now - waiting[0][1]) if waiting else None,
        }


class Pool:
    """One kind of work's own threads, made when first asked for and kept."""

    def __init__(self, name: str, threads: int):
        self.name = name
        self.threads = threads
        self._lock = threading.Lock()
        self._executor = None

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = Executor(self.name, self.threads)
        return self._executor

    def state(self):
        return self._executor.state() if self._executor is not None else None

    def run(self, function, *args):
        """The asyncio future of ``function(*args)`` run by one of the threads.

        The caller's context is not carried along, exactly as with the loop's
        own ``run_in_executor``: cleanup that must finish whatever becomes of
        the operation it belongs to relies on that."""
        return asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def run_in_context(self, function, *args, **kwargs):
        """The same, with the caller's context: the operation the work reports
        to and is cancelled by (``operations.to_thread``)."""
        context = contextvars.copy_context()
        return self.run(functools.partial(context.run, function, *args, **kwargs))


class _IoPool(Pool):
    """The running loop's default executor, which aiohttp shares."""

    @property
    def executor(self):
        return None     # run_in_executor's name for it

    def state(self):
        return None     # the watchdog holds each loop's own


# The default pool: short work on Puppy's own files, and compression. Nothing
# that runs a program, follows a path someone named, waits for a lock or reads
# without a bound belongs here - see ``tests/workers_test.py``.
io = _IoPool("io", IO_THREADS)
# Work a person or an agent started that takes as long as it takes: clones,
# copies and moves, archives, workspace sync, removing trees. One thread each,
# mostly waiting on Git or on the disk.
jobs = Pool("jobs", 16)
# Looking at repositories: every look runs Git, bounded by GIT_TIMEOUT.
git = Pool("git", 8)
# Reading conversations and the token ledger out of the main database.
history = Pool("history", 4)
# Readings of this host and of whether the sessions' folders are there.
host = Pool("host", 4)
# Files outside Puppy's own storage: they may be on a mount that is gone.
files = Pool("files", 4)
# Full-history searches (two lanes, see search) and the index's one writer.
search = Pool("search", 4)
index = Pool("index", 1)

POOLS = (jobs, git, history, host, files, search, index)


class Busy(RuntimeError):
    """No place came free in time."""


class Lane:
    """At most ``size`` of its jobs at a time, and ``per_key`` of one caller's.

    Those beyond that wait for a place in order - a caller already at its own
    limit lets the ones behind it past - on the event loop and never in a
    thread. ``waiting`` bounds the line. Everything here happens on the loop's
    own thread; nothing is bound to one loop, so a lane outlives each."""

    def __init__(self, size: int, per_key: int = 0, waiting: int = 64):
        self.size = size
        self.per_key = per_key or size
        self.waiting = waiting
        self.running = 0
        self._by_key = collections.Counter()
        self._line = []     # (key, future), in the order they came

    def _free(self, key) -> bool:
        return self.running < self.size and \
            (key is None or self._by_key[key] < self.per_key)

    def _take(self, key) -> None:
        self.running += 1
        if key is not None:
            self._by_key[key] += 1

    def _leave(self, key) -> None:
        self.running -= 1
        if key is not None:
            self._by_key[key] -= 1
            if self._by_key[key] <= 0:
                del self._by_key[key]
        for entry in list(self._line):
            if self.running >= self.size:
                break
            if self._free(entry[0]):
                self._line.remove(entry)
                self._take(entry[0])
                entry[1].set_result(None)

    async def _enter(self, key, wait: float) -> None:
        # Whoever is in line while a place is free is held back by its own
        # limit, so taking the place passes nobody who could have used it.
        if self._free(key):
            self._take(key)
            return
        if wait <= 0 or len(self._line) >= self.waiting:
            raise Busy()
        entry = (key, asyncio.get_running_loop().create_future())
        self._line.append(entry)
        try:
            await asyncio.wait({entry[1]}, timeout=wait)
        except asyncio.CancelledError:
            if not self._withdraw(entry):
                self._leave(key)    # it had just been given its place
            raise
        if self._withdraw(entry):
            raise Busy()

    def _withdraw(self, entry) -> bool:
        """Leave the line; False when the place has already been given."""
        if entry[1].done():
            return False
        self._line.remove(entry)
        entry[1].cancel()
        return True

    async def run(self, pool: Pool, function, *args, key=None, wait: float = 0.0,
                  abandoned=None):
        """``function(*args)`` in ``pool`` once there is a place for it.

        Raises Busy when none came within ``wait`` seconds. A place belongs to
        the thread, not to whoever waits for it: a caller that goes away calls
        ``abandoned`` (which asks the work to stop) and the place is free once
        the work has really ended."""
        await self._enter(key, wait)
        try:
            future = pool.run(function, *args)
        except BaseException:
            self._leave(key)
            raise

        def ended(done):
            self._leave(key)
            if not done.cancelled():
                done.exception()    # an abandoned failure has been seen
        future.add_done_callback(ended)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if abandoned is not None and not future.done():
                abandoned()
            raise


async def child_exit(pid: int, limit: float = 0.0) -> bool:
    """Collect one of this process's children without giving it a thread.

    A process may keep its parent waiting as long as it likes, so the wait is
    a look every little while. True once it has ended (or was never ours, or
    somebody else collected it), False when ``limit`` seconds passed first."""
    deadline = time.monotonic() + limit if limit else None
    pause = 0.01
    while True:
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except OSError:     # ChildProcessError: already collected
            return True
        if done == pid:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        await asyncio.sleep(pause)
        pause = min(0.5, pause * 1.5)


# ---- the watchdog ----

def stacks(skip=()) -> str:
    """Every thread's stack as text, threads in the same place together."""
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    places = collections.OrderedDict()
    for ident, frame in sorted(sys._current_frames().items()):
        if ident in skip:
            continue
        lines = []
        for item in traceback.extract_stack(frame)[-STACK_FRAMES:]:
            path = item.filename
            path = path[len(_ROOT):] if path.startswith(_ROOT) else \
                "/".join(path.split(os.sep)[-2:])
            lines.append("    {}:{} {}".format(path, item.lineno, item.name))
        places.setdefault("\n".join(lines), []).append(names.get(ident) or str(ident))
    out = []
    for place, threads in sorted(places.items(), key=lambda item: -len(item[1])):
        shown = ", ".join(threads[:8]) + (", …" if len(threads) > 8 else "")
        out.append("  {} {}: {}\n{}".format(
            len(threads), "thread" if len(threads) == 1 else "threads", shown, place))
    return "\n".join(out)


def pool_lines(states) -> str:
    out = []
    for state in states:
        if not state or not (state["running"] or state["waiting"]):
            continue
        line = "  {}: {} of {} threads busy".format(
            state["name"], state["running"], state["threads"])
        if state["longest"]:
            line += " (longest {} for {:.0f} s)".format(*state["longest"])
        if state["waiting"]:
            line += ", {} waiting (oldest {} for {:.0f} s)".format(
                state["waiting"], *state["oldest"])
        out.append(line)
    return "\n".join(out) or "  every pool is idle"


class _Probe:
    """One thing that must keep answering, asked again and again."""

    def __init__(self, name, ask, after, repeat):
        self.name = name
        self.ask = ask          # ask(answered) has ``answered()`` called soon
        self.after = after
        self.repeat = repeat
        self.asked = None       # when the question now out was put
        self.answer = []        # when it was answered
        self.reports = 0
        self.reported = 0.0

    def look(self, now):
        """What to say: None, ("stalled", for how long) or ("recovered", ...)."""
        said = None
        if self.asked is not None:
            if not self.answer:
                age = now - self.asked
                if age < self.after or self.reports >= MAX_REPORTS or \
                        (self.reports and now - self.reported < self.repeat):
                    return None
                self.reports += 1
                self.reported = now
                return "stalled", age
            late = self.answer[0] - self.asked
            # What keeps every thread from running keeps this one from looking:
            # such a stall is only known once it is over.
            if self.reports or late >= self.after:
                said = "recovered", late
                self.reports = 0
        answer = self.answer = []
        self.asked = now
        try:
            self.ask(lambda: answer.append(time.monotonic()))
        except RuntimeError:    # the loop or the pool is closing
            self.asked = None
        return said


class Watchdog:
    """Notices that the event loop or the default pool has stopped answering.

    It asks both every few seconds. One that has not answered for
    ``STALL_SECONDS`` is written to the log with what every pool is doing and
    every thread's stack, again every ``REPEAT_SECONDS`` while it lasts, and
    once more when it answers. ``tell(text)`` is called on the loop for what a
    console should show - a loop that stood still is told once it moves."""

    def __init__(self, loop, executor, tell=None, every=WATCH_SECONDS,
                 after=STALL_SECONDS, repeat=REPEAT_SECONDS):
        self.loop = loop
        self.executor = executor
        self.tell = tell
        self.every = every
        self._stop = threading.Event()
        self._backlogs = set()
        self._probes = (
            _Probe("loop", lambda answered: loop.call_soon_threadsafe(answered), after, repeat),
            _Probe("io", lambda answered: executor.submit(answered), after, repeat),
        )
        self._thread = threading.Thread(target=self._run, name="puppy-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(1.0)

    def states(self) -> list:
        return [self.executor.state()] + [pool.state() for pool in POOLS]

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            try:
                for probe in self._probes:
                    said = probe.look(time.monotonic())
                    if said is not None:
                        self._report(probe.name, *said)
                self._look_at_pools()
            except Exception:
                log.exception("the worker watchdog failed")

    def _report(self, name: str, what: str, seconds: float) -> None:
        if what == "recovered":
            log.warning("%s again after %.0f s", "the event loop answers" if name == "loop" else
                        "the shared worker threads answer", seconds)
            if name == "loop":
                self._tell("Puppy did not respond for {:.0f} s · details in the log".format(seconds))
            return
        log.error(
            "%s for %.0f s\n%s\n%s",
            "the event loop has not answered" if name == "loop" else
            "the shared worker threads have not been free", seconds,
            pool_lines(self.states()), stacks(skip=(threading.get_ident(),)))
        if name == "io" and self._probes[1].reports == 1:
            self._tell("Puppy's worker threads are all busy · pages may load slowly until one "
                       "is free · details in the log")

    def _look_at_pools(self) -> None:
        """Say once that one kind of work is waiting a long time for a thread."""
        for state in self.states():
            if not state:
                continue
            late = bool(state["oldest"]) and state["oldest"][1] >= BACKLOG_SECONDS
            if late and state["name"] not in self._backlogs:
                self._backlogs.add(state["name"])
                log.warning("work is waiting for a thread\n%s", pool_lines([state]))
            elif not late:
                self._backlogs.discard(state["name"])

    def _tell(self, text: str) -> None:
        if self.tell is None:
            return
        try:
            self.loop.call_soon_threadsafe(self.tell, text)
        except RuntimeError:
            pass    # the loop has closed: nobody is left to tell


def _notice(text: str) -> None:
    """A notice kept in the controller's history and shown on open consoles."""
    from puppy import notices, runner
    try:
        notices.record(text, "warn")
    except Exception:
        log.exception("could not keep a notice")
    try:
        runner.broadcast_update({"type": "toast", "level": "warn", "text": text,
                                 "recorded": True})
    except Exception:
        log.exception("could not show a notice")


_loops = weakref.WeakKeyDictionary()    # loop -> the default pool it was given


def install(loop) -> Executor:
    """Give ``loop`` the default pool, once however many apps run on it."""
    executor = _loops.get(loop)
    if executor is None:
        executor = _loops[loop] = Executor(io.name, io.threads)
        loop.set_default_executor(executor)
    return executor


async def _lifecycle(app):
    loop = asyncio.get_running_loop()
    watchdog = Watchdog(loop, install(loop),
                        tell=_notice if app.get("puppy_role") == "full" else None)
    app["puppy_watchdog"] = watchdog
    watchdog.start()
    try:
        yield
    finally:
        watchdog.stop()


def register(app) -> None:
    """Both runtimes' pools, in place before any other startup hook runs."""
    if app.get("puppy_workers_registered"):
        return
    app["puppy_workers_registered"] = True
    app.cleanup_ctx.insert(0, _lifecycle)
