"""Node-owned interactive PTY terminals shared by xterm.js and model turns.

Each identified terminal has exactly one PTY reader. Output is fanned out to
every attached WebSocket viewer and retained in a bounded buffer for the
turn-scoped terminal MCP bridge. User and agent input pass through one lock so
individual writes stay intact. The legacy anonymous WebSocket remains for
older controllers and keeps its original viewer-owned lifetime.
"""
from __future__ import annotations

import asyncio
import collections
import fcntl
import json
import logging
import os
import pty
import re
import secrets
import shlex
import signal
import string
import struct
import termios
import time

from aiohttp import WSMsgType, web

from puppy import config, live_websockets
from puppy.drivers.base import clean_env
from puppy.user_paths import service_home

log = logging.getLogger("puppy.terminal")

TERMINAL_ID_ALPHABET = string.ascii_uppercase + string.digits
TERMINAL_ID_RE = re.compile(r"^[A-Z0-9]{4}$")
# Preserve the viewer socket's existing one-frame paste ceiling. Agent-side
# typing has its own much smaller bound in terminal_agent.py.
MAX_INPUT = 1 << 20
MAX_RAW_OUTPUT = 2 * 1024 * 1024
MAX_AGENT_TEXT = 64 * 1024
MAX_COMMAND = 16 * 1024
MAX_CWD = 4096
IDLE_STOP_SECONDS = 15 * 60

_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.S)
_ESC_RE = re.compile(r"\x1b[@-_]")
_CONTROL_RE = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1f\x7f]")

_active_terminals = 0
_manager = None


class TerminalError(RuntimeError):
    pass


def _state_changed() -> None:
    """Publish the cheap catalog and refresh backend-upgrade readiness."""
    try:
        from puppy import state_stream
        instances = _manager.instance_payloads() if _manager is not None else []
        state_stream.publish({"type": "terminal_instances", "instances": instances})
        state_stream.wake("node")
    except Exception:
        pass


def active_count() -> int:
    return _active_terminals


def normalize_terminal_id(value) -> str:
    terminal_id = str(value or "").strip().upper()
    if not TERMINAL_ID_RE.fullmatch(terminal_id):
        raise TerminalError("invalid Terminal ID")
    return terminal_id


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _plain_terminal_text(raw: bytes, max_chars: int, max_lines: int) -> str:
    """Return a bounded shell-oriented view without terminal controls."""
    text = raw.decode("utf-8", errors="replace")
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    lines = [[]]
    cursor = 0
    for char in text:
        if char == "\n":
            lines.append([])
            cursor = 0
            continue
        if char == "\r":
            cursor = 0
            continue
        if char == "\b":
            cursor = max(0, cursor - 1)
            continue
        if char == "\t":
            spaces = 8 - (cursor % 8)
            for _ in range(spaces):
                line = lines[-1]
                if cursor < len(line):
                    line[cursor] = " "
                else:
                    line.append(" ")
                cursor += 1
            continue
        line = lines[-1]
        if cursor < len(line):
            line[cursor] = char
        else:
            if cursor > len(line):
                line.extend(" " for _ in range(cursor - len(line)))
            line.append(char)
        cursor += 1
    rendered = ["".join(line).rstrip() for line in lines]
    if max_lines > 0 and len(rendered) > max_lines:
        rendered = rendered[-max_lines:]
    result = "\n".join(rendered).rstrip()
    if len(result) > max_chars:
        result = result[-max_chars:]
    return result


