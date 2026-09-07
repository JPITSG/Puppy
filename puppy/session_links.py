"""Session references and the controller-owned channel between session nodes.

References use node UUIDs, never mutable names or controller-local backend ids.
The controller opens the relay; a backend never receives another node's token.
Only the stdio bridge authenticates a live originating turn. The node HTTP
surface is also available to authenticated consoles and paired controllers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

from aiohttp import web, WSMsgType, ClientTimeout

from puppy import backends, config, db, runner, search, session_aliases

log = logging.getLogger("puppy.session_links")
CAPABILITY = "session-references"
REF_RE = re.compile(r"^[a-f0-9]{32}/[1-9][0-9]{0,15}$")
MENTION_RE = re.compile(
    r"(?<!\w)@session\s+(?:([a-f0-9]{32}):)?"
    r"(all|[a-f0-9]{32}/[1-9][0-9]{0,15})(?![\w/])", re.I)
MAX_FRAME = 1024 * 1024
_app = None
_relays = {}                 # controller UUID -> backend end of its socket
_pending = {}                # relay request UUID -> future
_workers = {}                # backend id -> controller socket task
_scopes = {}                 # (session id, turn id) -> current selection
_operations = set()          # accepted durable submissions outlive tool waits
_catalog_task = None
_catalog_value = None
_catalog_until = 0.0


class SessionLinkError(ValueError):
    pass


def load_record(key):
    """Missing means uninitialized; malformed persisted state is never absent."""
    row = db.query_one("SELECT value FROM meta WHERE key=?", (key,))
    if row is None:
        return None
    try:
        value = json.loads(row["value"])
    except (ValueError, TypeError) as exc:
        raise SessionLinkError("session state is not current") from exc
    if not isinstance(value, dict) or type(value.get("format")) is not int or value["format"] != 1:
        raise SessionLinkError("session state is not current")
    if key.startswith("session_references."):
        return validate_scope(value)
    from puppy import session_actions, session_coordination
    shapes = {session_actions.INBOX_PREFIX: session_actions.INBOX_KEYS,
              session_actions.OUTBOX_PREFIX: session_actions.OUTBOX_KEYS,
              session_coordination.PREFIX: session_coordination.KEYS}
    for prefix, keys in shapes.items():
        if key.startswith(prefix) and set(value) != keys:
            raise SessionLinkError("session state is not current")
    return value


def integer(value, name, low=0, high=1000000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SessionLinkError("{} must be an integer from {} to {}".format(name, low, high))
    return value


def reference(sid, node=None):
    return "{}/{}".format(node or db.node_uuid(), sid)


def parse_ref(value):
    if not isinstance(value, str) or not REF_RE.fullmatch(value):
        raise SessionLinkError("use a session reference returned by sessions")
    node, sid = value.split("/")
    return node, int(sid)


def validate_scope(value):
    if not isinstance(value, dict) or set(value) != {"format", "controller", "refs"} or \
            type(value["format"]) is not int or value["format"] != 1 or not isinstance(value["controller"], str) or \
            not re.fullmatch(r"[a-f0-9]{32}", value["controller"]):
        raise SessionLinkError("session reference state is not current")
    refs = value["refs"]
    if not isinstance(refs, list) or not refs or len(refs) > 512 or \
            len(set(str(x) for x in refs)) != len(refs):
        raise SessionLinkError("invalid session reference selection")
    if refs != ["all"]:
        for item in refs:
            parse_ref(item)
    return value


async def prepare_turn(sid, turn_id, text):
    short = list(session_aliases.MENTION_RE.finditer(text or ""))
    matches = list(MENTION_RE.finditer(text or ""))
    key = "session_references.{}".format(sid)
    scope = load_record(key)
    if matches or short:
        controllers = {m[1].lower() for m in matches if m[1]}
        if len(controllers) > 1:
            raise SessionLinkError("reference sessions through one console at a time")
        if controllers:
            controller = next(iter(controllers))
        elif short and (_app is None or _app.get("puppy_role") == "full"):
            controller = db.node_uuid()
        elif short and _app and _app.get("puppy_role") == "backend" and len(_relays) > 1:
            raise SessionLinkError("several consoles are connected; short session IDs are ambiguous, use an explicit UUID reference")
        elif scope is not None:
            controller = validate_scope(scope)["controller"]
        elif _app and _app.get("puppy_role") == "backend" and _relays:
            if len(_relays) != 1:
                raise SessionLinkError("several consoles are connected; choose sessions using the mention picker")
            controller = next(iter(_relays))
        else:
            controller = db.node_uuid()
        refs = list(dict.fromkeys(m[2].lower() for m in matches))
        if short:
            codes = list(dict.fromkeys(m[2].upper() for m in short))
            if controller == db.node_uuid():
                refs.extend(session_aliases.resolve(codes))
            else:
                # Resolve on the selecting controller, never against this
                # backend's independent namespace. No scope is persisted on failure.
                resolved = await _controller_call(sid, turn_id, controller, "resolve",
                    {"codes": codes}, {"format": 1, "controller": controller, "refs": ["all"]})
                resolved = validate_scope(resolved)
                if resolved["controller"] != controller:
                    raise SessionLinkError("wrong reference controller")
                refs.extend(resolved["refs"])
        refs = list(dict.fromkeys(refs))
        scope = {"format": 1, "controller": controller,
                 "refs": ["all"] if "all" in refs else refs}
        validate_scope(scope)
        db.meta_set(key, scope)
    if scope is not None:
        validate_scope(scope)
    _scopes[(sid, turn_id)] = scope


def end_turn(sid, turn_id):
    _scopes.pop((sid, turn_id), None)


def session_info(session):
    value = runner.session_payload(session)
    hub = runner._hubs.get(session["id"])
    return {"ref": reference(session["id"]), "id": session["id"],
            "title": session["name"] or "Session {}".format(session["id"]),
            "node": db.node_uuid(), "node_name": config.get("instance_name") or "Puppy",
            "cwd": value.get("cwd", ""), "engine": session["engine"],
            "status": hub.status if hub else "idle", "archived": bool(session["archived"]),
            "updated_at": session["updated_at"],
            "turn_id": hub._active_turn_id if hub else "",
            "side_question": hub.side_question_state(session) if hub else {},
            "steering": hub.steering_state(session) if hub else {}}


def source_link(ref, seq=0):
    return "/#session={}&seq={}".format(ref, seq)


def _events(sid, args):
    limit = integer(args.get("limit", 40), "limit", 1, 100)
    after = args.get("after_seq")
    before = args.get("before_seq")
    if after is not None:
        integer(after, "after_seq")
    if before is not None:
        integer(before, "before_seq", 1)
    if after is not None and before is not None:
        raise SessionLinkError("use either after_seq or before_seq")
    rows = db.get_events(sid, after_seq=after, before_seq=before, limit=limit)
    # One huge tool result must remain readable through an explicit character
    # cursor; never silently cut a result and claim to have returned it all.
    offset = integer(args.get("offset", 0), "offset", 0, 100000000)
    if offset and limit != 1:
        raise SessionLinkError("character offset requires limit=1")
    budget = 120000
    result = []
    for row in rows:
        payload = row["data"]
        if row["kind"] == "info" and payload.get("subtype") == "session_task_archive":
            # the folded task reads in full through the character cursor
            from puppy import session_tasks
            raw = search._scrub(session_tasks.archive_text(payload))
        else:
            raw = search._scrub(search._text_of(payload.get("content", payload)))
        content = raw[offset:offset + min(budget, 16000)]
        item = {"seq": row["seq"], "kind": row["kind"], "ts": row["ts"],
                "text": content, "offset": offset,
                "next_offset": offset + len(content) if offset + len(content) < len(raw) else None,
                "source": source_link(reference(sid), row["seq"])}
        result.append(item)
        budget -= len(content)
        offset = 0
        if budget <= 0:
            break
    return {"events": result, "first_seq": result[0]["seq"] if result else None,
            "last_seq": result[-1]["seq"] if result else None,
            "read_at": time.time(), "page_full": len(rows) == limit or len(result) < len(rows)}


async def node_call(method, args):
    if method in ("request", "request_status", "request_cancel", "notice"):
        from puppy import session_actions
        return await session_actions.node_call(method, args)
    if method == "sessions":
        return {"node": db.node_uuid(), "sessions": [session_info(s) for s in db.list_sessions(True)]}
    if method in ("read", "search"):
        refs = args.get("refs")
        if not isinstance(refs, list) or not refs or len(refs) > 512:
            raise SessionLinkError("select between 1 and 512 session references")
        sessions = []
        for ref in refs:
            node, sid = parse_ref(ref)
            if node != db.node_uuid():
                raise SessionLinkError("reference belongs to a different node")
            session = db.get_session(sid)
            if session is None:
                raise SessionLinkError("session {} was deleted".format(ref))
            sessions.append(session)
        if method == "read":
            if len(sessions) != 1:
                raise SessionLinkError("read one session per call; search accepts several")
            return dict(_events(sessions[0]["id"], args), session=session_info(sessions[0]))
        query = args.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= search.MAX_QUERY_CHARS:
            raise SessionLinkError("supply a search query of 1 to 400 characters")
        offset = integer(args.get("offset", 0), "offset")
        limit = integer(args.get("limit", 5), "limit", 1, 100)
        session_offset = integer(args.get("session_offset", 0), "session_offset")
        session_limit = integer(args.get("session_limit", 20), "session_limit", 1, 50)
        page = sessions[session_offset:session_offset + session_limit]
        kinds = args.get("kinds", ["user", "assistant", "tool", "info", "title"])
        if not isinstance(kinds, list) or not kinds or any(x not in search.KINDS for x in kinds):
            raise SessionLinkError("invalid search kinds")
        def run():
            # Per-session queries enforce the selected boundary before search,
            # retain pagination, and reuse the existing tolerant query parser.
            found = []
            for session in page:
                payload = search.query_index(query, session_id=session["id"],
                                             kinds=kinds, limit=limit, offset=offset)
                for match in payload["matches"]:
                    match["source"] = source_link(reference(session["id"]), match["seq"])
                if payload["total"]:
                    found.append({"session": session_info(session), **payload})
            return {"node": db.node_uuid(), "results": found, "searched": len(page),
                    "selected": len(sessions), "read_at": time.time(),
                    "next_session_offset": session_offset + len(page) if session_offset + len(page) < len(sessions) else None}
        return await asyncio.get_running_loop().run_in_executor(None, run)
    raise SessionLinkError("unknown session operation")


async def remote_call(bid, method, args):
    if bid == 0:
        return await node_call(method, args)
    channel = backends.node_channel(bid)
    if channel is None:
        raise SessionLinkError("node is offline")
    if CAPABILITY not in channel["capabilities"]:
        raise SessionLinkError("node does not support session references")
    from puppy.workspace_links import _request_json
    return await asyncio.wait_for(_request_json(
        channel, "POST", "session-links/node", {"method": method, "params": args}), 12)


async def durable_call(awaitable):
    if len(_operations) >= 64:
        awaitable.close()
        raise SessionLinkError("too many concurrent session operations; retry shortly")
    task = asyncio.create_task(awaitable)
    _operations.add(task)
    def done(finished):
        _operations.discard(finished)
        if not finished.cancelled():
            finished.exception()  # retrieve failures even after a caller disconnects
    task.add_done_callback(done)
    return await asyncio.shield(task)


async def _load_catalog():
    nodes = [(0, config.get("instance_name") or "Puppy")]
    if _app is not None and _app.get("puppy_role") == "full":
        nodes.extend((b["id"], b["name"]) for b in backends.list_backends())
    values = await asyncio.gather(*(remote_call(bid, "sessions", {}) for bid, _ in nodes),
                                  return_exceptions=True)
    sessions, unavailable, seen = [], [], set()
    for (bid, name), value in zip(nodes, values):
        if isinstance(value, Exception):
            unavailable.append({"node": name, "error": str(value)})
            continue
        try:
            if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(value.get("node", ""))) or not isinstance(value.get("sessions"), list):
                raise SessionLinkError("invalid node session catalogue")
            for item in value["sessions"]:
                if not isinstance(item, dict):
                    raise SessionLinkError("invalid node session catalogue")
                node, sid = parse_ref(item.get("ref"))
                if node != value["node"] or type(item.get("id")) is not int or item["id"] != sid or any(
                        not isinstance(item.get(key), str) for key in ("title", "cwd", "engine", "status")):
                    raise SessionLinkError("invalid node session catalogue")
        except SessionLinkError as exc:
            unavailable.append({"node": name, "error": str(exc)})
            continue
        for item in value["sessions"]:
            if item["ref"] in seen:
                continue
            seen.add(item["ref"])
            sessions.append(dict(item, bid=bid, node_name=name))
    aliases = session_aliases.allocate(["all"] + [row["ref"] for row in sessions])
    for row in sessions:
        row["short_id"] = aliases[row["ref"]]
        row["mention"] = session_aliases.mention(row["title"], row["short_id"])
    return {"controller": db.node_uuid(), "sessions": sessions, "unavailable": unavailable,
            "all_mention": session_aliases.mention("All", aliases["all"])}


def invalidate_catalog(_sid=None):
    global _catalog_until
    _catalog_until = 0.0


async def catalog(force=False):
    """Coalesce fleet reads made by concurrently progressing requests."""
    global _catalog_task, _catalog_value, _catalog_until
    if _app is None:
        return await _load_catalog()
    if not force and _catalog_value is not None and time.monotonic() < _catalog_until:
        return _catalog_value
    if _catalog_task is None:
        _catalog_task = asyncio.create_task(_load_catalog())
    task = _catalog_task
    try:
        value = await asyncio.shield(task)
        _catalog_value = value
        _catalog_until = time.monotonic() + 2
        return value
    finally:
        if task.done() and _catalog_task is task:
            _catalog_task = None


async def broker(source, method, args, scope=None):
    """One controller's view of its fleet, also used by remote-origin calls."""
    if not isinstance(args, dict):
        raise SessionLinkError("tool arguments must be an object")
    if method == "resolve":
        return {"format": 1, "controller": db.node_uuid(),
                "refs": session_aliases.resolve(args.get("codes"))}
    if method in ("send", "wait", "cancel", "requests"):
        from puppy import session_actions
        operation = session_actions.operate(source, method, args, scope)
        return await durable_call(operation) if method in ("send", "cancel") else await operation
    if method in ("coordinate", "workflow", "workflows", "cancel_workflow"):
        from puppy import session_coordination
        operation = session_coordination.operate(source, method, args, scope)
        return await durable_call(operation) if method in ("coordinate", "cancel_workflow") else await operation
    if method not in ("sessions", "read", "search"):
        raise SessionLinkError("unknown session operation")
    data = await catalog()
    available = {row["ref"]: row for row in data["sessions"] if row["ref"] != source}
    if method == "sessions":
        query = str(args.get("query") or "").lower()
        offset = integer(args.get("offset", 0), "offset")
        limit = integer(args.get("limit", 100), "limit", 1, 200)
        rows = [s for s in available.values() if query in
                (s["title"] + " " + s["cwd"] + " " + s["node_name"] + " " + s["mention"]).lower()]
        return {"controller": data["controller"], "sessions": rows[offset:offset + limit],
                "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None,
                "unavailable": data["unavailable"]}
    refs = args.get("refs", (scope or {}).get("refs"))
    if refs == "all":
        refs = ["all"]
    if not isinstance(refs, list) or not refs or len(refs) > 512:
        raise SessionLinkError("select sessions with @Session or supply refs from sessions")
    if refs == ["all"]:
        refs = list(available)
    for ref in refs:
        parse_ref(ref)
        if ref == source:
            raise SessionLinkError("select another session")
        if scope and scope["refs"] != ["all"] and ref not in scope["refs"]:
            raise SessionLinkError("session is outside the selected references")
    groups = {}
    errors = list(data["unavailable"])
    for ref in dict.fromkeys(refs):
        row = available.get(ref)
        if row is None:
            errors.append({"ref": ref, "error": "session is deleted, offline, or unavailable"})
        else:
            groups.setdefault(row["bid"], []).append(ref)
    if method == "read" and len(refs) != 1:
        raise SessionLinkError("read requires exactly one selected reference")
    values = await asyncio.gather(*(remote_call(bid, method, dict(args, refs=items))
                                    for bid, items in groups.items()), return_exceptions=True)
    results = []
    for (bid, items), value in zip(groups.items(), values):
        if isinstance(value, Exception):
            errors.append({"refs": items, "error": str(value)})
        else:
            results.append(value)
    return {"results": results, "unavailable": errors, "selected": len(refs),
            "next_session_offset": max((value.get("next_session_offset") or 0 for value in results), default=0) or None}


