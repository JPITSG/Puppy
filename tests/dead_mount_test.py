#!/usr/bin/env python3
"""A drive that stops answering, against both runtimes.

First without any such drive: everything a console or an agent can ask for is
asked, with a project that counts as somebody else's mount, while every touch
of it from the event loop's own thread is caught. Then, as root with /dev/fuse,
with a drive that really hangs (tests/deadfs.py): what does not need it answers
at once, what does says so in time and does nothing later, other sessions work,
work that was under way leaves the rest alone, everything comes back by itself,
and Puppy starts, stops and is restarted meanwhile. No engine, network or quota.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch
import urllib.request

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))
from tests import deadfs  # noqa: E402

# before any thread is made: the mounts below are this process's own
FUSE = deadfs.private_mounts()

from tests.scratch import private_root  # noqa: E402

ROOT = private_root("dead-mount-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from puppy import (backup_schedule, cli_releases, config, db, files_agent, host_metrics,  # noqa: E402
                   place_scout, places, runner, schedules, search, session_directories, session_git,
                   session_links, session_tasks, task_agent, workers, workspace_sync)
from puppy.drivers import get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from puppy_backend.app import build_app as build_backend_app  # noqa: E402

TOUCH = 0.6
DRIVE = ROOT / "drive"          # what the sessions see
BACK = ROOT / "back"            # where its files are
PROJECT = DRIVE / "project"
LOCAL = ROOT / "local"
LINKED = ROOT / "linked"
GONE = "not answering"
FIXTURE = r'''
import json, os, sys, time
def read():
    return json.loads(sys.stdin.readline())
def send(value):
    print(json.dumps(value), flush=True)
initial = read()
send({"type": "control_response", "response": {"subtype": "success", "request_id": initial["request_id"], "response": {}}})
prompt = json.dumps(read())
send({"type": "system", "subtype": "init", "session_id": "native", "tools": []})
if "touch the drive" in prompt:
    time.sleep(0.4)
    os.stat(os.environ["DEAD_MOUNT_PROJECT"])
send({"type": "assistant", "message": {"model": "m", "content": [{"type": "text", "text": "worked"}]}})
send({"type": "result", "subtype": "success", "is_error": False, "session_id": "native", "num_turns": 1,
      "usage": {"input_tokens": 1, "output_tokens": 1}, "result": "worked"})
while sys.stdin.readline():
    pass
'''


async def _no_release_check(drivers, force=False):
    return 10 ** 9

cli_releases.refresh_if_due = _no_release_check


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", *args], cwd=str(cwd),
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


def make_project(path):
    path.mkdir(parents=True)
    (path / "AGENTS.md").write_text("notes\n")
    (path / "a.txt").write_text("one\n")
    (path / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    git("init", "-q", cwd=path)
    git("add", "-A", cwd=path)
    git("commit", "-qm", "first", cwd=path)
    (path / "a.txt").write_text("one\ntwo\n")


async def until(predicate, seconds=10.0, what="never came true"):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, what
        await asyncio.sleep(0.02)


class Site:
    """One runtime, its sessions, and how a console or an agent asks it."""

    def __init__(self, kind):
        self.kind = kind
        self.full = kind == "full"

    async def __aenter__(self):
        runner._draining = False    # the one before was shut down
        host_metrics.reset_for_tests()
        session_git.reset_for_tests()
        self.app = build_app() if self.full else build_backend_app()
        self.server = web.AppRunner(self.app, shutdown_timeout=1.0)     # five seconds in the service
        await self.server.setup()
        site = web.TCPSite(self.server, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:{}".format(site._server.sockets[0].getsockname()[1])
        self.http = aiohttp.ClientSession(headers={"X-Puppy-Token": config.get("auth.api_token")})
        return self

    async def __aexit__(self, *_exc):
        await self.http.close()
        await self.server.cleanup()
        sessions = [session["id"] for session in db.list_sessions(include_archived=True)]
        for sid in sorted(sessions, key=lambda sid: session_tasks.record(sid) is None):
            runner.drop_hub(sid)
            db.delete_session(sid)
        for lease in workspace_sync.list_leases():
            workspace_sync.release_lease(lease["id"])
        db.meta_apply(delete_keys=("backup_schedule", "backup_limit"))

    async def ask(self, method, path, **options):
        async with self.http.request(method, self.url + path, **options) as response:
            body = await response.read()
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body

    async def frame(self, path):
        async with self.http.ws_connect(self.url + path) as ws:
            return json.loads((await asyncio.wait_for(ws.receive(), 5)).data)["type"]

    def session(self, name, cwd):
        return db.create_session(name, "claude", str(cwd), "", "", "#e0784f", "ask")

    async def agent(self, bridge, sid, method, params):
        with patch.object(runner.hub(sid), "tool_turn_active", lambda turn: True):
            return await bridge._dispatch({"session_id": sid, "turn_id": "turn", "method": method,
                                           "params": params})

    async def prepare(self):
        self.there = self.session("On the drive", PROJECT)
        self.here = self.session("Local", LOCAL)
        self.moving = self.session("Moving", DRIVE / "carried")
        self.main = self.session("With tasks", PROJECT)
        self.spare = self.session("Spare", PROJECT)
        self.unused = self.session("Unused", PROJECT)
        self.carried = self.session("Carried", ROOT / "carry")
        # its folder is here; a note in it and its repository are on the drive
        self.linked = self.session("Linked", LINKED)
        db.add_event(self.there, "user", {"text": "some common words"})
        db.add_event(self.there, "tool_use", {"tool": "view_image", "tool_use_id": "v1",
                                              "input": {"path": str(PROJECT / "picture.png")}})
        self.picture = db.add_event(self.there, "tool_result", {"tool_use_id": "v1", "content": "ok"})["seq"]
        runner.broadcast_sessions()
        await until(lambda: session_directories.record(str(PROJECT)) is True)
        await until(lambda: session_directories.notes(str(LINKED)) == ["CLAUDE.md"])
        for folder in (PROJECT, LINKED):
            await until(lambda: (session_git.record(str(folder)) or {}).get("repo") is True)
        search.reconcile()
        status, answer = await self.ask("POST", "/api/workspace/leases", json={"root": str(DRIVE / "leased")})
        assert status == 200, answer
        self.lease = answer["lease"]["id"]
        self.task = await self.new_task(self.main)
        self.given_up = await self.new_task(self.spare)
        status, review = await self.ask("POST", "/api/sessions/{}/tasks/{}/review".format(self.main, self.task), json={})
        assert status == 200 and review["has_changes"], review
        self.token = review["token"]

    async def new_task(self, sid):
        status, answer = await self.ask("POST", "/api/sessions/{}/tasks".format(sid), json=self.task_body(sid))
        assert status == 200, answer
        tid = answer["session"]["id"]
        await until(lambda: runner.hub(tid).status == "idle")
        (Path(db.get_session(tid)["cwd"]) / "task-{}.txt".format(tid)).write_text("by the task\n")
        return tid

    def task_body(self, sid):
        return {"name": "Task", "prompt": "work", "engine": "claude", "model": "", "effort": "",
                "permission_mode": get_driver("claude").default_permission(),
                "request_id": os.urandom(8).hex()}

    async def turn(self, sid, text="hello"):
        """One turn to its end: the kinds of rows it left."""
        before = (db.query_one("SELECT MAX(seq) AS seq FROM events WHERE session_id=?", (sid,))["seq"] or 0)
        status, answer = await self.ask("POST", "/api/sessions/{}/message".format(sid), json={"text": text})
        assert status == 200, answer
        await until(lambda: runner.hub(sid).status == "idle")
        return [(event["kind"], event["data"].get("text", "")) for event in db.get_events(sid, after_seq=before)]

    # what is asked: (name, how, what it must answer while the drive is gone)

    def reading(self):
        """Nothing here needs the drive: it answers as ever, and at once."""
        sid = self.there
        ok = lambda answer: answer[0] == 200
        yield "session list", lambda: self.ask("GET", "/api/sessions"), ok
        yield "session", lambda: self.ask("GET", "/api/sessions/{}".format(sid)), ok
        yield "prompts", lambda: self.ask("GET", "/api/sessions/{}/prompts".format(sid)), ok
        yield "events", lambda: self.ask("GET", "/api/sessions/{}/events".format(sid)), ok
        yield "tasks", lambda: self.ask("GET", "/api/sessions/{}/tasks".format(self.main)), ok
        yield "shared files", lambda: self.ask("GET", "/api/sessions/{}/files".format(sid)), ok
        yield "upload", lambda: self.ask("POST", "/api/sessions/{}/upload?name=x.txt".format(sid), data=b"x"), ok
        yield "session socket", lambda: self.frame("/api/ws/session/{}".format(sid)), lambda kind: kind == "snapshot"
        yield "update stream", lambda: self.frame("/api/ws/updates"), bool
        yield "rename", lambda: self.ask("PATCH", "/api/sessions/{}".format(sid), json={"name": "Renamed"}), ok
        yield "order", lambda: self.ask("POST", "/api/sessions/reorder", json={
            "order": [row["id"] for row in db.list_sessions() if not session_tasks.record(row["id"])]}), bool
        yield "broadcast", self.broadcast, bool
        yield "search", lambda: self.ask("GET", "/api/search?q=common"), \
            lambda answer: answer[0] == 200 and answer[1]["total"] >= 1
        for method, params in (("sessions", {}), ("read", {"refs": [session_links.reference(sid)]}),
                               ("read", {"refs": [session_links.reference(sid)], "view": "turns"}),
                               ("search", {"refs": [session_links.reference(sid)], "query": "common"})):
            yield "agents' {}".format(method), lambda method=method, params=params: self.ask(
                "POST", "/api/session-links/node", json={"method": method, "params": params}), ok
        yield "task tool list", lambda: self.agent(task_agent, self.main, "tasks", {}), lambda answer: "Task" in answer["text"]
        yield "task's own review", lambda: self.ask(
            "POST", "/api/sessions/{}/tasks/{}/review".format(self.main, self.task), json={}), ok
        yield "the drive's parent folder", lambda: self.ask("GET", "/api/fs", params={"path": str(ROOT)}), ok
        yield "host", lambda: self.ask("GET", "/api/host/metrics?window=60"), \
            lambda answer: answer[0] == 200 and answer[1]["disks"]["stalled"] == [str(DRIVE)]
        if self.full:
            yield "state", lambda: self.ask("GET", "/api/state"), ok
            yield "schedules", lambda: self.ask("GET", "/api/schedules"), ok

    async def broadcast(self):
        runner.broadcast_sessions()
        return True

    def needing(self):
        """What needs the drive says that it does not answer."""
        sid, project = self.there, str(PROJECT)
        said = lambda status: lambda answer: answer[0] == status and GONE in answer[1]["error"]
        refused = said(409)
        failed = lambda answer: isinstance(answer, Exception) and GONE in str(answer)
        yield "folder", lambda: self.ask("GET", "/api/fs", params={"path": project}), refused
        yield "half a folder name", lambda: self.ask("GET", "/api/fs", params={"path": project[:-2]}), refused
        yield "new folder", lambda: self.ask("POST", "/api/fs/mkdir", json={"path": project + "/made"}), refused
        yield "notes", lambda: self.ask("GET", "/api/sessions/{}/agent-notes".format(sid)), refused
        yield "write notes", lambda: self.ask("PUT", "/api/sessions/{}/agent-notes".format(sid), json={
            "name": "CLAUDE.md", "text": "written"}), refused
        yield "new session", lambda: self.ask("POST", "/api/sessions", json={"engine": "claude", "cwd": project}), refused
        yield "new session in a new folder", lambda: self.ask("POST", "/api/sessions", json={
            "engine": "claude", "cwd": project + "/fresh", "mkdir": True}), refused
        yield "Git sheet", lambda: self.ask("GET", "/api/sessions/{}/git".format(sid)), \
            lambda answer: answer[0] == 200 and GONE in answer[1]["git"]["error"]
        yield "Git check", lambda: self.ask("POST", "/api/sessions/{}/git/refresh".format(sid)), \
            lambda answer: answer[0] == 200 and GONE in answer[1]["git"]["error"]
        yield "notes one of which is there", lambda: self.ask("GET", "/api/sessions/{}/agent-notes".format(
            self.linked)), refused
        yield "Git check of a worktree kept there", lambda: self.ask("POST", "/api/sessions/{}/git/refresh".format(
            self.linked)), lambda answer: answer[0] == 200 and GONE in answer[1]["git"]["error"]
        yield "Git history", lambda: self.ask("GET", "/api/sessions/{}/git/log".format(sid)), refused
        yield "push", lambda: self.ask("POST", "/api/sessions/{}/git/push".format(sid)), refused
        yield "revert", lambda: self.ask("POST", "/api/sessions/{}/git/revert".format(sid)), refused
        yield "picture", lambda: self.ask("GET", "/api/sessions/{}/tool-image/{}".format(sid, self.picture)), said(503)
        yield "terminal", lambda: self.ask("POST", "/api/terminal/instances", json={
            "command": "/bin/sleep 1", "cwd": project}), refused
        yield "spawn", lambda: self.ask("POST", "/api/spawn", json={
            "engine": "claude", "cwd": project, "prompt": "hello"}), refused
        yield "new task", lambda: self.ask("POST", "/api/sessions/{}/tasks".format(self.spare),
                                           json=self.task_body(self.spare)), refused
        yield "apply a task", lambda: self.ask("POST", "/api/sessions/{}/tasks/{}/apply".format(self.main, self.task),
                                               json={"token": self.token}), refused
        yield "refresh a task", lambda: self.ask("POST", "/api/sessions/{}/tasks/{}/refresh".format(
            self.main, self.task), json={}), refused
        yield "agent's new task", lambda: self.agent(task_agent, self.spare, "new_task", {"prompt": "x"}), failed
        yield "agent's share", lambda: self.agent(files_agent, sid, "share_file", {"path": "a.txt"}), failed
        yield "agent's share by its path", lambda: self.agent(files_agent, self.here, "share_file", {
            "path": project + "/a.txt"}), failed
        yield "link a workspace", lambda: self.ask("POST", "/api/workspace/leases", json={"root": project}), refused
        lease = "/api/workspace/leases/{}/".format(self.lease)
        yield "workspace manifest", lambda: self.ask("GET", lease + "manifest"), refused
        yield "workspace fetch", lambda: self.ask("POST", lease + "fetch", json={"paths": ["kept.txt"]}), refused
        yield "workspace apply", lambda: self.ask("POST", lease + "apply", data=self.frames(),
                                                  headers={"Content-Type": workspace_sync.CONTENT_TYPE}), refused
        yield "move a project off it", lambda: self.ask("POST", "/api/sessions/{}/workspace/move".format(self.moving),
                                                        json={"destination": str(ROOT / "moved")}), refused
        yield "move a project onto it", lambda: self.ask("POST", "/api/sessions/{}/workspace/move".format(self.carried),
                                                         json={"destination": project + "/moved"}), refused
        if self.full:
            yield "backups onto it", lambda: self.ask("PUT", "/api/snapshot/backups", json=self.backups()), said(400)

    @staticmethod
    def backups(enabled=False):
        return {"enabled": enabled, "at": "04:00", "directory": str(DRIVE / "backups"), "keep": 3,
                "archive_limit_mib": 512}

    @staticmethod
    def frames():
        body = b"from the other side\n"
        encode = workspace_sync.encode_frame
        return encode({"op": "mkdir", "p": "sent", "m": 0o755, "b": None}) + \
            encode({"op": "write", "p": "sent/file.txt", "s": len(body), "m": 0o644, "b": None,
                    "h": hashlib.sha256(body).hexdigest()}) + body + \
            encode({"p": "sent/file.txt", "ok": True}) + encode({"end": True})

    async def done(self, how):
        try:
            return await how()
        except Exception as exc:
            return exc


# ---- nothing touches a project from the event loop ----

class Guard:
    """Catches every touch of the drive made by the event loop's thread, and
    every program any thread starts inside it: until such a child has become
    the program it holds Puppy's own sockets, and a drive can keep it there."""

    NAMES = ("stat", "lstat", "readlink", "access", "listdir", "scandir", "open", "mkdir", "rename", "replace",
             "unlink", "remove", "rmdir", "statvfs", "chdir", "utime", "chmod", "symlink", "link")
    # by descriptor, whoever opened it: each of these waits for the disk too
    DESCRIPTORS = ("read", "write", "fsync", "close", "fchmod")
    EVENTS = ("open", "os.listdir", "os.scandir", "os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.chdir",
              "os.truncate", "os.utime", "os.chmod", "os.link", "os.symlink", "shutil.rmtree", "shutil.copyfile",
              "subprocess.Popen")

    def __init__(self):
        self.thread = threading.get_ident()
        self.on = False
        self.noting = threading.local()
        self.found = []
        self.real = {name: getattr(os, name) for name in self.NAMES + self.DESCRIPTORS}
        sys.addaudithook(self.audit)

    def note(self, what, path):
        if getattr(self.noting, "now", False):
            return
        self.noting.now = True
        try:
            if isinstance(path, bytes):
                path = os.fsdecode(path)
            if isinstance(path, (str, os.PathLike)) and (str(path) + "/").startswith(str(DRIVE) + "/"):
                stack = [frame for frame in traceback.extract_stack()[:-2] if "/puppy" in frame.filename and
                         "/tests/" not in frame.filename]
                if stack:
                    self.found.append("{} {} at {}".format(what, path, " < ".join(
                        "{}:{}".format(Path(frame.filename).name, frame.lineno) for frame in stack[::-1][:4])))
        finally:
            self.noting.now = False

    def audit(self, event, args):
        if not self.on or event not in self.EVENTS:
            return
        if event == "subprocess.Popen":
            self.note(event, args[2])
        elif threading.get_ident() == self.thread:
            self.note(event, args[0])

    def wrap(self, name):
        real = self.real[name]

        def wrapped(*args, **options):
            if self.on and threading.get_ident() == self.thread and args:
                if name in self.DESCRIPTORS:
                    if isinstance(args[0], int):
                        try:
                            path = self.real["readlink"]("/proc/self/fd/{}".format(args[0]))
                        except OSError:
                            path = ""
                        self.note(name, path)
                elif not options.get("dir_fd"):
                    self.note(name, args[0])
            return real(*args, **options)
        return wrapped

    @contextlib.contextmanager
    def watching(self):
        with contextlib.ExitStack() as stack:
            for name in self.real:
                stack.enter_context(patch.object(os, name, self.wrap(name)))
            self.on = True
            try:
                yield
            finally:
                self.on = False


