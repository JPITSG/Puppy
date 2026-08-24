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

from puppy import config, db, handoff
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

def updates_attach(ws) -> None:
    _updates_watchers.add(ws)


def updates_detach(ws) -> None:
    _updates_watchers.discard(ws)


def sessions_payload() -> dict:
    sessions = []
    for s in db.list_sessions(include_archived=True):
        h = _hubs.get(s["id"])
        sessions.append({
            "id": s["id"], "name": s["name"], "engine": s["engine"], "cwd": s["cwd"],
            "status": (h.status if h else "idle"), "archived": s["archived"],
            "updated_at": s["updated_at"], "model": s["model"], "last_model": s["last_model"],
            "effort": s["effort"], "color": s["color"], "permission_mode": s["permission_mode"],
            "has_native": bool(s["native_session_id"]),
        })
    return {"type": "sessions", "sessions": sessions}


def broadcast_sessions() -> None:
    payload = sessions_payload()
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
        self.proc = None
        self.turn_task = None
        self.pending_approval = None
        self.interrupted = False
        self.stderr_tail = ""
        self._stdin_lock = asyncio.Lock()

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
            "session": session,
            "events": db.get_events(self.id, limit=200),
            "status": self.status,
            "queued": list(self.queue),
            "pending_approval": self.pending_approval,
        }

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
        if self.status == "running":
            self.queue.append(text)
            self.broadcast({"type": "queued", "queued": list(self.queue)})
            return {"queued": True}
        self._start_turn(text)
        return {"queued": False}

    def _start_turn(self, text: str) -> None:
        self.status = "running"
        self.interrupted = False
        self.turn_task = asyncio.ensure_future(self._run_turn(text))

    async def interrupt(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        self.interrupted = True
        self.broadcast({"type": "status", "text": "interrupting..."})
        session = db.get_session(self.id)
        try:
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
                self.broadcast({"type": "session_meta", "session": db.get_session(self.id)})
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

    async def _run_turn(self, text: str) -> None:
        got_result = False
        try:
            session = db.get_session(self.id)
            driver = get_driver(session["engine"])
            db.touch_session(self.id, status="running")
            broadcast_sessions()

            do_handoff = (not session.get("native_session_id")) and handoff.needs_handoff(session)
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
                        if act["model"] != session.get("last_model"):
                            session["last_model"] = act["model"]
                            db.touch_session(self.id, last_model=act["model"])
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
            self.status = "idle"
            self.stderr_tail = ""
            try:
                db.touch_session(self.id, status="idle")
            except Exception:
                pass
            self.broadcast({"type": "turn_done"})
            broadcast_sessions()
            if self.queue:
                nxt = self.queue.pop(0)
                self.broadcast({"type": "queued", "queued": list(self.queue)})
                self._start_turn(nxt)

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
