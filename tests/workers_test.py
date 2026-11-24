#!/usr/bin/env python3
"""No-quota tests for the worker pools: who may use which threads (read from
the source), lanes, the default pool on both runtimes, the console answering
while every other pool is stuck, and the watchdog."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import functools
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import threading
import time

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("workers-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import cli_releases, config, db, notices, operations, runner, workers  # noqa: E402
from puppy.web import build_app  # noqa: E402
from puppy_backend.app import build_app as build_backend_app  # noqa: E402


async def _no_release_check(drivers, force=False):
    return 10 ** 9

cli_releases.refresh_if_due = _no_release_check


# ---- who uses which threads ----

# What may run in the default pool, which aiohttp serves every page through,
# and why each is short: it reads or writes a bounded piece of Puppy's own
# storage, or compresses something already in memory. A new entry is a claim
# that the work cannot run a program, follow a path somebody named, wait for a
# lock or take time that grows with the project; anything else has a pool of
# its own in puppy/workers.py.
IO_WORK = {
    ("puppy/compression.py", "_asset"),           # one script or style sheet of Puppy's own
    ("puppy/compression.py", "_gzip"),            # an answer already in memory
    ("puppy/browser_store.py", "_load_blocking"),  # one JSON file, bounded by its writer
    ("puppy/browser_store.py", "_save_blocking"),  # the same file
    ("puppy/runner.py", "uploads.store_tool_images"),  # pictures in memory, bounded, to Puppy's storage
    ("puppy/shared_files.py", "shared"),          # one session's own share folder
}
# Threads made by hand: each has a reason that a pool would not serve.
OWN_THREADS = {
    ("puppy/host_metrics.py", "threading.Thread"),  # one per mount, abandoned when it hangs
    ("puppy/workers.py", "threading.Thread"),     # the watchdog speaks while the pools are stuck
}


def _name(node) -> str:
    return ast.unparse(node) if hasattr(ast, "unparse") else ast.dump(node)


def test_sources():
    used = {}
    for path in sorted(list((BASE / "puppy").rglob("*.py")) +
                       list((BASE / "backend" / "puppy_backend").rglob("*.py"))):
        relative = str(path.relative_to(BASE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _name(node.func)
            last = called.rsplit(".", 1)[-1]
            where = "{}:{} {}".format(relative, node.lineno, called)
            if last in ("run_in_executor", "set_default_executor", "ThreadPoolExecutor",
                        "ProcessPoolExecutor") or called == "asyncio.to_thread":
                assert relative == "puppy/workers.py", \
                    "{}: name the pool it runs in (puppy/workers.py)".format(where)
            if last in ("Thread", "Timer") and "thread" in called.lower():
                assert (relative, called) in OWN_THREADS, \
                    "{}: a thread of its own needs a reason here".format(where)
            if called == "os.waitpid":
                # waiting for a process holds a thread for as long as it lives
                flags = _name(node.args[1]) if len(node.args) > 1 else ""
                assert flags == "os.WNOHANG", "{}: use workers.child_exit".format(where)
            parts = called.split(".")
            if len(parts) == 3 and parts[0] == "workers" and parts[2] in ("run", "run_in_context"):
                pool = parts[1]
                assert any(item.name == pool for item in workers.POOLS + (workers.io,)), where
                used.setdefault(pool, set()).add((relative, _name(node.args[0])))
            if called.endswith("lane.run") or called.endswith("_reads.run"):
                # a lane's pool is its first argument
                used.setdefault(_name(node.args[0]).split(".")[-1], set()).add((relative, "lane"))
    assert used["io"] == IO_WORK, \
        "the default pool is for short work only: new {}, gone {}".format(
            sorted(used["io"] - IO_WORK), sorted(IO_WORK - used["io"]))
    # every pool has work, so none is a name nobody uses
    assert set(used) == {pool.name for pool in workers.POOLS} | {"io"}, sorted(used)
    print("sources: {} places name their pool; the default pool runs {} kinds of short work".format(
        sum(len(value) for value in used.values()), len(IO_WORK)))


# ---- pools and lanes ----

_marker = contextvars.ContextVar("workers_test_marker", default="")


async def pools():
    loop = asyncio.get_running_loop()
    name = lambda: threading.current_thread().name
    assert (await workers.jobs.run(name)).startswith("puppy-jobs")
    assert (await workers.git.run(name)).startswith("puppy-git")
    # the caller's context goes along only where it is asked to: an operation
    # can be cancelled by its steps, and never its own cleanup
    _marker.set("operation")
    assert await workers.jobs.run(_marker.get) == ""
    assert await workers.jobs.run_in_context(_marker.get) == "operation"
    assert await workers.jobs.run_in_context(
        lambda a, b=0: (a, b, _marker.get()), 1, b=2) == (1, 2, "operation")
    token = operations._current.set(operations.Operation())
    try:
        assert await operations.to_thread(operations.active) is True
        assert await workers.jobs.run(operations.active) is False
        assert (await operations.to_thread(name)).startswith("puppy-jobs")
    finally:
        operations._current.reset(token)

    class Failed(Exception):
        pass

    def fail():
        raise Failed("here")
    for call in (workers.jobs.run, workers.jobs.run_in_context, workers.io.run):
        try:
            await call(fail)
        except Failed:
            continue
        raise AssertionError("an exception was lost")

    # what a pool is doing, by name and never by argument
    executor = workers.Executor("sample", 2)
    gate = threading.Event()

    def hold(secret):
        gate.wait(5)
    held = [loop.run_in_executor(executor, hold, "private-argument") for _ in range(2)]
    late = [loop.run_in_executor(executor, functools.partial(hold, "private-argument"))
            for _ in range(3)]
    for _ in range(200):
        if executor.state()["running"] == 2:
            break
        await asyncio.sleep(0.01)
    state = executor.state()
    assert (state["name"], state["threads"], state["running"], state["waiting"]) == \
        ("sample", 2, 2, 3), state
    assert "hold" in state["longest"][0] and "hold" in state["oldest"][0], state
    text = workers.pool_lines([state, workers.Executor("quiet", 1).state(), None])
    assert "sample: 2 of 2 threads busy" in text and "3 waiting" in text, text
    assert "quiet" not in text and "private-argument" not in text, text
    assert late[-1].cancel()            # it never had a thread
    await asyncio.sleep(0.05)
    assert executor.state()["waiting"] == 2
    gate.set()
    await asyncio.gather(*held, *late[:-1])
    for _ in range(200):
        if not executor.state()["running"]:
            break
        await asyncio.sleep(0.01)
    assert executor.state()["running"] == executor.state()["waiting"] == 0
    assert workers.pool_lines([executor.state()]) == "  every pool is idle"
    executor.shutdown(wait=False)
    print("pools: named threads, context only where asked, failures, and what each is doing")


async def lanes():
    pool = workers.Pool("lane", 4)
    gates, order = {}, []

    def work(tag):
        order.append(tag)
        assert gates[tag].wait(10)
        return tag

    def start(lane, tag, key=None, wait=5.0, **kwargs):
        gates[tag] = threading.Event()
        return asyncio.ensure_future(lane.run(pool, work, tag, key=key, wait=wait, **kwargs))

    async def until(condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("never came true")

    # in order, at most so many, and the rest never in a thread
    lane = workers.Lane(2, waiting=3)
    tasks = [start(lane, tag) for tag in "abcde"]
    await until(lambda: len(order) == 2)
    await asyncio.sleep(0.05)
    assert order == ["a", "b"] and lane.running == 2
    assert pool.state()["running"] == 2 and pool.state()["waiting"] == 0
    # a line that is full refuses at once
    started = time.monotonic()
    try:
        await start(lane, "f")
    except workers.Busy:
        assert time.monotonic() - started < 0.5
    else:
        raise AssertionError("the line has no bound")
    # so does a caller that will not wait
    for tag in "cde":
        gates[tag].set()
    gates["a"].set()
    assert await tasks[0] == "a"
    await until(lambda: "e" in order)
    gates["b"].set()
    assert await asyncio.gather(*tasks[1:]) == list("bcde")
    assert order == list("abcde")
    await until(lambda: lane.running == 0)
    first = start(lane, "g")
    second = start(lane, "h")
    await until(lambda: lane.running == 2)
    try:
        await start(lane, "i", wait=0)
    except workers.Busy:
        pass
    else:
        raise AssertionError("no place and no wait must refuse")
    # one that waited its time out leaves the line, which moves on without it
    short = start(lane, "j", wait=0.05)
    third = start(lane, "k")
    try:
        await short
    except workers.Busy:
        pass
    else:
        raise AssertionError("waited for ever")
    assert len(lane._line) == 1
    # and one whose caller goes while it waits
    gone = start(lane, "l")
    await asyncio.sleep(0.02)
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    assert len(lane._line) == 1 and "l" not in order
    for tag in "ghk":
        gates[tag].set()
    assert await asyncio.gather(first, second, third) == list("ghk")
    await until(lambda: lane.running == 0)

    # one caller's limit lets the others behind it past
    order.clear()
    lane = workers.Lane(2, per_key=1)
    tasks = {tag: start(lane, tag, key=tag[0]) for tag in ("x1", "x2", "x3", "y1", "y2")}
    await until(lambda: len(order) == 2)
    assert order == ["x1", "y1"]
    gates["x1"].set()
    await until(lambda: len(order) == 3)
    assert order[2] == "x2"         # not x3, and not y2: y is at its limit
    gates["x2"].set()
    gates["x3"].set()
    await until(lambda: "x3" in order)
    await tasks["x3"]
    assert "y2" not in order        # a free place, but y is at its own limit
    gates["y1"].set()
    gates["y2"].set()
    assert [await tasks[tag] for tag in ("x1", "x2", "y1", "y2")] == ["x1", "x2", "y1", "y2"]
    await until(lambda: lane.running == 0)
    assert not lane._by_key and not lane._line

    # a place is the thread's: a caller that goes asks its work to stop, and
    # the next one waits until it has
    order.clear()
    lane = workers.Lane(1)
    asked = []
    leaving = start(lane, "p", abandoned=lambda: asked.append("p"))
    await until(lambda: order == ["p"])
    waiting = start(lane, "q")
    await asyncio.sleep(0.02)
    leaving.cancel()
    await asyncio.gather(leaving, return_exceptions=True)
    assert asked == ["p"] and lane.running == 1 and order == ["p"]
    gates["p"].set()
    await until(lambda: order == ["p", "q"])
    gates["q"].set()
    assert await waiting == "q"
    await until(lambda: lane.running == 0)
    # cancelled in the very moment it was given its place: the place goes on
    blocker = start(lane, "r")
    await until(lambda: "r" in order)
    unlucky = start(lane, "s")
    after = start(lane, "t")
    await asyncio.sleep(0.02)
    gates["t"].set()
    entry = lane._line[0]
    entry[1].add_done_callback(lambda _future: unlucky.cancel())
    gates["r"].set()
    await blocker
    await asyncio.gather(unlucky, return_exceptions=True)
    assert await after == "t" and "s" not in order
    await until(lambda: lane.running == 0)
    # a failure frees its place like an answer
    try:
        await lane.run(pool, lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    await until(lambda: lane.running == 0)
    print("lanes: order, bounds, one caller's limit, waiting on the loop only, leaving, and "
          "places that belong to the work")


async def children():
    def child(*argv, **kwargs):
        # posix_spawn, so that no asyncio watcher or Popen collects it first
        return os.posix_spawn(argv[0], list(argv), dict(os.environ), **kwargs)
    before = threading.active_count()
    pids = [child("/bin/sleep", "0.2") for _ in range(40)]
    assert await workers.child_exit(pids[0], limit=0.01) is False
    assert all(await asyncio.gather(*(workers.child_exit(pid) for pid in pids)))
    assert threading.active_count() <= before, "waiting for a process takes no thread"
    assert await workers.child_exit(pids[0]) is True        # already collected
    stuck = child("/bin/sleep", "30")
    started = time.monotonic()
    assert await workers.child_exit(stuck, limit=0.1) is False
    assert time.monotonic() - started < 2
    os.kill(stuck, signal.SIGKILL)
    assert await workers.child_exit(stuck, limit=5) is True
    print("children: collected without threads, with and without a limit")


# ---- both runtimes ----

class Runtime:
    def __init__(self, name, build):
        self.name, self.build = name, build

    async def __aenter__(self):
        self.app = self.build()
        self.seen = []

        async def first(_app):
            # a hook like any other: the pool is already in place
            self.seen.append(await asyncio.get_running_loop().run_in_executor(
                None, lambda: threading.current_thread().name))
        self.app.on_startup.append(first)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:{}".format(site._server.sockets[0].getsockname()[1])
        self.http = aiohttp.ClientSession(
            headers={"X-Puppy-Token": config.get("auth.api_token")})
        return self

    async def __aexit__(self, *_exc):
        await self.http.close()
        await self.runner.cleanup()


async def runtimes(sid):
    loop = asyncio.get_running_loop()
    for name, build in (("full", build_app), ("backend", build_backend_app)):
        async with Runtime(name, build) as runtime:
            assert runtime.seen[0].startswith("puppy-io"), runtime.seen
            executor = workers.install(loop)
            assert loop._default_executor is executor and executor.size == workers.IO_THREADS == 32
            assert (await workers.io.run(lambda: threading.current_thread().name)).startswith("puppy-io")
            watchdog = runtime.app["puppy_watchdog"]
            assert watchdog._thread.is_alive() and watchdog.executor is executor
            # only a console can be told anything
            assert (watchdog.tell is not None) == (name == "full")
            await stuck_pools(runtime, sid)
        assert not watchdog._thread.is_alive()
    print("runtimes: one default pool of {} on each, before any hook, and a watchdog that goes "
          "with the app".format(workers.IO_THREADS))


async def stuck_pools(runtime, sid):
    """What the incident was: everything but the pages' own pool is full."""
    gate = threading.Event()
    stuck = []
    for pool in workers.POOLS:
        for _ in range(pool.threads + 20):
            stuck.append(pool.run(gate.wait, 60))
    try:
        for _ in range(500):
            if all(pool.state()["running"] == pool.threads for pool in workers.POOLS):
                break
            await asyncio.sleep(0.01)
        paths = ["/api/ping", "/api/sessions", "/api/sessions/{}".format(sid),
                 "/api/sessions/{}/events".format(sid), "/api/sessions/{}/files".format(sid)]
        if runtime.name == "full":
            paths += ["/", "/api/state", "/static/app.js", "/static/app.css", "/static/auth.css",
                      "/static/vendor/xterm.min.js", "/api/notices"]
        for path in paths:
            started = time.monotonic()
            async with runtime.http.get(runtime.url + path) as response:
                body = await response.read()
                assert response.status == 200, (runtime.name, path, response.status, body[:200])
            assert time.monotonic() - started < 2.0, (runtime.name, path)
        # a conversation opens too: its snapshot is larger than what aiohttp
        # compresses on the loop, so that frame goes through the default pool
        async with runtime.http.ws_connect(runtime.url + "/api/ws/session/{}".format(sid),
                                           compress=15) as ws:
            message = await asyncio.wait_for(ws.receive(), 5)
            snapshot = json.loads(message.data)
            assert len(message.data) > 64 * 1024, len(message.data)
            assert snapshot["type"] == "snapshot", snapshot["type"]
        assert all(pool.state()["running"] == pool.threads for pool in workers.POOLS)
    finally:
        gate.set()
        await asyncio.gather(*stuck, return_exceptions=True)
    print("  {}: pages, scripts, state, sessions and a conversation answer while every other "
          "pool is stuck".format(runtime.name))