async def off_the_loop(guard, kind):
    """The drive answers, and nobody goes to it from the event loop."""
    async with Site(kind) as site:
        asked = []
        with guard.watching():
            await site.prepare()
            for name, how, _expected in list(site.reading()) + list(site.needing()):
                asked.append(name)
                answer = await asyncio.wait_for(site.done(how), 20)
                assert not guard.found, (kind, name, guard.found)
                assert GONE not in str(answer), (name, answer)
                assert name != "host" or str(DRIVE) in {row["mount"] for row in answer[1]["disks"]["mounts"]}, answer
            # what an ordinary day does on its own
            assert ("assistant", "worked") in await site.turn(site.there)
            assert ("assistant", "worked") in await site.turn(site.here)
            status, answer = await site.ask("POST", "/api/sessions/{}/loop".format(site.spare), json={
                "text": "again", "iterations": 2})
            assert status == 200, answer
            await until(lambda: runner.hub(site.spare).status == "idle" and not runner.hub(site.spare).queue)
            # two of them at once: each has to know whose project the other is in
            runner.hub(site.there).send_message("touch the drive")
            await until(lambda: runner.hub(site.there)._active_turn_id)
            status, answer = await site.ask("POST", "/api/sessions/{}/tasks".format(site.spare),
                                            json=site.task_body(site.spare))
            assert status in (200, 409), answer
            await until(lambda: runner.hub(site.there).status == "idle")
            real_copy = session_tasks._copy_project

            def slowly(*args):
                time.sleep(0.5)
                return real_copy(*args)
            with patch.object(session_tasks, "_copy_project", slowly):
                making = asyncio.ensure_future(site.ask("POST", "/api/sessions/{}/tasks".format(site.spare),
                                                        json=site.task_body(site.spare)))
                await until(lambda: session_tasks._busy_roots)
                assert ("assistant", "worked") in await site.turn(site.here)
                status, answer = await site.ask("DELETE", "/api/sessions/{}".format(site.there))
                assert status == 409 and "workspace operation" in answer["error"], answer
                await making
            assert not guard.found, (kind, guard.found)
            status, answer = await site.ask("POST", "/api/sessions/{}/tasks/{}/auto-apply".format(
                site.main, site.task), json={"enabled": True})
            assert status == 200, answer
            await until(lambda: not session_tasks.children(site.main), what="the task was not applied and folded")
            (PROJECT / "AGENTS.md").rename(PROJECT / "OTHER.md")    # seen by its watch
            await until(lambda: session_directories.notes(str(PROJECT)) == [])
            (PROJECT / "OTHER.md").rename(PROJECT / "AGENTS.md")
            await until(lambda: session_directories.notes(str(PROJECT)) == ["AGENTS.md"])
            if site.full:
                await site.ask("PUT", "/api/snapshot/backups", json=site.backups())
                await site.ask("POST", "/api/snapshot/backups/run")
                await until(lambda: backup_schedule._load()["history"])
                status, saved = await site.ask("GET", "/api/snapshot/backups")
                assert status == 200 and saved["files"][0]["available"], saved
                status, body = await site.ask("GET", saved["files"][0]["download"])
                assert status == 200 and len(body) == saved["files"][0]["size"], (status, len(body))
                status, answer = await site.ask("DELETE", "/api/snapshot/saved/" + saved["files"][0]["id"])
                assert status == 200 and answer["deleted"] == "deleted", answer
            status, answer = await site.ask("DELETE", "/api/workspace/leases/" + site.lease)
            assert status == 200, answer
            status, answer = await site.ask("DELETE", "/api/sessions/{}".format(site.unused))
            assert status == 200, answer
            await asyncio.sleep(0.3)
            assert not guard.found, (kind, guard.found)
        assert guard.found == [] and not runner.hub(site.there).queue
    print("  {}: {} requests, turns, a loop, an automatic apply{} and the folder watch, none of them "
          "from the event loop".format(kind, len(asked), ", a backup" if kind == "full" else ""))


