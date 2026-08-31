"""Turn execution engine. One SessionHub per active session: spawns the engine
CLI per turn, pumps its event stream into the DB and to websocket watchers,
relays interactive approvals, handles interrupts and a simple message queue."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid

from puppy import (browser_agent, config, db, handoff, notify, system_prompts,
                   terminal_agent, uploads, workspace_sync, workspaces)
from puppy.drivers import get_driver
from puppy.drivers import base as driver_base
from puppy.drivers.base import clean_env

log = logging.getLogger("puppy.runner")

_hubs = {}
_updates_watchers = set()  # websockets watching the session list

STREAM_LIMIT = 16 * 1024 * 1024
QUEUE_REORDER_HOLD_SECONDS = 30
MAX_STEER_CHARS = 128 * 1024
MAX_STEER_REQUEST_ID_CHARS = 128


def hub(session_id: int) -> "SessionHub":
    h = _hubs.get(session_id)
    if h is None:
        h = SessionHub(session_id)
        _hubs[session_id] = h
    return h


def drop_hub(session_id: int) -> None:
    """Forget a session's hub when the session itself is being deleted.

    Queued and held work is discarded with its session before the asynchronous
    kill() runs, so parking cannot resurrect the durable queue record that
    db.delete_session removes. Shutdown keeps its parking semantics by calling
    kill() directly instead."""
    h = _hubs.pop(session_id, None)
    if h:
        h.queue.clear()
        h.held.clear()
        h.paused_queue.clear()
        asyncio.ensure_future(h.kill())


# ---- session-list broadcasting ----

def _is_queued_config(item) -> bool:
    """Queue items are prompt strings, except pending model/effort changes
    and pending engine switches."""
    return isinstance(item, dict) and item.get("kind") == "config"


def _is_queued_engine(item) -> bool:
    """A pending engine switch holding its place in the message queue."""
    return isinstance(item, dict) and item.get("kind") == "engine"


def _queued_config_key(fields: dict) -> str:
    """Stable identity a console echoes back to cancel a pending change."""
    return "config:" + json.dumps(fields, sort_keys=True)


def _queued_engine_key(fields: dict) -> str:
    """The same cancel identity for a pending engine switch; the distinct
    prefix can never collide with a model/effort change."""
    return "engine:" + json.dumps(fields, sort_keys=True)


def _queued_item_key(item: dict) -> str:
    return (_queued_engine_key if _is_queued_engine(item) else
            _queued_config_key)(item.get("fields") or {})


def _queued_item_wire(item):
    """Wire form of one queue entry: prompts stay plain strings, so consoles
    from before pending changes existed keep rendering them; a pending change
    is an additive {kind, key, ...fields} entry. Config rows carry the engine
    whose catalog validated them, engine rows the switch target."""
    if not isinstance(item, dict):
        return item
    entry = {"kind": item.get("kind") or "config", "key": item.get("key") or ""}
    entry.update(item.get("fields") or {})
    return entry


def _valid_restored_item(item) -> bool:
    """Exactly the current queue-item shape; anything older is rejected rather
    than converted (every config row must name the engine that validated it)."""
    if isinstance(item, str):
        return bool(item.strip())
    fields = item.get("fields") if isinstance(item, dict) else None
    if not isinstance(fields, dict) or not str(fields.get("engine") or ""):
        return False
    if _is_queued_engine(item):
        return True
    return _is_queued_config(item) and \
        any(k in fields for k in ("model", "effort"))


def _config_after(session: dict, items) -> dict:
    """The {engine, model, effort} left in force after the given queue rows
    apply in their visible order. Config rows whose validating engine no
    longer matches the engine at their position are skipped, exactly as
    application skips them."""
    cfg = {"engine": str(session.get("engine") or ""),
           "model": str(session.get("model") or ""),
           "effort": str(session.get("effort") or "")}
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") or {}
        if _is_queued_engine(item):
            cfg["engine"] = str(fields.get("engine") or "")
            cfg["model"] = str(fields.get("model") or "")
            cfg["effort"] = str(fields.get("effort") or "")
        elif str(fields.get("engine") or "") == cfg["engine"]:
            for k in ("model", "effort"):
                if k in fields:
                    cfg[k] = str(fields.get(k) or "")
    return cfg


def parse_used_config(raw):
    """{model, effort} of the last turn that actually ran, or None if the
    session has not run one since it was created or moved to this engine."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return {"model": str(data.get("model") or ""), "effort": str(data.get("effort") or "")}


def _starts_fresh_native_session(session: dict, workspace_reset: bool, driver) -> bool:
    """Whether this turn must allocate a new engine-native conversation."""
    return not session.get("native_session_id") or \
        (workspace_reset and driver.resume_requires_same_cwd)


def session_payload(session):
    if session is None:
        return None
    out = dict(session)
    out["workspace_missing"] = workspaces.is_temporary(out) and not workspaces.is_available(out)
    out["used_config"] = parse_used_config(out["used_config"])
    out["show_meta"] = out["show_meta"] != 0
    # raw descriptor JSON becomes a structured object (or None) on the wire
    out["workspace"] = workspace_sync.session_workspace(out)
    out["ws_dirty"] = bool(out.get("ws_dirty"))
    return out


def updates_attach(ws) -> None:
    _updates_watchers.add(ws)


def updates_detach(ws) -> None:
    _updates_watchers.discard(ws)


def sessions_payload() -> dict:
    now = time.time()
    sessions = []
    for s in db.list_sessions(include_archived=True):
        h = _hubs.get(s["id"])
        sessions.append({
            "id": s["id"], "name": s["name"], "engine": s["engine"], "cwd": s["cwd"],
            "status": (h.status if h else "idle"), "archived": s["archived"],
            "active_since": (h.active_since if h and h.status == "running" else None),
            "completion_status": (h.last_completion_status
                                  if h and h.status == "idle" else ""),
            "updated_at": s["updated_at"], "model": s["model"], "last_model": s["last_model"],
            "effort": s["effort"], "color": s["color"], "permission_mode": s["permission_mode"],
            "has_native": bool(s["native_session_id"]),
            "workspace_kind": s["workspace_kind"],
            "workspace_missing": workspaces.is_temporary(s) and not workspaces.is_available(s),
            "workspace": workspace_sync.session_workspace(s),
            "ws_dirty": bool(s["ws_dirty"]),
            # non-empty while a turn holds at a sync barrier; the controller's
            # watcher reads this to know a linked session needs service
            "workspace_phase": (h._ws_phase if h else ""),
            # the sidebar menu is the way back once the head is hidden, so the
            # list has to carry this too - reading it only from the single
            # session payload left that menu permanently showing "on"
            "show_meta": s["show_meta"] != 0,
        })
    return {"type": "sessions", "server_time": now, "sessions": sessions}


def upgrade_blockers() -> list:
    """Sessions whose running turn or queued work makes a restart unsafe."""
    return [
        {"id": h.id, "running": h.status == "running", "queued": len(h.queue)}
        for h in _hubs.values() if h.status == "running" or h.queue
    ]


def engine_blockers(engine: str) -> list:
    """Busy sessions that would spawn one engine - replacing that CLI under
    them is unsafe.

    An updater unlinks and rewrites the installed package, so only sessions
    that would spawn (or are running) this engine need to be idle; work on the
    other engines is unaffected. A queued engine switch commits the prompts
    behind it to its target, so queued switch targets count as well. Held work
    never runs on its own and its explicit re-send paths carry their own
    updater guard."""
    out = []
    for h in _hubs.values():
        if h.status != "running" and not h.queue:
            continue
        session = db.get_session(h.id) or {}
        engines = {str(session.get("engine") or "")}
        for item in h.queue:
            if _is_queued_engine(item):
                engines.add(str((item.get("fields") or {}).get("engine") or ""))
        if str(engine) not in engines:
            continue
        out.append({"id": h.id, "name": session.get("name") or "",
                    "running": h.status == "running", "queued": len(h.queue)})
    return out


async def detach_for_restore() -> None:
    """Close authenticated live views before replacing their backing database."""
    if upgrade_blockers():
        raise RuntimeError("sessions became busy while preparing the restore")
    sockets = set(_updates_watchers)
    for h in _hubs.values():
        sockets.update(h.watchers)
    async def close_socket(ws) -> None:
        try:
            await ws.close(code=1012, message=b"Puppy state restored")
        except Exception:
            pass

    if sockets:
        try:
            await asyncio.wait_for(
                asyncio.gather(*(close_socket(ws) for ws in sockets)), timeout=3)
        except asyncio.TimeoutError:
            pass
    _updates_watchers.clear()
    _hubs.clear()


_workspace_hooks = []   # in-process controller broker: fn(session_id, phase)


def register_workspace_hook(fn) -> None:
    """The controller broker asks to hear local sessions' barrier phases.

    Remote execution nodes have no broker; their controllers observe the same
    phases through the updates websocket instead."""
    if fn not in _workspace_hooks:
        _workspace_hooks.append(fn)


