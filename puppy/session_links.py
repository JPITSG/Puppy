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

from puppy import backends, config, db, prompt_index, protocol, runner, search, \
    session_aliases, workers

log = logging.getLogger("puppy.session_links")
CAPABILITY = "session-references"
# What one read answers, sized so a page fits a model's tool result whole
# (the audit of 2026-11-26 found reads of 50-138 KB, a quarter of them past
# what Claude Code takes): the events view's page, one message's share of it,
# and the turns view - the default - with its prompts and final answers.
EVENTS_PAGE = 20
EVENT_CHARS = 8000
EVENTS_BUDGET = 40000
TURNS_PAGE = 20
TURNS_MAX = 50
TURN_PROMPT_CHARS = 2000
TURN_ANSWER_CHARS = 4000
TURNS_BUDGET = 40000
# Answers of requests and workflows as an agent reads them: each at most so
# long, all of a result's together at most so long, a long one as its start
# and its end around what was left out.
AGENT_ANSWER_CHARS = 6000
AGENT_ANSWERS_BUDGET = 40000
AGENT_LISTED = 50
REF_RE = re.compile(r"^[a-f0-9]{32}/[1-9][0-9]{0,15}$")
# A session's four-character ID as the tools take it: bare, or in the
# @Session-NAME-XXXX mention the user wrote.
CODE_REF_RE = re.compile(r"(?:@?session-[\w-]*-)?([a-z0-9]{4})", re.I)
MENTION_RE = re.compile(
    r"(?<!\w)@session\s+(?:([a-f0-9]{32}):)?"
    r"(all|[a-f0-9]{32}/[1-9][0-9]{0,15})(?![\w/])", re.I)
MAX_FRAME = 1024 * 1024
# Turns reading conversations, two at a time: a turn's parallel reads, and its
# sub-agents', take their turns instead of every thread the console reads with,
# and one calling session has one place (relayed reads name no caller).
READS_WAIT = 20.0
# the events a read around one message shows: it in the middle, four either side
AROUND_PAGE = 9
_reads = workers.Lane(2, per_key=1, waiting=128)
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


def excerpt(text, limit):
    """A text cut to ``limit`` characters as its start and its end around
    what was left out, which it names: a long answer's conclusion is at its
    end, and its summary often at its start."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    room = max(0, int(limit) - 48)
    head = room // 3
    tail = room - head
    return text[:head] + "\n[… {} characters left out …]\n".format(
        len(text) - head - tail) + (text[-tail:] if tail else "")


def _word(text):
    """"all" or "this" as an agent may write it - any case, or in the
    brackets and quotes of the notation that describes it ("[all]")."""
    word = text.strip().strip("[]\"' ").lower()
    return word if word in ("all", "this") else None


def expand_refs(refs):
    """Refs as the tools take them: stable references, "all", "this", or a
    session's four-character ID - bare, or in its @Session-NAME-XXXX
    mention - resolved on this controller, which allocated it."""
    if isinstance(refs, str) and _word(refs):
        return [_word(refs)]
    if not isinstance(refs, list):
        return refs
    out = []
    for ref in refs:
        if isinstance(ref, str):
            text = ref.strip()
            # "all" and "this" in any case are those words, never a short ID
            if _word(text) or REF_RE.fullmatch(text.lower()):
                out.append(_word(text) or text.lower())
                continue
            match = CODE_REF_RE.fullmatch(text)
            if match:
                try:
                    out.extend(session_aliases.resolve([match.group(1).upper()]))
                except ValueError as exc:
                    raise SessionLinkError(str(exc))
                continue
        out.append(ref)     # refused later in the usual words
    return list(dict.fromkeys(out))


def scoped(scope):
    """Whether the user's mentions selected particular sessions - the
    boundary every read, search and send stays inside, across turns, until
    they mention others. None (nothing mentioned yet) and @Session-All
    select every session."""
    return bool(scope and scope["refs"] != ["all"])


def selection_refs(scope, reachable, source):
    """What ["all"] names: the sessions the user selected, or with every
    session selected each one reachable - never the asking session, whose
    own conversation is ["this"]."""
    refs = scope["refs"] if scoped(scope) else reachable
    return [ref for ref in refs if ref != source]


def outside_selection(ref, scope, known, source, data, what="session"):
    """The refusal of a ref past the user's selection, naming what was
    selected - so the call can be made again inside it - and that only the
    user widens it."""
    label = lambda item: known[item]["mention"] if item in known else item
    chosen = [label(item) for item in scope["refs"] if item != source]
    shown = ", ".join(chosen[:10]) + (", and {} more".format(len(chosen) - 10) if len(chosen) > 10 else "")
    return SessionLinkError(
        "{} {} is outside the sessions the user selected ({}); refs [\"all\"] or no refs "
        "mean those, [\"this\"] this conversation. Only the user widens the selection, by "
        "mentioning the session or {} for every one".format(
            what, label(ref), shown or "none but this conversation",
            data.get("all_mention") or "@Session-All"))


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


# what a payload carries that tells a reader nothing: identities, native ids,
# usage figures and timings (a tool's own arguments are always shown)
_QUIET_KEY = re.compile(r"(^|_)(id|ids|uuid)$|^native_|^usage$|^model_usage$|_ms$|^context_")


def _plain(value) -> str:
    """A value as one line: text as itself, anything else as compact JSON."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    except (TypeError, ValueError):
        return str(value)