async def dispatch(sid, turn_id, method, args):
    scope = _scopes.get((sid, turn_id))
    controller = (scope or {}).get("controller", db.node_uuid())
    return await _controller_call(sid, turn_id, controller, method, args, scope)


async def _controller_call(sid, turn_id, controller, method, args, scope):
    if controller == db.node_uuid():
        return await broker(reference(sid), method, args, scope)
    ws = _relays.get(controller)
    if ws is None or ws.closed:
        raise SessionLinkError("the console owning these references is disconnected")
    rid = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[rid] = (controller, future)
    try:
        await ws.send_json({"id": rid, "sid": sid, "turn_id": turn_id,
                            "method": method, "params": args, "scope": scope})
        return await asyncio.wait_for(future, 40)
    finally:
        _pending.pop(rid, None)


async def h_node(request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"method", "params"} or not isinstance(body["params"], dict):
            raise SessionLinkError("invalid session operation")
        return web.json_response(await node_call(body["method"], body["params"]))
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def h_catalog(request):
    try:
        return web.json_response(await catalog(force=True))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)


async def h_resolve(request):
    try:
        return web.json_response({"refs": session_aliases.resolve([request.query.get("code", "")])})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def h_action(request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"source", "method", "params"}:
            raise SessionLinkError("invalid session action")
        parse_ref(body["source"])
        result = await broker(body["source"], body["method"], body["params"])
        return web.json_response(result)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def h_relay(request):
    controller = request.query.get("controller", "")
    if not re.fullmatch(r"[a-f0-9]{32}", controller) or controller == db.node_uuid():
        raise web.HTTPBadRequest(text="invalid controller identity")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME)
    await ws.prepare(request)
    from puppy import live_websockets
    live_websockets.track(request, ws)
    previous = _relays.get(controller)
    _relays[controller] = ws
    if previous is not None:
        await previous.close()
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            value = json.loads(message.data)
            record = _pending.get(value.get("id"))
            if record and record[0] == controller and not record[1].done():
                if "error" in value:
                    record[1].set_exception(SessionLinkError(str(value["error"])))
                else:
                    record[1].set_result(value.get("result"))
    finally:
        if _relays.get(controller) is ws:
            _relays.pop(controller, None)
            for owner, future in list(_pending.values()):
                if owner == controller and not future.done():
                    future.set_exception(SessionLinkError("session console disconnected"))
    return ws


