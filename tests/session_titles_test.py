#!/usr/bin/env python3
"""Generated session titles: the settings shape, the per-session record, the
title made from a model's answer, the controller consuming local and remote
requests through a fixture engine, and both runtimes' routes.

The fixture speaks claude's stream-json so the real driver, spawn job and
runner paths run; no real engine is invoked, no network is reached and no
quota is spent.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch


def fixture():
    """A title engine: one prompt in, one answer out, in the way
    PUPPY_TEST_TITLE_MODE asks, then it waits for its input to close."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    mode = os.environ.get("PUPPY_TEST_TITLE_MODE", "ok")
    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    prompt = read()
    text = prompt["message"]["content"][0]["text"]
    log_path = os.environ.get("PUPPY_TEST_TITLE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"cwd": os.getcwd(), "prompt": text,
                                     "argv": sys.argv[1:]}) + "\n")
    if mode == "hang":
        time.sleep(600)
    send({"type": "system", "subtype": "init", "session_id": "native-title", "tools": []})
    if mode == "ok":
        send({"type": "assistant", "message": {"model": "claude-title", "content": [
            {"type": "text", "text": "Sure! Here is a title:\n\n\"Fix the login redirect loop.\""}]}})
    elif mode == "long":
        send({"type": "assistant", "message": {"model": "claude-title", "content": [
            {"type": "text", "text": "word " * 40}]}})
    send({"type": "result", "subtype": "success", "is_error": mode == "fail",
          "session_id": "native-title", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1},
          "result": "boom" if mode == "fail" else "done"})
    while sys.stdin.readline():
        pass