def somebody_elses(own=(), source="/proc/self/mountinfo", read=place_scout.read_table):
    table = read(own, source)
    table[str(DRIVE)] = place_scout.Mount(987654321, str(DRIVE))
    return table


# ---- a drive that really hangs ----

class Watch(threading.Thread):
    """Sees the event loop stand still, says where, and brings the drive back
    so that the rest can be looked at."""

    def __init__(self, loop, drive):
        super().__init__(daemon=True)
        self.loop, self.drive = loop, drive
        self.main = threading.main_thread().ident
        self.beat = time.monotonic()
        self.stood = []
        self.over = False
        self.start()

    def _beat(self):
        self.beat = time.monotonic()

    def run(self):
        while not self.over:
            try:
                self.loop.call_soon_threadsafe(self._beat)
            except RuntimeError:
                return
            time.sleep(0.05)
            if time.monotonic() - self.beat > 1.0:
                stack = traceback.extract_stack(sys._current_frames()[self.main])
                self.stood.append([(Path(frame.filename).name, frame.lineno, frame.name) for frame in stack][-5:])
                if self.drive.dead:
                    self.drive.revive()
                time.sleep(1.0)


def inside():
    """The threads of this very process that wait in the drive: there are none,
    ever, unless work was under way there when it stopped answering."""
    found = []
    for thread in os.listdir("/proc/self/task"):
        try:
            where = Path("/proc/self/task/{}/wchan".format(thread)).read_text()
        except OSError:
            continue
        if where == "request_wait_answer":
            found.append(thread)
    return found