def _notify_workspace_phase(session_id: int, phase: str) -> None:
    for hook in list(_workspace_hooks):
        try:
            hook(session_id, phase)
        except Exception:
            log.exception("workspace hook failed for session %s", session_id)


def broadcast_sessions() -> None:
    broadcast_update(sessions_payload())


def broadcast_update(payload: dict) -> None:
    """Send an additive controller update to every authenticated list watcher."""
    for ws in list(_updates_watchers):
        asyncio.ensure_future(_safe_send(ws, payload, _updates_watchers))


async def announce_node_stopping(reason: str = "shutdown") -> None:
    """Best-effort lifecycle notice while authenticated sockets are still open.

    Headless runtimes call this from aiohttp's shutdown signal, before the
    ordinary turn grace window begins.  Awaiting the writes for a short,
    bounded interval makes a SIGTERM useful to connected consoles without ever
    allowing a slow client to delay machine shutdown materially.
    """
    payload = {
        "type": "node_stopping",
        "reason": "restart" if reason == "restart" else "shutdown",
        "server_time": time.time(),
    }
    sockets = set(_updates_watchers)
    for h in _hubs.values():
        sockets.update(h.watchers)
    if not sockets:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(_safe_send(ws, payload) for ws in sockets),
                           return_exceptions=True),
            timeout=1.0)
    except asyncio.TimeoutError:
        log.debug("shutdown notice timed out for one or more websocket watchers")


async def _safe_send(ws, payload, pool=None) -> None:
    try:
        await ws.send_json(payload)
    except Exception:
        if pool is not None:
            pool.discard(ws)


