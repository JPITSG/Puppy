"""Whether a session's working directory is a Git repository, and whether
that repository is holding work.

The sidebar carries a Git mark on every row, between the pin and the agent
notes. It says two things: the directory is inside a Git work tree, or it is
not; and, inside one, whether there is uncommitted or unpushed work - the
heads-up that turns the mark orange. The answer is node-owned like the agent
notes beside it, because the directory lives on the node that runs the
session. A repository's mark opens a sheet that says exactly what the mark
summarises: ``GET /api/sessions/{sid}/git`` looks at the directory once more
and lists the paths behind ``changes`` and the commits behind ``unpushed``
(``detail``), bringing the mark's own record up to date by the same look.
The sheet also acts on the two counts, and these are the only writes this
module makes: ``POST /api/sessions/{sid}/git/push`` sends the checked-out
branch's commits where a push would go, and ``POST .../git/revert`` discards
every uncommitted change in the work tree. Both run under the same project
lock a task apply takes, so they refuse while a turn is running or queued
anywhere in the project and a turn sent meanwhile waits for them, and both
answer with the same fresh look the sheet's read gives.

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

from puppy import config, db, operations

log = logging.getLogger("puppy.session_git")

# The timers map key that paces the worker's full passes.
CHECK_TIMER = "git_check_minutes"
# The route suffix under /api/sessions/{sid}; the full runtime's snapshot
# guard leaves a refresh out of its mutation count because it changes nothing
# a backup could copy.
REFRESH_SUFFIX = "/git/refresh"
# The sheet's actions, one route suffix each under /api/sessions/{sid}/git.
ACTIONS = ("push", "revert")
# A gitfile is one short line; anything longer is not one.
GITFILE_BYTES = 4096
# Reading the work state runs git; every command is bounded by this, so a
# repository on a stalled network mount cannot hold the worker for ever.
GIT_TIMEOUT = 30.0
# The sheet's actions are bounded by this instead: a push crosses the
# network, and a reset of a large tree is longer than a status of it.
ACTION_TIMEOUT = 300.0
# Wording the record carries when git would not answer; cut like the
# discovery's own reasons so a label stays a label.
REASON_CHARS = 300
# The rundown behind the counts: the checked-out branch and the paths of
# ``changes`` by kind. Carried beside ``changes``/``unpushed`` when git read
# the repository, absent when it would not (and from a node before them).
DETAIL_KEYS = ("branch", "staged", "unstaged", "untracked", "conflicts")
# The sheet's listing is bounded: this many paths and this many commits at
# most, the rest reported as a count (``more_paths``/``more_commits``).
DETAIL_PATHS = 500
DETAIL_COMMITS = 200
# The sheet's history - the short log of everything on HEAD - is read a page
# at a time as the console scrolls it: this many commits per page at most,
# from the ``skip`` the console names.
LOG_PAGE = 100
# What a listing inspection carries beyond the record: the work tree's root
# and the listing itself. Neither belongs in the cache or a payload.
_LISTING_KEYS = ("root", "detail")
# What a status line is, by its first field in porcelain v2: an ordinary or
# renamed change (with the index and work-tree columns behind it), an
# unmerged path, an untracked one; ignored paths are never listed here.
_HEADER, _CHANGE, _RENAME, _UNMERGED, _UNTRACKED = "#", "1", "2", "u", "?"

_records: Dict[str, dict] = {}
_inflight: Dict[str, asyncio.Future] = {}
# the directories whose in-flight inspection is a listing one
_listing: Set[str] = set()
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


def _git_command(args) -> tuple:
    """The argv and environment of every git command here: non-interactive
    (no prompt on a terminal there is none of), no optional lock on the
    index (a session's engine may be using it), no file monitor daemon left
    behind, English messages for the record. The process's own git
    environment is not inherited: the session's directory is the
    repository, whatever this process was started with."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_NAMESPACE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    return ["git", "--no-optional-locks", "-c", "core.fsmonitor=false"] + list(args), env


def _run_git(cwd: str, *args: str) -> "subprocess.CompletedProcess":
    """One read-only git command in ``cwd`` (see _git_command), its whole
    process group ended when GIT_TIMEOUT runs out."""
    argv, env = _git_command(args)
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


def _parse_status(text: str) -> dict:
    """``git status --porcelain=v2 --branch -z`` read into its branch header
    - ``oid`` (None on an unborn branch), ``branch`` (None with HEAD
    detached), ``upstream`` and the ``ahead``/``behind`` counts git gives
    only with an upstream - and one ``(kind, code, path, origin)`` row per
    listed path: the kind what git would do with the path (``staged``,
    ``unstaged``, ``untracked``, ``conflicts``; "" for an entry kind this
    does not know, still a listed path), the code git's own two-column XY,
    and ``origin`` the old name of a rename or copy. NUL terminates every
    entry and separates a rename's two names, so a path is carried exactly
    as git holds it, never C-quoted."""
    head = {"oid": None, "branch": None, "upstream": None, "ahead": None, "behind": None}
    rows = []
    tokens = text.split("\0")
    index = 0
    while index < len(tokens):
        line = tokens[index]
        index += 1
        if not line:
            continue
        kind = line[0]
        if kind == _HEADER:
            fields = line.split(" ", 2)
            if len(fields) == 3:
                name, value = fields[1], fields[2]
                if name == "branch.oid":
                    head["oid"] = None if value == "(initial)" else value
                elif name == "branch.head":
                    head["branch"] = None if value == "(detached)" else value
                elif name == "branch.upstream":
                    head["upstream"] = value
                elif name == "branch.ab":
                    ahead, _, behind = value.partition(" ")
                    try:
                        head["ahead"] = int(ahead.lstrip("+"))
                        head["behind"] = int(behind.lstrip("-"))
                    except ValueError:
                        head["ahead"] = head["behind"] = None
            continue
        if kind == _UNTRACKED:
            rows.append(("untracked", "??", line[2:], None))
        elif kind == _UNMERGED:
            # "u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>"
            fields = line.split(" ", 10)
            rows.append(("conflicts", fields[1] if len(fields) > 1 else "",
                         fields[10] if len(fields) > 10 else line, None))
        elif kind in (_CHANGE, _RENAME):
            # "1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>" or
            # "2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>" with
            # the old name in the next entry: X is the index column, "."
            # when the index holds nothing for the path and only the work
            # tree changed
            fields = line.split(" ", 8 if kind == _CHANGE else 9)
            code = fields[1] if len(fields) > 1 else ""
            path = fields[-1] if len(fields) == (9 if kind == _CHANGE else 10) else line
            origin = None
            if kind == _RENAME:
                origin = tokens[index] if index < len(tokens) else None
                index += 1
            rows.append(("staged" if code[:1] != "." else "unstaged", code, path, origin))
        else:
            # an entry kind this does not know is still a listed path, so it
            # counts as a change and belongs to no kind
            rows.append(("", "", line, None))
    return {"head": head, "rows": rows}


def _commits(cwd: str, *revs: str, skip: int = 0, limit=None) -> list:
    """The commits git lists for ``revs`` - ``HEAD --not --remotes`` for
    the unpushed ones, ``HEAD`` alone for the history - newest first, at
    most ``limit`` of them (DETAIL_COMMITS unless given) from ``skip`` on:
    the abbreviated hash git chooses for this repository, the subject, the
    author and the commit time. NUL separates the fields and ends each
    record, so a subject is carried exactly."""
    if limit is None:
        limit = DETAIL_COMMITS
    out = _git(cwd, "log", *revs, "-z", "--skip={}".format(int(skip)),
               "--max-count={}".format(int(limit)), "--format=%h%x00%s%x00%an%x00%ct")
    fields = out.split("\0")
    commits = []
    for at in range(0, len(fields) - 3, 4):
        short, subject, author, stamp = fields[at:at + 4]
        if not short:
            continue
        try:
            when = int(stamp)
        except ValueError:
            when = None
        commits.append({"hash": short, "subject": subject, "author": author, "at": when})
    return commits


def work_state(cwd: str, listing: bool = False) -> dict:
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
    - so the four add up to ``changes``.

    With ``listing``, the sheet's ``detail`` as well, from the same status
    read: ``head`` (the commit HEAD is at, None on an unborn branch),
    ``upstream`` with its ``ahead``/``behind`` counts (None without one),
    the ``remotes``, the first DETAIL_PATHS ``paths`` behind ``changes``
    (``kind``, git's ``code``, ``path`` and the ``from`` of a rename) and
    the first DETAIL_COMMITS ``commits`` behind ``unpushed``, with
    ``more_paths``/``more_commits`` counting whatever the bounds cut.
    Raises GitRefused when git would not answer."""
    status = _parse_status(_git(cwd, "status", "--porcelain=v2", "--branch", "-z",
                                "--untracked-files=normal"))
    head, rows = status["head"], status["rows"]
    kinds = {"staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}
    for kind, _code, _path, _origin in rows:
        if kind in kinds:
            kinds[kind] += 1
    state = dict(kinds, changes=len(rows), branch=head["branch"])
    remotes = [line for line in _git(cwd, "remote").split("\n") if line.strip()]
    commits = []
    if not remotes:
        unpushed = None
    # an unborn branch has nothing to push yet, and no HEAD to count from
    elif _run_git(cwd, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode:
        unpushed = 0
    else:
        count = _git(cwd, "rev-list", "--count", "HEAD", "--not", "--remotes").strip()
        try:
            unpushed = int(count)
        except ValueError:
            raise GitRefused("git rev-list answered {!r}".format(count[:40]))
        if listing and unpushed:
            commits = _commits(cwd, "HEAD", "--not", "--remotes")
    state["unpushed"] = unpushed
    if listing:
        paths = []
        for kind, code, path, origin in rows[:DETAIL_PATHS]:
            entry = {"kind": kind, "code": code, "path": path}
            if origin is not None:
                entry["from"] = origin
            paths.append(entry)
        state["detail"] = {
            "head": head["oid"], "upstream": head["upstream"],
            "ahead": head["ahead"], "behind": head["behind"], "remotes": remotes,
            "push_to": _push_label(_push_target(cwd, head["branch"], remotes)),
            "paths": paths, "commits": commits,
            "more_paths": max(0, len(rows) - len(paths)),
            "more_commits": max(0, (unpushed or 0) - len(commits)),
        }
    return state


def _config_value(cwd: str, key: str) -> str:
    """One git configuration value, "" when it is not set."""
    result = _run_git(cwd, "config", "--get", key)
    return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""


def _push_target(cwd: str, branch, remotes: list) -> Optional[dict]:
    """Where ``git push`` would send the checked-out branch: the remote and
    the ref of its upstream when it has one, otherwise ``remote.pushDefault``
    or the only remote there is - a push that sets the upstream as it goes.
    None with HEAD detached, without a remote, or with several remotes and
    nothing saying which: the sheet offers no Push then."""
    if not isinstance(branch, str) or not branch or not remotes:
        return None
    remote = _config_value(cwd, "branch.{}.remote".format(branch))
    merge = _config_value(cwd, "branch.{}.merge".format(branch))
    if remote in remotes and merge:
        return {"remote": remote, "ref": merge, "upstream": True}
    remote = _config_value(cwd, "remote.pushDefault") or (remotes[0] if len(remotes) == 1 else "")
    if remote not in remotes:
        return None
    return {"remote": remote, "ref": "refs/heads/" + branch, "upstream": False}


def _push_label(target: Optional[dict]) -> Optional[str]:
    """The target as the sheet names it: ``origin/main``."""
    if not target:
        return None
    ref = target["ref"]
    return "{}/{}".format(target["remote"],
                          ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref)


def _work_fields(cwd: str, listing: bool = False) -> dict:
    """The work-state half of a repository's record: the counts (and the
    listing behind them when asked), or the reason git would not give
    them."""
    try:
        return work_state(cwd, listing)
    except GitRefused as exc:
        return {"changes": None, "unpushed": None, "error": str(exc)}
    except Exception as exc:  # a broken pipe, a decode error: still an answer
        log.warning("git work state of %s failed: %s", cwd, exc)
        return {"changes": None, "unpushed": None,
                "error": "check failed: {}".format(exc)[:REASON_CHARS]}


def inspect(cwd, listing: bool = False) -> dict:
    """One check of one directory, run in a thread by the worker and the
    refresh route. The record it returns is what the payload carries: the
    discovery's answer, and for a repository the work state git reports.
    A ``listing`` inspection - the sheet's read - adds what the payload
    never carries: the work tree's ``root`` the discovery stopped at, and
    the ``detail`` git listed behind the counts (absent when it refused)."""
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
            entry = dict({"repo": True, "checked_at": now}, **_work_fields(root, listing))
            if listing:
                entry["root"] = path
            return entry
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
    and the kinds are all part of what it says. A listing inspection's
    extras are not kept: the cache holds records, not path lists."""
    entry = {key: value for key, value in entry.items() if key not in _LISTING_KEYS}
    previous = _records.get(cwd)
    _records[cwd] = entry
    return previous is None or any(
        previous.get(key) != entry.get(key)
        for key in ("repo", "error", "changes", "unpushed") + DETAIL_KEYS)


async def _check(cwd: str, fresh: bool = False, listing: bool = False) -> dict:
    """One inspection per directory at a time: a pass and a focus refresh
    that meet on the same directory share the thread rather than racing. An
    inspection that fails outright is an answer too - "could not be checked"
    with the reason - never a hole a payload would keep asking to fill.

    ``fresh`` is the request form: the caller knows something changed just
    now, so an inspection already under way - which may have read the
    directory before that change - is not good enough to join. It finishes
    for those who asked for it, and this looks once more. ``listing`` is
    the sheet's form: an inspection without the listing is not good enough
    to join either, while one with it serves a plain check as well."""
    while True:
        future = _inflight.get(cwd)
        if future is None or not (fresh or (listing and cwd not in _listing)):
            break
        try:
            await asyncio.shield(future)
        except Exception:
            pass
        if _inflight.get(cwd) is future:
            future = None
            break
        # whatever replaced it began after this call did, which is all that
        # fresh asks for; whether it lists is judged again
        fresh = False
    if future is None:
        future = asyncio.get_running_loop().run_in_executor(None, inspect, cwd, listing)
        _inflight[cwd] = future
        if listing:
            _listing.add(cwd)

        def settled(done, key=cwd):
            if _inflight.get(key) is done:
                _inflight.pop(key, None)
                _listing.discard(key)
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


async def detail(session: dict) -> dict:
    """What the sheet shows: one more look at the session's directory, this
    time listing the paths and the commits behind the counts (``detail``,
    with the work tree's ``root``; both None outside a repository and the
    listing None when git refused), and the mark's own record brought up to
    date by the same look - published like a refresh when it changed."""
    cwd = str((session or {}).get("cwd") or "")
    if not cwd:
        return {"git": None, "root": None, "detail": None}
    entry = await _check(cwd, listing=True)
    if _store(cwd, entry):
        _broadcast()
    return {"git": _public(entry), "root": entry.get("root"), "detail": entry.get("detail")}


# ---- the history ----

def _log_page(cwd: str, skip: int, limit: int) -> dict:
    """One page of the short log: the ``total`` commits on HEAD (0 on an
    unborn branch, which has none to list), the ``commits`` from ``skip``
    on - ``limit`` at most, as ``_commits`` reads them - and whether
    ``more`` follow. Run in a thread like an inspection; git is asked from
    the session's own directory, so a directory outside a repository is
    git's refusal. Raises GitRefused when git would not answer."""
    if _run_git(cwd, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode:
        # no HEAD to count from: either an unborn branch or no repository,
        # which rev-parse tells apart by refusing the directory itself
        _git(cwd, "rev-parse", "--git-dir")
        return {"total": 0, "skip": skip, "commits": [], "more": False}
    count = _git(cwd, "rev-list", "--count", "HEAD").strip()
    try:
        total = int(count)
    except ValueError:
        raise GitRefused("git rev-list answered {!r}".format(count[:40]))
    commits = _commits(cwd, "HEAD", skip=skip, limit=limit) if skip < total else []
    return {"total": total, "skip": skip, "commits": commits,
            "more": skip + len(commits) < total}


async def history(session: dict, skip: int = 0, limit: int = LOG_PAGE) -> dict:
    """The sheet's history, one page: what ``_log_page`` reads for the
    session's directory, in a thread so a slow repository never holds the
    loop. Bounds are the caller's: ``skip`` is any count from 0, ``limit``
    at most LOG_PAGE."""
    cwd = str((session or {}).get("cwd") or "")
    if not cwd:
        raise GitRefused("the session has no working directory")
    skip = max(0, int(skip))
    limit = max(1, min(int(limit), LOG_PAGE))
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _log_page, cwd, skip, limit)


# ---- the actions ----

class ActionRefused(Exception):
    """The action cannot run as things stand; the reason is for the console."""


def _action_git(cwd: str, *args: str) -> "subprocess.CompletedProcess":
    """One git command of the sheet's actions - the reads' environment and
    prefix, ACTION_TIMEOUT, and the process group ended when the console
    cancels the operation as well as at the deadline."""
    argv, env = _git_command(args)
    try:
        return operations.run_process(argv, timeout=ACTION_TIMEOUT, cwd=cwd, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise GitRefused("git is not installed on this backend")
    except subprocess.TimeoutExpired:
        raise GitRefused("git did not answer within {:.0f} seconds".format(ACTION_TIMEOUT))
    except OSError as exc:
        raise GitRefused("git could not be run: {}".format(exc.strerror or exc)[:REASON_CHARS])


def _push_reason(stderr: bytes, returncode: int) -> str:
    """The line of a failed push worth showing: the rejected ref's own
    (``[rejected] main -> main (fetch first)``), else the first line that is
    not a hint, without git's ``fatal:``/``error:`` prefix."""
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines()
             if line.strip()]
    for line in lines:
        if line.startswith("! "):
            return " ".join(line[2:].split())[:REASON_CHARS]
    for line in lines:
        if line.startswith("hint:"):
            continue
        for prefix in ("fatal: ", "error: "):
            if line.startswith(prefix):
                line = line[len(prefix):]
        return line[:REASON_CHARS]
    return "git exited with status {}".format(returncode)


def _push(root: str) -> dict:
    """Push the checked-out branch where a push would go (_push_target),
    from the work tree's root: to its upstream, or to the one remote there
    is with the upstream set by the push. Hooks run as they would from a
    terminal; there is no force. Returns the target as the sheet names it."""
    status = _parse_status(_git(root, "status", "--porcelain=v2", "--branch", "-z",
                                "--untracked-files=no"))
    branch = status["head"]["branch"]
    if branch is None:
        raise ActionRefused("HEAD is detached; check out a branch to push it")
    remotes = [line for line in _git(root, "remote").split("\n") if line.strip()]
    target = _push_target(root, branch, remotes)
    if target is None:
        raise ActionRefused("No remote to push to" if not remotes else
                            "The branch has no upstream and there is more than one remote; "
                            "push it once with -u to choose")
    args = ["push"] + ([] if target["upstream"] else ["--set-upstream"]) + \
        [target["remote"], "{}:{}".format(branch, target["ref"])]
    result = _action_git(root, *args)
    if result.returncode:
        raise GitRefused(_push_reason(result.stderr, result.returncode))
    return {"to": _push_label(target)}


def _revert(root: str, cwd: str) -> None:
    """Discard every uncommitted change in the work tree, from its root:
    the index and the tracked paths back to HEAD (a merge stopped on a
    conflict is abandoned with them; an unborn branch has no HEAD, so its
    index is emptied instead), then the untracked paths removed - never the
    ignored ones, which were never part of the work, and never a nested
    repository. The session's own directory is put back if it went with
    them (an untracked directory the session was created in): empty, it is
    no change to git, and the session keeps a place to work."""
    if _action_git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode:
        result = _action_git(root, "read-tree", "--empty")
    else:
        result = _action_git(root, "reset", "--hard", "--quiet", "HEAD")
    if result.returncode:
        raise GitRefused(_reason(result.stderr, result.returncode))
    result = _action_git(root, "clean", "-fd", "--quiet")
    if result.returncode:
        raise GitRefused(_reason(result.stderr, result.returncode))
    os.makedirs(cwd, exist_ok=True)


async def act(session: dict, action: str) -> dict:
    """One of the sheet's actions on the session's repository, then the
    same fresh look the sheet's read gives, stored and published like it.
    Refused for a task's copy (reviewed and applied from Main) and a mirror
    of another node's project (acted on there), while Puppy drains, outside
    a repository or where git would not read one; under the project lock a
    task apply takes, so never beside a running or queued turn anywhere in
    the project, nor beside a task being prepared or applied - and a turn
    sent meanwhile waits until this is over. Answers ``git``/``root``/
    ``detail`` like the read, plus ``pushed`` (``to``, ``commits``) or
    ``reverted`` (``changes``) counting what the action took off the
    record - a nested repository, which a revert leaves alone, stays
    counted."""
    from puppy import runner, session_tasks, workspace_sync
    cwd = str((session or {}).get("cwd") or "")
    if not cwd:
        raise ActionRefused("The session has no working directory")
    if session_tasks.record(session["id"]):
        raise ActionRefused("A task's copy is reviewed and applied from Main, never pushed or reverted")
    if workspace_sync.session_workspace(session):
        raise ActionRefused("This session mirrors a project on another node; push or revert it there")
    if runner._draining:
        raise ActionRefused("Puppy is shutting down; retry after the restart")
    before = await _check(cwd, fresh=True, listing=True)
    if before.get("repo") is not True:
        raise ActionRefused("No Git repository" if before.get("repo") is False else
                            "Could not be checked · {}".format(before.get("error") or ""))
    if before.get("error"):
        raise ActionRefused("Could not be read · {}".format(before["error"]))
    root = before.get("root") or cwd
    async with session_tasks.workspace_operation(root):
        if action == "push":
            if not before.get("unpushed"):
                raise ActionRefused("Nothing to push")
            pushed = await operations.to_thread(_push, root)
        else:
            if not before.get("changes"):
                raise ActionRefused("Nothing to revert")
            await operations.to_thread(_revert, root, cwd)
        # the repository has changed: a cancel from here on would only
        # misreport what already happened
        operations.commit()
        after = await _check(cwd, fresh=True, listing=True)
    if _store(cwd, after):
        _broadcast()
    answer = {"git": _public(after), "root": after.get("root"), "detail": after.get("detail")}
    if action == "push":
        answer["pushed"] = {"to": pushed["to"], "commits": max(
            0, (before.get("unpushed") or 0) - (after.get("unpushed") or 0))}
    else:
        answer["reverted"] = {"changes": max(
            0, (before.get("changes") or 0) - (after.get("changes") or 0))}
    return answer


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


async def h_detail(request: web.Request):
    session = _session_or_404(request)
    return web.json_response(dict({"ok": True}, **await detail(session)))


def _page_int(request: web.Request, name: str, default: int, low: int, high: int) -> int:
    """A whole number from the query, within its bounds; anything else is
    the caller's mistake."""
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise web.HTTPBadRequest(text=json.dumps({"error": "{} must be a whole number".format(name)}),
                                 content_type="application/json")
    if value < low or value > high:
        raise web.HTTPBadRequest(text=json.dumps({"error": "{} must be between {} and {}".format(
            name, low, high)}), content_type="application/json")
    return value


async def h_history(request: web.Request):
    """The sheet's short log, one page: ``skip`` (from 0) and ``limit`` (1
    to LOG_PAGE, the default) name it; git's refusal is a 409 with its
    reason, like the actions' refusals."""
    session = _session_or_404(request)
    skip = _page_int(request, "skip", 0, 0, 2147483647)
    limit = _page_int(request, "limit", LOG_PAGE, 1, LOG_PAGE)
    try:
        page = await history(session, skip, limit)
    except GitRefused as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response(dict({"ok": True}, **page))


@operations.cancellable
async def h_action(request: web.Request):
    """Push or revert. The operation header makes a push cancellable while
    git still runs (its process group is ended); ownership outlives a
    caller that disconnects, like a task apply's, so a push is never
    abandoned halfway by a closed tab."""
    session = _session_or_404(request)
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response({"error": "Git actions are paused for a snapshot"}, status=409)
    from puppy import session_tasks
    try:
        answer = await session_tasks.durable_workspace_operation(
            act(session, request.match_info["action"]), session["id"])
    except (ActionRefused, GitRefused, session_tasks.TaskError) as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response(dict({"ok": True}, **answer))


def register(app) -> None:
    """Both runtimes: the worker, the focus refresh, the sheet's read, its
    paged history and its two actions."""
    if app.get("puppy_session_git_registered"):
        return
    app["puppy_session_git_registered"] = True
    app.router.add_get("/api/sessions/{sid:\\d+}/git", h_detail)
    app.router.add_get("/api/sessions/{sid:\\d+}/git/log", h_history)
    app.router.add_post("/api/sessions/{sid:\\d+}" + REFRESH_SUFFIX, h_refresh)
    app.router.add_post("/api/sessions/{sid:\\d+}/git/{action:" + "|".join(ACTIONS) + "}", h_action)
    app.cleanup_ctx.append(_lifecycle)


def reset_for_tests() -> None:
    global _task, _wake, _loop, _missing, _last_full
    _records.clear()
    _inflight.clear()
    _listing.clear()
    _requested.clear()
    _task = None
    _wake = None
    _loop = None
    _missing = False
    _last_full = None
