"""Move a session's project folder, and every session working in it.

A directory session's folder is the user's own project, so this is the
opposite of the scratch lifecycle in ``workspaces``: nothing here reclaims a
folder Puppy created. The one removal is the original of a finished
cross-filesystem copy, and only once the database already names the copy.

On one filesystem the whole tree moves with a single ``rename``, keeping every
inode, owner, hard link and open handle. Otherwise the tree is copied into a
freshly reserved destination - owners (when running as root), modes, times,
extended attributes, hard links within the tree, symlinks verbatim, FIFOs and
sockets recreated - flushed to disk, and Cancel stops that copy before
anything authoritative changes. Either way every session on this node whose
working directory is the folder or inside it follows in one transaction: its
path is rewritten and its native engine context cleared, because engines key
their conversations by path, so its next turn starts fresh with a transcript
handoff. Held prompts stay held and run in the new place when re-sent.

Nothing persisted changes shape: ``sessions.cwd`` holds the new path and each
transcript records a ``workspace_move`` info event, as a scratch promotion does.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import shutil
import stat
import subprocess
import sys

from puppy import config, db, operations, workspaces
from puppy.workspaces import WorkspaceError

log = logging.getLogger("puppy.project_move")

# Top-level system folders a session may work in but never carry away.
SYSTEM_FOLDERS = frozenset((
    "bin", "boot", "dev", "etc", "home", "lib", "lib32", "lib64", "libx32", "media",
    "mnt", "opt", "proc", "root", "run", "sbin", "snap", "srv", "sys", "tmp", "usr", "var"))
MOVED_TEXT = ("Project moved from {} to {}. The next turn starts fresh engine context "
              "with a transcript handoff.")
REPAIR_TIMEOUT = 30


def _uid() -> int:
    getter = getattr(os, "geteuid", None) or getattr(os, "getuid")
    return int(getter())


def _within(root: str, path: str):
    """``path`` relative to ``root`` when it is the root or lies inside it."""
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    return os.path.relpath(path, root)


def _protected() -> list:
    """What a move must never carry away: Puppy's data and program, the Python
    and libraries it runs on, and the home holding the engines' sign-ins and
    native conversations."""
    import aiohttp
    import puppy
    program = os.path.dirname(os.path.dirname(os.path.abspath(puppy.__file__)))
    return [(os.path.realpath(path), label) for path, label in (
        (config.DATA_DIR, "Puppy's data"),
        (program, "Puppy's own program"),
        (sys.executable, "the Python Puppy runs on"),
        (os.path.dirname(os.path.abspath(os.__file__)), "the Python Puppy runs on"),
        (os.path.dirname(os.path.abspath(aiohttp.__file__)), "the libraries Puppy runs on"),
        (os.path.expanduser("~"), "this account's home folder"),
    ) if path]


def _unescape_mount(field: bytes) -> str:
    return os.fsdecode(re.sub(rb"\\([0-7]{3})", lambda m: bytes((int(m.group(1), 8),)), field))


def _mount_points():
    """Every mount point this process sees, or None where the kernel does not
    say. Bind mounts share their device number, so only this list sees them."""
    try:
        with open("/proc/self/mountinfo", "rb") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    return [_unescape_mount(fields[4]) for fields in (line.split(b" ") for line in lines)
            if len(fields) > 4]


def _destination(value) -> str:
    """The requested path, checked for shape only; the filesystem is read
    once the project is owned."""
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise WorkspaceError("Choose an absolute destination path")
    target = os.path.expanduser(value)
    if not os.path.isabs(target):
        raise WorkspaceError("Choose an absolute destination path")
    target = os.path.normpath(target)
    if not os.path.basename(target):
        raise WorkspaceError("Choose a destination folder name")
    return target


def _movable(session) -> None:
    from puppy import session_tasks, workspace_sync
    if session is None:
        raise WorkspaceError("Session not found")
    if session.get("workspace_kind") != workspaces.KIND_DIRECTORY:
        raise WorkspaceError("This session does not work in a project directory")
    if session_tasks.record(session["id"]):
        raise WorkspaceError("Task copies cannot be moved")
    if workspace_sync.session_workspace(session):
        raise WorkspaceError("This session's project lives on another backend; "
                             "move it from a session there")


def _source(session) -> tuple:
    """(path as the session names it, real path) of a folder that may move."""
    raw = str(session.get("cwd") or "")
    if not os.path.isabs(raw):
        # Never the process's own directory, which a relative path would name.
        raise WorkspaceError("This session has no project folder to move")
    path = os.path.normpath(raw)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise WorkspaceError("The project folder is missing: {}".format(path))
    except OSError as exc:
        raise WorkspaceError("Cannot inspect {}: {}".format(path, exc.strerror or exc))
    if stat.S_ISLNK(info.st_mode):
        raise WorkspaceError("{} is a symbolic link; move the folder it points to instead".format(path))
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError("{} is not a folder".format(path))
    real = os.path.realpath(path)
    parent, name = os.path.split(real)
    if real == os.sep or (parent == os.sep and name in SYSTEM_FOLDERS):
        raise WorkspaceError("{} is a system folder and cannot be moved".format(real))
    data = os.path.realpath(config.DATA_DIR)
    if _within(data, real) is not None:
        raise WorkspaceError("Folders inside Puppy's data directory cannot be moved")
    for kept, label in _protected():
        if _within(real, kept) is not None:
            raise WorkspaceError("Moving {} would take {} with it".format(real, label))
    mounts = _mount_points()
    if mounts is None:
        mounts = [real] if os.path.ismount(real) else []
    for point in sorted(point for point in mounts if _within(real, point) is not None):
        if point == real:
            raise WorkspaceError("{} is a mount point; move it with the system's own tools".format(real))
        raise WorkspaceError("{} contains the mount point {}; unmount it first or move the "
                             "folder with the system's own tools".format(real, point))
    return path, real


def _resolve_target(target: str, real: str) -> str:
    parent, name = os.path.split(target)
    if not os.path.isdir(parent):
        raise WorkspaceError("The destination's parent directory must already exist")
    target = os.path.join(os.path.realpath(parent), name)
    if target == real:
        raise WorkspaceError("The project is already in {}".format(real))
    if _within(real, target) is not None:
        raise WorkspaceError("Choose a destination outside the project folder")
    data = os.path.realpath(config.DATA_DIR)
    if _within(data, target) is not None or _within(target, data) is not None:
        raise WorkspaceError("Choose a destination outside Puppy's data directory")
    if os.path.lexists(target):
        raise WorkspaceError("{} already exists; choose a new folder name".format(target))
    return target


def _check_links(real: str, target: str) -> None:
    """A folder another backend's linked session works on stays where its
    lease names it: moving it would cut that session off."""
    from puppy import workspace_sync
    for lease in workspace_sync.list_leases():
        root = os.path.realpath(str(lease.get("root") or ""))
        if _within(real, root) is not None or _within(root, real) is not None:
            raise WorkspaceError("A linked session on another backend works on {}, "
                                 "so it cannot move".format(root))
        if _within(root, target) is not None:
            raise WorkspaceError("A linked session on another backend works on {}; "
                                 "choose a destination outside it".format(root))


def _followers(path: str, real: str) -> dict:
    """Every session on this node working in the folder, archived ones too:
    id -> (session, its working directory relative to the folder).

    A path counts by how the session names it as well as where it resolves,
    so a working directory reached through a link inside the project follows
    the link, and one reached through a link from outside is re-pointed at
    the folder's new place."""
    from puppy import session_tasks, workspace_sync
    tasks = session_tasks.records()
    found = {}
    for session in db.list_sessions(include_archived=True):
        raw = str(session["cwd"] or "")
        if session["workspace_kind"] != workspaces.KIND_DIRECTORY or session["id"] in tasks or \
                workspace_sync.session_workspace(session) or not os.path.isabs(raw):
            continue
        cwd = os.path.normpath(raw)
        relative = _within(path, cwd)
        if relative is None:
            relative = _within(real, cwd)
        if relative is None:
            relative = _within(real, os.path.realpath(cwd))
        if relative is not None:
            found[session["id"]] = (session, relative)
    return found