async def dies(drive):
    """Nobody is inside by themselves at that moment."""
    monitor = session_directories._monitor
    await until(lambda: not (monitor.checking or monitor.wanted or monitor.wake.is_set() or
                             session_git._inflight or session_git._requested or inside()),
                what="somebody is always inside")
    drive.die()


async def noticed():
    session_directories._monitor.again()
    await until(lambda: session_directories.record(str(PROJECT)) is False, what="nobody noticed")


async def back(drive):
    if drive.dead:
        drive.revive()
    await until(lambda: not places._overdue, what="the drive is still silent")
    session_directories._monitor.again()
    await until(lambda: session_directories.record(str(PROJECT)) is True, what="the folder is still unavailable")
    await until(lambda: (session_git.record(str(PROJECT)) or {}).get("repo") is True and not session_git._inflight,
                what="its Git mark stayed as it was")


async def hanging(kind, drive):
    async with Site(kind) as site:
        watch = Watch(asyncio.get_running_loop(), drive)
        threads = threading.active_count()
        await site.prepare()
        # what is looked for below is there to be seen
        await dies(drive)
        one = threading.Thread(target=os.stat, args=(str(PROJECT),), daemon=True)
        one.start()
        await asyncio.sleep(0.3)
        assert len(inside()) == 1, "a thread that waits in the drive was not seen"
        drive.revive()
        one.join(5)
        await back(drive)

        # nothing that can do without the drive waits for it, even when nobody
        # knows yet that it hangs
        await dies(drive)
        for name, how, expected in site.reading():
            started = time.monotonic()
            answer = await asyncio.wait_for(site.done(how), 5)
            assert expected(answer), (kind, name, answer)
            limit = 3.0 if name in ("host", "task's own review") else 0.9
            assert time.monotonic() - started < limit, (kind, name, time.monotonic() - started)
            assert not watch.stood, (kind, name, watch.stood)
            assert not inside(), (kind, name)
        await back(drive)

        # what needs it says so in time, each the first to find out
        for name, how, expected in site.needing():
            await dies(drive)
            started = time.monotonic()
            answer = await asyncio.wait_for(site.done(how), 8)
            took = time.monotonic() - started
            assert expected(answer), (kind, name, answer)
            assert took < 4 * TOUCH + 0.5, (kind, name, took)
            assert not watch.stood, (kind, name, watch.stood)
            assert not inside(), (kind, name)
            await back(drive)

        # and at once, once it is known
        await dies(drive)
        await noticed()
        host_metrics.reset_for_tests()
        started = time.monotonic()
        status, answer = await site.ask("GET", "/api/host/metrics?window=60")
        assert answer["disks"]["stalled"] == [str(DRIVE)] and time.monotonic() - started < 0.9, answer["disks"]
        for name, how, expected in site.needing():
            started = time.monotonic()
            answer = await asyncio.wait_for(site.done(how), 8)
            assert expected(answer), (kind, name, answer)
            assert time.monotonic() - started < 0.5, (kind, name, time.monotonic() - started)
        # the console hears of it, and nothing starts there
        status, listed = await site.ask("GET", "/api/sessions")
        rows = {row["id"]: row for row in listed["sessions"]}
        assert rows[site.there]["cwd_available"] is False and rows[site.here]["cwd_available"] is True
        assert rows[site.there]["agent_notes"] == ["AGENTS.md"], "what was known of it still stands"
        # nor does a link to it make a folder unavailable, or wait for it each time
        assert rows[site.linked]["cwd_available"] is True and rows[site.linked]["agent_notes"] == ["CLAUDE.md"], \
            rows[site.linked]
        for _ in range(6):
            session_directories._monitor.again()
            session_git.request(str(LINKED))
            await asyncio.sleep(0.4)
        assert not inside()
        assert session_directories.record(str(LINKED)) is True and \
            session_directories.notes(str(LINKED)) == ["CLAUDE.md"]
        status, answer = await site.ask("POST", "/api/sessions/{}/message".format(site.there), json={"text": "go"})
        assert status == 400 and answer["error"] == runner.FOLDER_PAUSED, answer
        # what is let go of needs no drive
        status, answer = await site.ask("DELETE", "/api/sessions/{}".format(site.unused))
        assert status == 200, answer
        status, answer = await site.ask("POST", "/api/sessions/{}/tasks/{}/remove".format(site.spare, site.given_up),
                                        json={"fold": True})
        assert status == 200, answer
        # every other session works: a turn, a terminal, Git, a task
        assert ("assistant", "worked") in await site.turn(site.here)
        status, answer = await site.ask("POST", "/api/terminal/instances", json={
            "command": "/bin/sleep 5", "cwd": str(LOCAL)})
        assert status == 201, answer
        await site.ask("DELETE", "/api/terminal/instances/" + answer["terminal"]["id"])
        status, answer = await site.ask("GET", "/api/sessions/{}/git".format(site.here))
        assert status == 200 and answer["git"]["repo"] is True, answer
        status, answer = await site.ask("POST", "/api/sessions/{}/tasks".format(site.here), json=site.task_body(site.here))
        assert status == 200, answer
        other = answer["session"]["id"]
        await until(lambda: runner.hub(other).status == "idle")
        # whatever Puppy itself waits with, it is not a thread
        assert threading.active_count() - threads < 8, [thread.name for thread in threading.enumerate()]
        assert not inside()
        assert not watch.stood, watch.stood

        # it is back: the sessions by themselves, and nothing done behind anybody's back
        runner.hub(site.spare).queue.append("waited for the drive")
        revived = time.monotonic()
        drive.revive()
        await until(lambda: session_directories.record(str(PROJECT)) is True)
        assert time.monotonic() - revived < 3.0, time.monotonic() - revived
        await until(lambda: any(event["kind"] == "assistant" for event in db.get_events(site.spare)),
                    what="the queue did not move on")
        await until(lambda: runner.hub(site.spare).status == "idle")
        for path in (PROJECT / "made", PROJECT / "fresh", PROJECT / "moved", PROJECT / "CLAUDE.md",
                     DRIVE / "leased" / "sent", ROOT / "moved"):
            assert not path.exists(), "{} was made after all".format(path)
        assert (DRIVE / "carried" / "a.txt").exists() and not (PROJECT / "task-{}.txt".format(site.task)).exists()
        assert db.query_one("SELECT COUNT(*) AS n FROM sessions")["n"] == 9, "only the local task was made"
        await until(lambda: (session_git.record(str(PROJECT)) or {}).get("repo") is True, 5, "its Git mark stayed")
        assert (await site.ask("GET", "/api/sessions/{}/agent-notes".format(site.there)))[0] == 200
        for route in ("/git", "/git/log"):
            assert (await site.ask("GET", "/api/sessions/{}{}".format(site.there, route)))[0] == 200
        status, answer = await site.ask("POST", "/api/sessions/{}/tasks/{}/apply".format(site.main, site.task),
                                        json={"token": site.token})
        assert status == 200 and (PROJECT / "task-{}.txt".format(site.task)).exists(), answer
        print("  {}: {} things answer as ever, {} say so in time and at once, nothing starts there, "
              "everything else works, and it all comes back by itself".format(
                  kind, len(list(site.reading())), len(list(site.needing()))))

        await under_way(site, drive, watch)
        if site.full:
            await scheduled(site, drive, watch)
        # it goes with the drive still hanging, and work in it
        real_copy = session_tasks._copy_project

        def stuck(root, destination, committed=False):
            drive.die()
            return real_copy(root, destination, committed)
        with patch.object(session_tasks, "_copy_project", stuck):
            making = asyncio.ensure_future(site.ask("POST", "/api/sessions/{}/tasks".format(site.spare),
                                                    json=site.task_body(site.spare)))
            await until(lambda: drive.dead)
            await asyncio.sleep(0.3)
            await asyncio.wait_for(site.server.cleanup(), 6)
        assert session_tasks._operations, "it was still at work"
        watch.over = True
        assert not watch.stood, watch.stood
        drive.revive()
        await until(lambda: not places._overdue and not session_tasks._operations, 20)
        await asyncio.gather(making, return_exceptions=True)


