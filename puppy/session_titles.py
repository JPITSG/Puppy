"""Generated session titles: a model names an unnamed session or task from
its first message.

Two halves live here, like puppy/notify.py.

The node half (both runtimes) keeps a session's intent as the exact
``session_title.<sid>`` meta record. A session created with the toggle on and
no name is *armed*; its first prompt turns that into a *request* carrying a
bounded copy of the message and the placeholder name the runner gives the
session meanwhile (the first line of the message, as it always was). Every
session payload carries the additive ``auto_title`` record - ``state`` and
``requested_at``, never the text - so a controller sees what is waiting.
``GET /api/sessions/{sid}/title`` reads a request with its text and
``POST /api/sessions/{sid}/title`` settles it: with ``name`` the title is
applied only while the session still carries its placeholder (a name typed
meanwhile wins), with ``error`` the placeholder stays; either way the record
goes. The node also serves the job itself: ``POST /api/titles/jobs`` starts
one tool-free one-shot run of the chosen engine in an empty private directory
(never a project, so no agent notes are read), bounded like a spawned agent,
and the existing spawn job routes poll and cancel it.

The controller half (full runtime only) owns the settings - ``config.titles``:
the switch, the backend, engine, model and effort that answer, and the
prompt - and consumes requests: local ones the moment the runner records
them, remote ones from every sessions payload the controller receives from a
backend (its state stream, or the reads a console polls through the proxy).
One job runs per session at a time. A backend that cannot be reached, or
refuses for now, is retried on the sweep; a refusal that will not change
settles the request as failed; a failure is one warn toast to every console
and the placeholder name stays. Nothing here retries a model in a loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time

from puppy import config, db, protocol

log = logging.getLogger("puppy.titles")

PREFIX = "session_title."
STATE_ARMED = "armed"
STATE_REQUESTED = "requested"
KEYS = {"format", "state", "text", "placeholder", "requested_at"}
# how much of the first message a title is made from
TEXT_LIMIT = 4000
# the sessions table's name width, as every rename path cuts it
NAME_LIMIT = 80
# a title job is a spawned agent with one short question: bounded tighter
# than a delegate, and cancelled by the controller past GENERATE_TIMEOUT_S
JOB_IDLE_S = 60
JOB_RUNTIME_S = 180
GENERATE_TIMEOUT_S = 200
POLL_S = 20
# a node that could not be reached, or refused for now, is asked again
RETRY_S = 60.0
SWEEP_S = 30.0


class TitleError(ValueError):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        # True: nothing the node itself decided - retry later, keep the request
        self.transient = transient


# ---- the node half: the per-session record ----

def _key(sid) -> str:
    return PREFIX + str(int(sid))


def _validate(value):
    if not isinstance(value, dict) or set(value) != KEYS or \
            type(value["format"]) is not int or value["format"] != 1:
        raise TitleError("session title state is not current")
    state = value["state"]
    if state not in (STATE_ARMED, STATE_REQUESTED):
        raise TitleError("invalid session title state")
    text, placeholder, at = value["text"], value["placeholder"], value["requested_at"]
    if not isinstance(text, str) or len(text) > TEXT_LIMIT or \
            not isinstance(placeholder, str) or len(placeholder) > NAME_LIMIT or \
            type(at) not in (int, float) or isinstance(at, bool) or not math.isfinite(at):
        raise TitleError("invalid session title record")
    if state == STATE_ARMED and (text or placeholder or at != 0):
        raise TitleError("invalid armed session title record")
    if state == STATE_REQUESTED and (not text or at <= 0):
        raise TitleError("invalid session title request")
    return value


def record(sid):
    row = db.query_one("SELECT value FROM meta WHERE key=?", (_key(sid),))
    return _validate(json.loads(row["value"])) if row else None


def records() -> dict:
    return {int(row["key"][len(PREFIX):]): _validate(json.loads(row["value"]))
            for row in db.query("SELECT key,value FROM meta WHERE key GLOB ?", (PREFIX + "*",))}


def public(value):
    """The payload form: what is pending, never the message itself."""
    if value is None:
        return None
    return {"state": value["state"], "requested_at": value["requested_at"]}


def decorate(rows) -> None:
    """Add ``auto_title`` to a session list from one query."""
    pending = records()
    for row in rows:
        row["auto_title"] = public(pending.get(row["id"]))


def validate_persisted(connection) -> None:
    session_ids = {row[0] for row in connection.execute("SELECT id FROM sessions")}
    for key, raw in connection.execute("SELECT key,value FROM meta WHERE key GLOB ?",
                                       (PREFIX + "*",)):
        suffix = key[len(PREFIX):]
        if not re.fullmatch(r"[1-9][0-9]*", suffix) or int(suffix) not in session_ids:
            raise TitleError("session title record does not belong to a session")
        _validate(json.loads(raw))


def arm(sid) -> None:
    """The session was created unnamed with the toggle on: its first prompt
    becomes the request."""
    db.meta_set(_key(sid), {"format": 1, "state": STATE_ARMED, "text": "",
                            "placeholder": "", "requested_at": 0})


def title_text(text: str) -> str:
    """The message as a title is made from it: attachment marker lines keep
    their file names, not the node's private upload paths, and the whole is
    bounded."""
    from puppy import uploads
    lines = []
    for line in str(text or "").splitlines():
        for prefix, suffix, label in ((uploads.ATTACH_IMAGE_PREFIX, uploads.ATTACH_IMAGE_SUFFIX, "image attached"),
                                      (uploads.ATTACH_FILE_PREFIX, uploads.ATTACH_FILE_SUFFIX, "file attached")):
            if line.startswith(prefix) and line.endswith(suffix):
                name = os.path.basename(line[len(prefix):-len(suffix)].strip())
                line = "[{}: {}]".format(label, name) if name else "[{}]".format(label)
                break
        lines.append(line)
    return "\n".join(lines).strip()[:TEXT_LIMIT].strip()


def _request(sid, text: str, placeholder: str) -> dict:
    value = {"format": 1, "state": STATE_REQUESTED, "text": title_text(text),
             "placeholder": str(placeholder or "")[:NAME_LIMIT],
             "requested_at": round(time.time(), 3)}
    if not value["text"]:
        db.meta_del(_key(sid))
        return None
    db.meta_set(_key(sid), value)
    if _consumer:
        _schedule(0, int(sid), value["requested_at"])
    return value


def first_prompt(sid, text: str, placeholder: str):
    """The runner's call when an unnamed session takes its placeholder name
    from its first prompt: an armed session becomes a request, anything else
    is left alone. Returns the request or None."""
    value = record(sid)
    if value is None or value["state"] != STATE_ARMED:
        return None
    return _request(sid, text, placeholder)


def request(sid, text: str, placeholder: str):
    """A task is named at creation, so it asks directly."""
    return _request(sid, text, placeholder)


def settle(sid, requested_at, name: str = "", error: str = "") -> dict:
    """Apply a generated title, or record that none came, and drop the request.

    The title lands only while the session still carries the placeholder the
    request recorded: a name typed meanwhile is the person's choice."""
    value = record(sid)
    if value is None or value["state"] != STATE_REQUESTED or \
            value["requested_at"] != requested_at:
        raise TitleError("that title request is no longer pending")
    session = db.get_session(sid)
    if session is None:
        raise TitleError("session gone")
    name = str(name or "").strip()[:NAME_LIMIT]
    applied = False
    if name and session["name"] == value["placeholder"]:
        db.touch_session(sid, name=name)
        applied = True
    elif not name:
        log.info("session %s keeps its placeholder title: %s", sid,
                 error or "no title was generated")
    db.meta_del(_key(sid))
    from puppy import runner
    runner.broadcast_sessions()
    runner.hub(sid).broadcast({"type": "session_meta",
                               "session": runner.session_payload(db.get_session(sid))})
    return {"applied": applied, "name": db.get_session(sid)["name"]}


