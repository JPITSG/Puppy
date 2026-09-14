#!/usr/bin/env python3
"""The sidebar's Git mark, from the directory up: discovery against a real
tree, the node's cache and worker, the focus refresh on both authenticated
runtimes, the timer that paces it, and the guards around it. No engine,
network or quota; ``git`` itself is never run."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

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
from puppy import config, db, protocol, runner, session_git  # noqa: E402
from puppy import web as webui  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402


def git_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")


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
    (bare / "objects").mkdir()
    (bare / "refs").mkdir()
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


async def listed(client, headers):
    response = await client.get("/api/sessions", headers=headers)
    assert response.status == 200
    return {row["id"]: row["git"] for row in (await response.json())["sessions"]}


async def answered(client, headers, ids, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        rows = await listed(client, headers)
        if all(rows.get(sid) is not None for sid in ids):
            return rows
        assert time.monotonic() < deadline, rows
        await asyncio.sleep(.05)


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
            # the worker's first pass answers every session's directory
            rows = await answered(client, headers, ids)
            assert rows[ids[0]]["repo"] is True and "error" not in rows[ids[0]]
            assert rows[ids[1]]["repo"] is False
            assert rows[ids[2]]["repo"] is None and rows[ids[2]]["error"]
            response = await client.get("/api/sessions/{}".format(ids[0]), headers=headers)
            assert (await response.json())["session"]["git"]["repo"] is True

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
            new_id = db.create_session("", "codex", str(late), "", "", "", "default")
            assert (await listed(client, headers))[new_id] is None
            rows = await answered(client, headers, [new_id])
            assert rows[new_id]["repo"] is True

            # a directory no session uses any more is forgotten by the next pass
            db.delete_session(new_id)
            assert str(late) in session_git._records
            await session_git._pass(full=True)
            assert str(late) not in session_git._records
            assert str(repo) in session_git._records

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
        config_shape()
        await api_contract(webui.build_app)
        await api_contract(backend_app)
        print("session git tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