def _fields(data, quiet=False) -> str:
    return "\n".join("{}: {}".format(key, _plain(value)) for key, value in data.items()
                     if value not in (None, "", [], {}) and not (quiet and _QUIET_KEY.search(key)))


def event_text(kind, payload) -> str:
    """An event as an agent reads it: a message its text, a tool call its tool
    and its arguments a line each, a tool's result its output, a turn's end
    how it ended - never a payload's JSON with its native ids and usage."""
    if not isinstance(payload, dict):
        return search._text_of(payload)
    if kind == "tool_use":
        data = payload.get("input")
        shown = _fields(data) if isinstance(data, dict) else \
            _plain(data) if data not in (None, "") else ""
        return str(payload.get("tool") or "tool") + ("\n" + shown if shown else "")
    if kind == "tool_result":
        mark = "(interrupted) " if payload.get("interrupted") else \
            "(failed) " if payload.get("is_error") else ""
        return mark + search._text_of(payload.get("content"))
    if kind == "result":
        error = payload.get("error")
        words = "The turn completed" if payload.get("ok") else \
            "The turn failed" + (": " + _plain(error) if error else "")
        steps = payload.get("num_turns")
        return words + (" after {} model steps".format(steps)
                        if type(steps) is int and steps > 0 else "")
    if kind == "engine_switch":
        before = ", ".join(str(value) for value in (payload.get("from_model"),
                                                     payload.get("from_effort")) if value)
        return "Engine switched from {}{} to {}".format(
            payload.get("from") or "an engine", " ({})".format(before) if before else "",
            payload.get("to") or "another")
    if kind == "side_question":
        return "Asked beside the turn: " + _plain(payload.get("question") or "")
    text = payload.get("text")
    if isinstance(text, str):
        subtype = payload.get("subtype")
        tag = "[{}] ".format(subtype) if kind in ("info", "error") and \
            isinstance(subtype, str) and subtype else ""
        return tag + text
    if "content" in payload:
        return search._text_of(payload["content"])
    return _fields(payload, quiet=True)


def _events(sid, args):
    limit = integer(args.get("limit", EVENTS_PAGE), "limit", 1, 100)
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
    budget = EVENTS_BUDGET
    # one message read alone takes the whole page; among others, its share
    share = EVENTS_BUDGET if limit == 1 else EVENT_CHARS
    result = []
    for row in rows:
        payload = row["data"]
        if row["kind"] == "info" and payload.get("subtype") == "session_task_archive":
            # the folded task reads in full through the character cursor
            from puppy import session_tasks
            raw = search._scrub(session_tasks.archive_text(payload))
        else:
            raw = search._scrub(event_text(row["kind"], payload))
        content = raw[offset:offset + min(budget, share)]
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