async def under_way(site, drive, watch):
    """Work that had begun when the drive stopped answering."""
    real_names, real_git, real_copy = session_directories.inspect, session_git._run_git, session_tasks._copy_project

    # a turn whose folder goes just before it starts says so and is over
    await dies(drive)
    rows = await site.turn(site.there)
    assert any(kind == "error" and GONE in text for kind, text in rows), rows
    await back(drive)

    # an engine that waits for it, past any signal: its own session waits, and
    # Stop answers; everything else goes on
    runner.hub(site.there).send_message("touch the drive")
    await until(lambda: runner.hub(site.there)._active_turn_id and runner.hub(site.there).proc is not None)
    drive.die()
    await asyncio.sleep(0.8)
    started = time.monotonic()
    status, _ = await site.ask("POST", "/api/sessions/{}/interrupt".format(site.there))
    assert status == 200 and time.monotonic() - started < 2.0
    assert ("assistant", "worked") in await site.turn(site.here)
    assert runner.hub(site.there).status == "running", "its engine is still there"
    await back(drive)
    await until(lambda: runner.hub(site.there).status == "idle", what="the stopped turn never ended")

    # a look at a repository that dies halfway
    def dying(cwd, *args):
        if str(cwd) == str(PROJECT) and not drive.dead and dying.armed:
            dying.armed = False
            drive.die()
        return real_git(cwd, *args)
    dying.armed = True
    with patch.object(session_git, "_run_git", dying):
        started = time.monotonic()
        status, answer = await site.ask("POST", "/api/sessions/{}/git/refresh".format(site.there))
        assert status == 200 and GONE in answer["git"]["error"], answer
        assert time.monotonic() - started < 8 * TOUCH, time.monotonic() - started
        status, answer = await site.ask("POST", "/api/sessions/{}/git/refresh".format(site.here))
        assert status == 200 and answer["git"]["repo"] is True, answer
    await back(drive)

    # a task that is being made there keeps itself alone
    def stuck(root, destination, committed=False):
        if str(root) == str(PROJECT):
            drive.die()
        return real_copy(root, destination, committed)
    with patch.object(session_tasks, "_copy_project", stuck):
        made = asyncio.ensure_future(site.ask("POST", "/api/sessions/{}/tasks".format(site.spare),
                                              json=site.task_body(site.spare)))
        await until(lambda: drive.dead)
        await asyncio.sleep(0.3)
        status, answer = await site.ask("POST", "/api/sessions/{}/tasks".format(site.here),
                                        json=site.task_body(site.here))
        assert status == 200, answer
        assert ("assistant", "worked") in await site.turn(site.here)
        assert not made.done()
        await back(drive)
        status, answer = await asyncio.wait_for(made, 20)
        assert status in (200, 409), answer

    # a terminal whose process waits for it is closed all the same
    status, answer = await site.ask("POST", "/api/terminal/instances", json={
        "cwd": str(PROJECT), "command": "{} -c 'import os, time; time.sleep(0.5); os.stat(\"{}\")'".format(
            sys.executable, PROJECT)})
    assert status == 201, answer
    drive.die()
    await asyncio.sleep(1.0)
    status, answer = await asyncio.wait_for(
        site.ask("DELETE", "/api/terminal/instances/" + answer["terminal"]["id"]), 9)
    assert status == 200, answer
    await back(drive)
    assert not watch.stood, watch.stood
    assert session_directories.inspect is real_names
    print("  {}: a turn, an engine, a Git look, a task and a terminal that were under way keep "
          "themselves alone".format(site.kind))