# ---- the node half: the job ----

def job_cwd() -> str:
    """An empty private directory a title job runs in: never a project, so
    the engine reads no agent notes and no repository."""
    path = os.path.join(config.DATA_DIR, "titles")
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


async def start_job(body: dict):
    """One tool-free one-shot run answering a title prompt on this node."""
    from puppy import spawn_exec
    if not isinstance(body, dict):
        raise TitleError("title job must be an object")
    request_body = {
        "engine": body.get("engine"), "model": body.get("model") or "",
        "effort": body.get("effort") or "", "prompt": body.get("prompt"),
        "cwd": job_cwd(), "idle_timeout_s": JOB_IDLE_S,
        "max_runtime_s": JOB_RUNTIME_S,
    }
    try:
        prepared = await spawn_exec.prepare_request(request_body)
        prepared["guidance"] = False
        manager = spawn_exec.manager()
        manager.ensure_capacity(1)
        return manager.start_job(prepared, ("title",))
    except spawn_exec.SpawnError as exc:
        raise TitleError(str(exc), transient=exc.status in (409, 429)) from exc


# ---- the controller half: settings ----

def settings() -> dict:
    return dict(config.get("titles"))


def configured(values=None) -> bool:
    values = settings() if values is None else values
    return bool(values.get("engine"))