def _turn_text(kind, payload):
    """What a turn's prompt or its ending says: a failed result's error, an
    interruption's or an error row's words, a message's text."""
    if not isinstance(payload, dict):
        return search._scrub(search._text_of(payload))
    if kind == "result":
        text = payload.get("error") or payload.get("text") or \
            ("the turn completed" if payload.get("ok") else "the turn failed")
        return search._scrub(text if isinstance(text, str) else search._text_of(text))
    return search._scrub(search._text_of(payload.get("text", payload)))


def _turns(sid, args):
    """read's view "turns": one row per prompt, oldest first after the
    ``after_seq`` prompt, with its turn's final answer (the prompt index's
    answer and outcome) - the conversation's story without its tool output
    or thinking. A steered prompt joined the turn before it and has no
    answer of its own; a turn still running answers with its latest message.
    Long texts are cut and name their length: the events view reads a whole
    message (limit=1, after_seq=seq-1, offset)."""
    if args.get("before_seq") is not None or args.get("offset"):
        raise SessionLinkError("the turns view pages forward with after_seq only")
    after = integer(args.get("after_seq", 0), "after_seq", 0, 10 ** 16)
    limit = integer(args.get("limit", TURNS_PAGE), "limit", 1, TURNS_MAX)
    index = prompt_index.read(sid, after, limit)
    wanted = sorted({row[0] for row in index["prompts"]} |
                    {row[4] for row in index["prompts"] if row[4]})
    messages = {}
    for start in range(0, len(wanted), 400):
        part = wanted[start:start + 400]
        for row in db.query("SELECT seq, kind, payload, created_at FROM events WHERE session_id=? "
                            "AND seq IN ({})".format(",".join("?" * len(part))), [sid] + part):
            try:
                payload = json.loads(row[2])
            except (TypeError, ValueError):
                payload = {}
            messages[row[0]] = (row[1], payload, row[3])

    def piece(seq, cap):
        kind, payload, ts = messages.get(seq, ("", {}, 0))
        text = _turn_text(kind, payload)
        item = {"seq": seq, "ts": round(float(ts or 0), 3), "text": excerpt(text, cap),
                "chars": len(text), "truncated": len(text) > cap,
                "source": source_link(reference(sid), seq)}
        if item["truncated"]:
            # the call that reads it whole
            item["whole"] = {"view": "events", "after_seq": seq - 1, "limit": 1}
        return item

    turns, budget, last = [], TURNS_BUDGET, after
    for seq, _ts, steered, _excerpt, answer_seq, outcome in index["prompts"]:
        prompt = piece(seq, TURN_PROMPT_CHARS)
        row = {"prompt": prompt, "steered": bool(steered)}
        if not steered:
            if answer_seq:
                row["answer"] = dict(piece(answer_seq, TURN_ANSWER_CHARS),
                                     outcome=outcome or "unfinished")
            else:
                row["answer"] = None
        size = len(prompt["text"]) + len((row.get("answer") or {}).get("text", ""))
        if turns and size > budget:
            break
        turns.append(row)
        budget -= size
        last = seq
    more = index["more"] or len(turns) < len(index["prompts"])
    return {"view": "turns", "turns": turns, "total_prompts": index["total"],
            "next_after_seq": last if more else None, "read_at": time.time()}


async def node_call(method, args, source="", relayed=False):
    """One session operation on this node: for a turn (``source``, its
    session's reference) or relayed by a controller, which waits less."""
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
            view = read_view(args)
            if view not in ("events", "turns"):
                raise SessionLinkError("view is events or turns")
            reader = _turns if view == "turns" else _events
            # the session's live state is read here; its history off the loop
            info = session_info(sessions[0])
            try:
                body = await _reads.run(
                    workers.history, reader, sessions[0]["id"], args, key=source or None,
                    wait=search.RELAYED_WAIT if relayed else READS_WAIT)
            except workers.Busy:
                raise SessionLinkError("too many reads are waiting here; try again in a "
                                       "moment, one read at a time")
            return dict(body, session=info)
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
        # The selected sessions bound the search itself; each keeps its own
        # total and its own pages, and one pass over the index serves them all.
        try:
            matched = await search.for_agent(
                source or None, query, [session["id"] for session in page],
                wait=search.RELAYED_WAIT if relayed else search.AGENTS_WAIT,
                kinds=kinds, limit=limit, offset=offset)
        except search.SearchError as exc:
            raise SessionLinkError(str(exc))
        found = []
        for session in page:
            payload = matched.get(session["id"])
            if payload is None:
                continue
            for match in payload["matches"]:
                match["source"] = source_link(reference(session["id"]), match["seq"])
            found.append({"session": session_info(session), **payload})
        return {"node": db.node_uuid(), "results": found, "searched": len(page),
                "selected": len(sessions), "read_at": time.time(),
                "next_session_offset": session_offset + len(page) if session_offset + len(page) < len(sessions) else None}
    raise SessionLinkError("unknown session operation")


