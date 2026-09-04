"""Durable requests between existing sessions, using their ordinary queues.

The requesting controller owns an outbox; the receiving node owns an inbox.
Ids are chosen before transmission and are idempotent across lost replies and
restarts. Queued prompts remain strings. Their visible request marker connects
the exact prompt to its receipt, including held/reordered/retried work.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import weakref

from puppy import db, runner
from puppy import session_links as links

CAPABILITY = "session-communication"
TERMINAL = frozenset(("completed", "failed", "cancelled", "expired", "rejected", "lost"))
INBOX_PREFIX = "session_inbox."
OUTBOX_PREFIX = "session_outbox."
PROMPT_RE = re.compile(r"^\[Puppy session request ([a-f0-9]{32})\]\n")
_locks = weakref.WeakValueDictionary()
_active = {INBOX_PREFIX: set(), OUTBOX_PREFIX: set()}


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise links.SessionLinkError("invalid session request id")
    return value


def request_id(source, key):
    return hashlib.sha256((source + ":" + key).encode()).hexdigest()[:32]


def records(prefix):
    return [json.loads(row["value"]) for row in
            db.query("SELECT value FROM meta WHERE key GLOB ?", (prefix + "*",))]


def save(prefix, value):
    db.meta_set(prefix + value["id"], value)
    if prefix in _active:
        active = value["status"] not in TERMINAL or \
            (prefix == OUTBOX_PREFIX and not value["notified"])
        if active:
            _active[prefix].add(value["id"])
        else:
            _active[prefix].discard(value["id"])


def restore_tracking():
    for prefix, ids in _active.items():
        ids.clear()
        for value in records(prefix):
            if value["status"] not in TERMINAL or (prefix == OUTBOX_PREFIX and not value["notified"]):
                ids.add(value["id"])


def active_records(prefix):
    return [value for rid in list(_active[prefix]) for value in [links.load_record(prefix + rid)] if value]


def get(prefix, rid):
    value = links.load_record(prefix + _id(rid))
    if value is None:
        raise links.SessionLinkError("session request was not found")
    return value


def _notice(ref, text, request=None):
    node, sid = links.parse_ref(ref)
    if node != db.node_uuid() or db.get_session(sid) is None:
        return
    data = {"subtype": "session_request", "text": text}
    if request:
        data["session_request"] = request
    runner.hub(sid)._emit("info", data)


def _inbox_public(value):
    return {key: value[key] for key in
            ("id", "source", "target", "action", "status", "created_at", "deadline",
             "turn_id", "start_seq", "end_seq", "answer", "error")}


def _prompt(value):
    return ("[Puppy session request {id}]\nFrom session {source_title} ({source}).\n"
            "Request type: {action}.\n"
            "Answer this request in this session using its existing context. "
            "Puppy returns your answer to the requesting session. "
            "For a question, answer from the conversation only, without tools or changes. "
            "Keep this request's scope; further session actions require an explicit "
            "instruction in the request.\n\n{text}").format(**value)


def _active_inbox(sid, prompt):
    match = PROMPT_RE.match(prompt or "")
    if match is None:
        return None
    value = links.load_record(INBOX_PREFIX + match[1])
    if value and value["target"] == links.reference(sid) and value["prompt"] == prompt:
        return value
    return None


def turn_started(sid, prompt, turn_id, user_seq):
    value = _active_inbox(sid, prompt)
    if value is None or value["status"] in TERMINAL:
        return
    value.update(status="running", turn_id=turn_id, start_seq=user_seq)
    save(INBOX_PREFIX, value)


def turn_finished(sid, prompt, status, user_seq):
    value = _active_inbox(sid, prompt)
    if value is None or value["status"] in TERMINAL:
        return
    answers, end, total = [], user_seq, 0
    # Read bounded pages through this exact turn, before its successor starts.
    while True:
        rows = db.get_events(sid, after_seq=end, limit=200)
        for row in rows:
            end = row["seq"]
            if row["kind"] == "assistant":
                text = str(row["data"].get("text") or "")
                total += len(text)
                if sum(map(len, answers)) < 120000:
                    answers.append(text[:120000 - sum(map(len, answers))])
        if len(rows) < 200:
            break
    answer = "\n\n".join(answers)
    if total > 120000 or len(answer) > 120000:
        answer = answer[:120000]
        answer += "\n[Answer shortened; read the linked session for the complete response.]"
    value.update(status="completed" if status == "ok" else
                 "cancelled" if status == "interrupted" else "failed",
                 answer=answer, end_seq=end, start_seq=user_seq,
                 error="" if status == "ok" else "The requested turn ended: " + status)
    save(INBOX_PREFIX, value)


async def receive(args):
    if runner._draining or (links._app and links._app.get("puppy_snapshot_busy")):
        raise links.SessionLinkError("session requests are paused for shutdown or backup")
    required = {"id", "source", "source_title", "target", "action", "text", "deadline", "expected_turn_id"}
    if set(args) != required:
        raise links.SessionLinkError("invalid session request fields")
    rid = _id(args["id"])
    source = links.parse_ref(args["source"])
    node, sid = links.parse_ref(args["target"])
    if node != db.node_uuid() or args["target"] == args["source"]:
        raise links.SessionLinkError("invalid destination session")
    if args["action"] not in ("question", "task", "steer", "stop"):
        raise links.SessionLinkError("unknown session action")
    for key, maximum in (("text", 120000), ("source_title", 300), ("expected_turn_id", 128)):
        if not isinstance(args[key], str) or len(args[key]) > maximum:
            raise links.SessionLinkError("invalid " + key)
    if args["action"] != "stop" and not args["text"].strip():
        raise links.SessionLinkError("request text is empty")
    if type(args["deadline"]) not in (int, float) or not math.isfinite(args["deadline"]):
        raise links.SessionLinkError("invalid request deadline")
    lock = _locks.setdefault(rid, asyncio.Lock())
    async with lock:
        existing = links.load_record(INBOX_PREFIX + rid)
        if existing is not None:
            if any(existing[key] != args[key] for key in required):
                raise links.SessionLinkError("request id already used with different content")
            return _inbox_public(existing)
        if not time.time() < args["deadline"] <= time.time() + 7205:
            raise links.SessionLinkError("request deadline must be within the next two hours")
        session = db.get_session(sid)
        if session is None:
            raise links.SessionLinkError("destination session was deleted")
        hub = runner.hub(sid)
        value = dict(args, format=1, created_at=time.time(), status="submitting", prompt="",
                     turn_id="", start_seq=0, end_seq=0, answer="", error="")
        save(INBOX_PREFIX, value)
        _notice(value["target"], "{} from {}: {}".format(
            args["action"].capitalize(), args["source_title"], args["text"][:500]),
                {"id": rid, "source": args["source"], "target": args["target"]})
        if args["action"] == "question" and hub.side_question_state(session).get("ready"):
            state = hub.side_question_state(session)
            if not state.get("ready"):
                result = {"error": "This session cannot answer beside its current turn. "
                          "Use a task request explicitly to queue a conversational answer."}
            else:
                value["turn_id"] = state["turn_id"]
                save(INBOX_PREFIX, value)
                result = await hub.ask(args["text"], rid, expected_turn_id=state["turn_id"])
                value["status"] = "answering"
        elif args["action"] in ("steer", "stop"):
            expected = args["expected_turn_id"]
            if not expected or hub._active_turn_id != expected or hub.status != "running":
                result = {"error": "the target turn changed; inspect it again"}
            elif args["action"] == "steer":
                result = await hub.steer(args["text"], rid, expected_turn_id=expected)
                value["status"] = "completed"
                value["answer"] = "Steering handed to turn " + expected + " (" + str(result.get("status") or "sent") + ")"
            else:
                await hub.interrupt()
                result = {"ok": True}
                value["status"] = "completed"
                value["answer"] = "Stop requested for turn " + expected
        else:
            value["prompt"] = _prompt(value)
            value["status"] = "queued"
            save(INBOX_PREFIX, value)
            result = hub.send_message(value["prompt"])
        if result.get("error"):
            value.update(status="rejected", error=result["error"])
        save(INBOX_PREFIX, value)
        return _inbox_public(value)


async def inbox_status(args):
    rid = _id(args.get("id"))
    lock = _locks.setdefault(rid, asyncio.Lock())
    async with lock:
        return await _inbox_status(args)


async def _inbox_status(args):
    value = get(INBOX_PREFIX, args.get("id"))
    if args.get("source") != value["source"]:
        raise links.SessionLinkError("request belongs to another session")
    if value["status"] in TERMINAL:
        return _inbox_public(value)
    node, sid = links.parse_ref(value["target"])
    if db.get_session(sid) is None:
        value.update(status="lost", error="destination session was deleted")
    elif value["status"] == "answering":
        rows = db.query("SELECT seq,payload FROM events WHERE session_id=? AND kind='side_question_result' ORDER BY seq DESC",
                        (sid,))
        for row in rows:
            event = json.loads(row["payload"])
            if event.get("request_id") != value["id"]:
                continue
            ok = event.get("ok") and not event.get("synthetic")
            value.update(status="completed" if ok else "failed", answer=event.get("text", ""),
                         end_seq=row["seq"], error="" if ok else event.get("error") or "No substantive answer was returned")
            break
    else:
        hub = runner.hub(sid)
        if value["prompt"] in hub.held:
            value["status"] = "held"
        elif value["prompt"] in hub.queue:
            value["status"] = "queued"
        elif hub.status == "running" and hub._active_prompt_text == value["prompt"]:
            value["status"] = "running"
        else:
            value.update(status="lost", error="The requested prompt was removed or its execution was interrupted by a restart")
    if value["status"] not in TERMINAL and time.time() >= value["deadline"]:
        await _cancel_inbox({"id": value["id"], "source": value["source"]}, expired=True)
        value = get(INBOX_PREFIX, value["id"])
    save(INBOX_PREFIX, value)
    return _inbox_public(value)


async def cancel_inbox(args, expired=False):
    rid = _id(args.get("id"))
    lock = _locks.setdefault(rid, asyncio.Lock())
    async with lock:
        return await _cancel_inbox(args, expired)


async def _cancel_inbox(args, expired=False):
    value = get(INBOX_PREFIX, args.get("id"))
    if args.get("source") != value["source"]:
        raise links.SessionLinkError("request belongs to another session")
    if value["status"] in TERMINAL:
        return _inbox_public(value)
    _, sid = links.parse_ref(value["target"])
    hub = runner.hub(sid)
    prompt = value["prompt"]
    value.update(status="expired" if expired else "cancelled", error="Request deadline elapsed" if expired else "Cancelled by the requesting session")
    save(INBOX_PREFIX, value)
    if prompt:
        for index in reversed(range(len(hub.queue))):
            if hub.queue[index] == prompt:
                hub._pop_queue(index)
        hub.held[:] = [item for item in hub.held if item != prompt]
        hub._broadcast_queue()
        if hub.status == "running" and hub._active_prompt_text == prompt:
            await hub.interrupt()
    elif value["action"] == "question":
        record = hub._side_questions.get(value["id"])
        if record and record.get("status") == "pending":
            # The driver owns cancellation's native shape. Settling this one
            # question must never stop the target's main turn.
            driver = runner.get_driver(db.get_session(sid)["engine"])
            payload = driver.side_question_cancel_payload(
                db.get_session(sid), hub._driver_ctx, value["id"])
            if payload:
                await hub._write_stdin(payload)
            hub._settle_side_question(record, ok=False, error=value["error"])
    _notice(value["target"], "Session request " + value["status"], _inbox_public(value))
    return _inbox_public(value)


async def node_call(method, args):
    if method == "request":
        return await receive(args)
    if method == "request_status":
        return await inbox_status(args)
    if method == "request_cancel":
        return await cancel_inbox(args)
    if method == "notice":
        links.parse_ref(args.get("ref"))
        if not isinstance(args.get("text"), str) or len(args["text"]) > 2000:
            raise links.SessionLinkError("invalid notice")
        _notice(args["ref"], args["text"], args.get("request"))
        return {"ok": True}
    raise links.SessionLinkError("unknown session communication operation")


def _cycle(source, targets):
    graph = {}
    for record in records(OUTBOX_PREFIX):
        if record["status"] not in TERMINAL:
            graph.setdefault(record["source"], set()).update(
                r["target"] for r in record["results"] if r["status"] not in TERMINAL)
    todo, seen = list(targets), set()
    while todo:
        node = todo.pop()
        if node == source:
            return True
        if node not in seen:
            seen.add(node)
            todo.extend(graph.get(node, ()))
    return False


async def _call_ref(ref, method, args, catalog=None):
    data = catalog if catalog is not None else await links.catalog()
    row = next((row for row in data["sessions"] if row["ref"] == ref), None)
    if row is None:
        raise links.SessionLinkError("session is deleted or its node is unavailable")
    if row["bid"] and not backends_supports(row["bid"]):
        raise links.SessionLinkError("node does not support session communication")
    return await links.remote_call(row["bid"], method, args)


def backends_supports(bid):
    from puppy import backends
    return backends.backend_supports(bid, CAPABILITY)


async def _publish(record, text):
    try:
        await _call_ref(record["source"], "notice", {
            "ref": record["source"], "text": text,
            "request": {"id": record["id"], "source": record["source"],
                        "targets": record["targets"], "status": record["status"],
                        "controller": db.node_uuid(), "workflow": "steps" in record}})
        return True
    except Exception:
        return False


def _public(record):
    result = {key: record[key] for key in
            ("id", "source", "action", "targets", "status", "created_at", "deadline", "results", "unavailable", "workflow")}
    result["sources"] = [{"ref": row["target"], "url": links.source_link(row["target"], row.get("end_seq", 0))}
                         for row in record["results"]]
    return result


async def send(source, args, scope=None, workflow=""):
    if runner._draining or (links._app and links._app.get("puppy_snapshot_busy")):
        raise links.SessionLinkError("session requests are paused for shutdown or backup")
    request_key = args.get("request_id")
    if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request_key):
        raise links.SessionLinkError("supply a unique request_id and reuse it if the reply is lost")
    rid = request_id(source, request_key)
    refs = args.get("refs", (scope or {}).get("refs"))
    if refs == "all":
        refs = ["all"]
    if not isinstance(refs, list) or not refs or len(refs) > 512:
        raise links.SessionLinkError("select destination sessions")
    action = args.get("action", "task")
    text = args.get("text", "")
    timeout = links.integer(args.get("timeout_s", 3600), "timeout_s", 30, 7200)
    if action not in ("task", "question", "steer", "stop") or not isinstance(text, str) or len(text) > 120000:
        raise links.SessionLinkError("invalid action or request text")
    if action != "stop" and not text.strip():
        raise links.SessionLinkError("request text is empty")
    expected = args.get("expected_turn_ids", {})
    if not isinstance(expected, dict):
        raise links.SessionLinkError("expected_turn_ids must map each target to its current turn id")
    lock = _locks.setdefault("out:" + rid, asyncio.Lock())
    async with lock:
        existing = links.load_record(OUTBOX_PREFIX + rid)
        signature = {"refs": refs, "action": action, "text": text, "timeout_s": timeout, "expected_turn_ids": expected}
        if existing:
            if existing["signature"] != signature:
                raise links.SessionLinkError("request id already used with different content")
            return _public(existing)
        data = await links.catalog()
        rows = {row["ref"]: row for row in data["sessions"]}
        if source not in rows:
            raise links.SessionLinkError("the originating session is unavailable")
        targets = list(rows) if refs == ["all"] else list(dict.fromkeys(refs))
        if refs == ["all"]:
            targets = [ref for ref in targets if ref != source]
        if not targets or len(targets) > 512:
            raise links.SessionLinkError("select between 1 and 512 other sessions")
        for target in targets:
            links.parse_ref(target)
            if target == source:
                raise links.SessionLinkError("cannot send a request to the originating session")
            if scope and scope["refs"] != ["all"] and target not in scope["refs"]:
                raise links.SessionLinkError("destination is outside the selected references")
        if action in ("task", "question") and _cycle(source, targets):
            raise links.SessionLinkError("this request would create a waiting cycle between sessions")
        if action in ("steer", "stop") and any(not expected.get(target) for target in targets):
            raise links.SessionLinkError("inspect every destination and supply its expected_turn_id")
        now = time.time()
        record = {"format": 1, "id": rid, "source": source,
                  "source_title": rows.get(source, {}).get("title", source),
                  "signature": signature, "action": action, "text": text,
                  "targets": targets, "created_at": now, "deadline": now + timeout,
                  "status": "pending", "results": [], "unavailable": data["unavailable"],
                  "workflow": workflow, "notified": False}
        for target in targets:
            child = hashlib.sha256((rid + ":" + target).encode()).hexdigest()[:32]
            record["results"].append({"id": child, "source": source, "target": target,
                                      "status": "submitting", "error": "", "answer": ""})
        save(OUTBOX_PREFIX, record)
        await _publish(record, "{} request sent to {} session(s): {}".format(action.capitalize(), len(targets), text[:400]))
        async def start(row):
            payload = {"id": row["id"], "source": source, "source_title": record["source_title"],
                       "target": row["target"], "action": action, "text": text,
                       "deadline": record["deadline"], "expected_turn_id": expected.get(row["target"], "")}
            try:
                return await _call_ref(row["target"], "request", payload, data)
            except Exception as exc:
                # A timeout may follow a successful remote enqueue. Keep the
                # client-chosen id and poll it; never issue another prompt.
                refused = isinstance(exc, links.SessionLinkError) or 400 <= getattr(exc, "status", 0) < 500
                return dict(row, status="rejected" if refused else "unconfirmed", error=str(exc)[:4000])
        record["results"] = list(await asyncio.gather(*(start(row) for row in record["results"])))
        save(OUTBOX_PREFIX, record)
        return _public(record)


async def refresh(record, cancel=False):
    lock = _locks.setdefault("out:" + record["id"], asyncio.Lock())
    async with lock:
        return await _refresh(get(OUTBOX_PREFIX, record["id"]), cancel)


async def _refresh(record, cancel=False):
    # Persist cancellation before contacting destinations. A disconnected node
    # must receive it after reconnection even if the caller's turn has ended.
    cancel = cancel or record["status"] == "cancelling"
    if cancel and record["status"] not in TERMINAL:
        record["status"] = "cancelling"
        save(OUTBOX_PREFIX, record)
    data = await links.catalog()
    previous = [row["status"] for row in record["results"]]
    async def one(row):
        if row["status"] in TERMINAL:
            return row
        try:
            return await _call_ref(row["target"], "request_cancel" if cancel else "request_status",
                                   {"id": row["id"], "source": record["source"]}, data)
        except Exception as exc:
            status = "expired" if time.time() >= record["deadline"] else row["status"]
            return dict(row, status=status, error=str(exc))
    record["results"] = list(await asyncio.gather(*(one(row) for row in record["results"])))
    if record["status"] not in TERMINAL and all(row["status"] in TERMINAL for row in record["results"]):
        record["status"] = "completed" if all(row["status"] == "completed" for row in record["results"]) else "cancelled" if cancel else "failed"
    if record["status"] not in TERMINAL and previous != [row["status"] for row in record["results"]]:
        await _publish(record, "Session request progress: " + "; ".join(
            row["target"] + ": " + row["status"] for row in record["results"])[:1700])
    if record["status"] in TERMINAL and not record["notified"]:
        description = "; ".join("{}: {}".format(row["target"], row["status"]) for row in record["results"])
        record["notified"] = await _publish(record, "Session request {}. {}".format(record["status"], description)[:1900])
    save(OUTBOX_PREFIX, record)
    return _public(record)


async def operate(source, method, args, scope=None):
    if method == "send":
        return await send(source, args, scope)
    if method == "requests":
        return {"requests": [_public(r) for r in records(OUTBOX_PREFIX) if r["source"] == source][-100:]}
    rid = args.get("id")
    record = get(OUTBOX_PREFIX, rid)
    if record["source"] != source:
        raise links.SessionLinkError("request belongs to another session")
    if method == "cancel":
        return await refresh(record, cancel=True)
    if method == "wait":
        wait_s = links.integer(args.get("wait_s", 20), "wait_s", 0, 25)
        until = time.monotonic() + wait_s
        while True:
            record = get(OUTBOX_PREFIX, rid)
            result = await refresh(record)
            if result["status"] in TERMINAL or time.monotonic() >= until:
                return result
            await asyncio.sleep(min(1, max(0, until - time.monotonic())))
    raise links.SessionLinkError("unknown session request operation")


INBOX_KEYS = {"format", "id", "source", "source_title", "target", "action", "text", "deadline", "expected_turn_id", "created_at", "status", "prompt", "turn_id", "start_seq", "end_seq", "answer", "error"}
OUTBOX_KEYS = {"format", "id", "source", "source_title", "signature", "action", "text", "targets", "created_at", "deadline", "status", "results", "unavailable", "workflow", "notified"}


def validate_persisted(connection):
    for prefix, keys in ((INBOX_PREFIX, INBOX_KEYS), (OUTBOX_PREFIX, OUTBOX_KEYS)):
        for row in connection.execute("SELECT value FROM meta WHERE key GLOB ?", (prefix + "*",)):
            value = json.loads(row[0])
            if not isinstance(value, dict) or set(value) != keys or type(value["format"]) is not int or value["format"] != 1:
                raise links.SessionLinkError("session communication state is not current")
            _id(value["id"])
            links.parse_ref(value["source"])
            if value["action"] not in ("question", "task", "steer", "stop") or value["status"] not in TERMINAL | {"submitting", "queued", "running", "answering", "held", "pending", "unconfirmed", "cancelling"}:
                raise links.SessionLinkError("invalid session action or status")
            for name in ("created_at", "deadline"):
                if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] <= 0:
                    raise links.SessionLinkError("invalid session request timestamp")
            for name, maximum in (("text", 120000), ("source_title", 300)):
                if not isinstance(value[name], str) or len(value[name]) > maximum:
                    raise links.SessionLinkError("invalid session request text")
            if prefix == INBOX_PREFIX:
                links.parse_ref(value["target"])
                for name in ("start_seq", "end_seq"):
                    links.integer(value[name], name, 0, 10 ** 16)
                for name, maximum in (("prompt", 122000), ("answer", 121000), ("error", 10000), ("turn_id", 128), ("expected_turn_id", 128)):
                    if not isinstance(value[name], str) or len(value[name]) > maximum:
                        raise links.SessionLinkError("invalid session receipt")
            else:
                if not isinstance(value["targets"], list) or not 1 <= len(value["targets"]) <= 512 or len(set(value["targets"])) != len(value["targets"]):
                    raise links.SessionLinkError("invalid session destinations")
                for target in value["targets"]:
                    links.parse_ref(target)
                signature = value["signature"]
                if not isinstance(signature, dict) or set(signature) != {"refs", "action", "text", "timeout_s", "expected_turn_ids"} or \
                        signature["action"] != value["action"] or signature["text"] != value["text"] or \
                        type(value["notified"]) is not bool or not isinstance(value["workflow"], str) or \
                        (value["workflow"] and not re.fullmatch(r"[a-f0-9]{32}", value["workflow"])) or not isinstance(value["unavailable"], list):
                    raise links.SessionLinkError("invalid outgoing session request")
                links.integer(signature["timeout_s"], "timeout_s", 30, 7200)
                if not isinstance(signature["refs"], list) or not signature["refs"]:
                    raise links.SessionLinkError("invalid request selection")
                if signature["refs"] != ["all"]:
                    for ref in signature["refs"]:
                        links.parse_ref(ref)
                if not isinstance(signature["expected_turn_ids"], dict) or any(not isinstance(v, str) or len(v) > 128 for v in signature["expected_turn_ids"].values()):
                    raise links.SessionLinkError("invalid target turn ids")
                if not isinstance(value["results"], list) or len(value["results"]) != len(value["targets"]):
                    raise links.SessionLinkError("invalid session request results")
                for result, target in zip(value["results"], value["targets"]):
                    if not isinstance(result, dict) or set(result) not in (
                            {"id", "source", "target", "status", "error", "answer"},
                            {"id", "source", "target", "action", "status", "created_at", "deadline", "turn_id", "start_seq", "end_seq", "answer", "error"}) or \
                            result["target"] != target or result["source"] != value["source"] or \
                            result["status"] not in TERMINAL | {"submitting", "queued", "running", "answering", "held", "unconfirmed"} or \
                            not isinstance(result["answer"], str) or not isinstance(result["error"], str):
                        raise links.SessionLinkError("invalid session request result")
                    _id(result["id"])


async def worker():
    while True:
        try:
            if links._app and links._app.get("puppy_snapshot_busy"):
                await asyncio.sleep(2)
                continue
            for value in active_records(INBOX_PREFIX):
                if value["status"] not in TERMINAL:
                    await inbox_status({"id": value["id"], "source": value["source"]})
            pending = [value for value in active_records(OUTBOX_PREFIX)
                       if value["status"] not in TERMINAL or not value["notified"]]
            await asyncio.gather(*(refresh(value) for value in pending), return_exceptions=True)
        except Exception:
            links.log.exception("session request reconciliation failed")
        await asyncio.sleep(2)
