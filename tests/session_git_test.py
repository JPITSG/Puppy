#!/usr/bin/env python3
"""The sidebar's Git mark, from the directory up: discovery against a real
tree, the work state read from real repositories (uncommitted paths,
commits no remote holds, and every way git can decline to say), the
listing behind those counts that the mark's sheet reads, the node's cache
and worker, the focus refresh and the sheet's read on both authenticated
runtimes, the sheet's Push and Revert against a real bare remote - what
they do to the tree, what they answer, what they refuse and the cancel of
a push under way - the re-check a finished prompt asks for - through the
runner itself, with a task's prompt left out - the timer that paces it,
and the guards around it. No engine, network or quota; ``git`` runs only
on scratch repositories."""
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
from puppy import config, db, operations, protocol, runner, session_git, session_tasks, workspaces  # noqa: E402
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


def listing() -> None:
    """The sheet's read: the same inspection with the paths and the commits
    behind the counts listed, exactly as git holds them."""
    project = TREE / "listing"
    project.mkdir()
    git(project, "init", "-q")
    # an unborn branch with nothing yet: no HEAD, no upstream, no remote
    record = session_git.inspect(str(project), listing=True)
    assert record["root"] == str(project.resolve()), record
    detail = record["detail"]
    assert detail == {"head": None, "upstream": None, "ahead": None, "behind": None,
                      "remotes": [], "push_to": None, "paths": [], "commits": [],
                      "more_paths": 0, "more_commits": 0}, detail
    # a plain inspection carries neither the root nor the listing, the cache
    # keeps neither, and the payload never sees them
    plain = session_git.inspect(str(project))
    assert "root" not in plain and "detail" not in plain, plain
    session_git.reset_for_tests()
    assert session_git._store(str(project), record) is True
    assert set(session_git._records[str(project)]) == set(plain), session_git._records
    assert set(session_git._public(record)) == set(session_git._public(plain))
    session_git.reset_for_tests()

    (project / "kept.txt").write_text("k\n")
    (project / "old name.txt").write_text("o\n")
    (project / "edited.txt").write_text("e\n")
    (project / "gone.txt").write_text("g\n")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "one")
    git(project, "commit", "-q", "--allow-empty", "-m", "two: the second\n\nwith a body")
    git(TREE, "init", "-q", "--bare", "listing-remote.git")
    git(project, "remote", "add", "origin", str(TREE / "listing-remote.git"))
    git(project, "mv", "old name.txt", "new name.txt")
    (project / "new name.txt").write_text("renamed and edited\n")
    (project / "edited.txt").write_text("changed\n")
    (project / "gone.txt").unlink()
    (project / "added.txt").write_text("a\n")
    git(project, "add", "added.txt")
    (project / "loose.txt").write_text("l\n")
    (project / "dir").mkdir()
    (project / "dir" / "inner").write_text("i\n")
    record = session_git.inspect(str(project), listing=True)
    assert (record["changes"], record["staged"], record["unstaged"], record["untracked"],
            record["conflicts"]) == (6, 2, 2, 2, 0), record
    detail = record["detail"]
    # every listed path with its kind and git's own code, a rename with its
    # old name, a space carried exactly, an untracked directory as one
    assert detail["paths"] == [
        {"kind": "staged", "code": "A.", "path": "added.txt"},
        {"kind": "unstaged", "code": ".M", "path": "edited.txt"},
        {"kind": "unstaged", "code": ".D", "path": "gone.txt"},
        {"kind": "staged", "code": "RM", "path": "new name.txt", "from": "old name.txt"},
        {"kind": "untracked", "code": "??", "path": "dir/"},
        {"kind": "untracked", "code": "??", "path": "loose.txt"},
    ], detail["paths"]
    assert len(detail["paths"]) == record["changes"]
    assert detail["remotes"] == ["origin"] and detail["upstream"] is None
    assert detail["ahead"] is None and detail["behind"] is None
    # where a push would go without an upstream: the one remote there is
    assert detail["push_to"] == "origin/main"
    assert len(detail["head"]) == 40 and record["unpushed"] == 2
    # the commits newest first, the subject one line however the message
    # was written, the author and the commit time
    commits = detail["commits"]
    assert [c["subject"] for c in commits] == ["two: the second", "one"], commits
    assert all(c["author"] == "Puppy tests" and abs(c["at"] - time.time()) < 60 for c in commits), commits
    assert all(len(c["hash"]) >= 7 for c in commits), commits
    assert detail["head"].startswith(commits[0]["hash"])
    assert (detail["more_paths"], detail["more_commits"]) == (0, 0)
    # pushed with an upstream: nothing unpushed, the upstream named and even
    git(project, "push", "-q", "-u", "origin", "main")
    detail = session_git.inspect(str(project), listing=True)["detail"]
    assert detail["upstream"] == "origin/main" and (detail["ahead"], detail["behind"]) == (0, 0)
    assert detail["commits"] == [] and detail["push_to"] == "origin/main"
    # ahead of it again - two commits written past the index, so the staged
    # work stays staged - and the bounds: what they cut is counted
    for subject in ("three", "four"):
        tree = git(project, "rev-parse", "HEAD^{tree}").strip()
        commit = git(project, "commit-tree", tree, "-p", "HEAD", "-m", subject).strip()
        git(project, "update-ref", "HEAD", commit)
    with patch.object(session_git, "DETAIL_PATHS", 4), patch.object(session_git, "DETAIL_COMMITS", 1):
        record = session_git.inspect(str(project), listing=True)
    detail = record["detail"]
    assert (detail["ahead"], detail["behind"]) == (2, 0)
    assert record["changes"] == 6 and len(detail["paths"]) == 4 and detail["more_paths"] == 2
    assert record["unpushed"] == 2 and [c["subject"] for c in detail["commits"]] == ["four"]
    assert detail["more_commits"] == 1
    # a conflict is named as git names it, on both sides
    git(project, "checkout", "-q", "-b", "side")
    git(project, "commit", "-q", "-am", "side edit")
    git(project, "checkout", "-q", "main")
    (project / "edited.txt").write_text("main\n")
    git(project, "commit", "-q", "-am", "main edit")
    merge = subprocess.run(["git", "merge", "side"], cwd=str(project), capture_output=True, text=True)
    assert merge.returncode and "CONFLICT" in merge.stdout, merge
    detail = session_git.inspect(str(project), listing=True)["detail"]
    assert {"kind": "conflicts", "code": "UU", "path": "edited.txt"} in detail["paths"], detail["paths"]
    git(project, "merge", "--abort")
    # a detached HEAD: no branch, the commit it is at
    git(project, "checkout", "-q", "--detach", "main")
    record = session_git.inspect(str(project), listing=True)
    assert record["branch"] is None and len(record["detail"]["head"]) == 40
    assert record["detail"]["upstream"] is None and record["detail"]["push_to"] is None
    # git's refusal: the root is still known, the listing is not
    refused = TREE / "refused"
    record = session_git.inspect(str(refused), listing=True)
    assert record["repo"] is True and record["error"] and "detail" not in record, record
    assert record["root"] == str(refused.resolve())
    # outside a repository there is nothing to carry
    record = session_git.inspect(str(TREE / "plain"), listing=True)
    assert record == {"repo": False, "checked_at": record["checked_at"]}, record
    print("listing: paths by kind with git's codes and old names, commits, upstream, bounds and refusals")