async def _serve_relay(bid, channel):
    jobs = set()
    async def answer(ws, data):
        rid = data.get("id")
        try:
            sid = integer(data.get("sid"), "sid", 1)
            scope = validate_scope(data.get("scope"))
            if scope["controller"] != db.node_uuid():
                raise SessionLinkError("wrong reference controller")
            local = await remote_call(bid, "sessions", {})
            origin = next((row for row in local["sessions"] if row["id"] == sid), None)
            if not origin or origin["status"] != "running" or origin["turn_id"] != data.get("turn_id"):
                raise SessionLinkError("the originating turn is no longer active")
            result = await broker(reference(sid, local["node"]), data["method"], data["params"], scope)
            payload = {"id": rid, "result": result}
        except Exception as exc:
            payload = {"id": rid, "error": str(exc)}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > MAX_FRAME:
            encoded = json.dumps({"id": rid, "error": "result is too large; narrow the selection or page size"})
        if not ws.closed:
            await ws.send_str(encoded)
    try:
        async with backends.client().ws_connect(
                channel["urls"][0] + "/api/ws/session-links?controller=" + db.node_uuid(),
                headers={"X-Puppy-Token": channel["token"]}, ssl=channel["ssl"],
                heartbeat=20, max_msg_size=MAX_FRAME) as ws:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                if len(jobs) >= 32:
                    await ws.close()
                    break
                data = json.loads(message.data)
                job = asyncio.create_task(answer(ws, data))
                jobs.add(job)
                job.add_done_callback(jobs.discard)
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


