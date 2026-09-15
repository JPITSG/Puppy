#!/usr/bin/env python3
"""The sidebar's Git mark, from the directory up: discovery against a real
tree, the work state read from real repositories (uncommitted paths,
commits no remote holds, and every way git can decline to say), the node's
cache and worker, the focus refresh on both authenticated runtimes, the
re-check a finished prompt asks for - through the runner itself, with a
task's prompt left out - the timer that paces it, and the guards around it.
No engine, network or quota; ``git`` runs only on scratch repositories."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch


def fixture():
    """An engine that answers one prompt with one result, like the real CLI
    would after a turn that touched the working directory, then waits for
    the runner to close its input."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-session", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402
TEST_ROOT = private_root("session-git-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
TREE = TEST_ROOT / "tree"
# The scratch tree lives inside Puppy's own checkout, so without a ceiling
# every directory in it would answer "repository" from the project's .git;
# git honours this variable and so does the discovery.
os.environ["GIT_CEILING_DIRECTORIES"] = str(TREE)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import config, db, protocol, runner, session_git, session_tasks, workspaces  # noqa: E402
from puppy import web as webui  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402


def git_dir(path: Path) -> None:
    """The least git itself accepts as a git dir: HEAD, objects and refs."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    (path / "objects").mkdir(exist_ok=True)
    (path / "refs").mkdir(exist_ok=True)


def git(cwd, *args) -> str:
    """The real git on a scratch repository, with a fixed identity so no
    global configuration (signing, hooks, a default branch) can differ."""
    return subprocess.run(
        ["git", "-c", "user.name=Puppy tests", "-c", "user.email=tests@localhost",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).stdout


def state(cwd) -> tuple:
    """(repo, changes, unpushed, error) of one inspection."""
    record = session_git.inspect(str(cwd))
    return (record["repo"], record.get("changes"), record.get("unpushed"),
            record.get("error"))


def rundown(cwd) -> tuple:
    """(branch, staged, unstaged, untracked, conflicts) of one inspection:
    the tooltip's half of the record, which adds up to its changes."""
    record = session_git.inspect(str(cwd))
    kinds = tuple(record[key] for key in ("staged", "unstaged", "untracked", "conflicts"))
    assert sum(kinds) == record["changes"], record
    return (record["branch"],) + kinds


def discovery() -> None:
    repo = TREE / "repo"
    git_dir(repo / ".git")
    deep = repo / "src" / "deep"
    deep.mkdir(parents=True)
    plain = TREE / "plain"
    plain.mkdir()
    # a linked worktree or a submodule: .git is a file naming the git dir
    git_dir(repo / ".git" / "worktrees" / "wt")
    worktree = TREE / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/wt\n")
    absolute = TREE / "abs"
    absolute.mkdir()
    (absolute / ".git").write_text("gitdir: {}\n".format(repo / ".git"))
    broken = TREE / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: ../nowhere\n")
    notes = TREE / "notes"
    notes.mkdir()
    (notes / ".git").write_text("just a file\n")
    headless = TREE / "headless"
    (headless / ".git").mkdir(parents=True)
    bare = TREE / "bare"
    git_dir(bare)
    link = TREE / "link"
    link.symlink_to(repo / "src")
    (TREE / "file.txt").write_text("x")

    answers = {str(path): session_git.inspect(str(path))["repo"] for path in (
        repo, deep, plain, worktree, absolute, broken, notes, headless, bare, link)}
    assert answers == {
        str(repo): True, str(deep): True, str(plain): False, str(worktree): True,
        str(absolute): True, str(broken): False, str(notes): False,
        str(headless): False, str(bare): False, str(link): True}, answers
    for path in (TREE / "file.txt", TREE / "missing", ""):
        record = session_git.inspect(str(path))
        assert record["repo"] is None and record["error"], record
    stamp = session_git.inspect(str(repo))["checked_at"]
    assert abs(stamp - time.time()) < 5
    # the ceiling keeps the tree's parents out, and that is the only reason
    # the plain directory above answered "no": the checkout it lives in is one
    with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": ""}):
        assert session_git.inspect(str(plain))["repo"] is True
    # a ceiling that is the working directory itself is still examined; one
    # above it is never climbed into, exactly as git reads the variable
    with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(repo)}):
        assert session_git.inspect(str(repo))["repo"] is True
        assert session_git.inspect(str(deep))["repo"] is False
    with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": "{}{}{}".format(
            TREE / "elsewhere", os.pathsep, repo / "src")}):
        assert session_git.inspect(str(deep))["repo"] is False
        assert session_git.inspect(str(repo / "src"))["repo"] is True

    # Discovery stops where git stops by default: at a filesystem boundary.
    outer = TREE / "outer"
    git_dir(outer / ".git")
    inner = outer / "mount" / "inner"
    inner.mkdir(parents=True)
    assert session_git.inspect(str(inner))["repo"] is True
    real_stat = os.stat

    def other_device(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if os.path.realpath(path) == str(outer / "mount"):
            return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev + 1)
        return info

    with patch.object(session_git.os, "stat", other_device):
        assert session_git.inspect(str(inner))["repo"] is False
    print("discovery: git dirs, gitfiles, parents, links, boundaries and non-directories")