# ---- the watchdog ----

class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))

    def of(self, part):
        return [line for _level, line in self.lines if part in line]


async def watchdog():
    loop = asyncio.get_running_loop()
    lines = Lines()
    workers.log.addHandler(lines)
    told = []
    executor = workers.Executor("io", 2)
    dog = workers.Watchdog(loop, executor, tell=told.append, every=0.05, after=0.3, repeat=0.4)
    gate = threading.Event()

    def a_piece_of_work_that_never_ends(secret):
        gate.wait(30)
    dog.start()
    try:
        await asyncio.sleep(0.6)
        assert not lines.lines and not told, (lines.lines, told)    # all is well
        stuck = [loop.run_in_executor(executor, a_piece_of_work_that_never_ends, "secret-value")
                 for _ in range(2)]
        for _ in range(100):
            if told:
                break
            await asyncio.sleep(0.05)
        assert len(told) == 1 and "worker threads are all busy" in told[0], told
        report = lines.of("the shared worker threads have not been free")[0]
        assert "io: 2 of 2 threads busy" in report, report
        assert "a_piece_of_work_that_never_ends" in report and "secret-value" not in report, report
        # the two stuck threads stand together, with where they are
        assert "2 threads: puppy-io_0, puppy-io_1" in report or \
            "2 threads: puppy-io_1, puppy-io_0" in report, report
        assert "tests/workers_test.py" in report and "puppy-watchdog" not in report, report
        # it repeats itself in the log while it lasts, and only so often
        await asyncio.sleep(3.0)
        repeats = len(lines.of("the shared worker threads have not been free"))
        assert 2 <= repeats <= workers.MAX_REPORTS, repeats
        assert len(told) == 1, told     # the console hears of it once
        gate.set()
        await asyncio.gather(*stuck)
        for _ in range(100):
            if lines.of("the shared worker threads answer again"):
                break
            await asyncio.sleep(0.05)
        assert len(lines.of("the shared worker threads answer again after")) == 1
        # a loop that stands still says where, at once, and is told afterwards
        lines.lines.clear()
        told.clear()

        def standing_still():
            time.sleep(1.2)
        standing_still()
        during = lines.of("the event loop has not answered")
        assert during and "standing_still" in during[0] and "MainThread" in during[0], lines.lines
        assert not told
        for _ in range(100):
            if told:
                break
            await asyncio.sleep(0.05)
        assert len(told) == 1 and re.match(
            r"Puppy did not respond for [1-9]\d* s · details in the log$", told[0]), told
        assert lines.of("the event loop answers again after")
    finally:
        gate.set()
        dog.stop()
        executor.shutdown(wait=False)
    assert not dog._thread.is_alive()

    # work waiting a long time for another pool's threads is one log line
    lines.lines.clear()
    gate = threading.Event()
    pool = workers.Pool("sample", 1)
    held = [pool.run(gate.wait, 30), pool.run(gate.wait, 30)]
    quiet = workers.Watchdog(loop, workers.Executor("io", 1), every=0.05, after=30, repeat=30)
    previous, backlog = workers.POOLS, workers.BACKLOG_SECONDS
    workers.POOLS, workers.BACKLOG_SECONDS = (pool,), 0.2
    quiet.start()
    try:
        await asyncio.sleep(1.0)
        found = lines.of("work is waiting for a thread")
        assert len(found) == 1 and "sample: 1 of 1 threads busy" in found[0], lines.lines
        gate.set()
        await asyncio.gather(*held)
    finally:
        quiet.stop()
        workers.POOLS, workers.BACKLOG_SECONDS = previous, backlog
        gate.set()
    # a stall nobody could look at - every thread held back, this one too - is
    # still told of when it is over
    probe = workers._Probe("loop", lambda answered: answered(), 10.0, 300.0)
    assert probe.look(100.0) is None
    probe.answer[:] = [100.2]
    assert probe.look(105.0) is None and probe.asked == 105.0
    probe.answer[:] = [128.0]
    assert probe.look(128.5) == ("recovered", 23.0)
    probe.answer[:] = []
    assert probe.look(138.0) is None
    assert probe.look(138.6)[0] == "stalled" and probe.look(139.0) is None
    assert probe.look(500.0)[0] == "stalled"
    # a closed loop or pool is not a stall
    other = asyncio.new_event_loop()
    done = workers.Executor("io", 1)
    done.shutdown()
    other.close()
    dead = workers.Watchdog(other, done, tell=told.append, every=0.02, after=0.05, repeat=1)
    lines.lines.clear()
    dead.start()
    await asyncio.sleep(0.4)
    dead.stop()
    assert not lines.lines, lines.lines
    workers.log.removeHandler(lines)

    # what a console is shown is kept in the history and said once
    sent = []
    original = runner.broadcast_update
    runner.broadcast_update = sent.append
    try:
        workers._notice("Puppy did not respond for 12 s · details in the log")
        workers._notice("Puppy did not respond for 12 s · details in the log")
    finally:
        runner.broadcast_update = original
    newest = notices.payload()["items"][0]
    assert (newest["text"], newest["tone"], newest["count"]) == \
        ("Puppy did not respond for 12 s · details in the log", "warn", 2), newest
    assert [item["type"] for item in sent] == ["toast", "toast"] and sent[0]["recorded"] is True
    assert notices.clean_text(sent[0]["text"]) == sent[0]["text"]
    print("watchdog: a full default pool, a loop that stands still, a backlog, repeats, recovery, "
          "and the notice")


async def main():
    test_sources()
    await pools()
    await lanes()
    await children()
    await watchdog()
    sid = db.create_session("Garden planner", "claude", str(TEST_ROOT), "", "", "#e0784f", "ask")
    for index in range(120):
        db.add_event(sid, "assistant", {"text": "{} ".format(index) + os.urandom(600).hex()})
    await runtimes(sid)


if __name__ == "__main__":
    config.ensure_dirs()
    config.load()
    db.connect()
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    print("PASS: worker pools")
