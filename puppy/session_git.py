"""Whether a session's working directory is a Git repository, and whether
that repository is holding work.

The sidebar carries a Git mark on every row, between the pin and the agent
notes. It says two things: the directory is inside a Git work tree, or it is
not; and, inside one, whether there is uncommitted or unpushed work - the
heads-up that turns the mark orange. The answer is node-owned like the agent
notes beside it, because the directory lives on the node that runs the
session.

Unlike the notes, the answer is cached rather than read on every payload: one
record per working directory, kept in memory only, refreshed by a worker on
this node every ``timers.git_check_minutes``, again the moment a console
brings a session into focus (``POST /api/sessions/{sid}/git/refresh``,
registered in ``register_execution_api`` so both runtimes serve it and a
controller reaches a backend's copy through the ordinary proxy), and again
when something on this node may have changed the repository: a prompt ended
in the directory (``turn_finished`` - a task's prompt is left out, its copy
being a repository of its own), a task's changes were applied to it, or its
agent notes were written (``request``). A directory no payload has an answer
for yet wakes the worker, so a new session is answered within moments rather
than at the next scheduled pass. Nothing here is persisted and nothing
belongs in a backup.

Discovery follows Git's own rules closely enough for a mark: walk upward from
the directory looking for ``.git`` - a directory holding a ``HEAD`` is a git
dir, a file starting with ``gitdir:`` is the gitfile of a linked worktree or
submodule and counts when it names one - and give up at the filesystem root,
where the device changes, or below a ``GIT_CEILING_DIRECTORIES`` entry, which
is where ``git`` itself stops by default. It runs no process, so a directory
whose owner Git would call dubious still answers truthfully about what is on
disk. The work state is the one thing ``git`` itself is asked: what its
status lists - each path sorted by what git would do with it, so an orange
mark's tooltip can say why - the branch that is checked out, and which
commits no remote holds. A ``git`` that refuses (or is not installed) does
not unmake the repository - the record keeps ``repo`` true and carries the
reason instead of the counts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
import subprocess
import time
from typing import Dict, Optional, Set

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
# Reading the work state runs git; every command is bounded by this, so a
# repository on a stalled network mount cannot hold the worker for ever.
GIT_TIMEOUT = 30.0
# Wording the record carries when git would not answer; cut like the
# discovery's own reasons so a label stays a label.
REASON_CHARS = 300
# The rundown behind the counts: the checked-out branch and the paths of
# ``changes`` by kind. Carried beside ``changes``/``unpushed`` when git read
# the repository, absent when it would not (and from a node before them).
DETAIL_KEYS = ("branch", "staged", "unstaged", "untracked", "conflicts")
# What a status line is, by its first field in porcelain v2: an ordinary or
# renamed change (with the index and work-tree columns behind it), an
# unmerged path, an untracked one; ignored paths are never listed here.
_HEADER, _CHANGE, _RENAME, _UNMERGED, _UNTRACKED = "#", "1", "2", "u", "?"

_records: Dict[str, dict] = {}
_inflight: Dict[str, asyncio.Future] = {}
# directories to look at again before the next pass (request)
_requested: Set[str] = set()
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


class GitRefused(Exception):
    """git did not answer: it is missing, it timed out, or it refused with
    the message this carries."""


def _reason(stderr: bytes, returncode: int) -> str:
    for line in stderr.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line:
            for prefix in ("fatal: ", "error: "):
                if line.startswith(prefix):
                    line = line[len(prefix):]
            return line[:REASON_CHARS]
    return "git exited with status {}".format(returncode)


def _run_git(cwd: str, *args: str) -> "subprocess.CompletedProcess":
    """One read-only, non-interactive git command in ``cwd``: no prompt, no
    optional lock on the index (a session's engine may be using it), no file
    monitor daemon left behind, English messages for the record, and its
    whole process group ended when GIT_TIMEOUT runs out. The process's own
    git environment is not inherited: the session's directory is the
    repository, whatever this process was started with."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_NAMESPACE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    argv = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false"] + list(args)
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except FileNotFoundError:
        raise GitRefused("git is not installed on this backend")
    except OSError as exc:
        raise GitRefused("git could not be run: {}".format(exc.strerror or exc)[:REASON_CHARS])
    with process:
        try:
            out, err = process.communicate(timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise GitRefused("git did not answer within {:.0f} seconds".format(GIT_TIMEOUT))
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _git(cwd: str, *args: str) -> str:
    result = _run_git(cwd, *args)
    if result.returncode:
        raise GitRefused(_reason(result.stderr, result.returncode))
    return result.stdout.decode("utf-8", "replace")


def work_state(cwd: str) -> dict:
    """What the repository around ``cwd`` is holding, as ``git`` sees it:
    ``changes`` is the number of paths its status lists - staged, unstaged
    and untracked alike, an untracked directory as one, the whole work tree
    whatever subdirectory the session sits in - and ``unpushed`` the number
    of commits on HEAD that no remote-tracking branch holds, or None when
    no remote is configured and there is nowhere to push to. A branch
    without an upstream still counts: its commits are unpushed until some
    remote has them.

    Beside them, the rundown a tooltip needs: ``branch`` is the checked-out
    branch (an unborn one included), None with HEAD detached; ``staged``,
    ``unstaged``, ``untracked`` and ``conflicts`` sort the same paths by
    what git would do with them - a path with staged and further unstaged
    edits is staged, an unmerged path is a conflict whatever else it holds
    - so the four add up to ``changes``. Raises GitRefused when git would
    not answer."""
    status = _git(cwd, "status", "--porcelain=v2", "--branch", "--untracked-files=normal")
    branch = None
    kinds = {"staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}
    changes = 0
    for line in status.split("\n"):
        if not line:
            continue
        kind = line[0]
        if kind == _HEADER:
            if line.startswith("# branch.head "):
                head = line[len("# branch.head "):]
                branch = None if head == "(detached)" else head
            continue
        changes += 1
        if kind == _UNTRACKED:
            kinds["untracked"] += 1
        elif kind == _UNMERGED:
            kinds["conflicts"] += 1
        elif kind in (_CHANGE, _RENAME):
            # "<kind> <XY> ...": X is the index column, "." when the index
            # holds nothing for the path and only the work tree changed
            kinds["staged" if line[2:3] != "." else "unstaged"] += 1
        # an entry kind this does not know is still a listed path, so it
        # counts as a change and belongs to no kind
    state = dict(kinds, changes=changes, branch=branch)
    remotes = [line for line in _git(cwd, "remote").split("\n") if line.strip()]
    if not remotes:
        return dict(state, unpushed=None)
    # an unborn branch has nothing to push yet, and no HEAD to count from
    if _run_git(cwd, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode:
        return dict(state, unpushed=0)
    count = _git(cwd, "rev-list", "--count", "HEAD", "--not", "--remotes").strip()
    try:
        unpushed = int(count)
    except ValueError:
        raise GitRefused("git rev-list answered {!r}".format(count[:40]))
    return dict(state, unpushed=unpushed)


def _work_fields(cwd: str) -> dict:
    """The work-state half of a repository's record: the counts, or the
    reason git would not give them."""
    try:
        return work_state(cwd)
    except GitRefused as exc:
        return {"changes": None, "unpushed": None, "error": str(exc)}
    except Exception as exc:  # a broken pipe, a decode error: still an answer
        log.warning("git work state of %s failed: %s", cwd, exc)
        return {"changes": None, "unpushed": None,
                "error": "check failed: {}".format(exc)[:REASON_CHARS]}


def inspect(cwd) -> dict:
    """One check of one directory, run in a thread by the worker and the
    refresh route. The record it returns is what the payload carries: the
    discovery's answer, and for a repository the work state git reports."""
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
            return dict({"repo": True, "checked_at": now}, **_work_fields(root))
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
    if out["repo"] is True:
        out["changes"] = entry.get("changes")
        out["unpushed"] = entry.get("unpushed")
        # the rundown rides only with counts git gave: a record carrying
        # the reason instead has none, and a console says only what it knows
        for key in DETAIL_KEYS:
            if key in entry:
                out[key] = entry[key]
    if out["repo"] is None or (out["repo"] is True and entry.get("error")):
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
    """Keep one answer; True when a console would see a different mark,
    read a different label or a different tooltip - the counts, the branch
    and the kinds are all part of what it says."""
    previous = _records.get(cwd)
    _records[cwd] = entry
    return previous is None or any(
        previous.get(key) != entry.get(key)
        for key in ("repo", "error", "changes", "unpushed") + DETAIL_KEYS)


async def _check(cwd: str, fresh: bool = False) -> dict:
    """One inspection per directory at a time: a pass and a focus refresh
    that meet on the same directory share the thread rather than racing. An
    inspection that fails outright is an answer too - "could not be checked"
    with the reason - never a hole a payload would keep asking to fill.

    ``fresh`` is the request form: the caller knows something changed just
    now, so an inspection already under way - which may have read the
    directory before that change - is not good enough to join. It finishes
    for those who asked for it, and this looks once more."""
    future = _inflight.get(cwd)
    if fresh and future is not None:
        stale = future
        try:
            await asyncio.shield(stale)
        except Exception:
            pass
        future = _inflight.get(cwd)
        if future is stale:
            future = None
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


def request(cwd) -> None:
    """Something on this node may have changed the repository around
    ``cwd`` - a task's changes were applied to it, its agent notes were
    written - so it is looked at again by the worker's next iteration, not
    at the next scheduled pass, and a changed mark is published. A
    directory no session uses is ignored by the pass that serves this."""
    key = str(cwd or "")
    if not key:
        return
    _requested.add(key)
    wake()


def turn_finished(session) -> None:
    """A prompt ended in this session's directory. The turn may have edited,
    committed or pushed, so the mark is looked at again now. A task's
    prompt is left out: its copy is a repository of its own, nothing it
    does reaches the project's, and the project's mark moves when the task
    is applied (session_tasks asks for that through ``request``)."""
    if not session or not session.get("cwd"):
        return
    from puppy import session_tasks
    if session_tasks.record(session["id"]):
        return
    request(session["cwd"])


async def _pass(full: bool, requested=()) -> bool:
    """Check every directory a session uses (``full``), or only those
    without an answer and those asked about again (``requested``); forget
    the directories no session uses any more, and publish the list when
    any mark changed."""
    wanted = {}
    for session in db.list_sessions(include_archived=True):
        cwd = str(session["cwd"] or "")
        if cwd:
            wanted[cwd] = True
    changed = False
    for cwd in list(wanted):
        if full or cwd not in _records or cwd in requested:
            changed = _store(cwd, await _check(cwd, fresh=cwd in requested)) or changed
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
        requested = set(_requested)
        partial = _missing or bool(requested)
        # Clear the requests before the pass, so one raised while it runs
        # is answered by the next iteration instead of lost.
        _missing = False
        _requested.clear()
        _wake.clear()
        if full or partial:
            if full:
                _last_full = now
            try:
                await _pass(full, requested)
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
    _requested.clear()
    _task = None
    _wake = None
    _loop = None
    _missing = False
    _last_full = None