async def scheduled(site, drive, watch):
    """The controller's own jobs: backups onto the drive and runs in it."""
    for path in (DRIVE / "backups").glob("*"):
        path.unlink()
    status, _ = await site.ask("PUT", "/api/snapshot/backups", json=site.backups())
    assert status == 200
    scheduler = site.app["puppy_backups"]
    await site.ask("POST", "/api/snapshot/backups/run")
    await until(lambda: backup_schedule._load()["files"] and not scheduler.running, 20)
    # it hangs: a backup is not even begun, the list says what is not there
    await dies(drive)
    await site.ask("POST", "/api/snapshot/backups/run")
    await until(lambda: len(backup_schedule._load()["history"]) == 2, 20)
    line = backup_schedule._load()["history"][-1]
    assert line["tone"] == "bad" and GONE in line["message"], line
    status, answer = await site.ask("GET", "/api/snapshot/backups")
    assert status == 200 and [item["available"] for item in answer["files"]] == [False], answer
    status, answer = await site.ask("DELETE", "/api/snapshot/saved/" + answer["files"][0]["id"])
    assert status == 409 and GONE in answer["error"], answer
    await back(drive)
    # it stops answering halfway through the copy: given up, and cleaned up
    # once the drive answers
    real = backup_schedule._copy_archive

    def halfway(source, output):
        output.write(b"x" * 65536)
        output.flush()
        drive.die()
        real(source, output)
    with patch.dict(backup_schedule.ARCHIVE_WRITERS, {"none": halfway}), \
            patch.object(backup_schedule, "COPY_PATIENCE", 2 * TOUCH):
        await site.ask("POST", "/api/snapshot/backups/run")
        await until(lambda: len(backup_schedule._load()["history"]) == 3, 20)
    line = backup_schedule._load()["history"][-1]
    assert line["tone"] == "bad" and GONE in line["message"] and not scheduler.running, line
    await back(drive)
    await until(lambda: len(list((DRIVE / "backups").glob("*"))) == 1 and
                not list((DRIVE / "backups").glob(".*")), what="a half-made copy was left behind")
    # a run of a schedule whose project is there
    body = {"name": "Nightly", "prompt": "Check", "enabled": False, "backend": 0, "engine": "claude", "model": "",
            "effort": "", "permission_mode": get_driver("claude").default_permission(),
            "workspace_kind": "directory", "cwd": str(PROJECT), "timing": {"kind": "daily", "times": ["06:00"]},
            "until": "", "max_runs": 0, "late": "run", "limit_minutes": 60, "notify": "never",
            "alert_command": False, "keep": 10}
    status, answer = await site.ask("POST", "/api/schedules", json=body)
    assert status == 200, answer
    ident = answer["schedule"]["id"]
    drive.die()
    status, answer = await site.ask("POST", "/api/schedules/{}/run".format(ident))
    assert status == 202, answer

    def runs():
        return schedules._load_history(ident)["runs"]
    await until(lambda: runs() and runs()[-1]["state"] not in ("starting", "running", "waiting"), 20)
    assert GONE in json.dumps(runs()[-1]), runs()[-1]
    await back(drive)
    # stopping Puppy does not wait for a copy that hangs; the next start makes it
    with patch.dict(backup_schedule.ARCHIVE_WRITERS, {"none": halfway}):
        await site.ask("POST", "/api/snapshot/backups/run")
        await until(lambda: drive.dead, 20)
        await asyncio.wait_for(scheduler.stop(site.app), 10 * TOUCH)
    assert backup_schedule._load()["pending"] is not None
    await back(drive)
    await until(lambda: not list((DRIVE / "backups").glob(".*")), what="a half-made copy was left behind")
    assert not watch.stood, watch.stood
    print("  full: backups onto it and a schedule's run in it fail with the reason, and leave nothing behind")