def work_state() -> None:
    """The counts a repository's record carries, read by git itself."""
    project = TREE / "project"
    project.mkdir()
    git(project, "init", "-q")
    (project / "README").write_text("hello\n")
    # an unborn branch with no remote: the untracked file is the one change,
    # and there is nowhere to push to
    assert state(project) == (True, 1, None, None)
    assert rundown(project) == ("main", 0, 0, 1, 0), "the unborn branch is still named"
    git(project, "add", "README")
    assert state(project) == (True, 1, None, None), "staged is still uncommitted"
    assert rundown(project) == ("main", 1, 0, 0, 0)
    git(project, "commit", "-q", "-m", "one")
    assert state(project) == (True, 0, None, None)
    assert rundown(project) == ("main", 0, 0, 0, 0)
    # a remote that has never been pushed to holds nothing: every commit is
    # unpushed, an upstream or not
    git(TREE, "init", "-q", "--bare", "remote.git")
    git(project, "remote", "add", "origin", str(TREE / "remote.git"))
    assert state(project) == (True, 0, 1, None)
    git(project, "push", "-q", "-u", "origin", "main")
    assert state(project) == (True, 0, 0, None)
    # the whole work tree counts, whatever subdirectory the session sits in:
    # a modified file, an untracked file and an untracked directory as one
    (project / "README").write_text("changed\n")
    (project / "new.txt").write_text("x")
    (project / "dir").mkdir()
    (project / "dir" / "a").write_text("a")
    (project / "dir" / "b").write_text("b")
    assert state(project) == (True, 3, 0, None)
    assert state(project / "dir") == (True, 3, 0, None)
    assert rundown(project) == ("main", 0, 1, 2, 0), "the untracked directory is one path"
    git(project, "add", "-A")
    assert state(project) == (True, 4, 0, None), "staged paths are listed one by one"
    assert rundown(project) == ("main", 4, 0, 0, 0)
    # a path staged and then edited again is staged: one path, one kind
    (project / "README").write_text("changed again\n")
    assert state(project) == (True, 4, 0, None)
    assert rundown(project) == ("main", 4, 0, 0, 0)
    git(project, "add", "README")
    git(project, "commit", "-q", "-m", "two")
    assert state(project) == (True, 0, 1, None)
    git(project, "commit", "-q", "--allow-empty", "-m", "three")
    assert state(project) == (True, 0, 2, None)
    # a branch without an upstream is unpushed work like any other, until
    # some remote holds its commits - any remote, not only its upstream
    git(project, "checkout", "-q", "-b", "feature")
    git(project, "commit", "-q", "--allow-empty", "-m", "four")
    assert state(project) == (True, 0, 3, None)
    assert rundown(project) == ("feature", 0, 0, 0, 0)
    # a merge that stops on a conflict: the unmerged path is a conflict,
    # whatever else the index and the work tree hold for it
    (project / "README").write_text("feature\n")
    git(project, "commit", "-q", "-am", "feature readme")
    git(project, "checkout", "-q", "main")
    (project / "README").write_text("main\n")
    git(project, "commit", "-q", "-am", "main readme")
    merge = subprocess.run(["git", "merge", "feature"], cwd=str(project), capture_output=True, text=True)
    assert merge.returncode and "CONFLICT" in merge.stdout, merge
    assert state(project) == (True, 1, 3, None), "main's own commits since the push"
    assert rundown(project) == ("main", 0, 0, 0, 1)
    git(project, "merge", "--abort")
    git(project, "reset", "-q", "--hard", "HEAD~1")
    git(project, "checkout", "-q", "feature")
    git(project, "reset", "-q", "--hard", "HEAD~1")
    assert state(project) == (True, 0, 3, None)
    git(project, "push", "-q", "origin", "main")
    assert state(project) == (True, 0, 1, None)
    git(TREE, "init", "-q", "--bare", "backup.git")
    git(project, "remote", "add", "backup", str(TREE / "backup.git"))
    git(project, "push", "-q", "backup", "feature")
    assert state(project) == (True, 0, 0, None)
    # a detached head is counted the same way, and named as no branch
    git(project, "checkout", "-q", "--detach", "main")
    git(project, "commit", "-q", "--allow-empty", "-m", "five")
    assert state(project) == (True, 0, 1, None)
    assert rundown(project) == (None, 0, 0, 0, 0)
    # a fresh clone is clean, pushed and unremarkable
    git(TREE, "clone", "-q", str(TREE / "remote.git"), "clone")
    assert state(TREE / "clone") == (True, 0, 0, None)
    (TREE / "clone" / "note").write_text("n")
    assert state(TREE / "clone") == (True, 1, 0, None)

    # When git will not answer, the repository stays a repository - the
    # discovery read the tree itself - and the record carries the reason
    # in place of the counts: a git dir git rejects, a git that is not
    # installed, and a git that does not come back.
    refused = TREE / "refused"
    (refused / ".git").mkdir(parents=True)
    (refused / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    repo, changes, unpushed, error = state(refused)
    assert (repo, changes, unpushed) == (True, None, None) and "not a git repository" in error, error
    nobin = TREE / "nobin"
    nobin.mkdir()
    with patch.dict(os.environ, {"PATH": str(nobin)}):
        assert state(project) == (True, None, None, "git is not installed on this backend")
    slow = TREE / "slowbin"
    slow.mkdir()
    (slow / "git").write_text("#!/bin/sh\nexec /bin/sleep 5\n")
    (slow / "git").chmod(0o755)
    started = time.monotonic()
    with patch.dict(os.environ, {"PATH": str(slow)}), patch.object(session_git, "GIT_TIMEOUT", 1.0):
        assert state(project) == (True, None, None, "git did not answer within 1 seconds")
    assert time.monotonic() - started < 4, "the timeout ended the run"
    # the public record carries the counts and the rundown for a repository
    # and the reason in place of all of them when git declined; a
    # non-repository carries none of it, and a record seeded without the
    # rundown (a node from before it) is published without one
    assert set(session_git._public(session_git.inspect(str(project)))) == \
        {"repo", "checked_at", "changes", "unpushed", "branch", "staged", "unstaged", "untracked", "conflicts"}
    assert set(session_git._public(session_git.inspect(str(refused)))) == \
        {"repo", "checked_at", "changes", "unpushed", "error"}
    assert set(session_git._public(session_git.inspect(str(TREE / "plain")))) == {"repo", "checked_at"}
    assert session_git._public({"repo": True, "checked_at": 1, "changes": 2, "unpushed": 0}) == \
        {"repo": True, "checked_at": 1, "changes": 2, "unpushed": 0}
    # the rundown is part of what a console draws, so a re-sorted or
    # re-branched answer with the same totals is a changed record
    session_git.reset_for_tests()
    seed = {"repo": True, "checked_at": 1, "changes": 1, "unpushed": 0, "branch": "main",
            "staged": 1, "unstaged": 0, "untracked": 0, "conflicts": 0}
    assert session_git._store("/x", dict(seed)) is True
    assert session_git._store("/x", dict(seed, checked_at=2)) is False
    assert session_git._store("/x", dict(seed, staged=0, unstaged=1)) is True
    assert session_git._store("/x", dict(seed, staged=0, unstaged=1, branch="feature")) is True
    assert session_git._store("/x", dict(seed, staged=0, unstaged=1, branch=None)) is True
    session_git.reset_for_tests()
    print("work state: uncommitted paths by kind, unpushed commits, remotes, branches and git's refusals")


async def listed(client, headers):
    response = await client.get("/api/sessions", headers=headers)
    assert response.status == 200
    return {row["id"]: row["git"] for row in (await response.json())["sessions"]}


async def answered(client, headers, ids, condition=None, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        rows = await listed(client, headers)
        if all(rows.get(sid) is not None and (condition is None or condition(rows[sid]))
               for sid in ids):
            return rows
        assert time.monotonic() < deadline, rows
        await asyncio.sleep(.05)


async def run_turn(sid) -> None:
    """One prompt through the real runner, answered by the fixture engine."""
    assert db.get_session(sid)["engine"] == "claude", "the fixture speaks claude's stream-json"
    driver = get_driver("claude")
    hub = runner.hub(sid)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), "--fixture"]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        try:
            assert hub.send_message("touch the tree") == {"queued": False}
            deadline = time.monotonic() + 20
            while hub.status != "idle":
                assert time.monotonic() < deadline, "the fixture turn did not end"
                await asyncio.sleep(.02)
            await hub.turn_task
        finally:
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    events = db.get_events(sid)
    assert any(e["kind"] == "result" and e["data"].get("ok") for e in events), events