class _Viewer:
    """One ordered output queue so replay always precedes live PTY bytes."""

    def __init__(self, ws):
        self.ws = ws
        self.queue = asyncio.Queue(maxsize=512)
        self.closed = False
        self.task = asyncio.ensure_future(self._run())

    def _put(self, item) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.closed = True
            asyncio.ensure_future(self.ws.close(
                code=1011, message=b"terminal viewer could not keep up"))

    def send_bytes(self, data: bytes) -> None:
        self._put(("bytes", bytes(data)))

    def send_json(self, payload: dict) -> None:
        self._put(("json", dict(payload)))

    def finish(self) -> None:
        self._put(None)

    def cancel(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.task.cancel()

    async def _run(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    break
                kind, payload = item
                if kind == "bytes":
                    await self.ws.send_bytes(payload)
                else:
                    await self.ws.send_json(payload)
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self.closed = True
            try:
                await self.ws.close()
            except Exception:
                pass


class TerminalInstance:
    """One PTY process, its viewers, transcript, and serialized input."""

    def __init__(self, terminal_id: str, command: str, cwd: str, cols: int,
                 rows: int, origin: str = "user", owner_session=None):
        self.terminal_id = normalize_terminal_id(terminal_id)
        if origin not in ("user", "agent", "legacy"):
            raise TerminalError("invalid terminal origin")
        self.origin = origin
        self.owner_session = int(owner_session) if owner_session is not None else None
        self.command = command
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.pid = None
        self.master = None
        self.running = False
        self.closed = False
        self.started_at = 0.0
        self.ended_reason = ""
        self.viewers = {}
        self.input_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.raw_chunks = collections.deque()
        self.raw_size = 0
        self.output_sequence = 0
        self.output_truncated = False
        self.output_event = asyncio.Event()
        self.idle_task = None
        self.end_task = None

    def viewer_count(self) -> int:
        return len(self.viewers)

    def status_payload(self) -> dict:
        return {
            "type": "status", "terminal_id": self.terminal_id,
            "running": self.running, "command": self.command, "cwd": self.cwd,
            "cols": self.cols, "rows": self.rows,
            "sequence": self.output_sequence,
            "replay_truncated": self.output_truncated,
            "reason": self.ended_reason,
        }

    def _cancel_idle(self) -> None:
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None

    def _arm_idle(self) -> None:
        self._cancel_idle()

        async def later():
            try:
                await asyncio.sleep(IDLE_STOP_SECONDS)
            except asyncio.CancelledError:
                return
            if not self.viewers and self.running:
                await self.stop("No viewers for {} minutes".format(
                    IDLE_STOP_SECONDS // 60))

        self.idle_task = asyncio.ensure_future(later())

    def touch(self) -> None:
        """Restart the no-viewer grace period after agent-side activity."""
        if self.running and not self.viewers:
            self._arm_idle()

    async def start(self) -> None:
        global _active_terminals
        if self.running:
            return
        try:
            argv = shlex.split(self.command)
            if not argv:
                raise ValueError
        except Exception:
            raise TerminalError("bad terminal command")
        cwd = self.cwd if os.path.isdir(self.cwd) else "/"
        env = clean_env(dict(os.environ))
        env["TERM"] = "xterm-256color"
        if not env.get("HOME"):
            env["HOME"] = service_home()

        try:
            pid, master = pty.fork()
        except OSError:
            raise TerminalError("could not start terminal")
        if pid == 0:
            try:
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            except Exception:
                os._exit(127)

        try:
            _set_winsize(master, self.cols, self.rows)
            os.set_blocking(master, False)
            asyncio.get_event_loop().add_reader(master, self._on_readable)
        except Exception:
            try:
                os.close(master)
            except OSError:
                pass
            await _reap(pid)
            raise TerminalError("could not initialize terminal")
        self.pid = pid
        self.master = master
        self.cwd = cwd
        self.running = True
        self.started_at = time.time()
        _active_terminals += 1
        _state_changed()
        self._arm_idle()
        log.info("Terminal %s spawned pid=%s cmd=%r", self.terminal_id, pid,
                 self.command)

    def _wake_output_waiters(self) -> None:
        event = self.output_event
        self.output_event = asyncio.Event()
        event.set()

    def _append_output(self, data: bytes) -> None:
        start = self.output_sequence
        self.output_sequence += len(data)
        self.raw_chunks.append((start, self.output_sequence, bytes(data)))
        self.raw_size += len(data)
        while self.raw_size > MAX_RAW_OUTPUT and len(self.raw_chunks) > 1:
            _start, _end, dropped = self.raw_chunks.popleft()
            self.raw_size -= len(dropped)
            self.output_truncated = True
        for viewer in list(self.viewers.values()):
            viewer.send_bytes(data)
        self._wake_output_waiters()

    def _on_readable(self) -> None:
        if not self.running or self.master is None:
            return
        try:
            data = os.read(self.master, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if data:
            self._append_output(data)
            return
        try:
            asyncio.get_event_loop().remove_reader(self.master)
        except Exception:
            pass
        if self.end_task is None:
            self.end_task = asyncio.ensure_future(self._natural_end())

    def _raw_since(self, sequence=None):
        if sequence is None:
            sequence = self.raw_chunks[0][0] if self.raw_chunks else self.output_sequence
        try:
            sequence = max(0, int(sequence))
        except (TypeError, ValueError):
            sequence = 0
        oldest = self.raw_chunks[0][0] if self.raw_chunks else self.output_sequence
        truncated = sequence < oldest
        sequence = max(sequence, oldest)
        pieces = []
        for start, end, data in self.raw_chunks:
            if end <= sequence:
                continue
            offset = max(0, sequence - start)
            pieces.append(data[offset:])
        return b"".join(pieces), truncated

    def transcript(self, sequence=None, max_chars: int = 16000,
                   max_lines: int = 200) -> dict:
        max_chars = _bounded_int(max_chars, 16000, 1, MAX_AGENT_TEXT)
        max_lines = _bounded_int(max_lines, 200, 1, 2000)
        raw, truncated = self._raw_since(sequence)
        return {
            "terminal_id": self.terminal_id,
            "running": self.running,
            "sequence": self.output_sequence,
            "truncated": truncated or self.output_truncated,
            "output": _plain_terminal_text(raw, max_chars, max_lines),
            "command": self.command,
            "cwd": self.cwd,
            "reason": self.ended_reason,
        }

    async def wait_for(self, text: str = "", sequence=None, timeout_ms: int = 5000,
                       exit_requested: bool = False, quiet_ms: int = 0) -> dict:
        timeout_ms = _bounded_int(timeout_ms, 5000, 0, 30000)
        quiet_ms = _bounded_int(quiet_ms, 0, 0, 10000)
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_sequence = self.output_sequence
        quiet_since = time.monotonic()
        while True:
            event = self.output_event
            snapshot = self.transcript(sequence=sequence)
            if text and text in snapshot["output"]:
                snapshot["matched"] = True
                return snapshot
            if exit_requested and not self.running:
                snapshot["exited"] = True
                return snapshot
            now = time.monotonic()
            if self.output_sequence != last_sequence:
                last_sequence = self.output_sequence
                quiet_since = now
            if quiet_ms and (now - quiet_since) * 1000 >= quiet_ms:
                snapshot["quiet"] = True
                return snapshot
            remaining = deadline - now
            if remaining <= 0:
                snapshot["timed_out"] = True
                return snapshot
            try:
                await asyncio.wait_for(event.wait(), timeout=min(remaining, 0.25))
            except asyncio.TimeoutError:
                pass

    async def write(self, data: bytes, source: str = "user") -> None:
        if not isinstance(data, (bytes, bytearray)) or not data:
            return
        if len(data) > MAX_INPUT:
            raise TerminalError("terminal input is too large")
        if not self.running or self.master is None:
            raise TerminalError("Terminal {} has ended".format(self.terminal_id))
        if source == "agent":
            self._broadcast_json({
                "type": "agent_input", "terminal_id": self.terminal_id})
        async with self.input_lock:
            offset = 0
            deadline = time.monotonic() + 5.0
            while offset < len(data):
                if not self.running or self.master is None:
                    raise TerminalError("Terminal {} has ended".format(
                        self.terminal_id))
                try:
                    written = os.write(self.master, data[offset:])
                except (BlockingIOError, InterruptedError):
                    if time.monotonic() >= deadline:
                        raise TerminalError("terminal input timed out")
                    await asyncio.sleep(0.01)
                    continue
                except OSError:
                    raise TerminalError("Terminal {} has ended".format(
                        self.terminal_id))
                if written <= 0:
                    raise TerminalError("terminal input failed")
                offset += written

    async def resize(self, cols: int, rows: int) -> None:
        self.cols = _bounded_int(cols, 80, 10, 500)
        self.rows = _bounded_int(rows, 24, 4, 300)
        if self.running and self.master is not None:
            _set_winsize(self.master, self.cols, self.rows)
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except (ProcessLookupError, TypeError):
                pass

    def _broadcast_json(self, payload: dict) -> None:
        for viewer in list(self.viewers.values()):
            viewer.send_json(payload)

    async def attach_viewer(self, ws) -> None:
        if self.closed:
            raise TerminalError("Terminal {} is closed".format(self.terminal_id))
        self._cancel_idle()
        viewer = _Viewer(ws)
        viewer.send_json(self.status_payload())
        viewer.send_json(_binding_payload(self))
        raw, _truncated = self._raw_since(None)
        for offset in range(0, len(raw), 65536):
            viewer.send_bytes(raw[offset:offset + 65536])
        self.viewers[ws] = viewer
        _state_changed()
        if not self.running:
            viewer.finish()

    def detach_viewer(self, ws) -> None:
        viewer = self.viewers.pop(ws, None)
        if viewer is not None:
            viewer.cancel()
        if not self.viewers and self.running:
            self._arm_idle()
        _state_changed()

    def binding_changed(self) -> None:
        self._broadcast_json(_binding_payload(self))

    async def _natural_end(self) -> None:
        async with self.lifecycle_lock:
            if not self.running:
                return
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, os.waitpid, self.pid, 0)
            except (ChildProcessError, OSError):
                pass
            self._finish("Terminal ended")

    def _finish(self, reason: str) -> None:
        global _active_terminals
        if not self.running:
            return
        self.running = False
        self.ended_reason = reason
        self._cancel_idle()
        master, self.master = self.master, None
        if master is not None:
            try:
                asyncio.get_event_loop().remove_reader(master)
            except Exception:
                pass
            try:
                os.close(master)
            except OSError:
                pass
        _active_terminals = max(0, _active_terminals - 1)
        _state_changed()
        self._wake_output_waiters()
        payload = self.status_payload()
        for viewer in list(self.viewers.values()):
            viewer.send_json(payload)
            viewer.finish()
        log.info("Terminal %s pid=%s ended: %s", self.terminal_id, self.pid,
                 reason)

    async def stop(self, reason: str = "Terminal closed") -> None:
        async with self.lifecycle_lock:
            if not self.running:
                return
            master, self.master = self.master, None
            if master is not None:
                try:
                    asyncio.get_event_loop().remove_reader(master)
                except Exception:
                    pass
                try:
                    os.close(master)
                except OSError:
                    pass
            await _reap(self.pid)
            self._finish(reason)


def _binding_payload(instance: TerminalInstance) -> dict:
    session_id = instance.owner_session
    session_name = ""
    if session_id is not None:
        try:
            from puppy import db
            session = db.get_session(session_id)
        except Exception:
            session = None
        if session is not None:
            session_name = str(session.get("name") or
                               "session {}".format(session_id))[:200]
        else:
            session_id = None
    return {
        "type": "binding", "terminal_id": instance.terminal_id,
        "session_id": session_id, "session_name": session_name,
    }


class TerminalRegistry:
    """Process-local catalog for ephemeral identified terminal instances."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.instances = {}
        self.bindings = {}
        self.used_ids = set()

    @staticmethod
    def _normalize_owner(owner_session):
        if owner_session is None:
            return None
        if isinstance(owner_session, bool):
            raise TerminalError("invalid terminal session identity")
        try:
            owner_session = int(owner_session)
        except (TypeError, ValueError):
            raise TerminalError("invalid terminal session identity")
        if owner_session <= 0:
            raise TerminalError("invalid terminal session identity")
        return owner_session

    def _new_id(self) -> str:
        for _ in range(4096):
            terminal_id = "".join(
                secrets.choice(TERMINAL_ID_ALPHABET) for _ in range(4))
            if not any(char.isalpha() for char in terminal_id) or \
                    not any(char.isdigit() for char in terminal_id):
                continue
            if terminal_id not in self.used_ids:
                self.used_ids.add(terminal_id)
                return terminal_id
        raise TerminalError("could not allocate a unique Terminal ID")

    def _bind_locked(self, instance: TerminalInstance, owner_session) -> None:
        owner_session = self._normalize_owner(owner_session)
        affected = {instance.terminal_id}
        previous_owner = instance.owner_session
        if previous_owner is not None:
            self.bindings.pop(previous_owner, None)
        if owner_session is not None:
            previous_id = self.bindings.get(owner_session)
            if previous_id and previous_id != instance.terminal_id:
                previous = self.instances.get(previous_id)
                if previous is not None:
                    previous.owner_session = None
                    affected.add(previous_id)
            self.bindings[owner_session] = instance.terminal_id
        instance.owner_session = owner_session
        for terminal_id in affected:
            changed = self.instances.get(terminal_id)
            if changed is not None:
                changed.binding_changed()

    async def create(self, command: str = "", cwd: str = "", cols: int = 80,
                     rows: int = 24, origin: str = "user",
                     owner_session=None) -> TerminalInstance:
        command = str(command or "").strip() or config.get(
            "terminal.command", "/bin/bash -l")
        cwd = str(cwd or "").strip() or service_home()
        cols = _bounded_int(cols, 80, 10, 500)
        rows = _bounded_int(rows, 24, 4, 300)
        owner_session = self._normalize_owner(owner_session)
        async with self.lock:
            instance = TerminalInstance(
                self._new_id(), command, cwd, cols, rows, origin, owner_session)
            self.instances[instance.terminal_id] = instance
            if owner_session is not None:
                self._bind_locked(instance, owner_session)
        try:
            await instance.start()
        except Exception:
            async with self.lock:
                self.instances.pop(instance.terminal_id, None)
                if owner_session is not None and \
                        self.bindings.get(owner_session) == instance.terminal_id:
                    self.bindings.pop(owner_session, None)
            raise
        _state_changed()
        return instance

    def get(self, terminal_id) -> TerminalInstance:
        terminal_id = normalize_terminal_id(terminal_id)
        instance = self.instances.get(terminal_id)
        if instance is None or instance.closed:
            raise TerminalError(
                "Terminal {} is closed or unknown".format(terminal_id))
        return instance

    async def close(self, terminal_id, reason: str = "Closed by user") -> bool:
        terminal_id = normalize_terminal_id(terminal_id)
        async with self.lock:
            instance = self.instances.pop(terminal_id, None)
            if instance is None or instance.closed:
                return False
            instance.closed = True
            if instance.owner_session is not None and \
                    self.bindings.get(instance.owner_session) == terminal_id:
                self.bindings.pop(instance.owner_session, None)
            instance.owner_session = None
        await instance.stop(reason)
        _state_changed()
        return True

    async def bind(self, terminal_id, session_id=None) -> TerminalInstance:
        terminal_id = normalize_terminal_id(terminal_id)
        session_id = self._normalize_owner(session_id)
        async with self.lock:
            instance = self.instances.get(terminal_id)
            if instance is None or instance.closed:
                raise TerminalError(
                    "Terminal {} is closed or unknown".format(terminal_id))
            if not instance.running:
                raise TerminalError(
                    "Terminal {} has ended".format(terminal_id))
            self._bind_locked(instance, session_id)
            _state_changed()
            return instance

    async def clear_session_binding(self, session_id: int) -> None:
        session_id = self._normalize_owner(session_id)
        async with self.lock:
            terminal_id = self.bindings.pop(session_id, None)
            instance = self.instances.get(terminal_id) if terminal_id else None
            if instance is not None:
                instance.owner_session = None
                instance.binding_changed()
        _state_changed()

    async def clear_session_bindings(self) -> None:
        async with self.lock:
            self.bindings = {}
            for instance in self.instances.values():
                if instance.owner_session is not None:
                    instance.owner_session = None
                    instance.binding_changed()
        _state_changed()

    async def linked_terminal(self, session_id: int) -> TerminalInstance:
        """Return this chat's current terminal without allocating a new one."""
        session_id = self._normalize_owner(session_id)
        async with self.lock:
            terminal_id = self.bindings.get(session_id)
            instance = self.instances.get(terminal_id) if terminal_id else None
            if instance is None or instance.closed:
                raise TerminalError("No shared Terminal is linked to this session")
            return instance

    async def agent_terminal(self, session_id: int, requested_id=None,
                             fresh: bool = False) -> TerminalInstance:
        session_id = self._normalize_owner(session_id)
        if fresh:
            from puppy import db
            session = db.get_session(session_id) or {}
            return await self.create(
                cwd=session.get("cwd") or service_home(), origin="agent",
                owner_session=session_id)
        explicit = requested_id is not None and str(requested_id).strip() != ""
        if explicit:
            return await self.bind(requested_id, session_id)
        async with self.lock:
            terminal_id = self.bindings.get(session_id)
            instance = self.instances.get(terminal_id) if terminal_id else None
            if instance is not None and instance.running and not instance.closed:
                return instance
        from puppy import db
        session = db.get_session(session_id) or {}
        return await self.create(
            cwd=session.get("cwd") or service_home(), origin="agent",
            owner_session=session_id)

    def instance_payloads(self) -> list:
        return [{
            "id": instance.terminal_id,
            "origin": instance.origin,
            "running": instance.running,
            "viewers": instance.viewer_count(),
            "session_id": instance.owner_session,
            "command": instance.command,
            "cwd": instance.cwd,
            "created_at": instance.started_at,
        } for instance in self.instances.values()]

    async def stop(self, reason: str) -> None:
        async with self.lock:
            instances = list(self.instances.values())
            self.instances = {}
            self.bindings = {}
            for instance in instances:
                instance.closed = True
                instance.owner_session = None
        if instances:
            await asyncio.gather(
                *(instance.stop(reason) for instance in instances),
                return_exceptions=True)


def manager() -> TerminalRegistry:
    global _manager
    if _manager is None:
        _manager = TerminalRegistry()
    return _manager


def _request_spec(source) -> dict:
    command = source.get("cmd") or source.get("command") or ""
    cwd = source.get("cwd") or ""
    if not isinstance(command, str) or "\x00" in command or \
            len(command) > MAX_COMMAND:
        raise TerminalError("invalid terminal command")
    if not isinstance(cwd, str) or "\x00" in cwd or len(cwd) > MAX_CWD:
        raise TerminalError("invalid terminal working directory")
    command = command.strip()
    cwd = cwd.strip()
    return {
        "command": command,
        "cwd": cwd,
        "cols": _bounded_int(source.get("cols"), 80, 10, 500),
        "rows": _bounded_int(source.get("rows"), 24, 4, 300),
    }


async def h_instances(request: web.Request):
    return web.json_response({"instances": manager().instance_payloads()})


async def h_create(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "Puppy backup or restore in progress"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid terminal request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid terminal request"}, status=400)
    owner = body.get("session_id")
    if owner is not None:
        try:
            owner = TerminalRegistry._normalize_owner(owner)
        except TerminalError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        from puppy import db
        if db.get_session(owner) is None:
            return web.json_response({"error": "session not found"}, status=404)
    try:
        spec = _request_spec(body)
        instance = await manager().create(
            **spec, origin="user", owner_session=owner)
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    terminal_payload = next(item for item in manager().instance_payloads()
                            if item["id"] == instance.terminal_id)
    return web.json_response({
        "ok": True, "terminal": terminal_payload,
        **_binding_payload(instance),
    }, status=201)


async def h_close(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "Puppy backup or restore in progress"}, status=503)
    try:
        terminal_id = normalize_terminal_id(request.match_info.get("terminal_id"))
        closed = await manager().close(terminal_id)
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not closed:
        return web.json_response(
            {"error": "Terminal {} is closed or unknown".format(terminal_id)},
            status=404)
    return web.json_response({"ok": True, "id": terminal_id})


async def h_binding_get(request: web.Request):
    try:
        instance = manager().get(request.match_info.get("terminal_id"))
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    return web.json_response({"ok": True, **_binding_payload(instance)})


async def h_binding_set(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "Puppy backup or restore in progress"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": "invalid terminal binding request"}, status=400)
    raw_session = body.get("session_id") if isinstance(body, dict) else None
    try:
        session_id = TerminalRegistry._normalize_owner(raw_session)
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    from puppy import db
    if db.get_session(session_id) is None:
        return web.json_response({"error": "session not found"}, status=404)
    try:
        instance = await manager().bind(
            request.match_info.get("terminal_id"), session_id)
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    log.info("Terminal %s linked to session %s", instance.terminal_id, session_id)
    return web.json_response({"ok": True, **_binding_payload(instance)})


async def h_binding_clear(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "Puppy backup or restore in progress"}, status=503)
    try:
        instance = await manager().bind(
            request.match_info.get("terminal_id"), None)
    except TerminalError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    log.info("Terminal %s unlinked", instance.terminal_id)
    return web.json_response({"ok": True, **_binding_payload(instance)})


async def _serve_viewer(request: web.Request, instance: TerminalInstance,
                        close_after: bool = False) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
    await ws.prepare(request)
    live_websockets.track(request, ws)
    if request.app.get("puppy_snapshot_busy"):
        await ws.close(code=1013, message=b"Puppy backup or restore in progress")
        return ws
    try:
        await instance.attach_viewer(ws)
    except TerminalError as exc:
        try:
            await ws.send_json({"type": "error", "text": str(exc)})
        except Exception:
            pass
        await ws.close()
        return ws
    log.info("Terminal %s viewer attached (%s total) for %s",
             instance.terminal_id, instance.viewer_count(), request.remote)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                try:
                    await instance.write(msg.data, source="user")
                except TerminalError:
                    break
            elif msg.type == WSMsgType.TEXT:
                try:
                    ctl = json.loads(msg.data)
                    if isinstance(ctl, dict) and ctl.get("type") == "resize":
                        await instance.resize(ctl.get("cols"), ctl.get("rows"))
                except Exception:
                    pass
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        instance.detach_viewer(ws)
        if close_after:
            await manager().close(instance.terminal_id, "Legacy viewer closed")
        log.info("Terminal %s viewer detached (%s left)",
                 instance.terminal_id, instance.viewer_count())
    return ws


async def ws_terminal_instance(request: web.Request):
    try:
        instance = manager().get(request.match_info.get("terminal_id"))
    except TerminalError as exc:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
        await ws.prepare(request)
        await ws.send_json({"type": "error", "text": str(exc)})
        await ws.close()
        return ws
    return await _serve_viewer(request, instance)


async def ws_terminal(request: web.Request) -> web.WebSocketResponse:
    """Legacy anonymous create-and-own WebSocket used by older controllers."""
    if request.app.get("puppy_snapshot_busy"):
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
        await ws.prepare(request)
        await ws.close(code=1013, message=b"Puppy backup or restore in progress")
        return ws
    try:
        instance = await manager().create(**_request_spec(request.query),
                                          origin="legacy")
    except TerminalError as exc:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
        await ws.prepare(request)
        await ws.send_json({"type": "error", "text": str(exc)})
        await ws.close()
        return ws
    return await _serve_viewer(request, instance, close_after=True)


async def shutdown() -> None:
    if _manager is not None:
        await _manager.stop("Puppy is shutting down")


def register(app: web.Application) -> None:
    app.router.add_get("/api/terminal/instances", h_instances)
    app.router.add_post("/api/terminal/instances", h_create)
    app.router.add_delete(
        "/api/terminal/instances/{terminal_id:[A-Z0-9]{4}}", h_close)
    app.router.add_get(
        "/api/terminal/instances/{terminal_id:[A-Z0-9]{4}}/binding",
        h_binding_get)
    app.router.add_post(
        "/api/terminal/instances/{terminal_id:[A-Z0-9]{4}}/binding",
        h_binding_set)
    app.router.add_delete(
        "/api/terminal/instances/{terminal_id:[A-Z0-9]{4}}/binding",
        h_binding_clear)
    app.router.add_get(
        "/api/ws/terminal/{terminal_id:[A-Z0-9]{4}}", ws_terminal_instance)
    app.router.add_get("/api/ws/term", ws_terminal)

    async def on_startup(_app):
        manager()
        from puppy import terminal_agent
        await terminal_agent.start(_app)

    async def on_cleanup(_app):
        from puppy import terminal_agent
        await terminal_agent.stop(_app)
        await shutdown()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)


async def _reap(pid: int) -> None:
    for sig, wait in ((signal.SIGHUP, 0.5), (signal.SIGTERM, 1.0),
                      (signal.SIGKILL, 2.0)):
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return
        except ChildProcessError:
            return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        await asyncio.sleep(wait)
    try:
        await asyncio.get_event_loop().run_in_executor(None, os.waitpid, pid, 0)
    except Exception:
        pass