def _check_idle(sid: int, followers: dict) -> None:
    from puppy import runner
    for fid in sorted(followers, key=lambda fid: (fid != sid, fid)):
        hub = runner._hubs.get(fid)
        if hub is None or (hub.status != "running" and not hub.queue):
            continue
        if fid == sid:
            raise WorkspaceError("Finish or stop the turn and clear queued work before moving")
        session = followers[fid][0]
        raise WorkspaceError("{} is working in this folder; wait for it to finish or stop it, "
                             "and clear its queue".format(session["name"] or "Session {}".format(fid)))


def _reserve(target: str) -> None:
    try:
        os.mkdir(target, 0o700)   # exclusive: never merge into an existing folder
    except FileExistsError:
        raise WorkspaceError("{} already exists; choose a new folder name".format(target))
    except OSError as exc:
        raise WorkspaceError("Cannot create {}: {}".format(target, exc.strerror or exc))


def _discard(target: str) -> None:
    """Remove the reservation, or the copy made into it, before any commit."""
    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("unused move destination %s retained: %s", target, exc)


def _same_filesystem(real: str, target: str) -> bool:
    return os.lstat(real).st_dev == os.lstat(target).st_dev


def _fsync_directory(path: str) -> None:
    try:
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _rename(source: str, target: str) -> None:
    """One atomic rename over the empty reservation, made durable before the
    database is told about it."""
    os.rename(source, target)
    for folder in {os.path.dirname(source), os.path.dirname(target)}:
        _fsync_directory(folder)