async def api_contract(factory) -> None:
    session_git.reset_for_tests()
    repo = TREE / "repo"
    plain = TREE / "plain" / str(int(time.time() * 1000))
    plain.mkdir(parents=True)
    missing = TREE / "gone"
    ids = [db.create_session("", "claude", str(path), "", "", "", "default")
           for path in (repo, plain, missing)]
    app = factory()
    # Only the real authenticated routes and this module's own worker: the
    # startup hooks would also start the release, catalog and search workers,
    # which reach for the network and the installed CLIs.
    assert session_git._lifecycle in app.cleanup_ctx
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.SESSION_GIT_CAPABILITY in app["puppy_capabilities"]
    full = app["puppy_role"] == "full"
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    worker = None
    try:
        async with TestClient(TestServer(app)) as client:
            worker = session_git._lifecycle(app)
            await worker.__anext__()
            # the worker's first pass answers every session's directory,
            # counts and rundown included: the repository holds nothing git
            # would list (empty directories are not files) and no remote,
            # and the record says so and nothing else
            rows = await answered(client, headers, ids)
            assert rows[ids[0]] == {"repo": True, "checked_at": rows[ids[0]]["checked_at"],
                                    "changes": 0, "unpushed": None, "branch": "main",
                                    "staged": 0, "unstaged": 0, "untracked": 0,
                                    "conflicts": 0}, rows[ids[0]]
            assert rows[ids[1]]["repo"] is False and "changes" not in rows[ids[1]]
            assert rows[ids[2]]["repo"] is None and rows[ids[2]]["error"]
            (repo / "src" / "deep" / "file.txt").write_text("f")
            response = await client.post("/api/sessions/{}/git/refresh".format(ids[0]),
                                         headers=headers)
            refreshed = (await response.json())["git"]
            assert refreshed["changes"] == 1 and refreshed["untracked"] == 1, refreshed
            response = await client.get("/api/sessions/{}".format(ids[0]), headers=headers)
            assert (await response.json())["session"]["git"] == refreshed

            refresh = "/api/sessions/{}/git/refresh"
            response = await client.post(refresh.format(ids[1]))
            assert response.status == 401
            response = await client.post(refresh.format(987654), headers=headers)
            assert response.status == 404
            # a focus refresh answers at once and publishes a changed mark
            response = await client.post(refresh.format(ids[1]), headers=headers)
            assert response.status == 200
            assert (await response.json())["git"]["repo"] is False
            git_dir(plain / ".git")
            calls = []
            real_inspect = session_git.inspect

            def counted(cwd):
                calls.append(cwd)
                time.sleep(.2)
                return real_inspect(cwd)

            with patch.object(session_git, "inspect", counted):
                first, second = await asyncio.gather(
                    client.post(refresh.format(ids[1]), headers=headers),
                    client.post(refresh.format(ids[1]), headers=headers))
                assert (await first.json())["git"]["repo"] is True
                assert (await second.json())["git"]["repo"] is True
            assert calls == [str(plain)], "concurrent refreshes share one inspection"
            assert (await listed(client, headers))[ids[1]]["repo"] is True
            published = runner.publish_state(runner.sessions_payload(), broadcast=False)
            assert next(row["git"] for row in published["sessions"]
                        if row["id"] == ids[1])["repo"] is True

            # a new session's directory is answered without waiting for the
            # next scheduled pass: asking about it wakes the worker
            late = TREE / "late" / str(int(time.time() * 1000))
            git_dir(late / ".git")
            new_id = db.create_session("", "claude", str(late), "", "", "", "default")
            assert (await listed(client, headers))[new_id] is None
            rows = await answered(client, headers, [new_id])
            assert rows[new_id]["repo"] is True

            # Something on this node changed the repository: a request is
            # served by the worker's next iteration, not the next pass, and
            # a changed count is published like a changed mark.
            (late / "one.txt").write_text("1")
            assert (await listed(client, headers))[new_id]["changes"] == 0
            session_git.request(str(late))
            rows = await answered(client, headers, [new_id], lambda row: row["changes"] == 1)
            assert rows[new_id]["unpushed"] is None
            # a finished prompt asks the same way for an ordinary session
            (late / "two.txt").write_text("2")
            session_git.turn_finished(db.get_session(new_id))
            await answered(client, headers, [new_id], lambda row: row["changes"] == 2)
            # and not for a task: its copy is a repository of its own, and
            # its prompts never reach the project's
            task_cwd = Path(workspaces.create_temporary())
            git(task_cwd, "init", "-q")
            task_id = db.create_session("", "claude", str(task_cwd), "", "", "", "default",
                                        workspace_kind="temporary")
            session_tasks._save(task_id, {
                "format": 1, "parent": new_id, "request_id": "git-test-task", "prompt": "t",
                "context": "", "base": "0" * 40, "created_at": time.time(), "outcome": "pending",
                "summary": "", "completed_at": 0, "applied_at": 0, "result_seq": 0})
            rows = await answered(client, headers, [task_id])
            assert rows[task_id]["changes"] == 0, rows[task_id]
            (task_cwd / "edit.txt").write_text("e")
            session_git.turn_finished(db.get_session(task_id))
            assert str(task_cwd) not in session_git._requested
            await asyncio.sleep(.3)
            assert (await listed(client, headers))[task_id]["changes"] == 0, "not looked at again"
            session_git.request(str(task_cwd))
            await answered(client, headers, [task_id], lambda row: row["changes"] == 1)

            # The runner itself: a prompt that ran the engine ends with the
            # re-check for an ordinary session, and without one for a task.
            (late / "three.txt").write_text("3")
            (task_cwd / "more.txt").write_text("m")
            await run_turn(new_id)
            await answered(client, headers, [new_id], lambda row: row["changes"] == 3)
            await run_turn(task_id)
            await asyncio.sleep(.3)
            assert (await listed(client, headers))[task_id]["changes"] == 1, "a task's prompt asks nothing"
            db.query("DELETE FROM meta WHERE key=?", (session_tasks.PREFIX + str(task_id),))
            db.delete_session(task_id)

            # a directory no session uses any more is forgotten by the next pass
            db.delete_session(new_id)
            assert str(late) in session_git._records
            await session_git._pass(full=True)
            assert str(late) not in session_git._records
            assert str(repo) in session_git._records
            # a request about a directory no session uses is ignored
            session_git.request(str(late))
            await session_git._pass(full=False, requested={str(late)})
            assert str(late) not in session_git._records
            session_git._requested.clear()

            # the timer that paces the worker is the node's own, edited with
            # the other backend timers and waking the worker when it moves
            response = await client.get("/api/timers", headers=headers)
            timers = (await response.json())["timers"]
            assert timers["values"]["git_check_minutes"] == 15
            assert timers["defaults"]["git_check_minutes"] == 15
            assert timers["limits"]["git_check_minutes"] == {
                "min": 1, "max": 7 * 24 * 60, "unit": "minutes"}
            with patch.object(session_git, "settings_changed") as changed:
                response = await client.patch("/api/timers", headers=headers,
                                              json={"git_check_minutes": 3})
                assert response.status == 200, await response.text()
                assert changed.call_count == 1
                response = await client.patch("/api/timers", headers=headers,
                                              json={"git_check_minutes": 3})
                assert response.status == 200 and changed.call_count == 1
                response = await client.patch("/api/timers", headers=headers,
                                              json={"git_check_minutes": 0})
                assert response.status == 400 and changed.call_count == 1
            assert session_git.interval_seconds() == 180
            config.set_timers({"git_check_minutes": 15})

            if full:
                # a refresh changes nothing a backup copies, so it never counts
                # as a mutation a backup would wait for; the busy refusal holds
                seen = []

                def watching(cwd):
                    seen.append(app["puppy_mutations"])
                    return real_inspect(cwd)

                with patch.object(session_git, "inspect", watching):
                    response = await client.post(refresh.format(ids[0]), headers=headers)
                    assert response.status == 200
                assert seen == [0], seen
                app["puppy_snapshot_busy"] = "export"
                try:
                    response = await client.post(refresh.format(ids[0]), headers=headers)
                    assert response.status == 503
                finally:
                    app["puppy_snapshot_busy"] = None
            if worker is not None:
                try:
                    await worker.__anext__()
                except StopAsyncIteration:
                    pass
                worker = None
    finally:
        for sid in ids:
            db.delete_session(sid)
        (repo / "src" / "deep" / "file.txt").unlink()
        session_git.reset_for_tests()
    print("routes and worker on the {} runtime".format("full" if full else "headless"))


def config_shape() -> None:
    exported = config.export_data()
    assert exported["timers"]["git_check_minutes"] == 15
    assert config.normalize_import(exported) == exported
    outdated = config.export_data()
    del outdated["timers"]["git_check_minutes"]
    try:
        config.normalize_import(outdated)
    except ValueError as exc:
        assert "git_check_minutes" in str(exc), exc
    else:
        raise AssertionError("a timers map from before the Git check was accepted")
    print("config: the timers map carries git_check_minutes and refuses the shape without it")


async def main() -> None:
    try:
        TREE.mkdir(parents=True)
        config.load()
        db.connect()
        discovery()
        work_state()
        config_shape()
        await api_contract(webui.build_app)
        await api_contract(backend_app)
        print("session git tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
