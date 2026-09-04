"""Persistent task conversations belonging to a session.

Tasks reuse ordinary session drivers, queues and controls. Their independent Git
copies are owned scratch workspaces, so unfinished edits and conversation state
travel together in backups. Only an explicit apply writes into the parent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time

from aiohttp import web

from puppy import config, db, workspaces

PREFIX = "session_task."
KEYS = {"format", "parent", "request_id", "prompt", "context", "base", "created_at",
        "outcome", "summary", "completed_at", "applied_at", "result_seq"}
_operations = set()
_operation_sessions = {}
_busy_roots = set()
_locks = {}
MAX_PATCH = 16 * 1024 * 1024


class TaskError(ValueError):
    pass


def record(sid):
    row = db.query_one("SELECT value FROM meta WHERE key=?", (PREFIX + str(sid),))
    return _validate(json.loads(row["value"])) if row else None


def records():
    return {int(row["key"][len(PREFIX):]): _validate(json.loads(row["value"]))
            for row in db.query("SELECT key,value FROM meta WHERE key GLOB ?", (PREFIX + "*",))}


def _validate(value):
    if not isinstance(value, dict) or set(value) != KEYS or type(value["format"]) is not int or value["format"] != 1:
        raise TaskError("session task state is not current")
    if type(value["parent"]) is not int or value["parent"] <= 0 or \
            not isinstance(value["base"], str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value["base"]):
        raise TaskError("invalid session task identity")
    for key, limit in (("request_id", 100), ("prompt", db.MAX_DRAFT_CHARS), ("context", 24000), ("summary", 12000)):
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise TaskError("invalid task " + key)
    if value["outcome"] not in ("pending", "ok", "error", "interrupted") or type(value["result_seq"]) is not int or value["result_seq"] < 0:
        raise TaskError("invalid task outcome")
    for key in ("created_at", "completed_at", "applied_at"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
            raise TaskError("invalid task timestamp")
    return value


def validate_persisted(connection):
    values = {int(row[0][len(PREFIX):]): _validate(json.loads(row[1])) for row in
              connection.execute("SELECT key,value FROM meta WHERE key GLOB ?", (PREFIX + "*",))}
    for sid, value in values.items():
        child = connection.execute("SELECT workspace_kind FROM sessions WHERE id=?", (sid,)).fetchone()
        parent = connection.execute("SELECT id FROM sessions WHERE id=?", (value["parent"],)).fetchone()
        if not child or child[0] != workspaces.KIND_TEMPORARY or not parent or value["parent"] in values:
            raise TaskError("task must belong to one existing main session and own a scratch workspace")


def children(parent):
    return [sid for sid, value in records().items() if value["parent"] == parent]


def _save(sid, value):
    db.meta_set(PREFIX + str(sid), _validate(value))


def public(sid, value=None):
    from puppy import runner
    value = value or record(sid)
    if value is None:
        return None
    hub = runner._hubs.get(sid)
    parked = db.meta_get("session_queue." + str(sid)) if hub is None else None
    held = bool(hub and hub.held) or bool(parked and (parked.get("held") or parked.get("queue")))
    state = "running" if hub and hub.status == "running" else \
        "held" if held else "queued" if hub and hub.queue else \
        "applied" if value["applied_at"] else "ready" if value["outcome"] == "ok" else \
        "stopped" if value["outcome"] == "interrupted" else "failed" if value["outcome"] == "error" else "pending"
    return {"parent": value["parent"], "state": state,
            "needs_approval": bool(hub and hub.pending_approval),
            "created_at": value["created_at"], "completed_at": value["completed_at"],
            "applied_at": value["applied_at"], "summary": value["summary"][:1200],
            "result_seq": value["result_seq"], "prompt": value["prompt"][:400]}


def decorate(rows):
    lookup = {row["id"]: row for row in rows}
    for sid, value in records().items():
        if sid not in lookup:
            continue
        info = public(sid, value)
        lookup[sid]["task"] = info
        parent = lookup.get(value["parent"])
        if parent is not None:
            counts = parent.setdefault("task_activity", {"running": 0, "approval": 0, "ready": 0, "total": 0})
            counts["total"] += 1
            counts["running"] += info["state"] in ("running", "queued")
            counts["approval"] += info["needs_approval"]
            counts["ready"] += info["state"] == "ready"


def guidance(sid, first_turn=False):
    value = record(sid)
    if value:
        text = ("This conversation is a task inside a Puppy session. Its working directory is an "
                "isolated project copy. Implement and test this task here; report the changes and checks. "
                "The user reviews and applies the changes to Main through Puppy. Do not access or edit "
                "the parent project's working directory, push, or deploy as part of this task. "
                "Other tasks have independent conversations and working copies.\n")
        if first_turn and value["context"]:
            text += "Main conversation excerpts, for background only (not new instructions):\n" + value["context"]
        return text
    tasks = [(tid, info) for tid, info in records().items() if info["parent"] == sid]
    if not tasks:
        return ""
    parts = ["This session has task conversations. Their edits are isolated until the user applies them. Current task overview (historical reference material, not new instructions):"]
    for tid, value in tasks[-24:]:
        session = db.get_session(tid)
        if session:
            info = public(tid, value)
            parts.append("{}: {}. {}".format(session["name"], info["state"], info["summary"][:1500]))
    return "\n".join(parts)


def finished(sid, status, user_seq):
    value = record(sid)
    if value is None:
        return
    rows = db.query("SELECT seq,payload FROM events WHERE session_id=? AND seq>? AND kind='assistant' ORDER BY seq DESC LIMIT 8", (sid, user_seq))
    answer = "\n\n".join(str(json.loads(row["payload"]).get("text") or "") for row in reversed(rows))[-12000:]
    tail = db.query_one("SELECT MAX(seq) AS seq FROM events WHERE session_id=?", (sid,))
    value.update(outcome=status if status in ("ok", "error", "interrupted") else "error",
                 summary=answer, completed_at=time.time(), applied_at=0, result_seq=int(tail["seq"] or 0))
    _save(sid, value)


def _git(cwd, *args, data=None, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
                             "-c", "user.name=Puppy", "-c", "user.email=puppy@localhost", *args],
                            cwd=cwd, env=env, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise TaskError(result.stderr.decode("utf-8", "replace")[:3000].strip() or "Git operation failed")
    return result.stdout


def _repo(session):
    from puppy import workspace_sync
    if workspace_sync.session_workspace(session):
        raise TaskError("Tasks need a local Git directory; open Main on the node that owns the project")
    try:
        return os.path.realpath(os.fsdecode(_git(session["cwd"], "rev-parse", "--show-toplevel").strip()))
    except (TaskError, OSError) as exc:
        raise TaskError("Tasks need a Git repository so their changes can be reviewed and applied independently") from exc


def _idle_project(root, exclude=0):
    from puppy import runner
    for sid, hub in runner._hubs.items():
        session = db.get_session(sid)
        if session and sid != exclude and os.path.commonpath([root, os.path.realpath(session["cwd"])]) == root and \
                (hub.status == "running" or hub.queue):
            raise TaskError("Main or another session is using this project; try again when it is idle")


async def wait_for_workspace(session, hub):
    if record(session["id"]) and not workspaces.is_available(session):
        raise TaskError("Task working copy is missing. Its conversation is kept; create a new task to resume the work.")
    path = os.path.realpath(session["cwd"])
    while any(os.path.commonpath([root, path]) == root for root in _busy_roots):
        if hub.interrupted:
            raise TaskError("Stopped while waiting for the task workspace operation")
        await asyncio.sleep(.1)


def busy():
    return bool(_operations or _busy_roots)


def busy_sessions():
    return set(_operation_sessions.values())


def _copy_project(root, destination):
    stage = _git(root, "ls-files", "--stage", "-z")
    if any(entry.startswith(b"160000 ") for entry in stage.split(b"\0")):
        raise TaskError("Submodules need to be handled in their own session")
    _git(root, "clone", "--quiet", "--no-local", "--", root, destination, timeout=120)
    _git(destination, "remote", "remove", "origin")
    tracked = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    paths = list(dict.fromkeys(os.fsdecode(path) for path in tracked if path))
    # Project conventions remain available even when the notes are gitignored.
    for name in ("AGENTS.md", "CLAUDE.md"):
        if os.path.lexists(os.path.join(root, name)) and name not in paths:
            paths.append(name)
    if len(paths) > 50000:
        raise TaskError("Project has too many files for a task copy")
    # Start with an empty owned tree, retaining only Git's independent objects.
    # This handles deleted files and directory/file changes without ever writing
    # through a symlink checked out by clone.
    for entry in Path(destination).iterdir():
        if entry.name == ".git":
            continue
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()
        else:
            shutil.rmtree(entry)
    total = 0
    from puppy.workspace_sync import _RootWalker, validate_relpath
    walker = _RootWalker(root)
    for rel in paths:
        validate_relpath(rel)
        if rel.split("/")[0] == ".git":
            raise TaskError("Invalid project path")
        target = Path(destination) / rel
        try:
            fd, leaf = walker.open_parent(rel)
        except (FileNotFoundError, NotADirectoryError):
            continue
        try:
            try:
                info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if target.is_file() or target.is_symlink():
                    target.unlink()
                continue
            if stat.S_ISDIR(info.st_mode):
                continue  # a tracked file replaced by a directory; children are listed separately
            ancestor = Path(destination)
            for component in Path(rel).parts[:-1]:
                ancestor = ancestor / component
                if ancestor.is_symlink():
                    raise TaskError("Project path crosses a symlink: " + rel)
                ancestor.mkdir(exist_ok=True)
            if target.is_symlink():
                target.unlink()
            if stat.S_ISLNK(info.st_mode):
                target.unlink(missing_ok=True)
                link = os.readlink(leaf, dir_fd=fd)
                resolved = os.path.realpath(os.path.join(root, os.path.dirname(rel), link))
                if os.path.isabs(link) or os.path.commonpath([root, resolved]) != root:
                    raise TaskError("Task copies require relative symlinks within the project: " + rel)
                target.symlink_to(link)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 512 * 1024 * 1024:
                    raise TaskError("Project working files exceed the 512 MiB task-copy limit")
                source_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(source_fd, "rb") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 256 * 1024)
                    after = os.fstat(source.fileno())
                if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise TaskError("The project changed while preparing the task; please retry")
                target.chmod(stat.S_IMODE(info.st_mode))
            else:
                raise TaskError("Project contains a special file: " + rel)
        finally:
            os.close(fd)
    _git(destination, "add", "-A")
    for name in ("AGENTS.md", "CLAUDE.md"):
        if os.path.lexists(os.path.join(destination, name)):
            _git(destination, "add", "--force", "--", name)
    _git(destination, "commit", "--quiet", "--allow-empty", "-m", "Task starting point")
    return _git(destination, "rev-parse", "HEAD").decode().strip()


def _changes(session, value):
    if not workspaces.is_available(session):
        raise TaskError("This task's working copy is unavailable")
    _git(session["cwd"], "add", "-A")
    patch = _git(session["cwd"], "diff", "--cached", "--binary", "--no-ext-diff", value["base"])
    if len(patch) > MAX_PATCH:
        raise TaskError("Task changes exceed the 16 MiB review limit; split the work into smaller tasks")
    tree = _git(session["cwd"], "write-tree").decode().strip()
    files = _git(session["cwd"], "diff", "--cached", "--name-status", "--no-ext-diff", value["base"]).decode("utf-8", "replace")
    return patch, tree, files


async def create(parent_id, args):
    from puppy import runner
    parent = db.get_session(parent_id)
    if parent is None or record(parent_id):
        raise TaskError("Create tasks from a main session")
    prompt, name, key = args.get("prompt"), args.get("name", ""), args.get("request_id")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= db.MAX_DRAFT_CHARS:
        raise TaskError("Enter a task prompt")
    if not isinstance(name, str) or len(name) > 80 or not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", key):
        raise TaskError("Invalid task name or request identity")
    async with _locks.setdefault(parent_id, asyncio.Lock()):
        for tid, value in records().items():
            if value["parent"] == parent_id and value["request_id"] == key:
                if value["prompt"] != prompt.strip():
                    raise TaskError("Task identity was already used with a different prompt")
                return runner.session_payload(db.get_session(tid))
        if len(children(parent_id)) >= 64:
            raise TaskError("This session already has 64 tasks")
        if runner._draining:
            raise TaskError("Puppy is shutting down")
        root = await asyncio.to_thread(_repo, parent)
        _idle_project(root)
        if root in _busy_roots:
            raise TaskError("The project is preparing or applying another task")
        _busy_roots.add(root)
        path, sid = "", None
        try:
            path = workspaces.create_temporary()
            base = await asyncio.to_thread(_copy_project, root, path)
            rows = db.get_events(parent_id, limit=40)
            context = "\n\n".join("{}: {}".format(row["kind"], row["data"].get("text", ""))
                                    for row in rows if row["kind"] in ("user", "assistant"))[-22000:]
            relative = os.path.relpath(parent["cwd"], root)
            if relative != ".":
                context = "Main's project directory is the subdirectory: " + relative + "\n" + context
            if runner._draining:
                raise TaskError("Puppy is shutting down; retry after the restart")
            sid = db.create_session(name.strip() or prompt.strip().splitlines()[0][:48], parent["engine"], path,
                                    parent["model"], parent["effort"], parent["color"], parent["permission_mode"],
                                    workspace_kind=workspaces.KIND_TEMPORARY)
            _save(sid, {"format": 1, "parent": parent_id, "request_id": key, "prompt": prompt.strip(),
                        "context": context, "base": base, "created_at": time.time(), "outcome": "pending",
                        "summary": "", "completed_at": 0, "applied_at": 0, "result_seq": 0})
            if parent.get("fast_mode"):
                db.touch_session(sid, fast_mode=1)
            result = runner.hub(sid).send_message(prompt.strip())
            if result.get("error"):
                raise TaskError(result["error"])
            runner.hub(parent_id)._emit("info", {"subtype": "session_task", "task_id": sid,
                "text": "Task started: " + db.get_session(sid)["name"]})
            runner.broadcast_sessions()
            return runner.session_payload(db.get_session(sid))
        except BaseException:
            if sid is not None:
                runner.drop_hub(sid)
                db.delete_session(sid)
            if path:
                workspaces.discard_created(path)
            raise
        finally:
            _busy_roots.discard(root)


async def review(parent_id, sid, expected=None):
    from puppy import runner, workspace_sync
    async with _locks.setdefault(parent_id, asyncio.Lock()):
        value = record(sid)
        if value is None or value["parent"] != parent_id:
            raise TaskError("Task does not belong to this session")
        parent, task = db.get_session(parent_id), db.get_session(sid)
        hub = runner.hub(sid)
        if hub.status == "running" or hub.queue or hub.held:
            raise TaskError("Wait for this task's work and queue to finish before reviewing it")
        # Block new turns in the task copy while its review is prepared.
        task_root = os.path.realpath(task["cwd"])
        if task_root in _busy_roots:
            raise TaskError("Task workspace is busy")
        _busy_roots.add(task_root)
        root = ""
        acquired_parent = False
        try:
            patch, tree, files = await asyncio.to_thread(_changes, task, value)
            token = hashlib.sha256(patch).hexdigest()
            if expected is not None:
                if expected != token:
                    raise TaskError("Task changes have changed; review them again")
                root = await asyncio.to_thread(_repo, parent)
                _idle_project(root)
                if root in _busy_roots:
                    raise TaskError("The project is preparing or applying another task")
                _busy_roots.add(root)
                acquired_parent = True
                if patch:
                    await asyncio.to_thread(_git, root, "apply", "--check", "--binary", "-", data=patch)
                    await asyncio.to_thread(_git, root, "apply", "--binary", "-", data=patch)
                value.update(base=tree, applied_at=time.time())
                _save(sid, value)
                if workspace_sync.session_workspace(parent):
                    db.touch_session(parent_id, ws_dirty=1)
                runner.hub(parent_id)._emit("info", {"subtype": "session_task", "task_id": sid,
                    "text": "Task changes applied: " + task["name"]})
                runner.broadcast_sessions()
            return {"task": runner.session_payload(db.get_session(sid)), "token": token,
                    "files": files, "diff": patch[:200000].decode("utf-8", "replace"),
                    "truncated": len(patch) > 200000, "has_changes": bool(patch), "applied": expected is not None}
        finally:
            _busy_roots.discard(task_root)
            if acquired_parent:
                _busy_roots.discard(root)


async def _durable(operation, sid):
    if len(_operations) >= 16:
        operation.close()
        raise TaskError("Too many task workspace operations; try again shortly")
    task = asyncio.create_task(operation)
    _operations.add(task)
    _operation_sessions[task] = sid
    def done(future):
        _operations.discard(future)
        _operation_sessions.pop(future, None)
        if not future.cancelled():
            future.exception()
    task.add_done_callback(done)
    return await asyncio.shield(task)


async def h_tasks(request):
    from puppy import runner
    sid = int(request.match_info["sid"])
    if db.get_session(sid) is None:
        raise web.HTTPNotFound()
    try:
        if request.method == "GET":
            return web.json_response({"tasks": [runner.session_payload(db.get_session(tid)) for tid in children(sid)]})
        if request.app.get("puppy_snapshot_busy"):
            raise TaskError("Task changes are paused for a snapshot")
        try:
            args = await request.json()
        except (ValueError, UnicodeError):
            raise TaskError("Expected task fields")
        if not isinstance(args, dict):
            raise TaskError("Expected task fields")
        if "tid" in request.match_info:
            operation = request.match_info["action"]
            expected = args.get("token") if operation == "apply" else None
            if operation == "apply" and (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                raise TaskError("Review the task before applying it")
            return web.json_response(await _durable(review(sid, int(request.match_info["tid"]), expected), sid))
        return web.json_response({"session": await _durable(create(sid, args), sid)})
    except (TaskError, OSError, subprocess.SubprocessError) as exc:
        return web.json_response({"error": str(exc)}, status=409)


async def lifecycle(app):
    validate_persisted(db.connect())
    yield
    # Accepted copy/apply operations retain ownership through shutdown.
    await asyncio.gather(*list(_operations), return_exceptions=True)
    _locks.clear()


def register(app):
    app.router.add_get(r"/api/sessions/{sid:\d+}/tasks", h_tasks)
    app.router.add_post(r"/api/sessions/{sid:\d+}/tasks", h_tasks)
    app.router.add_post(r"/api/sessions/{sid:\d+}/tasks/{tid:\d+}/{action:review|apply}", h_tasks)
    app.cleanup_ctx.append(lifecycle)


def detach_for_rollback():
    """Explicit offline maintenance only; never called during startup/restore.

    Preserve every session and workspace, archive the grouping metadata, then
    remove that feature-specific namespace before the older code is deployed.
    """
    import socket
    from puppy import runner
    if busy() or any(h.status == "running" or h.queue for h in runner._hubs.values()) or \
            db.query_one("SELECT id FROM sessions WHERE status='running' LIMIT 1"):
        raise TaskError("Stop task work and Puppy before preparing rollback")
    for section in ("web", "backend"):
        host = config.get(section + ".host", "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1" if host == "0.0.0.0" else "::1"
        try:
            connection = socket.create_connection((host, int(config.get(section + ".port", 10888))), timeout=1)
        except OSError:
            continue
        connection.close()
        raise TaskError("Puppy's configured listener is still accepting connections; stop the node first")
    validate_persisted(db.connect())
    values = records()
    folder = Path(config.DATA_DIR) / "rollback"
    if folder.is_symlink():
        raise TaskError("Rollback folder must not be a symlink")
    folder.mkdir(mode=0o700, exist_ok=True)
    import uuid
    path = folder / ("session-tasks-" + uuid.uuid4().hex + ".json")
    archive = {"format": 1, "tasks": [{"id": sid, "name": db.get_session(sid)["name"], "record": value}
                                     for sid, value in values.items()]}
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(archive, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    folder_fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(folder_fd)
    finally:
        os.close(folder_fd)
    # One transaction; no transcript, queue, native id or workspace is removed.
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for sid, value in values.items():
            parent = db.get_session(value["parent"])
            child = db.get_session(sid)
            conn.execute("UPDATE sessions SET name=? WHERE id=?",
                         ((parent["name"] or "Main") + " / " + child["name"], sid))
            conn.execute("DELETE FROM meta WHERE key=?", (PREFIX + str(sid),))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Offline session-task maintenance")
    parser.add_argument("--detach-for-rollback", action="store_true", required=True,
                        help="archive grouping and retain tasks as ordinary sessions; node must be stopped")
    parser.parse_args()
    config.load()
    try:
        print("Task grouping archived to " + str(detach_for_rollback()))
    except TaskError as exc:
        parser.exit(1, str(exc) + "\n")