async def _relay_worker():
    while True:
        wanted = {}
        for backend in backends.list_backends():
            channel = backends.node_channel(backend["id"])
            if channel and CAPABILITY in channel["capabilities"]:
                wanted[backend["id"]] = channel
        for bid, (channel, task) in list(_workers.items()):
            if wanted.get(bid) != channel or task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                _workers.pop(bid, None)
        for bid, channel in wanted.items():
            if bid not in _workers:
                _workers[bid] = (channel, asyncio.create_task(_serve_relay(bid, channel)))
        await asyncio.sleep(5)


def validate_persisted(connection):
    session_aliases.reservations(connection)
    for row in connection.execute("SELECT value FROM meta WHERE key GLOB 'session_references.*'"):
        validate_scope(json.loads(row[0]))
    from puppy import session_actions
    session_actions.validate_persisted(connection)
    from puppy import session_coordination
    session_coordination.validate_persisted(connection)


async def _lifecycle(app):
    global _app, _catalog_task, _catalog_value, _catalog_until
    _app = app
    _catalog_value, _catalog_until = None, 0.0
    validate_persisted(db.connect())
    from puppy import session_agent
    await session_agent.start(app)
    from puppy import session_actions
    session_actions.restore_tracking()
    action_task = asyncio.create_task(session_actions.worker())
    from puppy import session_coordination
    session_coordination.restore_tracking()
    coordination_task = asyncio.create_task(session_coordination.worker())
    task = asyncio.create_task(_relay_worker()) if app.get("puppy_role") == "full" else None
    try:
        yield
    finally:
        action_task.cancel()
        coordination_task.cancel()
        await asyncio.gather(action_task, coordination_task, return_exceptions=True)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        tasks = [value[1] for value in _workers.values()]
        for worker in tasks:
            worker.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _workers.clear()
        for ws in list(_relays.values()):
            await ws.close()
        for operation in list(_operations):
            operation.cancel()
        await asyncio.gather(*list(_operations), return_exceptions=True)
        await session_agent.stop(app)
        if _catalog_task is not None:
            _catalog_task.cancel()
            await asyncio.gather(_catalog_task, return_exceptions=True)
            _catalog_task = None
        _catalog_value, _catalog_until = None, 0.0
        _scopes.clear()
        _app = None


def register(app):
    app.router.add_post("/api/session-links/node", h_node)
    app.router.add_get("/api/session-links/catalog", h_catalog)
    app.router.add_get("/api/session-links/resolve", h_resolve)
    app.router.add_post("/api/session-links/action", h_action)
    app.router.add_get("/api/ws/session-links", h_relay)
    app.cleanup_ctx.append(_lifecycle)
    db.add_change_listener(invalidate_catalog)