def history() -> None:
    """The sheet's History: the short log of everything on HEAD, a page at
    a time - the total the node counted, the commits from ``skip`` on with
    LOG_PAGE at most, whether more follow - against a real repository."""
    project = TREE / "history"
    project.mkdir()
    git(project, "init", "-q")
    # an unborn branch: nothing to list and nothing to count
    assert session_git._log_page(str(project), 0, 100) == {
        "total": 0, "skip": 0, "commits": [], "more": False}
    for at in range(1, 251):
        (project / "log.txt").write_text("{}\n".format(at))
        git(project, "add", "log.txt")
        git(project, "commit", "-q", "-m", "Entry {} with a subject long enough to test the width".format(at))
    first = session_git._log_page(str(project), 0, 100)
    assert first["total"] == 250 and first["skip"] == 0 and first["more"] is True, first
    assert len(first["commits"]) == 100 and first["commits"][0]["subject"].startswith("Entry 250 "), first["commits"][0]
    assert first["commits"][-1]["subject"].startswith("Entry 151 ")
    commit = first["commits"][0]
    assert set(commit) == {"hash", "subject", "author", "at"} and commit["author"] == "Puppy tests", commit
    assert isinstance(commit["at"], int) and len(commit["hash"]) >= 7, commit
    second = session_git._log_page(str(project), 100, 100)
    assert second["skip"] == 100 and second["more"] is True and len(second["commits"]) == 100
    assert second["commits"][0]["subject"].startswith("Entry 150 ")
    last = session_git._log_page(str(project), 200, 100)
    assert last["more"] is False and len(last["commits"]) == 50, last["more"]
    assert last["commits"][-1]["subject"].startswith("Entry 1 ")
    assert first["commits"] + second["commits"] + last["commits"] == \
        session_git._commits(str(project), "HEAD", limit=250), "the pages are the log in order"
    # past the end: nothing, and no git log run for it
    assert session_git._log_page(str(project), 900, 100) == {
        "total": 250, "skip": 900, "commits": [], "more": False}
    # a smaller page, and a session in a subdirectory reading the same log
    (project / "sub").mkdir()
    page = session_git._log_page(str(project / "sub"), 3, 5)
    assert page["total"] == 250 and len(page["commits"]) == 5 and page["more"] is True
    assert page["commits"][0]["subject"].startswith("Entry 247 "), page["commits"][0]
    # outside a repository git refuses, and the reason is git's
    try:
        session_git._log_page(str(TREE / "plain"), 0, 100)
    except session_git.GitRefused as exc:
        assert "not a git repository" in str(exc), exc
    else:
        raise AssertionError("a directory outside any repository answered a page")
    print("history: the short log paged from a real repository, in order, bounded, from a subdirectory, refused outside one")


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

            # the sheet's read: one more look with the listing behind the
            # counts, the record brought up to date by it and published like
            # a refresh, the root the discovery stopped at
            assert protocol.SESSION_GIT_DETAIL_CAPABILITY in app["puppy_capabilities"]
            sheet = "/api/sessions/{}/git"
            response = await client.get(sheet.format(ids[0]), headers=headers)
            data = await response.json()
            assert response.status == 200 and data["ok"] is True, data
            assert data["git"]["changes"] == 1 and data["git"]["untracked"] == 1, data
            assert data["root"] == str(repo.resolve()), data
            # the one untracked directory git lists, the whole of it new
            assert data["detail"]["paths"] == [{"kind": "untracked", "code": "??", "path": "src/"}], data
            assert data["detail"]["commits"] == [] and data["detail"]["remotes"] == []
            assert data["detail"]["head"] is None and data["detail"]["upstream"] is None
            assert (await listed(client, headers))[ids[0]] == data["git"], "the cache moved with the read"
            # outside a repository, and for a directory that is not there,
            # there is nothing to list and no root to name
            response = await client.get(sheet.format(ids[1]), headers=headers)
            data = await response.json()
            assert data["git"]["repo"] is False and data["root"] is None and data["detail"] is None, data
            response = await client.get(sheet.format(ids[2]), headers=headers)
            data = await response.json()
            assert data["git"]["repo"] is None and data["git"]["error"], data
            assert data["root"] is None and data["detail"] is None
            response = await client.get(sheet.format(ids[0]))
            assert response.status == 401
            response = await client.get(sheet.format(987654), headers=headers)
            assert response.status == 404

            # the sheet's History: one page of the short log through its
            # own route - the repository's unborn branch has none to list,
            # outside a repository and for a directory that is not there
            # git's refusal is the answer, and the page's bounds are held
            assert protocol.SESSION_GIT_LOG_CAPABILITY in app["puppy_capabilities"]
            log_route = "/api/sessions/{}/git/log"
            response = await client.get(log_route.format(ids[0]), headers=headers)
            data = await response.json()
            assert response.status == 200 and data == {
                "ok": True, "total": 0, "skip": 0, "commits": [], "more": False}, data
            for sid in ids[1:]:
                response = await client.get(log_route.format(sid), headers=headers)
                data = await response.json()
                assert response.status == 409 and data["error"], (sid, data)
            for query in ("skip=-1", "skip=x", "limit=0", "limit=101", "limit=1.5", "skip=1&limit="):
                response = await client.get(log_route.format(ids[0]) + "?" + query, headers=headers)
                assert response.status == (200 if query.endswith("limit=") else 400), (query, response.status)
            response = await client.get(log_route.format(ids[0]))
            assert response.status == 401
            response = await client.get(log_route.format(987654), headers=headers)
            assert response.status == 404
            # a repository with commits, read from a session in a
            # subdirectory: the whole log paged two at a time, newest first,
            # the last page short and the one past the end empty
            paged = TREE / "paged" / str(int(time.time() * 1000))
            (paged / "sub").mkdir(parents=True)
            git(paged, "init", "-q")
            for subject in ("first", "second", "third"):
                (paged / "log.txt").write_text(subject + "\n")
                git(paged, "add", "log.txt")
                git(paged, "commit", "-q", "-m", subject)
            paged_sid = db.create_session("", "claude", str(paged / "sub"), "", "", "", "default")
            try:
                pages = []
                for skip in (0, 2, 4):
                    response = await client.get(log_route.format(paged_sid) + "?skip={}&limit=2".format(skip),
                                                headers=headers)
                    data = await response.json()
                    assert response.status == 200 and data["ok"] is True and data["total"] == 3, data
                    assert data["skip"] == skip, data
                    pages.append(data)
                assert [c["subject"] for c in pages[0]["commits"]] == ["third", "second"], pages[0]
                assert pages[0]["more"] is True and pages[1]["more"] is False and pages[2]["more"] is False
                assert [c["subject"] for c in pages[1]["commits"]] == ["first"] and pages[2]["commits"] == []
                assert all(c["author"] == "Puppy tests" and isinstance(c["at"], int) and len(c["hash"]) >= 7
                           for c in pages[0]["commits"]), pages[0]
                # the default page is the whole short log here, and the
                # read is a read: never counted as a mutation
                before = app.get("puppy_mutations")
                response = await client.get(log_route.format(paged_sid), headers=headers)
                data = await response.json()
                assert [c["subject"] for c in data["commits"]] == ["third", "second", "first"] and data["more"] is False
                assert app.get("puppy_mutations") == before
            finally:
                db.delete_session(paged_sid)
            # a focus refresh answers at once and publishes a changed mark
            response = await client.post(refresh.format(ids[1]), headers=headers)
            assert response.status == 200
            assert (await response.json())["git"]["repo"] is False
            git_dir(plain / ".git")
            calls = []
            real_inspect = session_git.inspect

            def counted(cwd, listing=False):
                calls.append((cwd, listing))
                time.sleep(.2)
                return real_inspect(cwd, listing)

            with patch.object(session_git, "inspect", counted):
                first, second = await asyncio.gather(
                    client.post(refresh.format(ids[1]), headers=headers),
                    client.post(refresh.format(ids[1]), headers=headers))
                assert (await first.json())["git"]["repo"] is True
                assert (await second.json())["git"]["repo"] is True
            assert calls == [(str(plain), False)], "concurrent refreshes share one inspection"
            assert (await listed(client, headers))[ids[1]]["repo"] is True
            # a listing inspection serves the sheet's reads and a refresh
            # that meet on it alike; one without the listing serves no read,
            # which waits for it and looks once more
            calls.clear()
            with patch.object(session_git, "inspect", counted):
                first = asyncio.ensure_future(client.get(sheet.format(ids[1]), headers=headers))
                await asyncio.sleep(.05)
                second, third = await asyncio.gather(
                    client.get(sheet.format(ids[1]), headers=headers),
                    client.post(refresh.format(ids[1]), headers=headers))
                await first
                assert calls == [(str(plain), True)], calls
                assert (await (await first).json())["detail"]["paths"] == []
                assert (await second.json())["root"] == str(plain.resolve())
                assert (await third.json())["git"]["repo"] is True
                calls.clear()
                first = asyncio.ensure_future(client.post(refresh.format(ids[1]), headers=headers))
                await asyncio.sleep(.05)
                second = await client.get(sheet.format(ids[1]), headers=headers)
                await first
                assert calls == [(str(plain), False), (str(plain), True)], calls
                assert (await second.json())["detail"]["remotes"] == []
            assert not session_git._inflight and not session_git._listing
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

                def watching(cwd, listing=False):
                    seen.append(app["puppy_mutations"])
                    return real_inspect(cwd, listing)

                with patch.object(session_git, "inspect", watching):
                    response = await client.post(refresh.format(ids[0]), headers=headers)
                    assert response.status == 200
                    response = await client.get(sheet.format(ids[0]), headers=headers)
                    assert response.status == 200
                assert seen == [0, 0], seen
                app["puppy_snapshot_busy"] = "export"
                try:
                    response = await client.post(refresh.format(ids[0]), headers=headers)
                    assert response.status == 503
                    # a read is a read: it answers while a backup is built,
                    # and only a restore, which replaces the very list it
                    # reads, refuses it
                    response = await client.get(sheet.format(ids[0]), headers=headers)
                    assert response.status == 200
                    app["puppy_snapshot_busy"] = "restore"
                    response = await client.get(sheet.format(ids[0]), headers=headers)
                    assert response.status == 503
                finally:
                    app["puppy_snapshot_busy"] = None
            await actions(client, headers, app)
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
    print("routes, the sheet's read, its actions and worker on the {} runtime".format(
        "full" if full else "headless"))


