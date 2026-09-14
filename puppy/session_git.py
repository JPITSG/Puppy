"""Whether a session's working directory is a Git repository.

The sidebar carries a Git mark on every row, between the pin and the agent
notes. It says one thing for now - the directory is inside a Git work tree,
or it is not - and the answer is node-owned like the agent notes beside it,
because the directory lives on the node that runs the session.

Unlike the notes, the answer is cached rather than read on every payload: one
record per working directory, kept in memory only, refreshed by a worker on
this node every ``timers.git_check_minutes`` and again the moment a console
brings a session into focus (``POST /api/sessions/{sid}/git/refresh``,
registered in ``register_execution_api`` so both runtimes serve it and a
controller reaches a backend's copy through the ordinary proxy). A directory
no payload has an answer for yet wakes the worker, so a new session is
answered within moments rather than at the next scheduled pass. Nothing here
is persisted and nothing belongs in a backup.

Discovery follows Git's own rules closely enough for a mark: walk upward from
the directory looking for ``.git`` - a directory holding a ``HEAD`` is a git
dir, a file starting with ``gitdir:`` is the gitfile of a linked worktree or
submodule and counts when it names one - and give up at the filesystem root,
where the device changes, or below a ``GIT_CEILING_DIRECTORIES`` entry, which
is where ``git`` itself stops by default. It runs no process, so a directory
whose owner Git would call dubious still answers truthfully about what is on
disk.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time
from typing import Dict, Optional

from aiohttp import web

from puppy import config, db

log = logging.getLogger("puppy.session_git")

# The timers map key that paces the worker's full passes.
CHECK_TIMER = "git_check_minutes"
# The route suffix under /api/sessions/{sid}; the full runtime's snapshot
# guard leaves a refresh out of its mutation count because it changes nothing
# a backup could copy.
REFRESH_SUFFIX = "/git/refresh"
# A gitfile is one short line; anything longer is not one.
GITFILE_BYTES = 4096

_records: Dict[str, dict] = {}
_inflight: Dict[str, asyncio.Future] = {}
_task: Optional[asyncio.Task] = None
_wake: Optional[asyncio.Event] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_missing = False
_last_full: Optional[float] = None


# ---- discovery ----

def _git_dir_at(marker: str) -> bool:
    """Is ``marker`` (a ``<dir>/.git`` path) a git dir or a gitfile naming one?"""
    try:
        info = os.stat(marker)       # a symlinked .git is followed, as git does
    except OSError:
        return False
    if stat.S_ISDIR(info.st_mode):
        return os.path.isfile(os.path.join(marker, "HEAD"))
    if not stat.S_ISREG(info.st_mode) or info.st_size > GITFILE_BYTES:
        return False
    try:
        with open(marker, "rb") as handle:
            head = handle.read(GITFILE_BYTES)
    except OSError:
        return False
    if not head.startswith(b"gitdir:"):
        return False
    target = os.fsdecode(head[len(b"gitdir:"):].split(b"\n", 1)[0].strip())
    if not target:
        return False
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(marker), target)
    return os.path.isfile(os.path.join(target, "HEAD"))


def _ceilings() -> tuple:
    """Directories git will not climb into (GIT_CEILING_DIRECTORIES): the
    working directory itself is still examined, its parents above one of
    these are not."""
    raw = os.environ.get("GIT_CEILING_DIRECTORIES") or ""
    return tuple(os.path.realpath(entry) for entry in raw.split(os.pathsep) if entry)


def inspect(cwd) -> dict:
    """One check of one directory, run in a thread by the worker and the
    refresh route. The record it returns is what the payload carries."""
    now = time.time()
    root = str(cwd or "")
    try:
        here = os.stat(root) if root else None
    except OSError as exc:
        return {"repo": None, "checked_at": now,
                "error": exc.strerror or "directory is not available"}
    if here is None or not stat.S_ISDIR(here.st_mode):
        return {"repo": None, "checked_at": now,
                "error": "the working directory is not a directory"}
    ceilings = _ceilings()
    path = os.path.realpath(root)
    while True:
        if _git_dir_at(os.path.join(path, ".git")):
            return {"repo": True, "checked_at": now}
        parent = os.path.dirname(path)
        if parent == path or parent in ceilings:
            return {"repo": False, "checked_at": now}
        try:
            if os.stat(parent).st_dev != here.st_dev:
                return {"repo": False, "checked_at": now}
        except OSError:
            return {"repo": False, "checked_at": now}
        path = parent


# ---- the cache ----

def _public(entry: dict) -> dict:
    out = {"repo": entry.get("repo"), "checked_at": entry.get("checked_at")}
    if entry.get("repo") is None:
        out["error"] = str(entry.get("error") or "")
    return out


def record(cwd) -> Optional[dict]:
    """The record a session payload carries: a copy of the cached answer for
    ``cwd``, or None when this node has not looked yet. Asking about a
    directory nobody has looked at wakes the worker, so the None is brief."""
    key = str(cwd or "")
    entry = _records.get(key)
    if entry is None:
        if key:
            _note_missing()
        return None
    return _public(entry)


def _store(cwd: str, entry: dict) -> bool:
    """Keep one answer; True when a console would see a different mark."""
    previous = _records.get(cwd)
    _records[cwd] = entry
    return previous is None or previous.get("repo") != entry.get("repo") or \
        previous.get("error") != entry.get("error")


async def _check(cwd: str) -> dict:
    """One inspection per directory at a time: a pass and a focus refresh
    that meet on the same directory share the thread rather than racing. An
    inspection that fails outright is an answer too - "could not be checked"
    with the reason - never a hole a payload would keep asking to fill."""
    future = _inflight.get(cwd)
    if future is None:
        future = asyncio.get_running_loop().run_in_executor(None, inspect, cwd)
        _inflight[cwd] = future

        def settled(done, key=cwd):
            if _inflight.get(key) is done:
                _inflight.pop(key, None)
        future.add_done_callback(settled)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("git repository check of %s failed: %s", cwd, exc)
        return {"repo": None, "checked_at": time.time(),
                "error": "check failed: {}".format(exc)[:300]}


def _broadcast() -> None:
    from puppy import runner
    runner.broadcast_sessions()


async def refresh(session: dict) -> Optional[dict]:
    """Re-check one session's directory now (a console focused it) and
    publish the list if its mark changed."""
    cwd = str((session or {}).get("cwd") or "")
    if not cwd:
        return None
    entry = await _check(cwd)
    if _store(cwd, entry):
        _broadcast()
    return _public(entry)


# ---- the worker ----

def interval_seconds() -> float:
    return config.timer_seconds(CHECK_TIMER)


def wake() -> None:
    loop, event = _loop, _wake
    if loop is None or event is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        event.set()
    else:
        loop.call_soon_threadsafe(event.set)


def _note_missing() -> None:
    global _missing
    _missing = True
    wake()


def settings_changed() -> None:
    """The interval moved: the sleeping worker re-aims at the new deadline."""
    wake()


async def _pass(full: bool) -> bool:
    """Check every directory a session uses (``full``) or only those without
    an answer, forget the directories no session uses any more, and publish
    the list when any mark changed."""
    wanted = {}
    for session in db.list_sessions(include_archived=True):
        cwd = str(session["cwd"] or "")
        if cwd:
            wanted[cwd] = True
    changed = False
    for cwd in list(wanted):
        if full or cwd not in _records:
            changed = _store(cwd, await _check(cwd)) or changed
    for stale in [cwd for cwd in _records if cwd not in wanted]:
        _records.pop(stale, None)
    if changed:
        _broadcast()
    return changed


async def _run(app) -> None:
    global _missing, _last_full
    while True:
        if app.get("puppy_snapshot_busy"):
            # A restore is replacing the very list this reads; the answers
            # already held are about directories, not the database, and
            # keep. Look again once it is over.
            await asyncio.sleep(1.0)
            continue
        due = 0.0 if _last_full is None else _last_full + interval_seconds()
        now = time.monotonic()
        full = now >= due
        partial = _missing
        # Clear the requests before the pass, so one raised while it runs
        # is answered by the next iteration instead of lost.
        _missing = False
        _wake.clear()
        if full or partial:
            if full:
                _last_full = now
            try:
                await _pass(full)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("git repository check failed")
        remaining = (_last_full if _last_full is not None else now) + \
            interval_seconds() - time.monotonic()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=max(0.0, remaining))
        except asyncio.TimeoutError:
            pass


async def _lifecycle(app):
    global _task, _wake, _loop
    _loop = asyncio.get_running_loop()
    _wake = asyncio.Event()
    _task = asyncio.create_task(_run(app), name="puppy-session-git")
    try:
        yield
    finally:
        task, _task = _task, None
        _wake = None
        _loop = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ---- the route ----

def _session_or_404(request):
    session = db.get_session(int(request.match_info["sid"]))
    if session is None:
        raise web.HTTPNotFound(text=json.dumps({"error": "session not found"}),
                               content_type="application/json")
    return session


async def h_refresh(request: web.Request):
    session = _session_or_404(request)
    return web.json_response({"ok": True, "git": await refresh(session)})


def register(app) -> None:
    """Both runtimes: the worker and the focus refresh."""
    if app.get("puppy_session_git_registered"):
        return
    app["puppy_session_git_registered"] = True
    app.router.add_post("/api/sessions/{sid:\\d+}" + REFRESH_SUFFIX, h_refresh)
    app.cleanup_ctx.append(_lifecycle)


def reset_for_tests() -> None:
    global _task, _wake, _loop, _missing, _last_full
    _records.clear()
    _inflight.clear()
    _task = None
    _wake = None
    _loop = None
    _missing = False
    _last_full = None
