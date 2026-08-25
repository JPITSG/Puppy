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

from puppy import config, db, handoff, uploads, workspaces
from puppy.drivers import get_driver
from puppy.drivers.base import clean_env

log = logging.getLogger("puppy.runner")

_hubs = {}
_updates_watchers = set()  # websockets watching the session list

STREAM_LIMIT = 16 * 1024 * 1024


def hub(session_id: int) -> "SessionHub":
    h = _hubs.get(session_id)
    if h is None:
        h = SessionHub(session_id)
        _hubs[session_id] = h
    return h


def drop_hub(session_id: int) -> None:
    h = _hubs.pop(session_id, None)
    if h:
        asyncio.ensure_future(h.kill())


# ---- session-list broadcasting ----

def _is_queued_config(item) -> bool:
    """Queue items are prompt strings, except pending model/effort changes."""
    return isinstance(item, dict) and item.get("kind") == "config"


def _queued_config_key(fields: dict) -> str:
    """Stable identity a console echoes back to cancel a pending change."""
    return "config:" + json.dumps(fields, sort_keys=True)


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


def session_payload(session):
    if session is None:
        return None
    out = dict(session)
    out["workspace_kind"] = out.get("workspace_kind") or workspaces.KIND_DIRECTORY
    out["workspace_missing"] = workspaces.is_temporary(out) and not workspaces.is_available(out)
    out["used_config"] = parse_used_config(out.get("used_config"))
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
            "updated_at": s["updated_at"], "model": s["model"], "last_model": s["last_model"],
            "effort": s["effort"], "color": s["color"], "permission_mode": s["permission_mode"],
            "has_native": bool(s["native_session_id"]),
            "workspace_kind": s.get("workspace_kind") or workspaces.KIND_DIRECTORY,
            "workspace_missing": workspaces.is_temporary(s) and not workspaces.is_available(s),
        })
    return {"type": "sessions", "server_time": now, "sessions": sessions}


def upgrade_blockers() -> list:
    """Sessions whose running turn or queued work makes a restart unsafe."""
    return [
        {"id": h.id, "running": h.status == "running", "queued": len(h.queue)}
        for h in _hubs.values() if h.status == "running" or h.queue
    ]


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


def broadcast_sessions() -> None:
    broadcast_update(sessions_payload())