async def actions(client, headers, app) -> None:
    """The sheet's Push and Revert over the route, on a real repository with
    a real bare remote, from a session that sits in a subdirectory of it."""
    assert protocol.SESSION_GIT_ACTIONS_CAPABILITY in app["puppy_capabilities"]
    full = app["puppy_role"] == "full"
    stamp = str(int(time.time() * 1000))
    work = TREE / "acted" / stamp
    remote = TREE / "acted-remote" / (stamp + ".git")
    remote.parent.mkdir(parents=True, exist_ok=True)
    git(TREE, "init", "-q", "--bare", "-b", "main", str(remote))
    (work / "sub").mkdir(parents=True)
    git(work, "init", "-q")
    (work / ".gitignore").write_text("ignored.txt\n")
    (work / "kept.txt").write_text("original\n")
    git(work, "add", ".gitignore", "kept.txt")
    git(work, "commit", "-q", "-m", "first")
    git(work, "remote", "add", "origin", str(remote))
    sid = db.create_session("", "claude", str(work / "sub"), "", "", "", "default")
    push, revert = "/api/sessions/{}/git/push".format(sid), "/api/sessions/{}/git/revert".format(sid)
    sheet = "/api/sessions/{}/git".format(sid)
    try:
        response = await client.get(sheet, headers=headers)
        data = await response.json()
        # where a push would go, with the one remote and no upstream yet
        assert data["detail"]["push_to"] == "origin/main" and data["detail"]["upstream"] is None, data
        assert data["git"]["unpushed"] == 1 and data["git"]["changes"] == 0, data


        # Push: the commit sent to the one remote, the upstream set by the
        # push, the answer the read's fresh look plus what went where, the
        # cache and the list moved with it
        response = await client.post(push, headers=headers, json={})
        data = await response.json()
        assert response.status == 200 and data["ok"] is True, data
        assert data["pushed"] == {"to": "origin/main", "commits": 1}, data
        assert data["git"]["unpushed"] == 0 and data["root"] == str(work.resolve()), data
        assert data["detail"]["upstream"] == "origin/main" and data["detail"]["commits"] == [], data
        assert git(work, "rev-parse", "HEAD") == git(TREE, "--git-dir", str(remote), "rev-parse", "main")
        assert (await listed(client, headers))[sid] == data["git"]
        response = await client.post(push, headers=headers, json={})
        assert response.status == 409 and (await response.json())["error"] == "Nothing to push"

        # Revert, from the subdirectory the session sits in: the whole work
        # tree - a staged file, a modified one, an untracked one, a merge
        # stopped on a conflict - back to the last commit, the ignored file
        # and the nested repository left alone, and the session's own
        # directory - untracked, so cleaned away with its file - put back
        (work / "staged.txt").write_text("s\n")
        git(work, "add", "staged.txt")
        (work / "kept.txt").write_text("edited\n")
        (work / "sub" / "loose.txt").write_text("l\n")
        (work / "ignored.txt").write_text("i\n")
        (work / "nested").mkdir()
        git(work / "nested", "init", "-q")
        (work / "nested" / "inner.txt").write_text("n\n")
        response = await client.get(sheet, headers=headers)
        assert (await response.json())["git"]["changes"] == 4
        response = await client.post(revert, headers=headers, json={})
        data = await response.json()
        assert response.status == 200 and data["ok"] is True, data
        assert data["reverted"] == {"changes": 3}, data
        assert data["git"]["changes"] == 1 and data["git"]["untracked"] == 1, data
        assert data["detail"]["paths"] == [{"kind": "untracked", "code": "??", "path": "nested/"}], data
        assert not (work / "staged.txt").exists() and not (work / "sub" / "loose.txt").exists()
        assert (work / "sub").is_dir() and not os.listdir(work / "sub"), "the session's directory is back, empty"
        assert (work / "kept.txt").read_text() == "original\n"
        assert (work / "ignored.txt").read_text() == "i\n", "an ignored file was never part of the work"
        assert (work / "nested" / "inner.txt").exists(), "a nested repository is not cleaned"
        assert (await listed(client, headers))[sid] == data["git"]
        shutil.rmtree(work / "nested")
        response = await client.post(revert, headers=headers, json={})
        assert response.status == 409 and (await response.json())["error"] == "Nothing to revert"
        # a conflict: the merge abandoned with the paths
        git(work, "checkout", "-q", "-b", "side")
        (work / "kept.txt").write_text("side\n")
        git(work, "commit", "-q", "-am", "side")
        git(work, "checkout", "-q", "main")
        (work / "kept.txt").write_text("main\n")
        git(work, "commit", "-q", "-am", "main")
        assert subprocess.run(["git", "merge", "side"], cwd=str(work), capture_output=True).returncode
        response = await client.get(sheet, headers=headers)
        assert (await response.json())["git"]["conflicts"] == 1
        response = await client.post(revert, headers=headers, json={})
        data = await response.json()
        assert response.status == 200 and data["reverted"] == {"changes": 1}, data
        assert data["git"]["changes"] == 0 and (work / "kept.txt").read_text() == "main\n"
        assert not (work / ".git" / "MERGE_HEAD").exists(), "the merge is abandoned"

        # a rejected push is git's own reason; a detached HEAD has nothing
        # to push from and the listing says there is nowhere to push to
        git(TREE, "clone", "-q", str(remote), str(work.parent / (stamp + "-other")))
        other = work.parent / (stamp + "-other")
        (other / "theirs.txt").write_text("t\n")
        git(other, "add", "theirs.txt")
        git(other, "commit", "-q", "-m", "theirs")
        git(other, "push", "-q", "origin", "main")
        response = await client.post(push, headers=headers, json={})
        assert response.status == 409, await response.text()
        assert (await response.json())["error"] == "[rejected] main -> main (fetch first)"
        git(work, "checkout", "-q", "--detach")
        response = await client.get(sheet, headers=headers)
        data = await response.json()
        assert data["git"]["branch"] is None and data["detail"]["push_to"] is None, data
        response = await client.post(push, headers=headers, json={})
        assert response.status == 409
        assert (await response.json())["error"] == "HEAD is detached; check out a branch to push it"
        git(work, "checkout", "-q", "main")
        # two remotes and no upstream: nowhere a push would go, unless
        # remote.pushDefault says
        git(work, "branch", "--unset-upstream")
        git(work, "remote", "add", "backup", str(remote))
        response = await client.get(sheet, headers=headers)
        assert (await response.json())["detail"]["push_to"] is None
        git(work, "config", "remote.pushDefault", "backup")
        response = await client.get(sheet, headers=headers)
        assert (await response.json())["detail"]["push_to"] == "backup/main"
        git(work, "config", "--unset", "remote.pushDefault")
        git(work, "remote", "remove", "backup")
        git(work, "branch", "-u", "origin/main")

        # refused while a turn runs or waits anywhere in the project - a
        # second session in the same work tree counts - and for a task's
        # copy, whatever its state
        (work / "again.txt").write_text("a\n")
        peer = db.create_session("", "claude", str(work), "", "", "", "default")
        hub = runner.hub(peer)
        hub.status = "running"
        try:
            response = await client.post(revert, headers=headers, json={})
            assert response.status == 409, await response.text()
            assert (await response.json())["error"] == \
                "Main or another session is using this project; try again when it is idle"
            assert (work / "again.txt").exists()
            response = await client.post(push, headers=headers, json={})
            assert response.status == 409
        finally:
            hub.status = "idle"
            runner._hubs.pop(peer, None)
            db.delete_session(peer)
        task_cwd = Path(workspaces.create_temporary())
        git(task_cwd, "init", "-q")
        (task_cwd / "t.txt").write_text("t\n")
        task_id = db.create_session("", "claude", str(task_cwd), "", "", "", "default",
                                    workspace_kind="temporary")
        session_tasks._save(task_id, {
            "format": 1, "parent": sid, "request_id": "git-action-task", "prompt": "t",
            "context": "", "base": "0" * 40, "created_at": time.time(), "outcome": "pending",
            "summary": "", "completed_at": 0, "applied_at": 0, "result_seq": 0})
        try:
            response = await client.post("/api/sessions/{}/git/revert".format(task_id), headers=headers, json={})
            assert response.status == 409
            assert (await response.json())["error"].startswith("A task's copy is reviewed and applied from Main")
            assert (task_cwd / "t.txt").exists()
        finally:
            db.query("DELETE FROM meta WHERE key=?", (session_tasks.PREFIX + str(task_id),))
            db.delete_session(task_id)
        # the route's guards: authentication, the session, the two verbs
        response = await client.post(revert, json={})
        assert response.status == 401
        response = await client.post("/api/sessions/987654/git/push", headers=headers, json={})
        assert response.status == 404
        response = await client.post("/api/sessions/{}/git/reset".format(sid), headers=headers, json={})
        assert response.status == 404

        # a push under way is cancelled through the operation it was
        # started as: git's process group ended, the project released
        started = []

        def slow_push(root):
            started.append(time.monotonic())
            operations.run_process(["sleep", "30"], timeout=60)
            raise AssertionError("the push was not cancelled")

        identity = "git-action-cancel-" + stamp
        with patch.object(session_git, "_push", slow_push):
            request = asyncio.ensure_future(client.post(
                push, headers=dict(headers, **{operations.HEADER: identity}), json={}))
            deadline = time.monotonic() + 5
            while not started:
                assert time.monotonic() < deadline, "the push did not start"
                await asyncio.sleep(.02)
            response = await client.delete("/api/operations/" + identity, headers=headers)
            assert response.status == 200 and (await response.json())["state"] == "cancelling"
            response = await request
        assert response.status == 409 and (await response.json()).get("cancelled") is True
        assert time.monotonic() - started[0] < 5, "the sleeping git was ended, not waited out"
        assert not session_tasks.busy() and not session_tasks._operations
        assert (await listed(client, headers))[sid]["unpushed"] == 1, "nothing was pushed"
        # and a revert can follow at once
        response = await client.post(revert, headers=headers, json={})
        assert response.status == 200 and (await response.json())["reverted"] == {"changes": 1}

        if full:
            # an action is a write: it counts as a mutation a backup waits
            # for, and is refused while one is built
            seen = []
            real_revert = session_git._revert

            def watching(root, cwd):
                seen.append(app["puppy_mutations"])
                return real_revert(root, cwd)

            (work / "once.txt").write_text("o\n")
            with patch.object(session_git, "_revert", watching):
                response = await client.post(revert, headers=headers, json={})
                assert response.status == 200
            assert seen == [1], seen
            app["puppy_snapshot_busy"] = "export"
            try:
                response = await client.post(revert, headers=headers, json={})
                assert response.status == 503
            finally:
                app["puppy_snapshot_busy"] = None
        else:
            app["puppy_snapshot_busy"] = "export"
            try:
                response = await client.post(revert, headers=headers, json={})
                assert response.status == 409
                assert (await response.json())["error"] == "Git actions are paused for a snapshot"
            finally:
                app["puppy_snapshot_busy"] = None
    finally:
        db.delete_session(sid)
        shutil.rmtree(work.parent, ignore_errors=True)
        shutil.rmtree(remote.parent, ignore_errors=True)


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
        listing()
        history()
        config_shape()
        await api_contract(webui.build_app)
        await api_contract(backend_app)
        print("session git tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