async def starts_without_it(kind, drive):
    """Puppy started while the drive does not answer."""
    sid = db.create_session("There", "claude", str(PROJECT), "", "", "", "ask")
    workspace_sync.create_lease(str(DRIVE / "leased"), True)
    if kind == "full":
        db.meta_set("backup_schedule", dict(backup_schedule._new_state(), settings={
            "enabled": False, "at": "03:00", "directory": str(DRIVE / "backups"), "keep": 3}))
    drive.die()
    started = time.monotonic()
    watch = Watch(asyncio.get_running_loop(), drive)
    async with Site(kind) as site:
        assert time.monotonic() - started < 3.0, time.monotonic() - started
        status, listed = await site.ask("GET", "/api/sessions")
        assert status == 200 and listed["sessions"][0]["cwd_available"] is None, "nobody knows yet"
        await until(lambda: session_directories.record(str(PROJECT)) is False)
        assert (await site.ask("GET", "/api/sessions/{}".format(sid)))[0] == 200
        drive.revive()      # it is looked at again without being asked to
        await until(lambda: session_directories.record(str(PROJECT)) is True)
        assert session_directories.notes(str(PROJECT)) == ["AGENTS.md"]
        watch.over = True
        assert not watch.stood, watch.stood
    print("  {}: starts while it hangs".format(kind))