def active() -> bool:
    values = settings()
    return bool(values["enabled"]) and configured(values)


def public_state() -> dict:
    """What a console needs for the dialogs' toggle and its wording."""
    values = settings()
    return {"enabled": bool(values["enabled"]), "configured": configured(values),
            "backend": int(values["backend"]), "engine": values["engine"],
            "model": values["model"]}


def build_prompt(template: str, message: str) -> str:
    template = str(template or "").strip() or config.DEFAULT_TITLE_PROMPT
    message = str(message or "").strip()
    if config.TITLE_PLACEHOLDER in template:
        return template.replace(config.TITLE_PLACEHOLDER, message)
    return template + "\n\n" + message


_LABEL_RE = re.compile(r"^(?:(?:suggested |session |proposed )?title|name)\s*[:\-–—]\s*", re.I)
_LEAD_RE = re.compile(r"^(?:[-*•]\s+|\d+[.)]\s+)")
_WRAP = "\"'`“”‘’«»*_ "


def clean_title(answer: str) -> str:
    """The title in a model's answer: its first line that is not a preamble,
    without a label, quotes, emphasis or a trailing period, cut to the name
    width on a word."""
    lines = []
    for raw in str(answer or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        lines.append(line)
    if not lines:
        return ""
    # "Here is a title:" introduces the title on the next line
    candidates = [line for line in lines if not line.endswith(":")] or lines
    line = _LEAD_RE.sub("", candidates[0])
    line = _LABEL_RE.sub("", line)
    line = re.sub(r"\s+", " ", line).strip(_WRAP)
    line = line.rstrip(".").strip(_WRAP)
    if len(line) > NAME_LIMIT:
        cut = line[:NAME_LIMIT - 1].rstrip()
        space = cut.rfind(" ")
        if space > NAME_LIMIT // 2:
            cut = cut[:space]
        line = cut.rstrip(",;:-–— ") + "…"
    return line


# ---- the controller half: consuming requests ----

_consumer = False
_jobs = {}        # (bid, sid) -> Task
_retry_at = {}    # (bid, sid, requested_at) -> monotonic time to try again
_verdicts = {}    # (bid, sid, requested_at) -> a verdict its node has not taken yet
_settled = {}     # (bid, sid, requested_at) -> monotonic time it was settled
_sweeper = None
SETTLED_MEMORY_S = 3600.0


def _remember_settled(bid: int, sid: int, requested_at) -> None:
    """A payload the controller receives after it settled a request (a
    replayed snapshot, a read answered from an older list) asks nothing."""
    now = time.monotonic()
    for key, when in list(_settled.items()):
        if now - when > SETTLED_MEMORY_S:
            _settled.pop(key, None)
    _settled[(bid, sid, requested_at)] = now


def _schedule(bid: int, sid: int, requested_at) -> None:
    key = (int(bid), int(sid))
    task = _jobs.get(key)
    if task is not None and not task.done():
        return
    if (key[0], key[1], requested_at) in _settled:
        return
    if _retry_at.get((key[0], key[1], requested_at), 0.0) > time.monotonic():
        return
    try:
        _jobs[key] = asyncio.ensure_future(_generate(key[0], key[1], requested_at))
    except RuntimeError:
        # no running loop here (a synchronous caller outside the server): the
        # request is durable and the sweep picks it up
        pass


def observe_sessions(bid: int, sessions) -> None:
    """Every sessions payload the controller sees from a backend passes here:
    a request on it starts (or keeps) one job for that session."""
    if not _consumer or not isinstance(sessions, list):
        return
    for session in sessions:
        if not isinstance(session, dict):
            continue
        auto = session.get("auto_title")
        if not isinstance(auto, dict) or auto.get("state") != STATE_REQUESTED:
            continue
        try:
            _schedule(int(bid), int(session["id"]), float(auto["requested_at"]))
        except (TypeError, ValueError):
            continue


def _defer(bid: int, sid: int, requested_at) -> None:
    _retry_at[(bid, sid, requested_at)] = time.monotonic() + RETRY_S


def _channel(bid: int) -> dict:
    """The node a title job runs on: this one over loopback is never needed
    (local jobs start in-process), so this is a paired backend."""
    from puppy import backends
    if backends.get_backend(bid) is None:
        raise TitleError("the backend chosen for titles no longer exists")
    channel = backends.node_channel(bid)
    if channel is None:
        raise TitleError("the backend chosen for titles is offline", transient=True)
    caps = channel.get("capabilities") or []
    if protocol.SPAWN_EXEC_CAPABILITY not in caps or \
            protocol.SESSION_TITLES_CAPABILITY not in caps:
        raise TitleError("backend '{}' needs a Puppy upgrade before it can "
                         "generate titles".format(channel.get("name") or bid))
    return channel


def _session_channel(bid: int) -> dict:
    from puppy import backends
    channel = backends.node_channel(bid)
    if channel is None:
        raise TitleError("the session's backend is offline", transient=True)
    return channel


def _classify(exc) -> TitleError:
    from puppy import spawn_exec
    if isinstance(exc, TitleError):
        return exc
    if isinstance(exc, spawn_exec.SpawnError):
        return TitleError(str(exc), transient=exc.unreached or
                          exc.status in (409, 429, 502, 503))
    return TitleError(str(exc) or exc.__class__.__name__)


def _job_title(job: dict) -> str:
    """The title in a finished job: only a completed run's final answer."""
    if str(job.get("status")) != "done":
        return ""
    return clean_title(job.get("answer"))


def _failure_reason(job: dict) -> str:
    """Why a job produced no title, in the title's own words rather than a
    spawned agent's."""
    status = str(job.get("status") or "unknown")
    if status in ("done", "incomplete"):
        return "the model answered without a title"
    if status == "timeout":
        return "the model did not answer in time"
    return str(job.get("error") or "").strip() or "the title job ended as " + status


async def _run_job(bid: int, body: dict) -> dict:
    """Run one title job on ``bid`` and return its final payload. A job still
    running past GENERATE_TIMEOUT_S, or when this wait is cancelled, is
    cancelled with it."""
    from puppy import spawn_exec
    deadline = time.monotonic() + GENERATE_TIMEOUT_S
    if not bid:
        job = await start_job(body)
        manager = spawn_exec.manager()
        try:
            while job.running and time.monotonic() < deadline:
                await manager.wait(job, min(POLL_S, max(0.0, deadline - time.monotonic())))
        finally:
            if job.running:
                await asyncio.shield(manager.cancel(job, "title generation timed out"))
            manager.jobs.pop(job.id, None)
        return job.payload()
    channel = _channel(bid)
    try:
        data = await spawn_exec.node_request(
            channel, "POST", "titles/jobs", body=dict(body, wait_s=POLL_S),
            timeout_s=POLL_S + 25.0)
    except spawn_exec.SpawnError as exc:
        raise _classify(exc) from exc
    job = data.get("job") if isinstance(data.get("job"), dict) else None
    if not job or not job.get("id"):
        raise TitleError("backend '{}' returned an invalid title job".format(channel["name"]))

    async def cancel_remote():
        try:
            await spawn_exec.node_request(channel, "DELETE", "spawn/" + str(job["id"]),
                                          timeout_s=20.0)
        except spawn_exec.SpawnError:
            pass

    try:
        while str(job.get("status")) == "running" and time.monotonic() < deadline:
            job = await spawn_exec.refresh_remote(
                channel, job, min(POLL_S, max(1, int(deadline - time.monotonic()))))
    except asyncio.CancelledError:
        await asyncio.shield(cancel_remote())
        raise
    if str(job.get("status")) == "running":
        await cancel_remote()
        raise TitleError("title generation timed out on {}".format(channel["name"]))
    return job


async def _read(bid: int, sid: int):
    from puppy import spawn_exec
    if not bid:
        return record(sid)
    try:
        data = await spawn_exec.node_request(
            _session_channel(bid), "GET", "sessions/{}/title".format(sid), timeout_s=20.0)
    except spawn_exec.SpawnError as exc:
        if exc.status == 404 and not exc.unreached:
            return None
        raise _classify(exc) from exc
    value = data.get("title")
    return _validate(value) if isinstance(value, dict) else None


async def _settle(bid: int, sid: int, requested_at, name: str = "", error: str = "") -> None:
    from puppy import spawn_exec
    if not bid:
        try:
            settle(sid, requested_at, name=name, error=error)
        except TitleError:
            pass   # gone, or settled by another way meanwhile
        _remember_settled(bid, sid, requested_at)
        return
    body = {"requested_at": requested_at}
    if name:
        body["name"] = name
    else:
        body["error"] = error or "no title was generated"
    try:
        await spawn_exec.node_request(
            _session_channel(bid), "POST", "sessions/{}/title".format(sid),
            body=body, timeout_s=20.0)
    except spawn_exec.SpawnError as exc:
        if exc.status in (404, 409) and not exc.unreached:
            _remember_settled(bid, sid, requested_at)
            return
        raise _classify(exc) from exc
    _remember_settled(bid, sid, requested_at)


def _session_label(bid: int, sid: int) -> str:
    from puppy import backends
    name = ""
    if not bid:
        session = db.get_session(sid)
        name = str((session or {}).get("name") or "")
    else:
        for session in backends.cached_remote_sessions(bid):
            if isinstance(session, dict) and session.get("id") == sid:
                name = str(session.get("name") or "")
                break
    return name or "session {}".format(sid)


def _report(bid: int, sid: int, reason: str) -> None:
    """A title that did not come is one warn toast to every console; the
    placeholder name stays, so nothing is lost."""
    from puppy import runner
    text = "Could not generate a title for {} · {}".format(
        _session_label(bid, sid), str(reason or "unknown error").strip().rstrip("."))
    log.warning("%s", text)
    runner.broadcast_update({"type": "toast", "level": "warn", "text": text[:400]})


async def _decide(bid: int, sid: int, requested_at):
    """Make the verdict for one request: ``{"name"}`` with a title,
    ``{"error"}`` with why there is none (``silent`` when nobody needs
    telling), or None when the request is gone. Transient trouble raises."""
    values = settings()
    if not (values["enabled"] and values["engine"]):
        # off: the request is not kept for a later switch-on
        return {"error": "title generation is off", "silent": True}
    value = await _read(bid, sid)
    if value is None or value["state"] != STATE_REQUESTED or \
            value["requested_at"] != requested_at:
        return None
    body = {"engine": values["engine"], "model": values["model"],
            "effort": values["effort"],
            "prompt": build_prompt(values["prompt"], value["text"])}
    try:
        job = await _run_job(int(values["backend"]), body)
    except Exception as exc:
        raise _classify(exc) from exc
    title = _job_title(job)
    if title:
        return {"name": title}
    return {"error": _failure_reason(job)}


async def _generate(bid: int, sid: int, requested_at) -> None:
    """One request from decision to settlement. The verdict is kept until
    the session's node has taken it, so a node that could not be reached is
    told again rather than the model asked again."""
    key = (bid, sid, requested_at)
    try:
        verdict = _verdicts.get(key)
        if verdict is None:
            try:
                verdict = await _decide(bid, sid, requested_at)
            except TitleError as exc:
                if exc.transient:
                    raise
                verdict = {"error": str(exc)}
            if verdict is None:
                _remember_settled(bid, sid, requested_at)
                return
            _verdicts[key] = verdict
            if verdict.get("error") and not verdict.get("silent"):
                _report(bid, sid, verdict["error"])
            elif verdict.get("name"):
                log.info("session %s on backend %s titled: %s", sid, bid, verdict["name"])
        await _settle(bid, sid, requested_at, name=verdict.get("name", ""),
                      error=verdict.get("error", ""))
        _verdicts.pop(key, None)
    except TitleError as exc:
        log.info("title for session %s on backend %s deferred: %s", sid, bid, exc)
        _defer(bid, sid, requested_at)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("title generation failed for session %s on backend %s", sid, bid)
        _defer(bid, sid, requested_at)
    finally:
        if _jobs.get((bid, sid)) is asyncio.current_task():
            _jobs.pop((bid, sid), None)


def sweep_local() -> None:
    """Requests this node already holds: after a start, or a switch-on."""
    if not _consumer:
        return
    try:
        pending = records()
    except (TitleError, ValueError) as exc:
        log.error("session title records are not current: %s", exc)
        return
    for sid, value in pending.items():
        if value["state"] == STATE_REQUESTED:
            _schedule(0, sid, value["requested_at"])


def _sweep() -> None:
    now = time.monotonic()
    for (bid, sid, requested_at), when in list(_retry_at.items()):
        if when <= now:
            _retry_at.pop((bid, sid, requested_at), None)
            _schedule(bid, sid, requested_at)
    sweep_local()


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(SWEEP_S)
        try:
            _sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("title sweep failed")


async def test_title(values: dict, message: str) -> dict:
    """Settings' Try it: one job with the panel's values, unsaved."""
    checked = config.normalize_titles({**settings(), **values})
    if not checked["engine"]:
        raise TitleError("Choose an engine first")
    body = {"engine": checked["engine"], "model": checked["model"],
            "effort": checked["effort"],
            "prompt": build_prompt(checked["prompt"], title_text(message))}
    try:
        job = await _run_job(int(checked["backend"]), body)
    except Exception as exc:
        raise _classify(exc) from exc
    title = _job_title(job)
    if not title:
        raise TitleError(_failure_reason(job))
    return {"title": title, "answer": str(job.get("answer") or "")[:2000],
            "elapsed_s": int(job.get("elapsed_s") or 0),
            "model": str(job.get("model_used") or job.get("model") or "")}


async def start_worker(app) -> None:
    """The full runtime consumes requests; a headless backend only keeps them."""
    global _consumer, _sweeper
    from puppy import backends
    _consumer = True
    backends.add_sessions_observer(observe_sessions)
    sweep_local()
    for bid in [int(item["id"]) for item in backends.list_backends()]:
        observe_sessions(bid, backends.cached_remote_sessions(bid))
    if _sweeper is None or _sweeper.done():
        _sweeper = asyncio.create_task(_sweep_loop())


async def stop_worker(_app=None) -> None:
    global _consumer, _sweeper
    _consumer = False
    sweeper, _sweeper = _sweeper, None
    if sweeper is not None:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
    for task in list(_jobs.values()):
        task.cancel()
    if _jobs:
        await asyncio.gather(*list(_jobs.values()), return_exceptions=True)
    _jobs.clear()
    _retry_at.clear()
    _settled.clear()
    _verdicts.clear()


def settings_changed() -> None:
    """Enabling starts whatever waited; disabling lets running jobs finish."""
    if _consumer:
        sweep_local()


# ---- HTTP: the node routes (both runtimes) ----

async def h_title(request):
    from aiohttp import web
    sid = int(request.match_info["sid"])
    if db.get_session(sid) is None:
        raise web.HTTPNotFound()
    try:
        if request.method == "GET":
            value = record(sid)
            if value is None:
                return web.json_response({"error": "no title request"}, status=404)
            return web.json_response({"title": value})
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or not {"requested_at"} <= set(body) <= {
                "requested_at", "name", "error"} or \
                type(body["requested_at"]) not in (int, float) or \
                isinstance(body["requested_at"], bool):
            return web.json_response(
                {"error": "supply requested_at with a name or an error"}, status=400)
        name = body.get("name", "")
        error = body.get("error", "")
        if not isinstance(name, str) or not isinstance(error, str) or \
                bool(name.strip()) == bool(error.strip()):
            return web.json_response(
                {"error": "supply exactly one of name and error"}, status=400)
        result = settle(sid, body["requested_at"], name=name, error=error)
        return web.json_response(dict(result, ok=True))
    except TitleError as exc:
        return web.json_response({"error": str(exc)}, status=409)


async def h_job(request):
    """Start a title job on this node; the spawn job routes take it from here."""
    from aiohttp import web
    from puppy import spawn_exec
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "title job must be an object"}, status=400)
    try:
        job = await start_job(body)
    except TitleError as exc:
        return web.json_response({"error": str(exc)},
                                 status=409 if exc.transient else 400)
    await spawn_exec.manager().wait(job, spawn_exec._clamp_wait(body.get("wait_s"), default=0))
    return web.json_response({"ok": True, "job": job.payload()})


def register(app) -> None:
    app.router.add_get(r"/api/sessions/{sid:\d+}/title", h_title)
    app.router.add_post(r"/api/sessions/{sid:\d+}/title", h_title)
    app.router.add_post("/api/titles/jobs", h_job)

    async def validate(_app):
        validate_persisted(db.connect())
    app.on_startup.append(validate)