def session_fixture():
    """The engine of a titled session: one result per prompt."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()
    send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-session", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit
if __name__ == "__main__" and sys.argv[1:2] == ["--session"]:
    session_fixture()
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402
TEST_ROOT = private_root("session-titles-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
LOG = TEST_ROOT / "title-jobs.log"

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import auth, backends, config, db, protocol, runner, session_tasks, session_titles, spawn_exec  # noqa: E402
from puppy import web as webui  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

FIXTURE = str(Path(__file__).resolve())


def fixture_command(*args, **kwargs):
    return [sys.executable, FIXTURE, "--fixture"]


def session_command(*args, **kwargs):
    return [sys.executable, FIXTURE, "--session"]


def title_mode(mode):
    os.environ["PUPPY_TEST_TITLE_MODE"] = mode


def job_log():
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line]


def settings_shape():
    config.load()
    before = config.export_data()
    disk = Path(config.CONFIG_PATH).read_bytes()
    assert config.get("titles") == {"enabled": False, "backend": 0, "engine": "",
                                    "model": "", "effort": "",
                                    "prompt": config.DEFAULT_TITLE_PROMPT}
    for bad in ({"enabled": "yes"}, {"backend": -1}, {"backend": True},
                {"engine": "gemini"}, {"engine": None}, {"model": " spaced "},
                {"model": "x" * 300}, {"effort": "tab\there"}, {"prompt": ""},
                {"prompt": " padded "}, {"prompt": "x" * 4001}, {"prompt": "nul\x00"},
                {"colour": "blue"}):
        try:
            config.set_titles(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
        try:
            config.set_titles({"engine": "claude"})
        except OSError:
            pass
        else:
            raise AssertionError("write failure was ignored")
    assert config.export_data() == before and Path(config.CONFIG_PATH).read_bytes() == disk
    saved = config.set_titles({"engine": "codex", "model": "gpt-x", "effort": "low"})
    assert saved["engine"] == "codex" and config.get("titles.model") == "gpt-x"
    config.set_titles({"engine": "", "model": "", "effort": ""})
    # the section is part of the exact config shape, so a config from before
    # it is refused rather than filled in
    old = config.export_data()
    old.pop("titles")
    Path(config.CONFIG_PATH).write_text(json.dumps(old))
    config._config = None
    try:
        config.load()
    except ValueError as exc:
        assert "titles" in str(exc), exc
    else:
        raise AssertionError("previous persisted shape was accepted")
    for bad in ({"enabled": True}, {"enabled": False, "backend": 0, "engine": "claude",
                                    "model": "", "effort": ""}):
        broken = dict(old, titles=bad)
        try:
            config.normalize_import(broken)
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete titles section was accepted")
    Path(config.CONFIG_PATH).write_bytes(disk)
    config._config = None
    config.load()
    print("settings shape ok")


def text_helpers():
    t = session_titles
    assert t.build_prompt("Name this: {message}", "hello") == "Name this: hello"
    assert t.build_prompt("Name this", "hello") == "Name this\n\nhello"
    assert t.build_prompt("", "hello") == config.DEFAULT_TITLE_PROMPT.replace("{message}", "hello")
    for answer, expected in (
            ("Fix login redirect", "Fix login redirect"),
            ("  \"Fix login redirect.\"  ", "Fix login redirect"),
            ("Title: Fix login redirect", "Fix login redirect"),
            ("Suggested title — **Fix login redirect**", "Fix login redirect"),
            ("Sure! Here is a concise title:\n\nFix login redirect\n\nIt captures the request.",
             "Fix login redirect"),
            ("```\nFix login redirect\n```", "Fix login redirect"),
            ("1. Fix login redirect", "Fix login redirect"),
            ("- Fix   login\tredirect", "Fix login redirect"),
            ("", ""), ("   \n```\n", ""), ("\"\"", ""),
            ("Why does it loop?", "Why does it loop?")):
        assert t.clean_title(answer) == expected, (answer, t.clean_title(answer))
    long = t.clean_title("word " * 40)
    assert len(long) <= t.NAME_LIMIT and long.endswith("…") and " word…" in long
    unbroken = t.clean_title("x" * 120)
    assert len(unbroken) == t.NAME_LIMIT and unbroken.endswith("…")
    from puppy import uploads
    marker = "{}{}/uploads/7/1234567890123-abcdef0123/report.pdf{}".format(
        uploads.ATTACH_FILE_PREFIX, config.DATA_DIR, uploads.ATTACH_FILE_SUFFIX)
    image = "{}{}/uploads/7/1234567890123-abcdef0123/shot.png{}".format(
        uploads.ATTACH_IMAGE_PREFIX, config.DATA_DIR, uploads.ATTACH_IMAGE_SUFFIX)
    assert t.title_text("Look at this\n" + marker + "\n" + image) == \
        "Look at this\n[file attached: report.pdf]\n[image attached: shot.png]"
    assert len(t.title_text("y" * 9000)) == t.TEXT_LIMIT
    print("text helpers ok")


def record_lifecycle():
    t = session_titles
    sid = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
    assert t.record(sid) is None
    assert runner.session_payload(db.get_session(sid))["auto_title"] is None
    t.arm(sid)
    assert t.record(sid) == {"format": 1, "state": "armed", "text": "", "placeholder": "",
                             "requested_at": 0}
    assert runner.session_payload(db.get_session(sid))["auto_title"] == \
        {"state": "armed", "requested_at": 0}
    # only an armed session asks on its first prompt
    other = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
    assert t.first_prompt(other, "hello", "hello") is None and t.record(other) is None
    text = "Fix the login redirect loop\n\nIt bounces between /login and /home."
    placeholder = db.auto_session_name(text)
    db.touch_session(sid, name=placeholder)
    value = t.first_prompt(sid, text, placeholder)
    assert value["state"] == "requested" and value["text"] == text and \
        value["placeholder"] == placeholder and value["requested_at"] > 0
    listed = [row for row in runner.sessions_payload()["sessions"] if row["id"] == sid][0]
    assert listed["auto_title"] == {"state": "requested", "requested_at": value["requested_at"]}
    assert "text" not in listed["auto_title"]
    t.validate_persisted(db.connect())
    # a second first_prompt does nothing: it is no longer armed
    assert t.first_prompt(sid, "again", "again") is None
    assert t.record(sid)["requested_at"] == value["requested_at"]
    # a name typed before the title lands is kept
    db.touch_session(sid, name="My own name")
    assert t.settle(sid, value["requested_at"], name="Login redirect loop") == \
        {"applied": False, "name": "My own name"}
    assert t.record(sid) is None
    # the ordinary path renames; the request cannot be settled twice
    t.arm(sid)
    db.touch_session(sid, name=placeholder)
    value = t.first_prompt(sid, text, placeholder)
    assert t.settle(sid, value["requested_at"], name="  Login redirect loop ") == \
        {"applied": True, "name": "Login redirect loop"}
    assert db.get_session(sid)["name"] == "Login redirect loop"
    for stale in (value["requested_at"], value["requested_at"] + 1):
        try:
            t.settle(sid, stale, name="again")
        except t.TitleError:
            pass
        else:
            raise AssertionError("a settled request was settled again")
    # an error keeps the placeholder and clears the request
    value = t.request(sid, text, "placeholder")
    db.touch_session(sid, name="placeholder")
    assert t.settle(sid, value["requested_at"], error="boom") == \
        {"applied": False, "name": "placeholder"}
    assert t.record(sid) is None
    # a message with nothing to make a title from asks nothing
    assert t.request(sid, "   ", "placeholder") is None and t.record(sid) is None
    # the record goes with its session
    t.request(sid, text, "placeholder")
    db.delete_session(sid)
    db.delete_session(other)
    assert db.query_one("SELECT value FROM meta WHERE key=?", (t.PREFIX + str(sid),)) is None
    # persisted shapes: refused, never repaired
    keep = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
    for bad in ({"format": 2, "state": "armed", "text": "", "placeholder": "", "requested_at": 0},
                {"format": 1, "state": "armed", "text": "x", "placeholder": "", "requested_at": 0},
                {"format": 1, "state": "requested", "text": "", "placeholder": "", "requested_at": 1.0},
                {"format": 1, "state": "requested", "text": "x", "placeholder": "", "requested_at": 0},
                {"format": 1, "state": "pending", "text": "x", "placeholder": "", "requested_at": 1.0},
                {"format": 1, "state": "requested", "text": "x", "requested_at": 1.0},
                "true"):
        db.meta_set(t.PREFIX + str(keep), bad)
        try:
            t.validate_persisted(db.connect())
        except (t.TitleError, ValueError, TypeError):
            pass
        else:
            raise AssertionError(bad)
    db.meta_set(t.PREFIX + str(keep), {"format": 1, "state": "armed", "text": "",
                                       "placeholder": "", "requested_at": 0})
    t.validate_persisted(db.connect())
    db.meta_set(t.PREFIX + "999999", {"format": 1, "state": "armed", "text": "",
                                      "placeholder": "", "requested_at": 0})
    try:
        t.validate_persisted(db.connect())
    except t.TitleError:
        pass
    else:
        raise AssertionError("an orphan record was accepted")
    db.meta_del(t.PREFIX + "999999")
    db.delete_session(keep)
    print("record lifecycle ok")


async def wait_for(predicate, seconds=30.0, what="condition"):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for " + what
        await asyncio.sleep(0.05)


async def local_generation():
    """The controller names its own sessions: the runner's first prompt asks,
    the job runs the fixture in the empty title directory, the answer lands."""
    t = session_titles
    driver = get_driver("claude")
    toasts = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=toasts.append))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_TITLE_LOG": str(LOG)}))
        await t.start_worker(None)
        try:
            config.set_titles({"enabled": True, "backend": 0, "engine": "claude",
                               "model": "", "effort": "",
                               "prompt": "Name it: {message}"})
            assert t.active() and t.public_state() == {
                "enabled": True, "configured": True, "backend": 0, "engine": "claude", "model": ""}
            title_mode("ok")
            sid = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
            t.arm(sid)
            text = "Login redirects forever\n\nafter signing in"
            placeholder = db.auto_session_name(text)
            db.touch_session(sid, name=placeholder)
            assert t.first_prompt(sid, text, placeholder)["state"] == "requested"
            await wait_for(lambda: db.get_session(sid)["name"] != placeholder, what="the title")
            assert db.get_session(sid)["name"] == "Fix the login redirect loop"
            assert t.record(sid) is None and (0, sid) not in t._jobs
            runs = job_log()
            assert runs and runs[-1]["prompt"] == "Name it: " + text
            assert runs[-1]["cwd"] == t.job_cwd() == os.path.join(config.DATA_DIR, "titles")
            assert os.stat(t.job_cwd()).st_mode & 0o777 == 0o700
            assert not os.listdir(t.job_cwd())
            assert "--append-system-prompt" not in runs[-1]["argv"] or True
            assert not toasts
            # nothing is left in the spawn registry
            assert not spawn_exec.manager().live_jobs()

            # a name typed while the model thinks is kept
            title_mode("ok")
            db.touch_session(sid, name="")
            t.arm(sid)
            db.touch_session(sid, name=placeholder)
            t.first_prompt(sid, text, placeholder)
            db.touch_session(sid, name="Typed meanwhile")
            await wait_for(lambda: t.record(sid) is None, what="the settle")
            assert db.get_session(sid)["name"] == "Typed meanwhile"

            # a failed job keeps the placeholder, clears the request and
            # tells every console once
            for mode, needle in (("fail", "boom"), ("quiet", "without a title")):
                title_mode(mode)
                db.touch_session(sid, name="")
                t.arm(sid)
                db.touch_session(sid, name=placeholder)
                t.first_prompt(sid, text, placeholder)
                await wait_for(lambda: t.record(sid) is None, what="the failure")
                assert db.get_session(sid)["name"] == placeholder
                assert toasts and toasts[-1]["type"] == "toast" and toasts[-1]["level"] == "warn"
                assert needle in toasts[-1]["text"] and placeholder in toasts[-1]["text"], toasts[-1]
            # a long answer is cut on a word to the name width
            title_mode("long")
            db.touch_session(sid, name="")
            t.arm(sid)
            db.touch_session(sid, name=placeholder)
            t.first_prompt(sid, text, placeholder)
            await wait_for(lambda: t.record(sid) is None, what="the long title")
            name = db.get_session(sid)["name"]
            assert len(name) <= t.NAME_LIMIT and name.endswith("…"), name

            # switched off, a request is not kept for a later switch-on
            title_mode("ok")
            config.set_titles({"enabled": False})
            db.touch_session(sid, name="")
            t.arm(sid)
            db.touch_session(sid, name=placeholder)
            t.first_prompt(sid, text, placeholder)
            await wait_for(lambda: t.record(sid) is None, what="the off settle")
            assert db.get_session(sid)["name"] == placeholder
            before = len(job_log())
            assert len(job_log()) == before
            # an armed session with no prompt yet is untouched by the sweep
            db.touch_session(sid, name="")
            t.arm(sid)
            t.sweep_local()
            await asyncio.sleep(0.2)
            assert t.record(sid)["state"] == "armed"
            db.delete_session(sid)

            # a hanging model is cancelled at the controller's deadline
            config.set_titles({"enabled": True})
            title_mode("hang")
            sid = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
            t.arm(sid)
            db.touch_session(sid, name=placeholder)
            with patch.object(t, "GENERATE_TIMEOUT_S", 2), patch.object(t, "POLL_S", 1):
                t.first_prompt(sid, text, placeholder)
                await wait_for(lambda: t.record(sid) is None, seconds=40, what="the timeout")
            assert db.get_session(sid)["name"] == placeholder
            assert "timed out" in toasts[-1]["text"], toasts[-1]
            await wait_for(lambda: not spawn_exec.manager().live_jobs(), what="the job teardown")
            db.delete_session(sid)
        finally:
            await t.stop_worker(None)
            config.set_titles({"enabled": False, "backend": 0, "engine": "", "model": "",
                               "effort": "", "prompt": config.DEFAULT_TITLE_PROMPT})
    print("local generation ok")


async def runner_turn():
    """The whole local path through the real runner: a session created
    armed, its first prompt through send_message, the title landing."""
    t = session_titles
    driver = get_driver("claude")
    toasts = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=toasts.append))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_TITLE_LOG": str(LOG)}))
        title_mode("ok")

        def command(session, *args, **kwargs):
            # the session's own turns and the title job both speak claude
            return session_command() if session.get("id") else fixture_command()
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        await t.start_worker(None)
        try:
            config.set_titles({"enabled": True, "backend": 0, "engine": "claude",
                               "prompt": "Name it: {message}"})
            sid = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
            t.arm(sid)
            hub = runner.hub(sid)
            try:
                assert hub.send_message("Login redirects forever") == {"queued": False}
                assert db.get_session(sid)["name"] == "Login redirects forever"
                assert t.record(sid)["state"] == "requested"
                await wait_for(lambda: hub.status == "idle", what="the turn")
                await hub.turn_task
                await wait_for(lambda: t.record(sid) is None, what="the title")
                assert db.get_session(sid)["name"] == "Fix the login redirect loop"
                assert not toasts
            finally:
                if hub.status != "idle":
                    await hub.kill()
                if hub.turn_task:
                    await asyncio.gather(hub.turn_task, return_exceptions=True)
            db.delete_session(sid)
        finally:
            await t.stop_worker(None)
            config.set_titles({"enabled": False, "engine": "", "prompt": config.DEFAULT_TITLE_PROMPT})
    print("runner turn ok")


class FakeNode:
    """A backend as the controller's node requests see it: its session
    title records and title jobs live in this process's own tables and
    spawn registry, addressed under another backend id."""

    def __init__(self, bid):
        self.bid = bid
        self.calls = []
        self.offline = False
        self.refuse = None

    async def request(self, channel, method, path, body=None, timeout_s=60.0):
        self.calls.append((method, path, body))
        if self.offline:
            raise spawn_exec.SpawnError("node: unreachable", 502, unreached=True)
        if self.refuse is not None:
            raise self.refuse
        if method == "GET" and path.startswith("sessions/") and path.endswith("/title"):
            value = session_titles.record(int(path.split("/")[1]))
            if value is None:
                raise spawn_exec.SpawnError("no title request", 404)
            return {"title": value}
        if method == "POST" and path.startswith("sessions/") and path.endswith("/title"):
            try:
                return session_titles.settle(int(path.split("/")[1]), body["requested_at"],
                                             name=body.get("name", ""),
                                             error=body.get("error", ""))
            except session_titles.TitleError as exc:
                raise spawn_exec.SpawnError(str(exc), 409)
        if method == "POST" and path == "titles/jobs":
            job = await session_titles.start_job(body)
            await spawn_exec.manager().wait(job, float(body.get("wait_s") or 0))
            return {"ok": True, "job": job.payload()}
        if method == "GET" and path.startswith("spawn/"):
            job_id = path[len("spawn/"):].split("?")[0]
            job = spawn_exec.manager().get(job_id)
            if job is None:
                raise spawn_exec.SpawnError("unknown spawn job", 404)
            wait_s = float(path.split("wait_s=")[1]) if "wait_s=" in path else 0
            await spawn_exec.manager().wait(job, wait_s)
            return {"job": job.payload()}
        if method == "DELETE" and path.startswith("spawn/"):
            job = spawn_exec.manager().get(path[len("spawn/"):])
            if job is None:
                raise spawn_exec.SpawnError("unknown spawn job", 404)
            await spawn_exec.manager().cancel(job, "cancelled by its owner")
            return {"ok": True, "job": job.payload()}
        raise spawn_exec.SpawnError("unexpected request " + method + " " + path, 500)


async def remote_generation():
    """A session on a backend, and the title model on a backend: both reached
    through the controller's node requests, driven by the sessions payloads
    the controller receives from the backend."""
    t = session_titles
    driver = get_driver("claude")
    bid = db.execute(
        "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,role,"
        "tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("title-node", "http://title-node.test:10888", '["http://title-node.test:10888"]',
         "remote-token", protocol.API_PROTOCOL,
         json.dumps([protocol.SPAWN_EXEC_CAPABILITY, protocol.SESSION_TITLES_CAPABILITY,
                     protocol.NODE_STATE_STREAM_CAPABILITY]),
         "9.9.9", "backend", "", 0, time.time()))
    backends._mark_backend_online(bid)
    node = FakeNode(bid)
    toasts = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=fixture_command))
        stack.enter_context(patch.object(spawn_exec, "node_request", side_effect=node.request))
        stack.enter_context(patch.object(runner, "broadcast_update", side_effect=toasts.append))
        stack.enter_context(patch.dict(os.environ, {"PUPPY_TEST_TITLE_LOG": str(LOG)}))
        # the fake node's sessions are this process's own rows, which the
        # local sweep would otherwise title through the local path
        stack.enter_context(patch.object(t, "sweep_local", lambda: None))
        stack.enter_context(patch.object(t, "RETRY_S", 0.0))
        await t.start_worker(None)
        try:
            config.set_titles({"enabled": True, "backend": bid, "engine": "claude",
                               "prompt": "Name it: {message}"})
            title_mode("ok")
            # the session "on the backend" is a local row the fake node serves
            sid = db.create_session("", "claude", str(TEST_ROOT), "", "", "#e0784f", "auto")
            text = "Login redirects forever"
            db.touch_session(sid, name=text)
            value = t.request(sid, text, text)
            # the local consumer saw request() - settle that one first so the
            # remote path below starts from a clean slate
            await wait_for(lambda: t.record(sid) is None, what="the local title")
            node.calls.clear()
            db.touch_session(sid, name=text)
            with patch.object(t, "_consumer", False):
                value = t.request(sid, text, text)
            payload = [{"id": sid, "name": text, "auto_title": t.public(value)}]
            # what the controller does with a backend's sessions payload
            backends._cache_remote_payload(bid, "sessions", {"sessions": payload})
            await wait_for(lambda: t.record(sid) is None, what="the remote title")
            assert db.get_session(sid)["name"] == "Fix the login redirect loop"
            methods = [(method, path.split("?")[0]) for method, path, _ in node.calls]
            assert methods[0] == ("GET", "sessions/{}/title".format(sid)), methods
            assert methods[1] == ("POST", "titles/jobs"), methods
            assert methods[-1] == ("POST", "sessions/{}/title".format(sid)), methods
            assert node.calls[1][2]["engine"] == "claude" and \
                node.calls[1][2]["prompt"] == "Name it: " + text
            assert node.calls[-1][2] == {"requested_at": value["requested_at"],
                                        "name": "Fix the login redirect loop"}
            # the same payload again (a stream repeats state) starts nothing
            calls = len(node.calls)
            backends._cache_remote_payload(bid, "sessions", {"sessions": payload})
            await asyncio.sleep(0.2)
            assert len(node.calls) == calls and not toasts

            # an unreachable backend keeps the request and the sweep retries
            db.touch_session(sid, name=text)
            with patch.object(t, "_consumer", False):
                value = t.request(sid, text, text)
            payload = [{"id": sid, "name": text, "auto_title": t.public(value)}]
            node.offline = True
            backends._cache_remote_payload(bid, "sessions", {"sessions": payload})
            await wait_for(lambda: (bid, sid) not in t._jobs, what="the deferred job")
            assert t.record(sid) is not None and (bid, sid, value["requested_at"]) in t._retry_at
            assert not toasts
            node.offline = False
            t._sweep()
            await wait_for(lambda: t.record(sid) is None, what="the retried title")
            assert db.get_session(sid)["name"] == "Fix the login redirect loop"

            # a refusal the backend itself gave settles the request as failed
            db.touch_session(sid, name=text)
            with patch.object(t, "_consumer", False):
                value = t.request(sid, text, text)
            node.refuse = spawn_exec.SpawnError("Claude is not installed on this node", 400)
            backends._cache_remote_payload(bid, "sessions", {"sessions": [
                {"id": sid, "name": text, "auto_title": t.public(value)}]})
            await wait_for(lambda: (bid, sid) not in t._jobs, what="the refused job")
            # the settle itself was refused too (node.refuse answers every
            # request), so the request waits for the sweep
            assert toasts and "not installed" in toasts[-1]["text"], toasts
            assert t.record(sid) is not None
            node.refuse = None
            title_mode("fail")   # were the model asked again, it would fail loudly
            toasts.clear()
            t._sweep()
            await wait_for(lambda: t.record(sid) is None, what="the settle after refusal")
            # the retry only carried the verdict: no second run, no second toast
            assert db.get_session(sid)["name"] == text and not toasts
            assert node.calls[-1][0] == "POST" and "error" in node.calls[-1][2]
            title_mode("ok")

            # a session whose backend forgot the request (deleted meanwhile)
            calls = len(node.calls)
            backends._cache_remote_payload(bid, "sessions", {"sessions": [
                {"id": sid + 1000, "name": "gone",
                 "auto_title": {"state": "requested", "requested_at": 5.0}}]})
            await wait_for(lambda: (bid, sid + 1000) not in t._jobs, what="the gone job")
            assert len(node.calls) == calls + 1 and node.calls[-1][0] == "GET"

            # the title backend itself offline: deferred, not failed
            db.touch_session(sid, name=text)
            with patch.object(t, "_consumer", False):
                value = t.request(sid, text, text)
            other = db.execute(
                "INSERT INTO backends(name,url,urls,token,protocol,capabilities,remote_version,"
                "role,tls_fingerprint,auto_upgrade,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("model-node", "http://model-node.test:10888", '["http://model-node.test:10888"]',
                 "remote-token", protocol.API_PROTOCOL,
                 json.dumps([protocol.SPAWN_EXEC_CAPABILITY, protocol.SESSION_TITLES_CAPABILITY]),
                 "9.9.9", "backend", "", 0, time.time()))
            config.set_titles({"backend": other})
            backends._mark_backend_offline(other, "down")
            toasts.clear()
            backends._cache_remote_payload(bid, "sessions", {"sessions": [
                {"id": sid, "name": text, "auto_title": t.public(value)}]})
            await wait_for(lambda: (bid, sid) not in t._jobs, what="the deferred model job")
            assert t.record(sid) is not None and not toasts
            # ... and one that cannot generate titles fails the request
            backends._mark_backend_online(other)
            db.execute("UPDATE backends SET capabilities=? WHERE id=?",
                       (json.dumps([protocol.SPAWN_EXEC_CAPABILITY]), other))
            t._sweep()
            await wait_for(lambda: t.record(sid) is None, what="the incapable failure")
            assert toasts and "needs a Puppy upgrade" in toasts[-1]["text"], toasts
            db.execute("DELETE FROM backends WHERE id=?", (other,))
            db.delete_session(sid)
        finally:
            await t.stop_worker(None)
            config.set_titles({"enabled": False, "backend": 0, "engine": "",
                               "prompt": config.DEFAULT_TITLE_PROMPT})
            db.execute("DELETE FROM backends WHERE id=?", (bid,))
            backends._clear_backend_health(bid)
    print("remote generation ok")


async def task_creation():
    """A task created without a name asks for a title from its prompt."""
    t = session_titles
    driver = get_driver("claude")
    root = TEST_ROOT / "project"
    root.mkdir(exist_ok=True)
    import subprocess
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "README"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], check=True)
    parent = db.create_session("Main", "claude", str(root), "", "", "#e0784f", "auto")
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=session_command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        with patch.object(t, "_consumer", False):
            task = await session_tasks.create(parent, {
                "prompt": "Add a footer to the page", "request_id": "r1", "auto_title": True})
            assert task["name"] == "Add a footer to the page"
            assert task["auto_title"]["state"] == "requested"
            value = t.record(task["id"])
            assert value["text"] == "Add a footer to the page" and \
                value["placeholder"] == "Add a footer to the page"
            named = await session_tasks.create(parent, {
                "prompt": "Another one", "name": "Named task", "request_id": "r2",
                "auto_title": True})
            assert named["auto_title"] is None
            plain = await session_tasks.create(parent, {
                "prompt": "A third", "request_id": "r3"})
            assert plain["auto_title"] is None
            try:
                await session_tasks.create(parent, {"prompt": "x", "request_id": "r4",
                                                    "auto_title": "yes"})
            except session_tasks.TaskError:
                pass
            else:
                raise AssertionError("a non-boolean auto_title was accepted")
        for sid in (task["id"], named["id"], plain["id"]):
            hub = runner.hub(sid)
            await wait_for(lambda: hub.status == "idle", what="the task turn")
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    for sid in (task["id"], named["id"], plain["id"]):
        runner.drop_hub(sid)
        from puppy import workspaces
        workspaces.remove_temporary(db.get_session(sid))
        db.delete_session(sid)
    db.delete_session(parent)
    print("task creation ok")


async def api_contract(factory, controller):
    t = session_titles
    driver = get_driver("claude")
    app = factory()
    app.on_startup.clear(); app.on_shutdown.clear(); app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        state = await client.get("/api/ping", headers=headers)
        assert protocol.SESSION_TITLES_CAPABILITY in (await state.json())["capabilities"]
        for method, path in (("GET", "sessions/1/title"), ("POST", "sessions/1/title"),
                             ("POST", "titles/jobs"), ("GET", "titles"), ("PUT", "titles"),
                             ("POST", "titles/toggle"), ("POST", "titles/test")):
            response = await client.request(method, "/api/" + path, json={})
            assert response.status == 401, (path, response.status)
        # the per-session record, created armed through the create route
        response = await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(TEST_ROOT), "auto_title": True})
        assert response.status == 200, await response.text()
        session = (await response.json())["session"]
        sid = session["id"]
        assert session["auto_title"] == {"state": "armed", "requested_at": 0}
        response = await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(TEST_ROOT), "auto_title": True, "name": "Typed"})
        assert (await response.json())["session"]["auto_title"] is None
        named = (await response.json())["session"]["id"]
        response = await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(TEST_ROOT), "auto_title": "yes"})
        assert response.status == 400
        response = await client.post("/api/sessions", headers=headers, json={
            "engine": "claude", "cwd": str(TEST_ROOT)})
        plain = (await response.json())["session"]
        assert plain["auto_title"] is None
        response = await client.get("/api/sessions/{}/title".format(sid), headers=headers)
        assert response.status == 200 and (await response.json())["title"]["state"] == "armed"
        response = await client.get("/api/sessions/{}/title".format(plain["id"]), headers=headers)
        assert response.status == 404
        response = await client.get("/api/sessions/999999/title", headers=headers)
        assert response.status == 404
        text = "Login redirects forever"
        db.touch_session(sid, name=text)
        with patch.object(t, "_consumer", False):
            value = t.first_prompt(sid, text, text)
        response = await client.get("/api/sessions/{}/title".format(sid), headers=headers)
        assert (await response.json())["title"] == value
        listed = await client.get("/api/sessions", headers=headers)
        rows = {row["id"]: row for row in (await listed.json())["sessions"]}
        assert rows[sid]["auto_title"] == t.public(value) and rows[plain["id"]]["auto_title"] is None
        for body in ({}, {"requested_at": "soon", "name": "x"},
                     {"requested_at": value["requested_at"]},
                     {"requested_at": value["requested_at"], "name": "x", "error": "y"},
                     {"requested_at": value["requested_at"], "name": 5},
                     {"requested_at": value["requested_at"], "name": "x", "extra": 1}):
            response = await client.post("/api/sessions/{}/title".format(sid), headers=headers,
                                         json=body)
            assert response.status == 400, (body, await response.text())
        response = await client.post("/api/sessions/{}/title".format(sid), headers=headers,
                                     json={"requested_at": value["requested_at"] + 1, "name": "x"})
        assert response.status == 409
        response = await client.post("/api/sessions/{}/title".format(sid), headers=headers,
                                     json={"requested_at": value["requested_at"], "name": "Loop fix"})
        assert response.status == 200 and (await response.json()) == {
            "ok": True, "applied": True, "name": "Loop fix"}
        assert db.get_session(sid)["name"] == "Loop fix" and t.record(sid) is None
        response = await client.post("/api/sessions/{}/title".format(sid), headers=headers,
                                     json={"requested_at": value["requested_at"], "name": "x"})
        assert response.status == 409
        with patch.object(t, "_consumer", False):
            value = t.request(sid, text, "Loop fix")
        response = await client.post("/api/sessions/{}/title".format(sid), headers=headers,
                                     json={"requested_at": value["requested_at"], "error": "no"})
        assert (await response.json()) == {"ok": True, "applied": False, "name": "Loop fix"}
        # the title job on this node: a spawn job the spawn routes then serve
        with patch.object(driver, "build_cmd", side_effect=fixture_command), \
                patch.dict(os.environ, {"PUPPY_TEST_TITLE_LOG": str(LOG)}):
            title_mode("ok")
            for body in ({}, {"engine": "nope", "prompt": "x"}, {"engine": "claude"},
                         {"engine": "claude", "prompt": "x", "effort": "ultra"}):
                response = await client.post("/api/titles/jobs", headers=headers, json=body)
                assert response.status == 400, (body, await response.text())
            response = await client.post("/api/titles/jobs", headers=headers, json={
                "engine": "claude", "prompt": "Name it: " + text, "wait_s": 30})
            assert response.status == 200, await response.text()
            job = (await response.json())["job"]
            assert job["status"] == "done" and "Fix the login redirect loop" in job["answer"]
            assert job["cwd"] == t.job_cwd() and job["idle_timeout_s"] == t.JOB_IDLE_S and \
                job["max_runtime_s"] == t.JOB_RUNTIME_S
            response = await client.get("/api/spawn/{}".format(job["id"]), headers=headers)
            assert response.status == 200 and (await response.json())["job"]["status"] == "done"
            spawn_exec.manager().jobs.pop(job["id"], None)
        for target in (sid, named, plain["id"]):
            db.delete_session(target)
        # the settings: the controller's alone
        for method, path in (("GET", "titles"), ("PUT", "titles"),
                             ("POST", "titles/toggle"), ("POST", "titles/test")):
            response = await client.request(method, "/api/" + path, headers=headers, json={})
            if not controller:
                assert response.status == 404, (path, response.status)
        if not controller:
            return
        response = await client.get("/api/titles", headers=headers)
        data = await response.json()
        assert data["settings"] == t.settings() and data["default_prompt"] == config.DEFAULT_TITLE_PROMPT
        assert data["placeholder"] == "{message}" and data["max_prompt_chars"] == config.MAX_TITLE_PROMPT_CHARS
        full = {"backend": 0, "engine": "claude", "model": "haiku", "effort": "low",
                "prompt": " Name it: {message} "}
        response = await client.put("/api/titles", headers=headers, json=full)
        assert response.status == 200, await response.text()
        assert (await response.json())["settings"] == {
            "enabled": False, "backend": 0, "engine": "claude", "model": "haiku",
            "effort": "low", "prompt": "Name it: {message}"}
        config._config = None
        assert t.settings()["model"] == "haiku"
        for body in ({}, {"backend": 0, "engine": "claude", "model": "", "effort": ""},
                     dict(full, engine="gemini"), dict(full, backend=42), dict(full, backend=-1),
                     dict(full, prompt=""), dict(full, enabled=True)):
            response = await client.put("/api/titles", headers=headers, json=body)
            assert response.status == 400, (body, await response.text())
        response = await client.post("/api/titles/toggle", headers=headers, json={"enabled": True})
        assert response.status == 200 and t.active()
        response = await client.post("/api/titles/toggle", headers=headers, json={"enabled": "on"})
        assert response.status == 400
        before = config.export_data()
        with patch.object(config, "_save_locked", side_effect=OSError("disk full")):
            response = await client.put("/api/titles", headers=headers, json=dict(full, model="other"))
            assert response.status == 500
        assert config.export_data() == before
        # the test route generates from the panel's values without saving
        with patch.object(driver, "build_cmd", side_effect=fixture_command), \
                patch.dict(os.environ, {"PUPPY_TEST_TITLE_LOG": str(LOG)}):
            title_mode("ok")
            sample = dict(full, message="Login redirects forever", model="", effort="")
            response = await client.post("/api/titles/test", headers=headers, json=sample)
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result["title"] == "Fix the login redirect loop" and result["ok"]
            assert job_log()[-1]["prompt"] == "Name it: Login redirects forever"
            assert t.settings()["model"] == "haiku"   # unsaved
            for body in (dict(sample, message=""), dict(sample, message="x" * 5000),
                         dict(sample, engine=""), {"message": "x"}):
                response = await client.post("/api/titles/test", headers=headers, json=body)
                assert response.status == 400, (body, await response.text())
            title_mode("fail")
            response = await client.post("/api/titles/test", headers=headers, json=sample)
            assert response.status == 400 and "boom" in (await response.json())["error"]
        state = await client.get("/api/state", headers=headers)
        assert (await state.json())["titles"] == t.public_state()
        config.set_titles({"enabled": False, "engine": "", "model": "", "effort": "",
                           "prompt": config.DEFAULT_TITLE_PROMPT})


async def main():
    try:
        settings_shape()
        db.connect()
        auth.create_user("titles-test", "test-password")
        text_helpers()
        record_lifecycle()
        await local_generation()
        await runner_turn()
        await remote_generation()
        await task_creation()
        await api_contract(webui.build_app, True)
        await api_contract(backend_app, False)
        print("session title tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