def restarted(drive):
    """The whole process, as a service manager sees it: it answers while the
    drive hangs, and is gone when it is told to stop."""
    data = ROOT / "service"
    data.mkdir()
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    # no engine, no account and no release to look for: git and a shell alone
    tools = ROOT / "tools"
    for folder in (tools, ROOT / "home"):
        folder.mkdir()
    for name in ("git", "sh"):
        (tools / name).symlink_to(shutil.which(name))
    environment = {"PUPPY_DATA": str(data), "PATH": str(tools), "HOME": str(ROOT / "home"), "PYTHONPATH": str(BASE)}
    subprocess.run([sys.executable, "-c", (
        "from puppy import config, db\n"
        "config.ensure_dirs(); config.load(); db.connect()\n"
        "config.set_value('web.port', {}); config.set_value('web.host', '127.0.0.1')\n"
        "config.set_value('engines.usage_refresh_minutes', 0)\n"
        "db.create_session('There', 'claude', {!r}, '', '', '', 'ask')\n").format(port, str(PROJECT))],
        cwd=str(ROOT), env=environment, check=True)
    token = json.loads((data / "config.json").read_text())["auth"]["api_token"]
    code = ("from puppy import cli_releases\n"
            "async def none(*_args, **_options):\n"
            "    return 10 ** 9\n"
            "cli_releases.refresh_if_due = none\n"
            "from puppy.main import main\n"
            "main()\n")

    def answered(timeout=30.0):
        request = urllib.request.Request("http://127.0.0.1:{}/api/sessions".format(port),
                                         headers={"X-Puppy-Token": token})
        deadline = time.monotonic() + timeout
        while True:
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return json.loads(response.read())["sessions"][0]
            except OSError:
                assert time.monotonic() < deadline, "the service does not answer"
                time.sleep(0.1)

    for hangs_first in (False, True):
        if hangs_first:
            drive.die()
        service = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            row = answered()
            if not hangs_first:
                # whoever is inside when the drive dies is kept by it: not at
                # this moment, when everything there is being looked at
                while row["cwd_available"] is not True or not (row["git"] or {}).get("repo"):
                    assert time.monotonic() < deadline, row
                    time.sleep(0.2)
                    row = answered()
                time.sleep(1.0)
                drive.die()
            while answered()["cwd_available"] is not False:     # the real ten seconds
                assert time.monotonic() < deadline, "nobody noticed"
                time.sleep(0.5)
            service.send_signal(signal.SIGTERM)
            started = time.monotonic()
            service.wait(30)
            assert time.monotonic() - started < 10.0 and service.returncode == 0, \
                (time.monotonic() - started, service.returncode)
        finally:
            if service.poll() is None:
                os.killpg(service.pid, signal.SIGKILL)
            drive.revive()
            service.wait(10)
    print("  a service: answers while it hangs, and is stopped and started meanwhile")


def stays_behind():
    """Work that cannot be called back does not keep the process: the pools'
    threads are waited for by Python itself, and only for so long by Puppy."""
    code = (
        "import asyncio, os, signal, sys, threading, time\n"
        "from aiohttp import web\n"
        "from puppy import workers\n"
        "workers.EXIT_SECONDS = 1.0\n"
        "async def begin(app):\n"
        "    workers.jobs.run(time.sleep, 60)\n"
        "    if sys.argv[1] == 'task':\n"
        "        async def holds():\n"
        "            while True:\n"
        "                try:\n"
        "                    await asyncio.sleep(60)\n"
        "                except asyncio.CancelledError:\n"
        "                    pass\n"
        "        app['held'] = asyncio.ensure_future(holds())\n"
        "    threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()\n"
        "app = web.Application()\n"
        "app.on_startup.append(begin)\n"
        "workers.run_app(app, grace=0, host='127.0.0.1', port=0, print=None)\n")
    for what in ("thread", "task"):
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-c", code, what], cwd=str(BASE), timeout=30,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert result.returncode == 0 and 1.0 < time.monotonic() - started < 8.0, \
            (what, result.returncode, time.monotonic() - started, result.stdout[-400:])
    print("the process ends although a worker thread or a task of it does not")


@contextlib.contextmanager
def fixture():
    script = ROOT / "fixture.py"
    script.write_text(FIXTURE)
    os.environ["DEAD_MOUNT_PROJECT"] = str(PROJECT)
    driver = get_driver("claude")

    async def no_refresh(*_args, **_options):
        return None
    with contextlib.ExitStack() as stack:
        for target, name, value in (
                (driver, "build_cmd", lambda *a, **k: [sys.executable, str(script)]),
                (driver, "resolved_binary", lambda: sys.executable),
                (driver, "refresh_model_options", no_refresh),
                (runner, "STEERING_ACK_GRACE", 0.1), (runner, "SIDE_QUESTION_GRACE", 0.1),
                # looked at when asked to, never in between: whoever is inside
                # when the drive dies would be what the test looks for
                (session_directories, "CHECK_SECONDS", 3600), (session_directories, "PROMPT_SECONDS", 0.2),
                (workers, "LEAVE_SECONDS", 1.0),
                (places, "TOUCH_SECONDS", TOUCH)):
            stack.enter_context(patch.object(target, name, value))
        yield


async def everything():
    config.ensure_dirs()
    config.load()
    config.set_value("sessions.shutdown_grace", 1)
    db.connect()
    LOCAL.mkdir()
    make_project(LOCAL / "repository")
    for name in os.listdir(str(LOCAL / "repository")):
        shutil.move(str(LOCAL / "repository" / name), str(LOCAL / name))
    (LOCAL / "repository").rmdir()
    LINKED.mkdir()
    (LINKED / "CLAUDE.md").symlink_to(PROJECT / "AGENTS.md")
    (LINKED / ".git").write_text("gitdir: {}\n".format(PROJECT / ".git"))

    def fresh(top):
        for old in (top, ROOT / "moved", ROOT / "carry"):
            shutil.rmtree(str(old), ignore_errors=True)
        (ROOT / "carry").mkdir()
        make_project(top / "project")
        make_project(top / "carried")
        (top / "leased").mkdir()
        (top / "leased" / "kept.txt").write_text("kept\n")
        (top / "backups").mkdir()

    print("without any such drive:")
    guard = Guard()
    with patch.object(place_scout, "read_table", somebody_elses):
        for kind in ("full", "backend"):
            fresh(DRIVE)
            places._table_at = None
            await off_the_loop(guard, kind)
    shutil.rmtree(str(DRIVE))
    places._table_at = None
    stays_behind()
    if not FUSE:
        print("SKIP: a drive that really hangs needs root and /dev/fuse")
        return
    print("with a drive that hangs:")
    for kind in ("full", "backend"):
        fresh(BACK)
        drive = deadfs.Mount(BACK, DRIVE)
        try:
            await asyncio.sleep(places.TABLE_SECONDS)
            await hanging(kind, drive)
            await starts_without_it(kind, drive)
            if kind == "full":
                await asyncio.get_running_loop().run_in_executor(None, restarted, drive)
        finally:
            drive.close()


def main():
    logging.disable(logging.CRITICAL)
    try:
        with fixture():
            asyncio.run(everything())
        print("dead mount tests passed")
    finally:
        shutil.rmtree(str(ROOT), ignore_errors=True)


if __name__ == "__main__":
    main()