def _restore(source: str, destination: str, info, as_root: bool) -> None:
    if as_root:
        # Before the mode: changing the owner clears set-id bits.
        os.lchown(destination, info.st_uid, info.st_gid)
    shutil.copystat(source, destination, follow_symlinks=False)


def _copy_file(source: str, destination: str) -> None:
    # No-follow and non-blocking: an entry swapped for a link or a FIFO since
    # it was listed is refused rather than followed or waited on.
    reader = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(reader).st_mode):
            raise WorkspaceError("{} changed while the project was being copied".format(source))
        writer = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except BaseException:
        os.close(reader)
        raise
    with os.fdopen(reader, "rb") as src, os.fdopen(writer, "wb") as dst:
        operations.copyfileobj(src, dst)


def _copy_tree(source: str, target: str) -> None:
    """Copy ``source``'s contents into the reserved, empty ``target``.

    Runs in a worker thread under the request's operation, so Cancel lands
    between entries and inside long files. Entries are read with lstat only:
    a symlink is copied as a link, never followed. Each folder must be
    writable, because the original is removed once the copy is committed."""
    from puppy import runner
    root = os.lstat(source)
    as_root = _uid() == 0
    linked = {}
    folders = []
    stack = [(source, target)]
    while stack:
        folder, copy = stack.pop()
        if not as_root and not os.access(folder, os.W_OK | os.X_OK):
            raise WorkspaceError("{} is not writable, so the original could not be removed "
                                 "after copying".format(folder))
        with os.scandir(folder) as entries:
            for entry in entries:
                operations.checkpoint()
                if runner._draining:
                    raise WorkspaceError("Puppy is shutting down; retry after the restart")
                src, dst = entry.path, os.path.join(copy, entry.name)
                info = entry.stat(follow_symlinks=False)
                kind = stat.S_IFMT(info.st_mode)
                if kind == stat.S_IFDIR:
                    if info.st_dev != root.st_dev:
                        raise WorkspaceError("{} is another filesystem mounted inside the "
                                             "project".format(src))
                    os.mkdir(dst, 0o700)
                    folders.append((src, dst, info))
                    stack.append((src, dst))
                    continue
                key = (info.st_dev, info.st_ino)
                if kind == stat.S_IFREG and info.st_nlink > 1 and key in linked:
                    os.link(linked[key], dst)
                    continue
                if kind == stat.S_IFLNK:
                    os.symlink(os.readlink(src), dst)
                elif kind == stat.S_IFREG:
                    _copy_file(src, dst)
                    if info.st_nlink > 1:
                        linked[key] = dst
                elif kind == stat.S_IFIFO:
                    os.mkfifo(dst, 0o600)
                else:
                    # Sockets and device nodes, as `cp -a` recreates them.
                    os.mknod(dst, kind | 0o600, info.st_rdev)
                _restore(src, dst, info, as_root)
    # Folders last, children before parents, so no later write moves a time.
    for src, dst, info in reversed(folders):
        _restore(src, dst, info, as_root)
    _restore(source, target, root, as_root)


