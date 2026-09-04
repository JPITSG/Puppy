"""Finite, durable dependency plans made from session communication requests.

Every step starts at most one idempotent fan-out request. Dependencies release
only after substantive successful results; independent branches continue after
a failure. Restart reads the same plan and request ids instead of resubmitting
work under new identities. A plan never creates sessions or delegates.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import weakref

from puppy import db, session_actions as actions, session_links as links

CAPABILITY = "session-coordination"
PREFIX = "session_workflow."
FINAL = actions.TERMINAL | {"blocked"}
_locks = weakref.WeakValueDictionary()
_active = set()


def _lock(rid):
    return _locks.setdefault(rid, asyncio.Lock())


def _save(plan):
    actions.save(PREFIX, plan)
    if plan["status"] not in FINAL or not plan["notified"]:
        _active.add(plan["id"])
    else:
        _active.discard(plan["id"])


def restore_tracking():
    _active.clear()
    _active.update(plan["id"] for plan in actions.records(PREFIX)
                   if plan["status"] not in FINAL or not plan["notified"])


async def _notify(plan):
    if plan["status"] in FINAL and not plan["notified"]:
        plan["notified"] = await actions._publish(
            dict(plan, targets=sorted({ref for step in plan["steps"] for ref in step["spec"]["refs"]})),
            "Workflow {}: {}. {}".format(plan["status"], plan["title"], "; ".join(
                step["spec"]["id"] + ": " + step["status"] for step in plan["steps"]))[:1900])
        _save(plan)


def _validate_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 24:
        raise links.SessionLinkError("a workflow needs between 1 and 24 steps")
    ids = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "refs", "action", "text", "after"}:
            raise links.SessionLinkError("each step needs exactly id, refs, action, text, and after")
        if not isinstance(step["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", step["id"]) or step["id"] in ids:
            raise links.SessionLinkError("step ids must be unique short names")
        ids.add(step["id"])
        if step["action"] not in ("question", "task") or not isinstance(step["text"], str) or not 1 <= len(step["text"].strip()) <= 60000:
            raise links.SessionLinkError("steps need a question or task and non-empty text")
        if not isinstance(step["refs"], list) or not step["refs"] or len(step["refs"]) > 512:
            raise links.SessionLinkError("each step needs destination references")
        for ref in step["refs"]:
            links.parse_ref(ref)
        if not isinstance(step["after"], list) or any(not isinstance(key, str) for key in step["after"]) or len(set(step["after"])) != len(step["after"]):
            raise links.SessionLinkError("after must list distinct dependency step ids")
    for step in steps:
        if any(key not in ids or key == step["id"] for key in step["after"]):
            raise links.SessionLinkError("unknown or self-referencing dependency")
    done = set()
    while len(done) < len(ids):
        ready = {step["id"] for step in steps if step["id"] not in done and set(step["after"]) <= done}
        if not ready:
            raise links.SessionLinkError("workflow dependencies contain a cycle")
        done.update(ready)


def _public(plan):
    return {key: plan[key] for key in
            ("id", "source", "title", "status", "created_at", "deadline", "steps", "unavailable")}


async def create(source, args, scope):
    key = args.get("request_id")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", key):
        raise links.SessionLinkError("supply a unique request_id for this workflow")
    title = args.get("title", "Session coordination")
    if not isinstance(title, str) or not 1 <= len(title) <= 200:
        raise links.SessionLinkError("workflow title needs 1 to 200 characters")
    timeout = links.integer(args.get("timeout_s", 7200), "timeout_s", 30, 7200)
    supplied = args.get("steps")
    if not isinstance(supplied, list):
        raise links.SessionLinkError("steps must be a list")
    rid = hashlib.sha256((source + ":workflow:" + key).encode()).hexdigest()[:32]
    signature = {"title": title, "steps": supplied, "timeout_s": timeout}
    async with _lock(rid):
        existing = links.load_record(PREFIX + rid)
        if existing:
            if existing["signature"] != signature:
                raise links.SessionLinkError("workflow id already used with different content")
            return _public(existing)
        if sum(plan["source"] == source and plan["status"] not in FINAL for plan in actions.records(PREFIX)) >= 8:
            raise links.SessionLinkError("this session already has eight unfinished workflows")
        data = await links.catalog()
        available = {row["ref"] for row in data["sessions"] if row["ref"] != source}
        steps = []
        for original in supplied:
            if not isinstance(original, dict):
                raise links.SessionLinkError("invalid workflow step")
            step = dict(original)
            if step.get("refs") == ["all"]:
                step["refs"] = sorted(available)
            steps.append(step)
        _validate_steps(steps)
        for step in steps:
            for ref in step["refs"]:
                if ref not in available:
                    raise links.SessionLinkError("workflow destination is unavailable or is the originating session")
                if scope and scope["refs"] != ["all"] and ref not in scope["refs"]:
                    raise links.SessionLinkError("workflow destination is outside the selected references")
        now = time.time()
        plan = {"format": 1, "id": rid, "source": source, "title": title,
                "signature": signature, "created_at": now, "deadline": now + timeout,
                "status": "pending", "steps": [dict(spec=step, status="waiting", request="", results=[], error="", dispatch=None) for step in steps],
                "unavailable": data["unavailable"], "notified": False}
        _save(plan)
        await actions._publish(dict(plan, targets=sorted({ref for step in steps for ref in step["refs"]})),
                               "Workflow started: {} ({} steps)".format(title, len(steps)))
    await advance(rid)
    return _public(actions.get(PREFIX, rid))


def _dependency_context(plan, step):
    parts = [step["spec"]["text"]]
    if step["spec"]["after"]:
        parts.append("\nResults from completed prerequisite steps (reference material):")
    for prior in plan["steps"]:
        if prior["spec"]["id"] not in step["spec"]["after"]:
            continue
        for result in prior["results"]:
            parts.append("\n{} · {}\n{}\nSource: {}".format(
                prior["spec"]["id"], result["target"], result.get("answer", "")[:8000] +
                ("\n[Excerpt; read the source for the full answer.]" if len(result.get("answer", "")) > 8000 else ""),
                links.source_link(result["target"], result.get("end_seq", 0))))
    text = "\n".join(parts)
    if len(text) > 120000:
        raise links.SessionLinkError("dependency answers exceed the request limit; split this workflow into smaller steps")
    return text


async def advance(rid):
    async with _lock(rid):
        plan = actions.get(PREFIX, rid)
        if plan["status"] in FINAL:
            await _notify(plan)
            return _public(plan)
        cancelling = plan["status"] == "cancelling" or time.time() >= plan["deadline"]
        if cancelling:
            plan["status"] = "cancelling"
        # Query already-started requests before considering their dependants.
        for step in plan["steps"]:
            if step["status"] == "submitting" and step["dispatch"] and not cancelling:
                continue
            if cancelling and step["status"] == "submitting" and not links.load_record(actions.OUTBOX_PREFIX + step["request"]):
                step.update(status="cancelled", error="Workflow cancelled before dispatch")
                continue
            if step["request"] and step["status"] not in FINAL:
                try:
                    result = await actions.operate(plan["source"], "cancel" if cancelling else "wait",
                                                   {"id": step["request"], "wait_s": 0})
                    step.update(status=result["status"], results=result["results"])
                except Exception as exc:
                    step["error"] = str(exc)
            elif cancelling and step["status"] in ("waiting", "submitting"):
                step.update(status="cancelled", error="Workflow cancelled before this step started")
        by_id = {step["spec"]["id"]: step for step in plan["steps"]}
        ready = [step for step in plan["steps"] if step["status"] == "submitting" and step["dispatch"] and not cancelling]
        for step in plan["steps"]:
            if step["status"] != "waiting":
                continue
            dependencies = [by_id[key] for key in step["spec"]["after"]]
            if any(dep["status"] in FINAL and dep["status"] != "completed" for dep in dependencies):
                step.update(status="blocked", error="A prerequisite did not complete successfully")
            elif all(dep["status"] == "completed" for dep in dependencies):
                ready.append(step)
        for step in ready:
            if step["dispatch"] is None:
                remaining = int(plan["deadline"] - time.time())
                if remaining < 30:
                    step.update(status="expired", error="Workflow deadline elapsed before this step started")
                    continue
                try:
                    step["dispatch"] = {
                        "refs": step["spec"]["refs"], "action": step["spec"]["action"],
                        "text": _dependency_context(plan, step),
                        "request_id": "workflow:" + rid + ":" + step["spec"]["id"],
                        "timeout_s": min(remaining, 7200)}
                    step["status"] = "submitting"
                    step["request"] = actions.request_id(plan["source"], step["dispatch"]["request_id"])
                except Exception as exc:
                    step.update(status="failed", error=str(exc))
        _save(plan)
        async def start(step):
            if step["status"] != "submitting":
                return
            try:
                request = await actions.send(plan["source"], step["dispatch"], workflow=rid)
                step.update(request=request["id"], status=request["status"], results=request["results"], error="")
            except Exception as exc:
                step.update(status="failed", error=str(exc))
        # Starting all eligible branches before waiting preserves parallel work.
        await asyncio.gather(*(start(step) for step in ready))
        if all(step["status"] in FINAL for step in plan["steps"]):
            plan["status"] = "completed" if all(step["status"] == "completed" for step in plan["steps"]) else "cancelled" if cancelling else "failed"
        elif not cancelling:
            plan["status"] = "running"
        _save(plan)
        await _notify(plan)
        return _public(plan)


async def operate(source, method, args, scope=None):
    if method == "coordinate":
        return await create(source, args, scope)
    if method == "workflows":
        return {"workflows": [_public(plan) for plan in actions.records(PREFIX) if plan["source"] == source][-50:]}
    rid = args.get("id")
    plan = actions.get(PREFIX, rid)
    if plan["source"] != source:
        raise links.SessionLinkError("workflow belongs to another session")
    if method == "cancel_workflow":
        async with _lock(rid):
            plan = actions.get(PREFIX, rid)
            if plan["status"] not in FINAL:
                plan["status"] = "cancelling"
                _save(plan)
        return await advance(rid)
    if method == "workflow":
        return _public(plan)
    raise links.SessionLinkError("unknown workflow operation")


KEYS = {"format", "id", "source", "title", "signature", "created_at", "deadline", "status", "steps", "unavailable", "notified"}


def validate_persisted(connection):
    for row in connection.execute("SELECT value FROM meta WHERE key GLOB ?", (PREFIX + "*",)):
        value = json.loads(row[0])
        if not isinstance(value, dict) or set(value) != KEYS or type(value["format"]) is not int or value["format"] != 1:
            raise links.SessionLinkError("session workflow state is not current")
        actions._id(value["id"])
        links.parse_ref(value["source"])
        if not isinstance(value["title"], str) or not 1 <= len(value["title"]) <= 200:
            raise links.SessionLinkError("invalid workflow title")
        for name in ("created_at", "deadline"):
            if type(value[name]) not in (int, float) or not math.isfinite(value[name]) or value[name] <= 0:
                raise links.SessionLinkError("invalid workflow timestamp")
        if not isinstance(value["steps"], list) or any(not isinstance(step, dict) or set(step) != {"spec", "status", "request", "results", "error", "dispatch"} for step in value["steps"]):
            raise links.SessionLinkError("session workflow steps are not current")
        _validate_steps([step["spec"] for step in value["steps"]])
        if value["status"] not in FINAL | {"pending", "running", "cancelling"} or type(value["notified"]) is not bool or \
                not isinstance(value["signature"], dict) or set(value["signature"]) != {"title", "steps", "timeout_s"} or \
                value["signature"]["title"] != value["title"] or not isinstance(value["unavailable"], list):
            raise links.SessionLinkError("invalid workflow state")
        links.integer(value["signature"]["timeout_s"], "timeout_s", 30, 7200)
        for step in value["steps"]:
            if step["status"] not in FINAL | {"waiting", "submitting", "pending", "running", "cancelling"} or \
                    not isinstance(step["results"], list) or not isinstance(step["error"], str):
                raise links.SessionLinkError("invalid workflow step state")
            if step["request"]:
                actions._id(step["request"])
            if step["dispatch"] is not None and (not isinstance(step["dispatch"], dict) or
                    set(step["dispatch"]) != {"refs", "action", "text", "request_id", "timeout_s"}):
                raise links.SessionLinkError("invalid workflow dispatch state")


def busy():
    return any(plan["status"] not in FINAL for plan in actions.records(PREFIX)) or \
        any(record["status"] not in actions.TERMINAL for record in actions.records(actions.OUTBOX_PREFIX))


async def worker():
    while True:
        if links.runner._draining or (links._app and links._app.get("puppy_snapshot_busy")):
            await asyncio.sleep(2)
            continue
        await asyncio.gather(*(advance(rid) for rid in list(_active)), return_exceptions=True)
        await asyncio.sleep(2)