class SessionHub:
    def __init__(self, session_id: int):
        self.id = session_id
        self.watchers = set()
        self.queue = []
        # Indexes in ``queue`` whose prompt must not start automatically.  The
        # queue itself deliberately stays in its old string/config wire shape;
        # an additive parallel list lets older consoles keep rendering it.
        self.paused_queue = set()
        # Queue mutations carry a process-local revision so a drag can submit
        # a permutation of duplicate prompts without relying on their text.
        # The acknowledged hold prevents automatic dequeue while its owner has
        # a row in flight; disconnect and a short lease both fail open.
        self.queue_revision = 0
        self._queue_reorder = None       # (watcher, request id)
        self._queue_reorder_timer = None
        self._queue_waiting_completion = None
        # Work that survived a restart or an engine kill. Held items never run
        # on their own: the world may have moved since they were written, so
        # each one waits for an explicit re-send (or discard) in the console.
        self.held = []
        try:
            record = db.meta_get("session_queue.{}".format(session_id)) or {}
            restored = list(record.get("queue") or []) + list(record.get("held") or [])
            for item in restored:
                if not _valid_restored_item(item):
                    continue
                if isinstance(item, dict):
                    # rebuilt in the current shape; the cancel identity is
                    # recomputed rather than trusted from disk
                    item = {"kind": item.get("kind"),
                            "fields": dict(item.get("fields") or {})}
                    item["key"] = _queued_item_key(item)
                self.held.append(item)
            if record:
                self._persist_queue()   # everything now lives under "held"
        except Exception as exc:
            log.warning("could not restore queue for session %s: %s", session_id, exc)
        self.status = "idle"
        # Start of one uninterrupted block of work. Queued turns inherit this
        # timestamp; it is cleared only when the turn and its queue are empty.
        self.active_since = None
        self.proc = None
        self._proc_ready = False
        self._driver_ctx = None
        self._interrupt_protocol_sent = False
        self.turn_task = None
        self.pending_approval = None
        self.interrupted = False
        # The outcome which ended the most recent activity block. It is
        # transient protocol state, used by controllers to distinguish a user
        # stop from work that completed while their session socket was hidden.
        self.last_completion_status = ""
        self.stderr_tail = ""
        self._stdin_lock = asyncio.Lock()
        # whether this turn's divider already announced a model move, so the
        # engine reporting that same move is not repeated as if it surprised us
        self._model_move_announced = False
        # The browser MCP subprocess is authorized for exactly this engine
        # turn. Each distinct browser it touches is announced once to the UI.
        self._active_turn_id = ""
        self._browser_activity_announced = set()
        self._terminal_activity_announced = set()
        # The active prompt normally becomes a durable user event at the start
        # of _run_turn.  Keep it visible to attachment reference checks until
        # the turn closes so cancelling a duplicate queued marker cannot race
        # that event write and remove bytes the active turn still needs.
        self._active_prompt_text = ""
        # Draft mutations from several session sockets are serialized through
        # one lock so every watcher observes the same revision order.
        self._draft_lock = asyncio.Lock()
        # Remote-workspace barrier state. While _ws_phase is set, the turn is
        # holding for the controller to reconcile the mirror; the grant route
        # (or the in-process hook for local sessions) releases it. _ws_map
        # rewrites the private mirror prefix to the authoritative project path
        # in everything persisted or shown, and back again for approval edits.
        self._ws_phase = ""
        self._ws_event = None
        self._ws_result = None
        self._ws_progress_at = 0.0
        self._ws_map = None
        self._interrupt_count = 0

    # ---- watchers ----

    def attach(self, ws) -> None:
        self.watchers.add(ws)

    async def attach_with_snapshot(self, ws) -> None:
        """Attach without allowing a newer draft to overtake its snapshot."""
        async with self._draft_lock:
            self.attach(ws)
            try:
                await ws.send_json(self.snapshot())
            except Exception:
                self.detach(ws)
                raise

    def detach(self, ws) -> None:
        self.watchers.discard(ws)
        context = self._queue_reorder
        if context is not None and context[0] is ws:
            self._release_queue_reorder(ws, context[1])
            self._resume_after_queue_reorder()

    def broadcast(self, payload: dict) -> None:
        for ws in list(self.watchers):
            asyncio.ensure_future(_safe_send(ws, payload, self.watchers))

    async def _broadcast_draft(self, payload: dict) -> None:
        """Write one ordered draft frame to every attached viewer."""
        sockets = list(self.watchers)
        if not sockets:
            return
        await asyncio.gather(
            *(_safe_send(ws, payload, self.watchers) for ws in sockets),
            return_exceptions=True)

    @staticmethod
    def _draft_identity(client_id, client_seq) -> dict:
        client_id = str(client_id or "")[:80]
        try:
            client_seq = int(client_seq)
        except (TypeError, ValueError):
            client_seq = 0
        return {"client_id": client_id, "client_seq": max(0, client_seq)}

    def _draft_payload(self, record: dict, client_id="", client_seq=0,
                       consumed=None) -> dict:
        payload = {"type": "draft", **record,
                   **self._draft_identity(client_id, client_seq)}
        if consumed is not None:
            payload["consumed"] = bool(consumed)
        return payload

    async def update_draft(self, text, client_id="", client_seq=0) -> dict:
        """Persist and fan out one full composer value in a total order."""
        if not isinstance(text, str):
            return {"error": "draft text must be text"}
        if len(text) > db.MAX_DRAFT_CHARS:
            return {"error": "draft cannot exceed {} characters".format(
                db.MAX_DRAFT_CHARS)}
        async with self._draft_lock:
            if db.get_session(self.id) is None:
                return {"error": "session gone"}
            try:
                _, current = db.set_session_draft(self.id, text)
            except Exception:
                log.exception("could not persist draft for session %s", self.id)
                return {"error": "could not save draft"}
            payload = self._draft_payload(current, client_id, client_seq)
            await self._broadcast_draft(payload)
            # Do not delete an upload merely because this revision dropped its
            # marker. A concurrent client may already have sent a later full
            # draft which still carries it but whose frame has not reached us
            # yet. Session deletion remains the unambiguous cleanup boundary.
            return payload

    async def consume_draft(self, expected_text, client_id="", client_seq=0,
                            recipient=None) -> dict:
        """Clear the value just sent without erasing a concurrent newer edit."""
        if not isinstance(expected_text, str):
            return {"error": "draft text must be text"}
        if len(expected_text) > db.MAX_DRAFT_CHARS:
            return {"error": "draft cannot exceed {} characters".format(
                db.MAX_DRAFT_CHARS)}
        async with self._draft_lock:
            try:
                consumed, previous, current = db.consume_session_draft(
                    self.id, expected_text)
            except Exception:
                log.exception("could not consume draft for session %s", self.id)
                return {"error": "could not save draft"}
            payload = self._draft_payload(
                current, client_id, client_seq, consumed=consumed)
            if consumed:
                await self._broadcast_draft(payload)
                self._discard_abandoned_uploads([previous["text"]])
            elif recipient is not None:
                # Other viewers already have this revision. The sender still
                # needs the authoritative value so its optimistic clear cannot
                # hide a concurrent edit.
                await _safe_send(recipient, payload, self.watchers)
            return payload

    def tool_turn_active(self, turn_id: str) -> bool:
        """Whether a private per-turn agent bridge belongs to this engine."""
        return self.status == "running" and bool(turn_id) and \
            turn_id == self._active_turn_id

    def browser_turn_active(self, turn_id: str) -> bool:
        """Compatibility name retained for the browser bridge and its tests."""
        return self.tool_turn_active(turn_id)

    def browser_activity(self, turn_id: str, browser_id: str) -> bool:
        """Authorize and announce one browser used by the current turn."""
        if not self.browser_turn_active(turn_id):
            return False
        if browser_id in self._browser_activity_announced:
            return True
        self._browser_activity_announced.add(browser_id)
        payload = {"type": "browser_activity", "turn_id": turn_id,
                   "browser_id": browser_id}
        self.broadcast(payload)
        broadcast_update({**payload, "session_id": self.id})
        return True

    def terminal_activity(self, turn_id: str, terminal_id: str) -> bool:
        """Authorize and announce one shared terminal used by this turn."""
        if not self.tool_turn_active(turn_id):
            return False
        if terminal_id in self._terminal_activity_announced:
            return True
        self._terminal_activity_announced.add(terminal_id)
        payload = {"type": "terminal_activity", "turn_id": turn_id,
                   "terminal_id": terminal_id}
        self.broadcast(payload)
        broadcast_update({**payload, "session_id": self.id})
        return True

    def snapshot(self) -> dict:
        session = db.get_session(self.id)
        return {
            "type": "snapshot",
            "session": session_payload(session),
            "events": db.get_events(self.id, limit=200),
            "status": self.status,
            "active_since": self.active_since if self.status == "running" else None,
            "completion_status": (self.last_completion_status
                                  if self.status == "idle" else ""),
            "server_time": time.time(),
            "queued": self._queue_wire(),
            "queue_revision": self.queue_revision,
            "paused": self._paused_wire(),
            "held": self._held_wire(),
            "pending_approval": self._scrub_value(self.pending_approval),
            "uploads": uploads.settings_payload(),
            "draft": db.get_session_draft(self.id),
            "draft_max_chars": db.MAX_DRAFT_CHARS,
        }

    def _queue_wire(self) -> list:
        return [_queued_item_wire(item) for item in self.queue]

    def _held_wire(self) -> list:
        return [_queued_item_wire(item) for item in self.held]

    def _paused_wire(self) -> list:
        """Queue indexes paused by the user, scrubbed against the live queue.

        Only prompt strings can be paused. Pending configuration and engine
        rows keep their ordered relationship to those prompts and held work
        already has its own explicit resend state.
        """
        return sorted(index for index in self.paused_queue
                      if 0 <= index < len(self.queue) and
                      isinstance(self.queue[index], str))

    def _pop_queue(self, index: int):
        """Pop one queue entry and keep every paused index aligned with it."""
        item = self.queue.pop(index)
        self.paused_queue = {
            paused - 1 if paused > index else paused
            for paused in self.paused_queue if paused != index
        }
        return item

    def _persist_queue(self) -> None:
        """Write-through on every mutation: queued work belongs to the user,
        not to this process, so it must survive puppy being restarted or
        killed mid-turn. Prompts stay strings and pending changes stay their
        raw dicts, so a restore is lossless."""
        try:
            key = "session_queue.{}".format(self.id)
            if self.queue or self.held:
                db.meta_set(key, {"queue": self.queue, "held": self.held,
                                  "paused": self._paused_wire()})
            else:
                # deleted, not nulled: an emptied record must not linger, and
                # a session deletion racing a late kill() must not resurrect it
                db.meta_del(key)
        except Exception as exc:
            log.warning("could not persist queue for session %s: %s", self.id, exc)

    def _broadcast_queue(self) -> None:
        self.queue_revision += 1
        self._persist_queue()
        self.broadcast({"type": "queued", "queued": self._queue_wire(),
                        "queue_revision": self.queue_revision,
                        "paused": self._paused_wire(), "held": self._held_wire()})

    def _discard_abandoned_uploads(self, items, retained=()) -> None:
        abandoned = [item for item in items if isinstance(item, str)]
        if not abandoned:
            return
        try:
            retained = list(retained or ()) + [
                item for item in self.queue + self.held if isinstance(item, str)]
            # A cancelled queued/held prompt may share a staged file with the
            # live composer on another device. The durable draft is an owner.
            retained.append(db.get_session_draft(self.id)["text"])
            if self._active_prompt_text:
                retained.append(self._active_prompt_text)
            uploads.discard_abandoned(self.id, abandoned, retained)
        except Exception as exc:
            # Queue controls must remain usable even if private storage has
            # become unavailable.  The upload helper itself also fails closed.
            log.warning("could not discard abandoned uploads for session %s: %s",
                        self.id, exc)

    # ---- public ops ----

    def send_message(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"error": "empty message"}
        session = db.get_session(self.id)
        if session is None:
            return {"error": "session gone"}
        # The upgrade route checks the opposite direction before claiming its
        # slot. Check here as well so a message cannot spawn an engine after
        # its installed package has begun being rewritten in place. A new
        # prompt joins the queue tail, so the engine it will spawn is whatever
        # every pending switch ahead of it leaves in force.
        engine = self.pending_config()["engine"]
        from puppy import cli_upgrade
        if cli_upgrade.is_running(engine):
            return {"error": "{} is being updated - try again when it finishes".format(
                engine or "the engine")}
        if not session["name"]:
            name = text.splitlines()[0][:48]
            db.touch_session(self.id, name=name)
            broadcast_sessions()
        # also queue behind a non-empty queue while idle (the moment between a
        # turn ending and its successor starting): a pending change in there
        # must still apply before this prompt runs
        if self.status == "running" or self.queue:
            self.queue.append(text)
            self._broadcast_queue()
            # A queue idle only because every earlier prompt is paused stays
            # idle on its own. This prompt is runnable now, so it pokes the
            # scheduler itself instead of waiting for a later pause toggle.
            if self.status != "running":
                self._start_queue_if_ready()
            return {"queued": True}
        self._start_turn(text)
        return {"queued": False}

    async def steer(self, text: str, request_id: str = "") -> dict:
        """Send additional user guidance to this hub's active native turn.

        This path is intentionally separate from send_message(): it never
        joins the ordinary queue and a driver must prove that its live stdin
        protocol supports same-turn input before any transcript row is added.
        """
        if not isinstance(text, str):
            return {"error": "steering text must be text"}
        text = text.strip()
        if not text:
            return {"error": "empty steering message"}
        if len(text) > MAX_STEER_CHARS:
            return {"error": "steering message cannot exceed {} characters".format(
                MAX_STEER_CHARS)}
        if not isinstance(request_id, str) or \
                len(request_id) > MAX_STEER_REQUEST_ID_CHARS:
            return {"error": "invalid steering request id"}
        request_id = request_id or str(uuid.uuid4())

        session = db.get_session(self.id)
        if session is None:
            return {"error": "session gone"}
        if self.status != "running":
            return {"error": "there is no active turn to steer"}
        if self.interrupted:
            return {"error": "the active turn is stopping"}
        if self.pending_approval is not None:
            return {"error": "resolve the pending approval before steering"}
        try:
            driver = get_driver(session["engine"])
        except KeyError:
            return {"error": "the active engine is unavailable"}
        if not driver.supports_steering:
            return {"error": "{} does not support active-turn steering".format(
                driver.label)}

        proc = self.proc
        ctx = self._driver_ctx
        if not self._proc_ready or proc is None or proc.returncode is not None or \
                proc.stdin is None or proc.stdin.is_closing():
            return {"error": "the active turn is not ready for steering"}
        payload = driver.steer_payload(session, ctx, text, request_id)
        if not isinstance(payload, dict):
            return {"error": "the active turn is not ready for steering"}
        try:
            await self._write_stdin(payload)
        except (BrokenPipeError, ConnectionError, RuntimeError) as exc:
            log.info("steering write failed for session %s: %s", self.id, exc)
            return {"error": "the active turn stopped before steering was delivered"}

        self._emit("user", {"text": text, "steering": True,
                            "request_id": request_id})
        return {"ok": True, "request_id": request_id}

    def pending_config(self) -> dict:
        """The {engine, model, effort} in force after everything queued: what
        the next newly queued prompt or change would run under."""
        session = db.get_session(self.id) or {}
        return _config_after(session, self.queue)

    def queue_config(self, fields: dict) -> dict:
        """Hold a model/effort change until everything already queued has run:
        the user changed it after sending those prompts, so they belong to the
        configuration that was showing when they were written.

        ``fields["engine"]`` names the engine whose catalog validated the
        change, and must match the engine in force at the queue tail (the
        session's own engine plus every pending switch). A mismatch means the
        session moved while the caller was validating, and is an explicit
        error rather than a silent misapply.

        Only a real difference is held. Consecutive changes collapse into the
        one pending entry, a change made while an engine switch is pending
        folds into that switch, and each field is measured against what is
        already in force at that point in the queue - fiddling with the
        pickers and landing back on the current value leaves nothing pending,
        and trims a pending entry that no longer changes anything.

        {"handled": False} means the caller applies these fields now."""
        clean = {k: str(v) for k, v in fields.items() if k in ("model", "effort")}
        tag = str(fields.get("engine") or "")

        def moved(current):
            return {"error": "that change belongs to {} - this session is on "
                             "{} now, so pick it again".format(
                                 tag, current or "another engine")}

        if not clean:
            return {"handled": False}
        session = db.get_session(self.id) or {}
        if self.status != "running" and not self.queue:
            if tag and tag != str(session.get("engine") or ""):
                return moved(session.get("engine"))
            return {"handled": False}
        tail = self.queue[-1] if self.queue and isinstance(self.queue[-1], dict) \
            else None
        # what runs just before the tail entry: the session's own configuration
        # plus every pending change queued ahead of it
        base = _config_after(session, [item for item in self.queue
                                       if item is not tail])
        if tail is not None and _is_queued_engine(tail):
            # picked while this switch is pending: the pick belongs to its
            # target, so the switch itself reseeds with it
            merged = dict(tail.get("fields") or {})
            if tag and tag != str(merged.get("engine") or ""):
                return moved(merged.get("engine"))
            merged.update(clean)
            tail["fields"] = merged
            tail["key"] = _queued_engine_key(merged)
            self._broadcast_queue()
            return {"handled": True}
        if tag and tag != base["engine"]:
            return moved(base["engine"])
        # a plain tail row from another engine (a reorder can leave one) must
        # not absorb picks validated for the engine now in force
        absorb = tail is not None and \
            str((tail.get("fields") or {}).get("engine") or "") == base["engine"]
        merged = {k: v for k, v in (tail.get("fields") or {}).items()
                  if k in ("model", "effort")} if absorb else {}
        merged.update(clean)
        merged = {k: v for k, v in merged.items() if v != base.get(k, "")}
        if absorb and merged:
            tail["fields"] = {**merged, "engine": base["engine"]}
            tail["key"] = _queued_config_key(tail["fields"])
        elif absorb:
            self._pop_queue(len(self.queue) - 1)
        elif merged:
            entry = {**merged, "engine": base["engine"]}
            self.queue.append({"kind": "config", "fields": entry,
                               "key": _queued_config_key(entry)})
        else:
            # already in force further down the queue: nothing to do
            return {"handled": True}
        self._broadcast_queue()
        return {"handled": True}

    def request_engine_switch(self, engine: str, model: str,
                              effort: str = "") -> dict:
        """Switch now when nothing is pending, otherwise hold the switch at
        the queue tail so prompts sent before it keep the engine they were
        written under. Consecutive requests collapse into the one pending
        switch (last pick wins, its model/effort reset to the new target's
        defaults), and a switch to the current engine stays meaningful:
        applying it reseeds a fresh native session from the transcript."""
        if db.get_session(self.id) is None:
            return {"error": "session gone"}
        fields = {"engine": str(engine or ""), "model": str(model or ""),
                  "effort": str(effort or "")}
        if self.status == "running" or self.queue:
            tail = self.queue[-1] if self.queue and \
                _is_queued_engine(self.queue[-1]) else None
            if tail is not None:
                tail["fields"] = fields
                tail["key"] = _queued_engine_key(fields)
            else:
                self.queue.append({"kind": "engine", "fields": fields,
                                   "key": _queued_engine_key(fields)})
            self._broadcast_queue()
            log.info("session %s queued engine switch -> %s", self.id, engine)
            return {"queued": True}
        if not self._apply_engine_switch(fields):
            return {"error": "could not switch engines"}
        return {"queued": False}

    def _apply_engine_switch(self, fields: dict) -> bool:
        """One engine transition: the divider event, a reset to the target's
        defaults, and a fresh native conversation (whose first turn seeds the
        new engine with the transcript handoff). Runs between turns, never
        inside one."""
        engine = str(fields.get("engine") or "")
        session = db.get_session(self.id)
        if session is None:
            return False
        try:
            driver = get_driver(engine)
        except KeyError:
            self._emit("error", {
                "text": "Cannot switch to unknown engine '{}'".format(engine)})
            return False
        old = session["engine"]
        # Snapshot what the outgoing engine actually ran, so the transcript
        # divider can name both configurations - a model picked but never sent
        # anything never ran, and must not be recorded as if it had. A session
        # with no completed turn has no such record and falls back to its
        # selection. The incoming engine is reset to its defaults just below
        # and its model is chosen later, so the WebUI resolves that side from
        # what runs next rather than from here.
        used = parse_used_config(session["used_config"]) or \
            {"model": session["model"] or session["last_model"],
             "effort": session["effort"]}
        self._emit("engine_switch", {
            "from": old, "to": engine,
            "from_model": used["model"], "from_effort": used["effort"],
        })
        db.touch_session(
            self.id, engine=engine, native_session_id="",
            model=fields.get("model") or driver.default_model(),
            effort=fields.get("effort") or "", last_model="", used_config="",
            permission_mode=driver.default_permission())
        self.broadcast({"type": "session_meta",
                        "session": session_payload(db.get_session(self.id))})
        broadcast_sessions()
        log.info("session %s switched %s -> %s", self.id, old, engine)
        return True

    def unqueue(self, index: int, text: str) -> dict:
        """Drop a message or pending change that is still waiting. The index is
        guarded by the item's text (its key, for a change) so a turn finishing
        between the click and this call - which shifts every index down by one
        - cannot cancel the wrong item."""
        if not 0 <= index < len(self.queue):
            return {"error": "that message already started"}
        item = self.queue[index]
        ident = item.get("key") if isinstance(item, dict) else item
        if ident != text:
            return {"error": "that message already started"}
        removed = self._pop_queue(index)
        if not self._start_queue_if_ready():
            self._broadcast_queue()
        # Persist the queue mutation first.  A crash may leave an orphan for a
        # later cleanup, but must never restore a prompt whose file was already
        # deleted.
        self._discard_abandoned_uploads([removed])
        return {"ok": True}

    async def edit_queued(self, index: int, text: str) -> dict:
        """Move one waiting prompt into the durable shared composer.

        Queue identity is checked while holding the draft mutation lock. Once
        acquired, the draft write, in-memory pop, paused-index remap, and
        durable queue write all finish before this method yields again, so a
        turn ending beside the click can neither run the edited prompt nor
        remove a shifted neighbor.
        """
        if not isinstance(text, str):
            return {"error": "message text must be text"}
        if len(text) > db.MAX_DRAFT_CHARS:
            return {"error": "that queued message is too long to edit"}
        async with self._draft_lock:
            if not 0 <= index < len(self.queue):
                return {"error": "that message already started"}
            item = self.queue[index]
            if isinstance(item, dict):
                return {"error": "queued changes cannot be edited as messages"}
            if item != text:
                return {"error": "that message already started"}
            if db.get_session(self.id) is None:
                return {"error": "session gone"}
            try:
                previous, current = db.set_session_draft(self.id, item)
            except Exception:
                log.exception("could not move queued message into draft for session %s",
                              self.id)
                return {"error": "could not save draft"}

            removed = self._pop_queue(index)
            started = self._start_queue_if_ready()
            if not started:
                self._broadcast_queue()
            payload = self._draft_payload(current)
            await self._broadcast_draft(payload)

        # The queued prompt is now the durable draft, so any attachment marker
        # it owns remains retained. The composer value it explicitly replaced
        # is no longer an owner, while queued/held/active references remain
        # protected by the ordinary abandoned-upload check.
        self._discard_abandoned_uploads([removed, previous["text"]])
        return {"ok": True, "started": started, "draft": payload}

    def set_queue_paused(self, index: int, text: str, paused: bool) -> dict:
        """Pause or resume one still-waiting prompt.

        The text guard makes an index shifted by a finishing turn harmless,
        just like ``unqueue``. Paused prompts remain in the visible order but
        automatic dequeue skips over them; configuration rows encountered on
        the way to a later runnable prompt still apply in their visible order.
        """
        if not 0 <= index < len(self.queue):
            return {"error": "that message already started"}
        item = self.queue[index]
        if isinstance(item, dict):
            return {"error": "queued changes cannot be paused"}
        if item != text:
            return {"error": "that message already started"}
        if paused:
            self.paused_queue.add(index)
        else:
            self.paused_queue.discard(index)

        # Either direction can expose runnable work in an otherwise-idle
        # queue: pausing its first prompt may reveal the next one, while
        # resuming may make the formerly-paused prompt runnable itself.
        if self._start_queue_if_ready():
            return {"ok": True, "paused": paused}
        self._broadcast_queue()
        return {"ok": True, "paused": paused}

    def _queue_reorder_result(self, started=False, error="") -> dict:
        result = {
            "ok": not bool(error), "started": bool(started),
            "queued": self._queue_wire(), "queue_revision": self.queue_revision,
            "paused": self._paused_wire(), "held": self._held_wire(),
        }
        if error:
            result["error"] = error
        return result

    def begin_queue_reorder(self, owner, request_id: str, revision: int) -> dict:
        """Acquire the short scheduler hold used by one queue drag.

        The UI does not let a native drag start until this acknowledgement is
        received, so a turn finishing while a row is actually in flight cannot
        consume any waiting item. The revision is a duplicate-safe snapshot
        guard; prompt text is deliberately not used as an identity here.
        """
        request_id = str(request_id or "")[:80]
        if not request_id:
            return {"error": "queue reorder request is missing an id"}
        if type(revision) is not int or revision != self.queue_revision:
            return {"error": "the queue changed before the drag started"}
        if len(self.queue) < 2:
            return {"error": "the queue no longer has enough items to reorder"}
        if self._queue_reorder is not None:
            if self._queue_reorder == (owner, request_id):
                return {"ok": True, "queue_revision": self.queue_revision}
            return {"error": "another queue reorder is already in progress"}
        self._queue_reorder = (owner, request_id)
        loop = asyncio.get_event_loop()
        self._queue_reorder_timer = loop.call_later(
            QUEUE_REORDER_HOLD_SECONDS,
            self._expire_queue_reorder, owner, request_id)
        return {"ok": True, "queue_revision": self.queue_revision}

    def _release_queue_reorder(self, owner, request_id: str) -> bool:
        if self._queue_reorder != (owner, str(request_id or "")[:80]):
            return False
        self._queue_reorder = None
        timer = self._queue_reorder_timer
        self._queue_reorder_timer = None
        if timer is not None:
            timer.cancel()
        return True

    def _expire_queue_reorder(self, owner, request_id: str) -> None:
        if not self._release_queue_reorder(owner, request_id):
            return
        self.broadcast({
            "type": "queue_reorder_cancelled", "request_id": request_id,
            "text": "Queue reorder timed out; automatic processing resumed",
        })
        self._resume_after_queue_reorder()

    def _resume_after_queue_reorder(self) -> bool:
        deferred = self._queue_waiting_completion
        started = self._start_queue_if_ready()
        if deferred is None:
            return started
        self._queue_waiting_completion = None
        completion_status = ""
        if not started:
            completion_status = self._finish_activity_block(*deferred)
        # Every viewer saw the preceding turn enter a temporary idle state.
        # Tell all of them whether releasing the drag resumed the same activity
        # block or finally completed it; the request response covers only the
        # socket which owned the drag.
        self.broadcast({
            "type": "turn_done", "continued": started,
            "queue_waiting": False, "completion_status": completion_status,
        })
        broadcast_sessions()
        return started

    def finish_queue_reorder(self, owner, request_id: str) -> dict:
        """Release a drag which ended without changing the queue."""
        if not self._release_queue_reorder(owner, request_id):
            return self._queue_reorder_result(error="that queue reorder is no longer active")
        return self._queue_reorder_result(
            started=self._resume_after_queue_reorder())

    def reorder_queue(self, owner, request_id: str, revision: int, order) -> dict:
        """Apply one full old-index permutation and release its scheduler hold."""
        if self._queue_reorder != (owner, str(request_id or "")[:80]):
            return self._queue_reorder_result(error="that queue reorder is no longer active")
        error = ""
        if type(revision) is not int or revision != self.queue_revision:
            error = "the queue changed while it was being reordered"
        elif not isinstance(order, list) or \
                any(type(index) is not int for index in order) or \
                sorted(order) != list(range(len(self.queue))):
            error = "invalid queue order"
        if error:
            self._release_queue_reorder(owner, request_id)
            started = self._resume_after_queue_reorder()
            return self._queue_reorder_result(started=started, error=error)

        previous = list(self.queue)
        previous_paused = set(self.paused_queue)
        changed = any(old_index != new_index
                      for new_index, old_index in enumerate(order))
        if changed:
            self.queue = [previous[index] for index in order]
            self.paused_queue = {
                new_index for new_index, old_index in enumerate(order)
                if old_index in previous_paused
            }
        self._release_queue_reorder(owner, request_id)
        started = self._resume_after_queue_reorder()
        if changed and not started:
            self._broadcast_queue()
        return self._queue_reorder_result(started=started)

    def _held_matches(self, index: int, ident: str):
        """The item at index, but only if ident still names it - the same
        stale-index guard unqueue uses."""
        if not 0 <= index < len(self.held):
            return None
        item = self.held[index]
        current = item.get("key") if isinstance(item, dict) else item
        return item if current == ident else None

    def requeue_held(self, index: int, ident: str) -> dict:
        """Send one held item again, through the same paths a fresh submit
        takes: a prompt starts now or queues behind running work, a pending
        change applies now or re-queues, an engine switch switches now or
        re-queues. A change validated by an engine the session is no longer
        heading for is refused rather than misapplied."""
        item = self._held_matches(index, ident)
        if item is None:
            return {"error": "that held item is gone"}
        if _is_queued_engine(item):
            fields = dict(item.get("fields") or {})
            engine = str(fields.get("engine") or "")
            from puppy import cli_upgrade
            if cli_upgrade.is_running(engine):
                return {"error": "{} is being updated - try again when it "
                                 "finishes".format(engine or "that engine")}
            self.held.pop(index)
            result = self.request_engine_switch(
                engine, fields.get("model") or "", effort=fields.get("effort") or "")
            if "error" in result:
                self.held.insert(index, item)   # nothing changed: keep it held
            self._broadcast_queue()
            return result if "error" in result else {"ok": True}
        if _is_queued_config(item):
            fields = dict(item.get("fields") or {})
            self.held.pop(index)
            result = self.queue_config(fields)
            if result.get("error"):
                self.held.insert(index, item)
                self._broadcast_queue()
                return {"error": result["error"]}
            if not result.get("handled"):
                self._apply_queued_config(fields)
            self._broadcast_queue()
            return {"ok": True}
        self.held.pop(index)
        result = self.send_message(item)
        if "error" in result:
            self.held.insert(index, item)   # nothing was sent: keep it held
        self._broadcast_queue()
        return result

    def discard_held(self, index: int, ident: str) -> dict:
        item = self._held_matches(index, ident)
        if item is None:
            return {"error": "that held item is gone"}
        self.held.pop(index)
        self._broadcast_queue()
        self._discard_abandoned_uploads([item])
        return {"ok": True}

    def clear_queue(self) -> int:
        """Drop everything that has not started yet and return the count."""
        count = len(self.queue)
        if count:
            removed = list(self.queue)
            self.queue.clear()
            self.paused_queue.clear()
            self._broadcast_queue()
            self._discard_abandoned_uploads(removed)
        return count

    def _start_turn(self, text: str) -> None:
        if self.active_since is None:
            self.active_since = time.time()
            self.last_completion_status = ""
        self.status = "running"
        self.interrupted = False
        self._proc_ready = False
        self._driver_ctx = None
        self._interrupt_protocol_sent = False
        self._active_prompt_text = text
        self.turn_task = asyncio.ensure_future(self._run_turn(text))
        # Publish the active block immediately, before process startup and the
        # first persisted event have a chance to yield the event loop.
        broadcast_sessions()

    def _start_queue_if_ready(self) -> bool:
        """Start the front prompt when an idle queue mutation unblocks it."""
        if self.status == "running":
            return False
        next_turn = self._take_next_turn()
        if next_turn is None:
            return False
        self._broadcast_queue()
        self._start_turn(next_turn)
        return True

    def _take_next_turn(self):
        """Advance within an activity block, or close it when the queue is
        out of runnable prompts. Paused prompts stay visible but are skipped;
        pending model/effort changes before the selected runnable prompt apply
        in their visible order."""
        # A shutdown grace window only has to outlast the RUNNING turn. Feeding
        # it the next queued prompt would consume that prompt and then kill it
        # mid-answer; leaving the queue untouched lets kill() park it instead.
        if _draining and self.queue:
            self.status = "idle"
            self.active_since = None
            return None
        # A row may move only after the server acknowledges this hold. Nothing
        # can be consumed until the owner drops/cancels or the lease expires.
        if self._queue_reorder is not None and self.queue:
            self.status = "idle"
            return None

        prompt_indexes = [index for index, item in enumerate(self.queue)
                          if not isinstance(item, dict)]
        if not prompt_indexes:
            applied = False
            while self.queue:
                self._apply_queued_item(self._pop_queue(0))
                applied = True
            if applied:
                self._broadcast_queue()
            self.status = "idle"
            self.active_since = None
            return None
        next_prompt = next((index for index in prompt_indexes
                            if index not in self.paused_queue), None)
        if next_prompt is None:
            self.status = "idle"
            self.active_since = None
            return None

        # Remove only configuration/engine rows on the path to the selected
        # prompt. Earlier paused prose remains where the user left it and will
        # run later under whatever configuration its new execution position
        # sees.
        applied = False
        scan = 0
        while scan < next_prompt:
            if isinstance(self.queue[scan], dict):
                self._apply_queued_item(self._pop_queue(scan))
                next_prompt -= 1
                applied = True
            else:
                scan += 1
        if applied:
            self._broadcast_queue()
        item = self._pop_queue(next_prompt)
        self._persist_queue()   # consumed: a crash must not run it twice
        return item

    def _apply_queued_item(self, item) -> None:
        """Apply one pending configuration or engine row. Never raises: this
        runs in the turn-completion path, where one bad row must not stop the
        queue behind it."""
        try:
            if _is_queued_engine(item):
                self._apply_engine_switch(item.get("fields") or {})
            else:
                self._apply_queued_config(item.get("fields") or {})
        except Exception:
            log.exception("could not apply a queued change for session %s",
                          self.id)

    def _apply_queued_config(self, fields: dict) -> None:
        clean = {k: v for k, v in fields.items() if k in ("model", "effort")}
        if not clean:
            return
        session = db.get_session(self.id)
        if session is None:
            return
        tag = str(fields.get("engine") or "")
        if tag and tag != str(session.get("engine") or ""):
            # its engine switch was cancelled or reordered away: dropping the
            # orphan beats applying model ids another engine validated
            self._emit("info", {
                "subtype": "config_skipped",
                "text": "Skipped a queued model/effort change that belonged "
                        "to {} - this session is on {}.".format(
                            tag, session.get("engine") or "another engine"),
            })
            return
        db.touch_session(self.id, **clean)
        self.broadcast({"type": "session_meta",
                        "session": session_payload(db.get_session(self.id))})
        broadcast_sessions()

    def _finish_activity_block(self, block_started, interrupted,
                               block_status) -> str:
        """Commit the one true idle transition for an activity block."""
        completion_status = "interrupted" if interrupted else block_status
        self.status = "idle"
        self.active_since = None
        self.last_completion_status = completion_status
        try:
            db.touch_session(self.id, status="idle")
        except Exception:
            pass
        try:
            notify.session_finished(
                db.get_session(self.id), completion_status,
                int(time.time() - block_started) if block_started else 0)
        except Exception:
            log.exception("completion notify failed for session %s", self.id)
        return completion_status

    async def interrupt(self, clear_queue: bool = False) -> None:
        if clear_queue:
            self.clear_queue()
        if self.status != "running":
            return
        # counted separately from the sticky flag: a post-turn sync barrier
        # aborts only on a stop pressed after it began, never on the stop
        # that ended the engine turn it is trying to persist
        self._interrupt_count += 1
        already_interrupted = self.interrupted
        self.interrupted = True
        if not already_interrupted:
            self.broadcast({"type": "status", "text": "Interrupting..."})
        proc = self.proc
        # A stop can arrive while create_subprocess_exec or the driver's initial
        # stdin handshake is in flight. _run_turn observes the flag as soon as
        # the process is ready, so this keypress is not lost in that window.
        if already_interrupted or proc is None or proc.returncode is not None or not self._proc_ready:
            return
        await self._interrupt_proc(proc)

    async def _interrupt_proc(self, proc, driver=None) -> None:
        try:
            session = db.get_session(self.id) or {}
            if driver is None:
                driver = get_driver(session["engine"])
            if self.pending_approval is not None:
                cancelled = driver.cancel_approval_payload(self.pending_approval)
                if cancelled is not None and proc.stdin is not None and \
                        not proc.stdin.is_closing():
                    await self._write_stdin(cancelled)
                rid = self.pending_approval.get("request_id", "")
                self.pending_approval = None
                self.broadcast({"type": "approval_resolved", "request_id": rid,
                                "behavior": "cancelled"})
            await self._send_protocol_interrupt(driver, session)
        except Exception as e:
            log.warning("interrupt payload failed for session %s: %s", self.id, e)
        for delay, sig in ((3, signal.SIGINT), (8, signal.SIGKILL)):
            asyncio.get_event_loop().call_later(delay, self._signal_if_alive, proc, sig)

    async def _send_protocol_interrupt(self, driver, session) -> bool:
        """Send the engine-native interrupt once its allocated turn id exists.

        A stop can race a JSONL engine's initialize/thread handshake. The first
        attempt then has no addressable turn; the stdout pump retries after
        each protocol message while the existing signal timers remain the
        bounded fallback.
        """
        if self._interrupt_protocol_sent:
            return True
        proc = self.proc
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            return False
        payload = driver.interrupt_payload(session, self._driver_ctx)
        if payload is None:
            return False
        await self._write_stdin(payload)
        self._interrupt_protocol_sent = True
        return True

    def _signal_if_alive(self, proc, sig) -> None:
        # Engines are spawned in their own process group (start_new_session):
        # signal the whole group, because e.g. codex is a node wrapper whose
        # native child would otherwise survive as an orphan holding thread locks.
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    pass

    async def approval_response(self, request_id: str, behavior: str,
                                message: str = "", updated_permissions=None) -> None:
        pending = self.pending_approval
        if not pending or pending.get("request_id") != request_id:
            return
        self.pending_approval = None
        session = db.get_session(self.id)
        driver = get_driver(session["engine"])
        try:
            payload = driver.approval_payload(request_id, behavior, pending.get("input") or {},
                                              message=self._unscrub_value(message),
                                              updated_permissions=self._unscrub_value(
                                                  updated_permissions),
                                              request=pending)
            await self._write_stdin(payload)
        except Exception as e:
            log.error("approval write failed for session %s: %s", self.id, e)
        # a setMode suggestion accepted -> persist as the session's mode for future turns
        for perm in updated_permissions or []:
            if isinstance(perm, dict) and perm.get("type") == "setMode" and perm.get("mode"):
                db.touch_session(self.id, permission_mode=perm["mode"])
                self.broadcast({"type": "session_meta",
                                "session": session_payload(db.get_session(self.id))})
        self.broadcast({"type": "approval_resolved", "request_id": request_id, "behavior": behavior})

    async def kill(self) -> None:
        context = self._queue_reorder
        if context is not None:
            self._release_queue_reorder(context[0], context[1])
        if self.queue:
            # Parked, not discarded: these prompts were written by a person.
            # They come back after the restart marked as held, for an explicit
            # re-send, because the transcript they were queued behind may have
            # ended mid-thought.
            self.held.extend(self.queue)
            self.queue.clear()
            # Held work is already stopped and has resend/discard controls of
            # its own. Carrying pause into that state would be redundant and
            # would make a later explicit resend surprisingly stay blocked.
            self.paused_queue.clear()
            self._broadcast_queue()
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        # SIGINT first: engines abort the turn cleanly (codex releases its
        # thread writer and records the interruption), then escalate.
        self._signal_if_alive(proc, signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            self._signal_if_alive(proc, signal.SIGKILL)

    # ---- remote workspace ----

    def workspace_barrier_active(self) -> bool:
        """Whether this session's mirror is holding at a sync barrier."""
        return bool(self._ws_phase)

    def workspace_grant(self, body: dict) -> dict:
        """Controller-reported progress or result for a sync barrier."""
        if body.get("progress"):
            if not self._ws_phase:
                return {"ok": False, "error": "no sync barrier is waiting"}
            self._ws_progress_at = time.monotonic()
            return {"ok": True, "phase": self._ws_phase}
        phase = str(body.get("phase") or "")
        if self._ws_phase and phase == self._ws_phase:
            self._ws_result = dict(body)
            self._ws_progress_at = time.monotonic()
            if self._ws_event is not None:
                self._ws_event.set()
            return {"ok": True, "phase": phase}
        if phase == "idle" and self.status != "running":
            # a parked mirror was reconciled between turns: keep the durable
            # dirty flag truthful so blockers and badges clear themselves
            if body.get("ok") and body.get("clean"):
                try:
                    db.touch_session(self.id, ws_dirty=0)
                except Exception:
                    log.warning("could not clear the dirty flag for session %s",
                                self.id)
                broadcast_sessions()
            return {"ok": True, "phase": "idle"}
        return {"ok": False, "error": "no matching sync barrier is waiting"}

    async def _workspace_barrier(self, phase: str) -> dict:
        """Hold the turn at a sync barrier until a controller grant.

        Pre-turn, the engine must not start on a stale mirror; post-turn, the
        turn is not complete until the authoritative project durably holds its
        changes. The hub stays status='running' throughout, so queues, the
        shutdown grace window, and idle gating need no special cases.
        """
        self._ws_phase = phase
        self._ws_event = asyncio.Event()
        self._ws_result = None
        self._ws_progress_at = time.monotonic()
        started = time.monotonic()
        interrupts_before = self._interrupt_count
        self.broadcast({"type": "status", "text": "Syncing workspace files..."})
        broadcast_sessions()   # watchers see workspace_phase and service it
        _notify_workspace_phase(self.id, phase)
        # generous while a controller keeps reporting progress, strict when
        # nothing claims the barrier at all
        hard_limit = 1800.0 if phase == "pre" else 900.0
        quiet_limit = 240.0
        try:
            while not self._ws_event.is_set():
                try:
                    await asyncio.wait_for(self._ws_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                if phase == "pre" and self.interrupted:
                    return {"ok": False, "interrupted": True}
                if phase == "post" and self._interrupt_count > interrupts_before:
                    return {"ok": False,
                            "error": "sync wait stopped by the user"}
                if _draining:
                    return {"ok": False, "error": "backend is shutting down"}
                now = time.monotonic()
                if now - started > hard_limit or \
                        now - self._ws_progress_at > quiet_limit:
                    return {"ok": False,
                            "error": "no controller completed the workspace "
                                     "sync in time"}
            result = self._ws_result
            if not isinstance(result, dict):
                return {"ok": False, "error": "invalid sync grant"}
            return result
        finally:
            self._ws_phase = ""
            self._ws_event = None
            self._ws_result = None
            broadcast_sessions()

    def _park_queue(self) -> None:
        """Move waiting prompts to held, like a restart does: the failure
        that stopped this turn would stop each of them identically."""
        if not self.queue:
            return
        self.held.extend(self.queue)
        self.queue.clear()
        self.paused_queue.clear()
        self._broadcast_queue()

    def _scrub_value(self, value):
        """Present the authoritative project path instead of the mirror."""
        if not self._ws_map:
            return value
        prefix, label = self._ws_map
        if isinstance(value, str):
            return value.replace(prefix, label) if prefix in value else value
        if isinstance(value, list):
            return [self._scrub_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._scrub_value(item) for key, item in value.items()}
        return value

    def _unscrub_value(self, value):
        """User-authored approval edits travel back in the engine's paths."""
        if not self._ws_map:
            return value
        prefix, label = self._ws_map
        if isinstance(value, str):
            return value.replace(label, prefix) if label in value else value
        if isinstance(value, list):
            return [self._unscrub_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._unscrub_value(item) for key, item in value.items()}
        return value

    # ---- turn internals ----

    async def _write_stdin(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("no stdin")
        async with self._stdin_lock:
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            await proc.stdin.drain()

    def _emit(self, kind: str, data: dict) -> dict:
        ev = db.add_event(self.id, kind, self._scrub_value(data))
        self.broadcast({"type": "event", "event": ev})
        return ev

    def _note_turn_config(self, session) -> None:
        """Record the model/effort this turn is actually run with, and mark the
        transcript when it differs from the previous turn's.

        Deliberately driven by turns, not by the picker: choosing a model in the
        UI changes nothing until a prompt is sent under it, so a selection that
        never reached an engine must not claim the session moved. The first turn
        of a session - or the first after an engine switch - only establishes
        the baseline; there is no earlier configuration to have moved from."""
        previous = parse_used_config(session.get("used_config"))
        current = {"model": session.get("model") or "", "effort": session.get("effort") or ""}
        self._model_move_announced = False
        if previous == current:
            return
        if previous is not None:
            def desc(cfg):
                return " ".join(x for x in (cfg["model"] or "default", cfg["effort"]) if x)
            self._emit("info", {
                "subtype": "config_change",
                "text": f"model/effort changed: {desc(previous)} → {desc(current)}",
                # the engine these names belong to: a later switch must not make
                # the WebUI read this line against a different engine's catalog
                "engine": session.get("engine") or "",
                "from_model": previous["model"], "from_effort": previous["effort"],
                "to_model": current["model"], "to_effort": current["effort"],
            })
            self._model_move_announced = current["model"] != previous["model"]
        raw = json.dumps(current)
        session["used_config"] = raw
        db.touch_session(self.id, used_config=raw)
        self.broadcast({"type": "session_meta",
                        "session": session_payload(db.get_session(self.id))})

    async def _run_turn(self, text: str) -> None:
        got_result = False
        user_event_persisted = False
        engine_ran = False
        descriptor = None
        self._block_status = "error"   # until a result says otherwise
        try:
            session = db.get_session(self.id)
            descriptor = workspace_sync.session_workspace(session)
            self._ws_map = ((session["cwd"],
                             str(descriptor.get("root") or session["cwd"]))
                            if descriptor else None)
            try:
                session, workspace_reset = workspaces.ensure_session(session)
            except workspaces.WorkspaceError as exc:
                self._emit("error", {"text": "Scratch workspace unavailable: {}".format(exc)})
                return
            if workspace_reset:
                self._emit("info", {
                    "subtype": "workspace_reset",
                    "text": "Scratch workspace recreated; its previous temporary files were cleared.",
                })
                self.broadcast({"type": "session_meta", "session": session_payload(session)})
                broadcast_sessions()
            driver = get_driver(session["engine"])
            db.touch_session(self.id, status="running")
            broadcast_sessions()

            fresh_native_session = _starts_fresh_native_session(
                session, workspace_reset, driver)
            do_handoff = fresh_native_session and \
                (workspace_reset or handoff.needs_handoff(session))
            # ahead of the prompt: the divider introduces the turns below it
            self._note_turn_config(session)
            user_ev = self._emit("user", {"text": text})
            user_event_persisted = True

            if descriptor is not None:
                grant = await self._workspace_barrier("pre")
                if grant.get("interrupted"):
                    self._emit("info", {
                        "subtype": "interrupted",
                        "text": "Turn interrupted while syncing the workspace"})
                    return
                if not grant.get("ok"):
                    self._emit("error", {"text": "Workspace sync failed: {}".format(
                        grant.get("error") or "unknown error")})
                    self._park_queue()
                    return
                if grant.get("conflicts"):
                    self._emit("info", {
                        "subtype": "workspace_conflicts",
                        "text": "{} path(s) differ between the project and this "
                                "session's copy; conflicting paths keep their "
                                "local state this turn - resolve them from the "
                                "workspace chip.".format(grant["conflicts"])})
                try:
                    db.touch_session(self.id, ws_dirty=1)
                except Exception:
                    log.warning("could not mark session %s workspace dirty",
                                self.id)

            prompt = text
            first_turn = fresh_native_session
            if do_handoff:
                prompt = handoff.build(session, exclude_seq=user_ev["seq"]) + text
                self.broadcast({"type": "status", "text": "Seeding new engine with handoff..."})

            pinned = str(uuid.uuid4())
            self._active_turn_id = pinned
            self._browser_activity_announced = set()
            self._terminal_activity_announced = set()
            browser_mcp = browser_agent.turn_mcp(self.id, pinned)
            terminal_mcp = terminal_agent.turn_mcp(self.id, pinned)
            system_prompt_text = system_prompts.turn_prompt(
                remote_workspace=descriptor is not None)
            argv = driver.build_cmd(
                session, first_turn, prompt, pinned, browser_mcp=browser_mcp,
                terminal_mcp=terminal_mcp,
                system_prompt=system_prompt_text)
            env = clean_env(dict(os.environ))
            runtime_home = os.path.expanduser("~")
            if runtime_home and runtime_home != "~":
                env.setdefault("HOME", runtime_home)
            env.update(driver.build_env(
                session, first_turn, prompt, pinned, browser_mcp=browser_mcp,
                terminal_mcp=terminal_mcp,
                system_prompt=system_prompt_text))
            ctx = driver.turn_context(
                session, first_turn, prompt, pinned, browser_mcp=browser_mcp,
                terminal_mcp=terminal_mcp,
                system_prompt=system_prompt_text)
            if not isinstance(ctx, dict):
                ctx = {}
            self._driver_ctx = ctx

            cwd = session["cwd"]
            if not os.path.isdir(cwd):
                self._emit("error", {"text": f"Working directory missing: {cwd}"})
                return

            log.info("session %s turn: %s", self.id, " ".join(argv[:8]) + " ...")
            turn_started = time.time()
            self.proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE if driver.uses_stdin_stream else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,   # own process group so signals reach wrapper + children
                limit=STREAM_LIMIT)
            engine_ran = True

            stderr_task = asyncio.ensure_future(self._pump_stderr(self.proc))

            if driver.uses_stdin_stream:
                for obj in driver.initial_stdin(session, prompt):
                    await self._write_stdin(obj)

            self._proc_ready = True
            if self.interrupted:
                await self._interrupt_proc(self.proc, driver)

            timeout = float(config.get("sessions.turn_timeout", 7200))
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._emit("error", {"text": f"Turn timeout after {int(timeout)}s - killed"})
                    self._signal_if_alive(self.proc, signal.SIGKILL)
                    break
                try:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=min(remaining, 60))
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                try:
                    actions = driver.parse_line(line.decode(errors="replace").strip(), ctx)
                except Exception as e:
                    log.exception("parse_line failed: %s", e)
                    continue
                for act in actions:
                    a = act.get("a")
                    if a == "event":
                        self._emit(act["kind"], act["data"])
                    elif a == "transient":
                        self.broadcast(self._scrub_value(act["msg"]))
                    elif a == "native_id":
                        nid = act.get("id") or ""
                        if nid and nid != session.get("native_session_id"):
                            session["native_session_id"] = nid
                            db.touch_session(self.id, native_session_id=nid)
                    elif a == "model":
                        new_model = act["model"]
                        old_model = session.get("last_model") or ""
                        if new_model != old_model:
                            requested = (session.get("model") or "").strip()
                            mismatch = requested and requested.lower() not in new_model.lower()
                            # this turn's own divider already announced the move,
                            # so only an unasked-for one is worth a warning line
                            announced = self._model_move_announced and not mismatch
                            self._model_move_announced = False
                            if not announced:
                                if old_model:
                                    self._emit("info", {"subtype": "model_switch",
                                                        "text": f"engine model changed: {old_model} → {new_model}"})
                                elif mismatch:
                                    self._emit("info", {"subtype": "model_switch",
                                                        "text": f"requested model '{requested}' but engine is serving {new_model}"})
                            session["last_model"] = new_model
                            db.touch_session(self.id, last_model=new_model)
                            self.broadcast({"type": "session_meta",
                                            "session": session_payload(db.get_session(self.id))})
                    elif a == "approval":
                        # stored raw: the reply must echo the engine's own
                        # paths, so only the broadcast copy is rewritten
                        self.pending_approval = act["req"]
                        self.broadcast({"type": "approval_request",
                                        "req": self._scrub_value(act["req"])})
                    elif a == "approval_cancel":
                        if self.pending_approval and self.pending_approval.get("request_id") == act.get("request_id"):
                            self.pending_approval = None
                        self.broadcast({"type": "approval_resolved",
                                        "request_id": act.get("request_id", ""), "behavior": "cancelled"})
                    elif a == "stdin":
                        payload = act.get("data")
                        if isinstance(payload, dict):
                            await self._write_stdin(payload)
                    elif a == "rate_limit":
                        # stamped so consoles can say how fresh the figure is;
                        # additive beside the CLI's own camelCase keys
                        info = dict(act["info"] or {})
                        info["captured_at"] = time.time()
                        db.meta_set(f"rate_limit.{session['engine']}", info)
                        self.broadcast({"type": "rate_limit", "engine": session["engine"], "info": info})
                    elif a == "result":
                        if self.interrupted and act["data"].get("stop_reason") in \
                                ("cancelled", "canceled"):
                            # A persistent JSONL service still needs EOF before
                            # it exits. Preserve the existing interrupted
                            # transcript path while releasing that service.
                            if driver.uses_stdin_stream and self.proc.stdin is not None:
                                try:
                                    self.proc.stdin.close()
                                except Exception:
                                    pass
                            continue
                        got_result = True
                        # Every engine's turn reports how long it took: drivers
                        # whose CLI times its own work keep that figure, the
                        # rest (codex) get the wall clock from spawn to result.
                        if act["data"].get("duration_ms") is None:
                            act["data"]["duration_ms"] = int((time.time() - turn_started) * 1000)
                        self._block_status = "ok" if act["data"].get("ok") else "error"
                        if act["data"].get("ok"):
                            driver_base.clear_auth_failure(session["engine"])
                        elif driver_base.looks_like_auth_failure(act["data"].get("error")):
                            driver_base.note_auth_failure(
                                session["engine"], str(act["data"].get("error") or ""))
                        self._emit("result", act["data"])
                        # claude: close stdin so the process exits cleanly
                        if driver.uses_stdin_stream and self.proc.stdin is not None:
                            try:
                                self.proc.stdin.close()
                            except Exception:
                                pass

                if self.interrupted and not self._interrupt_protocol_sent:
                    try:
                        await self._send_protocol_interrupt(driver, session)
                    except Exception as e:
                        log.warning("deferred interrupt payload failed for session %s: %s",
                                    self.id, e)

            try:
                await asyncio.wait_for(self.proc.wait(), timeout=20)
            except asyncio.TimeoutError:
                self._signal_if_alive(self.proc, signal.SIGKILL)
                await self.proc.wait()
            stderr_task.cancel()

            if not got_result:
                if self.interrupted:
                    self._emit("info", {"subtype": "interrupted", "text": "Turn interrupted by user"})
                else:
                    tail = self.stderr_tail.strip()[-1500:]
                    if driver_base.looks_like_auth_failure(tail):
                        driver_base.note_auth_failure(session["engine"], tail[-400:])
                    self._emit("error", {"text": "Engine exited without a result"
                                                 + (f" (exit {self.proc.returncode})" if self.proc.returncode else "")
                                                 + (f"\n{tail}" if tail else "")})
        except Exception as e:
            log.exception("turn failed for session %s", self.id)
            try:
                self._emit("error", {"text": f"Internal error: {e}"})
            except Exception:
                pass
        finally:
            self._active_prompt_text = ""
            if not user_event_persisted:
                self._discard_abandoned_uploads([text])
            self._active_turn_id = ""
            self._browser_activity_announced = set()
            self._terminal_activity_announced = set()
            if self.pending_approval is not None:
                rid = self.pending_approval.get("request_id", "")
                self.pending_approval = None
                self.broadcast({"type": "approval_resolved", "request_id": rid, "behavior": "cancelled"})
            self.proc = None
            self._proc_ready = False
            self._driver_ctx = None
            self._interrupt_protocol_sent = False
            self.stderr_tail = ""
            # A failed turn is fresh evidence about the engine (auth revoked,
            # binary broken): drop its cached probe so the next status poll
            # re-reads the truth instead of serving up to five stale minutes.
            if self._block_status == "error" and not self.interrupted:
                try:
                    driver_base.invalidate_status(session["engine"])
                except Exception:
                    pass
            if descriptor is not None and engine_ran:
                # the turn is not over until the authoritative project holds
                # this turn's files; an unserviced barrier leaves the durable
                # dirty flag set and the controller retries while idle
                try:
                    grant = await self._workspace_barrier("post")
                    if grant.get("ok"):
                        if grant.get("clean"):
                            db.touch_session(self.id, ws_dirty=0)
                        if grant.get("conflicts"):
                            self._emit("info", {
                                "subtype": "workspace_conflicts",
                                "text": "{} path(s) changed on both sides "
                                        "during this turn; both versions are "
                                        "preserved - resolve them from the "
                                        "workspace chip.".format(
                                            grant["conflicts"])})
                    else:
                        self._emit("info", {
                            "subtype": "workspace_sync_pending",
                            "text": "Workspace sync did not complete: {}. This "
                                    "backend keeps the changes and Puppy retries "
                                    "automatically.".format(
                                        grant.get("error") or "unknown error")})
                except Exception:
                    log.exception("post-turn workspace barrier failed for "
                                  "session %s", self.id)
            block_started = self.active_since
            queue_waiting = self._queue_reorder is not None and bool(self.queue)
            if queue_waiting:
                # The acknowledged drag owns the transition. Mark the engine
                # idle without ending the activity block; its drop/cancel path
                # either starts the chosen next prompt or commits completion.
                nxt = None
                continued = False
                completion_status = ""
                self.status = "idle"
                self._queue_waiting_completion = (
                    block_started, self.interrupted, self._block_status)
                try:
                    db.touch_session(self.id, status="idle")
                except Exception:
                    pass
            else:
                nxt = self._take_next_turn()
                continued = nxt is not None
                completion_status = "" if continued else \
                    self._finish_activity_block(
                        block_started, self.interrupted, self._block_status)
            self.broadcast({"type": "turn_done", "continued": continued,
                            "queue_waiting": queue_waiting,
                            "completion_status": completion_status})
            if continued:
                self._broadcast_queue()
                self._start_turn(nxt)
            else:
                broadcast_sessions()

    async def _pump_stderr(self, proc) -> None:
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                self.stderr_tail = (self.stderr_tail + chunk.decode(errors="replace"))[-4000:]
        except (asyncio.CancelledError, Exception):
            pass


_draining = False


def begin_shutdown() -> None:
    """Synchronously stop a finishing turn from consuming queued work."""
    global _draining
    _draining = True


async def shutdown() -> None:
    begin_shutdown()
    # Restarts are often triggered by an agent running INSIDE puppy (the user
    # drives puppy development through puppy). Give in-flight turns a grace
    # window to finish instead of SIGKILLing them mid-answer; the host service's
    # stop timeout must stay above this.
    grace = float(config.get("sessions.shutdown_grace", 60))
    deadline = time.time() + grace
    running = [h.id for h in _hubs.values() if h.status == "running"]
    if running:
        log.info("shutdown: waiting up to %.0fs for running turn(s) in session(s) %s",
                 grace, running)
    while time.time() < deadline and any(h.status == "running" for h in _hubs.values()):
        await asyncio.sleep(0.5)
    for h in list(_hubs.values()):
        await h.kill()