def read_view(args):
    """read's view: as named, else the turns - the conversation's story -
    unless the call pages events by their own cursors (before_seq, a
    character offset)."""
    view = args.get("view")
    if view is None:
        return "events" if args.get("before_seq") is not None or args.get("offset") or \
            args.get("around_seq") is not None else "turns"
    return view


async def remote_call(bid, method, args, source=""):
    if bid == 0:
        return await node_call(method, args, source=source)
    channel = backends.node_channel(bid)
    if channel is None:
        raise SessionLinkError("node is offline")
    if CAPABILITY not in channel["capabilities"]:
        raise SessionLinkError("node does not support session references")
    if method == "read":
        # the node is told the view, whatever its own default is
        view = read_view(args)
        if view == "turns" and protocol.SESSION_TURNS_VIEW_CAPABILITY not in channel["capabilities"]:
            if args.get("view") is not None:
                raise SessionLinkError("node '{}' cannot read by turns yet - upgrade its Puppy "
                                       "backend, or read its events".format(channel.get("name") or bid))
            view = "events"
        args = dict(args, view=view)
    from puppy.workspace_links import _request_json
    try:
        return await asyncio.wait_for(_request_json(
            channel, "POST", "session-links/node", {"method": method, "params": args}), 12)
    except asyncio.TimeoutError:
        raise SessionLinkError("node '{}' did not answer within 12 seconds".format(
            channel.get("name") or bid))


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
    if "refs" in args:
        args = dict(args, refs=expand_refs(args["refs"]))
    if method == "coordinate" and isinstance(args.get("steps"), list):
        args = dict(args, steps=[
            dict(step, refs=expand_refs(step["refs"]))
            if isinstance(step, dict) and "refs" in step else step for step in args["steps"]])
    if method == "resolve":
        return {"format": 1, "controller": db.node_uuid(),
                "refs": session_aliases.resolve(args.get("codes"))}
    if method in ("send", "wait", "cancel", "requests"):
        from puppy import session_actions
        operation = session_actions.operate(source, method, args, scope)
        if method == "cancel":
            return await durable_call(operation)
        if method != "send":
            return await operation
        sent = await durable_call(operation)
        # the answers a short wait brings; the request itself is already durable
        wait_s = integer(args.get("wait_s", 0), "wait_s", 0, 25)
        return await session_actions.wait_for(sent["id"], wait_s) if wait_s else sent
    if method in ("coordinate", "workflow", "workflows", "cancel_workflow"):
        from puppy import session_coordination
        operation = session_coordination.operate(source, method, args, scope)
        return await durable_call(operation) if method in ("coordinate", "cancel_workflow") else await operation
    if method not in ("sessions", "read", "search"):
        raise SessionLinkError("unknown session operation")
    data = await catalog()
    # every session the controller reaches, the asking one included: it reads
    # and searches its own conversation, while "all" stays every other one
    known = {row["ref"]: row for row in data["sessions"]}
    available = {ref: row for ref, row in known.items() if ref != source}
    if method == "sessions":
        query = str(args.get("query") or "").lower()
        offset = integer(args.get("offset", 0), "offset")
        limit = integer(args.get("limit", 100), "limit", 1, 200)
        listed = ([dict(known[source], this=True)] if source in known else []) + \
            list(available.values())
        if scoped(scope):
            # what read, search and send reach: the user's selection
            listed = [dict(row, selected=True) if row["ref"] in scope["refs"] and
                      not row.get("this") else row for row in listed]
        # every word somewhere in the row, in any order ("camera gimbal")
        words = query.split()
        rows = [s for s in listed if all(word in (
            s["title"] + " " + s["cwd"] + " " + s["node_name"] + " " + s["mention"]).lower()
            for word in words)]
        listing = {"controller": data["controller"], "sessions": rows[offset:offset + limit],
                   "total": len(rows),
                   "next_offset": offset + limit if offset + limit < len(rows) else None}
        if scoped(scope):
            listing["selection"] = (
                "the user selected the sessions marked selected: read, search and send reach "
                "those and this one; only the user widens that, by mentioning sessions "
                "({} for every one)".format(data["all_mention"]))
        if data["unavailable"]:
            listing["unavailable"] = data["unavailable"]
        return listing
    refs = args.get("refs", (scope or {}).get("refs"))
    if refs in ("all", "this"):
        refs = [refs]
    if not isinstance(refs, list) or not refs or len(refs) > 512:
        raise SessionLinkError("select sessions with @Session, supply refs from sessions, "
                               "or pass [\"this\"] for this conversation")
    # ["all"] is the user's selection - every session only when they
    # selected every one, or nothing yet
    every = refs == ["all"]
    if every:
        refs = selection_refs(scope, list(available), source)
        if not refs and scoped(scope):
            raise SessionLinkError(
                "the user selected no session but this conversation: pass refs [\"this\"] to "
                "read it; only the user widens the selection, by mentioning sessions ({} for "
                "every one)".format(data["all_mention"]))
    refs = [source if ref == "this" else ref for ref in refs]
    for ref in refs:
        parse_ref(ref)
        if ref == source:
            continue    # its own conversation is always the turn's to read
        if scoped(scope) and ref not in scope["refs"]:
            raise outside_selection(ref, scope, known, source, data)
    groups = {}
    # A node that cannot be reached is named where it costs coverage: in a
    # read or search of every session, or for a session that is not found -
    # never beside a session that answered.
    errors = list(data["unavailable"]) if every and not scoped(scope) else []
    unreached = ", ".join(item["node"] for item in data["unavailable"])
    for ref in dict.fromkeys(refs):
        row = known.get(ref)
        if row is None:
            errors.append({"ref": ref, "error": "session is deleted{}".format(
                ", or on a node not reached now ({})".format(unreached) if unreached
                else " or unavailable")})
        else:
            groups.setdefault(row["bid"], []).append(ref)
    if method == "read" and len(refs) != 1:
        raise SessionLinkError("read requires exactly one selected reference")
    around = None
    if method == "read" and args.get("around_seq") is not None:
        # a search hit opened in one call: the page whose middle is that event,
        # as an ordinary forward page - a session's seqs have no gaps - so any
        # node, an older one too, reads it
        around = integer(args["around_seq"], "around_seq", 1)
        if read_view(args) != "events" or any(args.get(key) is not None for key in
                                               ("after_seq", "before_seq", "offset")):
            raise SessionLinkError("around_seq reads events on its own, without other cursors")
        limit = integer(args.get("limit", AROUND_PAGE), "limit", 1, 100)
        args = {key: value for key, value in args.items() if key != "around_seq"}
        args.update(view="events", limit=limit, after_seq=max(0, around - 1 - (limit - 1) // 2))
    values = await asyncio.gather(*(remote_call(bid, method, dict(args, refs=items), source=source)
                                    for bid, items in groups.items()), return_exceptions=True)
    if around is not None:
        for value in values:
            for item in (value.get("events") or []) if isinstance(value, dict) else []:
                if item.get("seq") == around:
                    item["hit"] = True
    results = []
    for (bid, items), value in zip(groups.items(), values):
        if isinstance(value, Exception):
            errors.append({"refs": items, "error": str(value)})
        else:
            results.append(value)
    answer = {"results": results, "selected": len(refs),
              "next_session_offset": max((value.get("next_session_offset") or 0
                                          for value in results), default=0) or None}
    if errors:
        answer["unavailable"] = errors
    return answer


async def dispatch(sid, turn_id, method, args):
    scope = _scopes.get((sid, turn_id))
    controller = (scope or {}).get("controller", db.node_uuid())
    return for_agent(method, await _controller_call(sid, turn_id, controller, method, args, scope))


_SESSION_NOISE = ("id", "node", "bid", "side_question", "steering")


def _session_row(row):
    """A session as an agent reads it: its ref says its node and number, and
    its live channels' states are the node's business."""
    if not isinstance(row, dict):
        return row
    item = {key: value for key, value in row.items() if key not in _SESSION_NOISE}
    if not item.get("turn_id"):
        item.pop("turn_id", None)
    return item


def _answer_rows(results, answers=True, sharing=0):
    """A request's per-session results: status, error, and the answer - each
    within its share of the result (``sharing`` answers in all, these by
    default), its end kept - with the link to it."""
    each = max(1500, min(AGENT_ANSWER_CHARS,
                         AGENT_ANSWERS_BUDGET // max(1, sharing or len(results))))
    rows = []
    for row in results:
        if not isinstance(row, dict):
            continue
        item = {"target": row.get("target"), "status": row.get("status")}
        answer = str(row.get("answer") or "")
        if answer:
            if answers:
                item["answer"] = excerpt(answer, each)
            if not answers or len(answer) > each:
                item["answer_chars"] = len(answer)
        if row.get("error"):
            item["error"] = str(row["error"])[:2000]
        if row.get("target"):
            item["source"] = source_link(row["target"], row.get("end_seq") or 0)
        rows.append(item)
    return rows


def _request_view(record, answers=True):
    view = {key: record[key] for key in ("id", "action", "status", "created_at", "deadline")
            if key in record}
    if record.get("workflow"):
        view["workflow"] = record["workflow"]
    view["results"] = _answer_rows(record.get("results") or [], answers)
    return view


def _workflow_view(plan, answers=True):
    view = {key: plan[key] for key in ("id", "title", "status", "created_at", "deadline")
            if key in plan}
    steps = plan.get("steps") or []
    count = sum(len(step.get("results") or []) for step in steps if isinstance(step, dict))
    view["steps"] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        spec = step.get("spec") or {}
        item = {"id": spec.get("id"), "action": spec.get("action"),
                "after": spec.get("after") or [], "status": step.get("status")}
        if step.get("request"):
            item["request"] = step["request"]
        if step.get("error"):
            item["error"] = str(step["error"])[:2000]
        # every step's answers share the one result
        item["results"] = _answer_rows(step.get("results") or [], answers, count)
        view["steps"].append(item)
    return view


def _newest(items, limit=AGENT_LISTED):
    ordered = sorted((item for item in items if isinstance(item, dict)),
                     key=lambda item: -float(item.get("created_at") or 0))
    return ordered[:limit], len(ordered)


def for_agent(method, result):
    """A result as the agent's tool returns it: sessions without the fields
    that say nothing to it, requests and workflows newest first with their
    answers bounded and their ends kept, listings without answers."""
    if not isinstance(result, dict):
        return result
    if method == "sessions":
        return dict(result, sessions=[_session_row(row) for row in result.get("sessions") or []])
    if method in ("read", "search"):
        results = []
        for value in result.get("results") or []:
            if not isinstance(value, dict):
                continue
            value = dict(value)
            if "session" in value:
                value["session"] = _session_row(value["session"])
            if isinstance(value.get("results"), list):
                value["results"] = [dict(row, session=_session_row(row["session"]))
                                    if isinstance(row, dict) and "session" in row else row
                                    for row in value["results"]]
            results.append(value)
        return dict(result, results=results)
    if method in ("send", "wait", "cancel"):
        return _request_view(result)
    if method == "requests":
        listed, total = _newest(result.get("requests") or [])
        return {"requests": [_request_view(item, answers=False) for item in listed],
                "total": total, "note": "newest first; wait with a request's id for its answers"}
    if method in ("coordinate", "workflow", "cancel_workflow"):
        return _workflow_view(result)
    if method == "workflows":
        listed, total = _newest(result.get("workflows") or [])
        return {"workflows": [_workflow_view(item, answers=False) for item in listed],
                "total": total, "note": "newest first; workflow with an id for its answers"}
    return result


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
        return web.json_response(await node_call(body["method"], body["params"], relayed=True))
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
    global _app, _catalog_task, _catalog_value, _catalog_until, _reads
    _app = app
    _reads = workers.Lane(2, per_key=1, waiting=128)
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