def _copy(source: str, target: str) -> None:
    try:
        _copy_tree(source, target)
    except OSError as exc:
        where = exc.filename or source
        raise WorkspaceError("Could not copy {}: {}".format(where, exc.strerror or exc)) from exc
    os.sync()


def _remove_original(path: str) -> str:
    """Remove a committed copy's original; the reason it stopped, if it did."""
    failures = []
    shutil.rmtree(path, onerror=lambda _function, name, info: failures.append((name, info[1])))
    if not failures:
        return ""
    name, exc = failures[0]
    reason = getattr(exc, "strerror", None) or str(exc)
    return "{}: {}".format(name, reason)


def _repair_worktrees(sources, target: str, folders) -> None:
    """Re-link Git worktrees whose absolute paths named the old place.

    A linked worktree records its main repository, and the main repository
    records every worktree, as absolute paths. ``git worktree repair`` fixes
    both sides; worktrees that moved inside the folder are named to it
    explicitly. Best effort: a failure is logged and never undoes the move."""
    for folder in sorted(set(folders)):
        dotgit = os.path.join(folder, ".git")
        paths = []
        if os.path.isdir(dotgit) and not os.path.islink(dotgit):
            admin = os.path.join(dotgit, "worktrees")
            try:
                names = sorted(os.listdir(admin))
            except OSError:
                continue   # no linked worktrees: nothing records a path
            if not names:
                continue
            for name in names:
                try:
                    with open(os.path.join(admin, name, "gitdir"), encoding="utf-8") as handle:
                        recorded = os.path.normpath(handle.read().strip())
                except (OSError, UnicodeError):
                    continue
                relative = None
                if os.path.isabs(recorded):
                    for source in sources:
                        relative = _within(source, recorded)
                        if relative is not None:
                            break
                if relative is not None:
                    moved = os.path.dirname(os.path.join(target, relative))
                    if os.path.isdir(moved):
                        paths.append(moved)
        elif os.path.isfile(dotgit):
            try:
                with open(dotgit, encoding="utf-8") as handle:
                    line = handle.read(4096).strip()
            except (OSError, UnicodeError):
                continue
            # Only a linked worktree's gitfile; a submodule's names its module.
            if not line.startswith("gitdir:") or \
                    os.path.basename(os.path.dirname(os.path.normpath(line[7:].strip()))) != "worktrees":
                continue
        else:
            continue
        argv = ["git", "-c", "core.fsmonitor=false", "worktree", "repair", *paths]
        try:
            result = operations.run_process(argv, cwd=folder, timeout=REPAIR_TIMEOUT,
                                            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("git worktree repair in %s failed: %s", folder, exc)
            continue
        if result.returncode:
            log.warning("git worktree repair in %s: %s", folder,
                        result.stderr.decode("utf-8", "replace").strip()[:500])


async def _relocate(sid: int, path: str, real: str, target: str) -> dict:
    from puppy import runner
    loop = asyncio.get_running_loop()
    await operations.to_thread(_reserve, target)
    try:
        same = await operations.to_thread(_same_filesystem, real, target)
        if not same:
            await operations.to_thread(_copy, real, target)
        # Last cancellable point: the files and the sessions change below.
        operations.commit()
    except BaseException:
        await loop.run_in_executor(None, _discard, target)
        raise
    # Found again now the folder is owned and still in place: a session made
    # during a long copy follows too.
    followers = _followers(path, real)
    moves = {fid: os.path.normpath(os.path.join(target, relative))
             for fid, (_session, relative) in followers.items()}
    renamed = False
    if same:
        try:
            await operations.to_thread(_rename, real, target)
            renamed = True
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                await loop.run_in_executor(None, _discard, target)
                raise WorkspaceError("Could not move {}: {}".format(real, exc.strerror or exc)) from exc
        if not renamed:
            # A bind mount shares its device number but not its mount: copy.
            try:
                await operations.to_thread(_copy, real, target)
            except BaseException:
                await loop.run_in_executor(None, _discard, target)
                raise
    try:
        db.relocate_sessions(moves)
    except BaseException:
        if renamed:
            try:
                await operations.to_thread(_rename, target, real)
            except OSError as exc:
                log.error("project %s moved to %s but neither recorded nor put back: %s",
                          real, target, exc)
                raise WorkspaceError("The project is now in {} but its sessions could not be "
                                     "updated; move it back by hand".format(target)) from exc
        else:
            await loop.run_in_executor(None, _discard, target)
        raise
    leftover = "" if renamed else await loop.run_in_executor(None, _remove_original, real)
    if leftover:
        log.warning("moved project's original %s retained: %s", real, leftover)
    await loop.run_in_executor(None, _repair_worktrees, (real, path), target,
                               [target, *moves.values()])
    for fid, (session, _relative) in sorted(followers.items()):
        if db.get_session(fid) is None:
            continue   # deleted while a long copy ran: no transcript left to tell
        old = os.path.normpath(session["cwd"])
        text = MOVED_TEXT.format(old, moves[fid])
        if fid == sid and leftover:
            text += " The original folder {} could not be removed completely ({}); remove it " \
                    "by hand.".format(real, leftover)
        try:
            event = db.add_event(fid, "info", {"subtype": "workspace_move", "text": text})
        except Exception:
            log.warning("could not record the move in session %s", fid, exc_info=True)
            continue
        hub = runner._hubs.get(fid)
        if hub is not None:
            hub.broadcast({"type": "event", "event": event})
            hub.broadcast({"type": "session_meta",
                           "session": runner.session_payload(db.get_session(fid))})
    runner.broadcast_sessions()
    log.info("session %s moved project %s to %s (%s; %d session(s))", sid, real, target,
             "renamed" if renamed else "copied", len(moves))
    return {"session": db.get_session(sid), "moved": sorted(moves),
            "retained": real if leftover else ""}


async def move(sid: int, destination, expected_cwd=None) -> dict:
    """Move session ``sid``'s project folder to ``destination`` and point every
    session working in it at the new place. ``expected_cwd``, when given, is
    the folder the request was made about: a folder that has moved since is
    refused rather than moved again. Shares the task and scratch-move guards:
    the project's operation lock gates turns, backups and upgrades."""
    from puppy import runner, session_tasks
    requested = _destination(destination)
    if expected_cwd is not None and not isinstance(expected_cwd, str):
        raise WorkspaceError("Expected the project's current folder")

    async def move_locked():
        if runner._draining:
            raise WorkspaceError("Puppy is shutting down; retry after the restart")
        session = db.get_session(sid)
        _movable(session)
        if expected_cwd is not None and session["cwd"] != expected_cwd:
            raise WorkspaceError("The project is no longer in {}; open Move project "
                                 "again".format(expected_cwd))
        path, real = _source(session)
        target = _resolve_target(requested, real)
        _check_links(real, target)
        _check_idle(sid, _followers(path, real))
        if session_tasks.overlaps_busy(real) or session_tasks.overlaps_busy(target):
            raise WorkspaceError("Another workspace operation is using this project; "
                                 "try again when it finishes")
        async with session_tasks.workspace_operation(real):
            async with session_tasks.workspace_operation(target):
                # Waiting for either lock may have let the world move on.
                current = db.get_session(sid)
                if current is None or current["cwd"] != session["cwd"] or \
                        current["workspace_kind"] != workspaces.KIND_DIRECTORY:
                    raise WorkspaceError("The project changed while waiting; try again")
                if os.path.lexists(target):
                    raise WorkspaceError("{} already exists; choose a new folder name".format(target))
                return await _relocate(sid, path, real, target)

    async def run():
        # The parent lock task creation shares: no task captures the old path
        # while the folder moves.
        async with session_tasks.session_operation(sid):
            return await move_locked()

    return await session_tasks.durable_workspace_operation(run(), sid)