def broadcast_update(payload: dict) -> None:
    """Send an additive controller update to every authenticated list watcher."""
    for ws in list(_updates_watchers):
        asyncio.ensure_future(_safe_send(ws, payload, _updates_watchers))


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
        self.status = "idle"
        # Start of one uninterrupted block of work. Queued turns inherit this
        # timestamp; it is cleared only when the turn and its queue are empty.
        self.active_since = None
        self.proc = None
        self._proc_ready = False
        self.turn_task = None
        self.pending_approval = None
        self.interrupted = False
        self.stderr_tail = ""
        self._stdin_lock = asyncio.Lock()
        # whether this turn's divider already announced a model move, so the
        # engine reporting that same move is not repeated as if it surprised us
        self._model_move_announced = False

    # ---- watchers ----

    def attach(self, ws) -> None:
        self.watchers.add(ws)

    def detach(self, ws) -> None:
        self.watchers.discard(ws)

    def broadcast(self, payload: dict) -> None:
        for ws in list(self.watchers):
            asyncio.ensure_future(_safe_send(ws, payload, self.watchers))

    def snapshot(self) -> dict:
        session = db.get_session(self.id)
        return {
            "type": "snapshot",
            "session": session_payload(session),
            "events": db.get_events(self.id, limit=200),
            "status": self.status,
            "active_since": self.active_since if self.status == "running" else None,
            "server_time": time.time(),
            "queued": self._queue_wire(),
            "pending_approval": self.pending_approval,
            "uploads": uploads.settings_payload(),
        }

    def _queue_wire(self) -> list:
        """Wire form of the queue: prompts stay plain strings, so consoles from
        before pending changes existed keep rendering them; a pending change is
        an additive {kind:"config", key, model?, effort?} entry."""
        out = []
        for item in self.queue:
            if _is_queued_config(item):
                entry = {"kind": "config", "key": item.get("key") or ""}
                entry.update(item.get("fields") or {})
                out.append(entry)
            else:
                out.append(item)
        return out

    def _broadcast_queue(self) -> None:
        self.broadcast({"type": "queued", "queued": self._queue_wire()})

    # ---- public ops ----

    def send_message(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"error": "empty message"}
        session = db.get_session(self.id)
        if session is None:
            return {"error": "session gone"}
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
            return {"queued": True}
        self._start_turn(text)
        return {"queued": False}

    def queue_config(self, fields: dict) -> bool:
        """Hold a model/effort change until everything already queued has run:
        the user changed it after sending those prompts, so they belong to the
        configuration that was showing when they were written.

        Only a real difference is held. Consecutive changes collapse into the
        one pending entry, and each field is measured against what is already
        in force at that point in the queue - fiddling with the pickers and
        landing back on the current value leaves nothing pending, and trims a
        pending entry that no longer changes anything.

        True means the caller must not apply these fields to the session now."""
        clean = {k: str(v) for k, v in fields.items() if k in ("model", "effort")}
        if not clean:
            return False
        if self.status != "running" and not self.queue:
            return False
        session = db.get_session(self.id) or {}
        tail = self.queue[-1] if self.queue and _is_queued_config(self.queue[-1]) else None
        # what runs just before the tail entry: the session's own configuration
        # plus every pending change queued ahead of it
        base = {"model": session.get("model") or "", "effort": session.get("effort") or ""}
        for item in self.queue:
            if _is_queued_config(item) and item is not tail:
                base.update(item.get("fields") or {})
        merged = dict(tail.get("fields") or {}) if tail else {}
        merged.update(clean)
        merged = {k: v for k, v in merged.items() if v != base.get(k, "")}
        if tail and merged:
            tail["fields"] = merged
            tail["key"] = _queued_config_key(merged)
        elif tail:
            self.queue.pop()
        elif merged:
            self.queue.append({"kind": "config", "fields": merged,
                               "key": _queued_config_key(merged)})
        else:
            return True   # already in force further down the queue: nothing to do
        self._broadcast_queue()
        return True

    def unqueue(self, index: int, text: str) -> dict:
        """Drop a message or pending change that is still waiting. The index is
        guarded by the item's text (its key, for a change) so a turn finishing
        between the click and this call - which shifts every index down by one
        - cannot cancel the wrong item."""
        if not 0 <= index < len(self.queue):
            return {"error": "that message already started"}
        item = self.queue[index]
        ident = item.get("key") if _is_queued_config(item) else item
        if ident != text:
            return {"error": "that message already started"}
        self.queue.pop(index)
        self._broadcast_queue()
        return {"ok": True}

    def clear_queue(self) -> int:
        """Drop everything that has not started yet and return the count."""
        count = len(self.queue)
        if count:
            self.queue.clear()
            self._broadcast_queue()
        return count

    def _start_turn(self, text: str) -> None:
        if self.active_since is None:
            self.active_since = time.time()
        self.status = "running"
        self.interrupted = False
        self._proc_ready = False
        self.turn_task = asyncio.ensure_future(self._run_turn(text))
        # Publish the active block immediately, before process startup and the
        # first persisted event have a chance to yield the event loop.
        broadcast_sessions()

    def _take_next_turn(self):
        """Advance within an activity block, or close it when the queue is
        empty. Pending model/effort changes at the front apply now - everything
        queued ahead of them has finished - so the next prompt taken runs under
        the configuration that was current when it was sent."""
        applied = False
        while self.queue and _is_queued_config(self.queue[0]):
            self._apply_queued_config(self.queue.pop(0).get("fields") or {})
            applied = True
        if applied:
            self._broadcast_queue()
        if self.queue:
            return self.queue.pop(0)
        self.status = "idle"
        self.active_since = None
        return None

    def _apply_queued_config(self, fields: dict) -> None:
        clean = {k: v for k, v in fields.items() if k in ("model", "effort")}
        if not clean:
            return
        db.touch_session(self.id, **clean)
        self.broadcast({"type": "session_meta",
                        "session": session_payload(db.get_session(self.id))})
        broadcast_sessions()

    async def interrupt(self, clear_queue: bool = False) -> None:
        if clear_queue:
            self.clear_queue()
        if self.status != "running":
            return
        already_interrupted = self.interrupted
        self.interrupted = True
        if not already_interrupted:
            self.broadcast({"type": "status", "text": "interrupting..."})
        proc = self.proc
        # A stop can arrive while create_subprocess_exec or the driver's initial
        # stdin handshake is in flight. _run_turn observes the flag as soon as
        # the process is ready, so this keypress is not lost in that window.
        if already_interrupted or proc is None or proc.returncode is not None or not self._proc_ready:
            return
        await self._interrupt_proc(proc)

    async def _interrupt_proc(self, proc, driver=None) -> None:
        try:
            if driver is None:
                session = db.get_session(self.id)
                driver = get_driver(session["engine"])
            payload = driver.interrupt_payload()
            if payload is not None and proc.stdin is not None and not proc.stdin.is_closing():
                await self._write_stdin(payload)
        except Exception as e:
            log.warning("interrupt payload failed for session %s: %s", self.id, e)
        for delay, sig in ((3, signal.SIGINT), (8, signal.SIGKILL)):
            asyncio.get_event_loop().call_later(delay, self._signal_if_alive, proc, sig)

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
                                              message=message, updated_permissions=updated_permissions)
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
        self.queue.clear()
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

    # ---- turn internals ----

    async def _write_stdin(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("no stdin")
        async with self._stdin_lock:
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            await proc.stdin.drain()

    def _emit(self, kind: str, data: dict) -> dict:
        ev = db.add_event(self.id, kind, data)
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
        try:
            session = db.get_session(self.id)
            try:
                session, workspace_reset = workspaces.ensure_session(session)
            except workspaces.WorkspaceError as exc:
                self._emit("error", {"text": "scratch workspace unavailable: {}".format(exc)})
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

            do_handoff = (not session.get("native_session_id")) and \
                (workspace_reset or handoff.needs_handoff(session))
            # ahead of the prompt: the divider introduces the turns below it
            self._note_turn_config(session)
            user_ev = self._emit("user", {"text": text})

            prompt = text
            first_turn = not session.get("native_session_id")
            if do_handoff:
                prompt = handoff.build(session, exclude_seq=user_ev["seq"]) + text
                self.broadcast({"type": "status", "text": "seeding new engine with handoff..."})

            pinned = str(uuid.uuid4())
            argv = driver.build_cmd(session, first_turn, prompt, pinned)
            env = clean_env(dict(os.environ))
            env.setdefault("HOME", "/root")

            cwd = session["cwd"]
            if not os.path.isdir(cwd):
                self._emit("error", {"text": f"working directory missing: {cwd}"})
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

            stderr_task = asyncio.ensure_future(self._pump_stderr(self.proc))

            if driver.uses_stdin_stream:
                for obj in driver.initial_stdin(session, prompt):
                    await self._write_stdin(obj)

            self._proc_ready = True
            if self.interrupted:
                await self._interrupt_proc(self.proc, driver)

            timeout = float(config.get("sessions.turn_timeout", 7200))
            deadline = time.time() + timeout
            ctx = {}
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._emit("error", {"text": f"turn timeout after {int(timeout)}s - killed"})
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
                        self.broadcast(act["msg"])
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
                        self.pending_approval = act["req"]
                        self.broadcast({"type": "approval_request", "req": act["req"]})
                    elif a == "approval_cancel":
                        if self.pending_approval and self.pending_approval.get("request_id") == act.get("request_id"):
                            self.pending_approval = None
                        self.broadcast({"type": "approval_resolved",
                                        "request_id": act.get("request_id", ""), "behavior": "cancelled"})
                    elif a == "rate_limit":
                        db.meta_set(f"rate_limit.{session['engine']}", act["info"])
                        self.broadcast({"type": "rate_limit", "engine": session["engine"], "info": act["info"]})
                    elif a == "result":
                        got_result = True
                        # Every engine's turn reports how long it took: drivers
                        # whose CLI times its own work keep that figure, the
                        # rest (codex) get the wall clock from spawn to result.
                        if act["data"].get("duration_ms") is None:
                            act["data"]["duration_ms"] = int((time.time() - turn_started) * 1000)
                        self._emit("result", act["data"])
                        # claude: close stdin so the process exits cleanly
                        if driver.uses_stdin_stream and self.proc.stdin is not None:
                            try:
                                self.proc.stdin.close()
                            except Exception:
                                pass

            try:
                await asyncio.wait_for(self.proc.wait(), timeout=20)
            except asyncio.TimeoutError:
                self._signal_if_alive(self.proc, signal.SIGKILL)
                await self.proc.wait()
            stderr_task.cancel()

            if not got_result:
                if self.interrupted:
                    self._emit("info", {"subtype": "interrupted", "text": "turn interrupted by user"})
                else:
                    tail = self.stderr_tail.strip()[-1500:]
                    self._emit("error", {"text": "engine exited without a result"
                                                 + (f" (exit {self.proc.returncode})" if self.proc.returncode else "")
                                                 + (f"\n{tail}" if tail else "")})
        except Exception as e:
            log.exception("turn failed for session %s", self.id)
            try:
                self._emit("error", {"text": f"internal error: {e}"})
            except Exception:
                pass
        finally:
            if self.pending_approval is not None:
                rid = self.pending_approval.get("request_id", "")
                self.pending_approval = None
                self.broadcast({"type": "approval_resolved", "request_id": rid, "behavior": "cancelled"})
            self.proc = None
            self._proc_ready = False
            self.stderr_tail = ""
            nxt = self._take_next_turn()
            continued = nxt is not None
            if not continued:
                try:
                    db.touch_session(self.id, status="idle")
                except Exception:
                    pass
            self.broadcast({"type": "turn_done", "continued": continued})
            if continued:
                self.broadcast({"type": "queued", "queued": list(self.queue)})
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


async def shutdown() -> None:
    # Restarts are often triggered by an agent running INSIDE puppy (the user
    # drives puppy development through puppy). Give in-flight turns a grace
    # window to finish instead of SIGKILLing them mid-answer; supervisor's
    # stopwaitsecs must stay above this.
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
