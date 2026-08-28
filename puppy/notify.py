"""Prompt-completion notification: run a configured command on a chosen node
when a session finishes its work.

Controller-owned. The command, target backend and armed state persist in
config (notify.*) and ride along in Settings backup archives. The controller
fires for its own sessions from the runner; completions on remote backends are
reported by consoles through /api/notify/fire, deduplicated here so several
open browsers produce one execution. The command itself runs through the
shared /api/notify/exec surface: locally for backend 0, else POSTed to the
chosen backend (token + pinned TLS), where it executes only if that node was
built with its shell surface (terminal) enabled.

A notification fires when a session goes idle - its turn and everything queued
behind it finished - not once per queued prompt. A turn stopped by the user is
not a completion and never fires the command.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time

from puppy import config

log = logging.getLogger("puppy.notify")

# {placeholder} substitutions (shell-quoted) and PUPPY_* environment variables
PLACEHOLDERS = ("backend", "session", "engine", "model", "status", "duration",
                "duration_hms", "cwd", "id")
EXEC_TIMEOUT = 30.0
DEDUPE_SECONDS = 8.0
MAX_COMMAND = 1000

_recent = {}   # (bid, sid) -> monotonic stamp of the last accepted remote fire


def settings() -> dict:
    return {
        "enabled": bool(config.get("notify.enabled", False)),
        "backend": int(config.get("notify.backend", 0) or 0),
        "command": str(config.get("notify.command", "") or ""),
    }


def configured() -> bool:
    return bool(settings()["command"].strip())


def active() -> bool:
    s = settings()
    # Enabled and configured are intentionally independent: the Settings
    # switch may be on before a command is supplied, but that must stay inert.
    return bool(s["command"].strip()) and s["enabled"]


def public_state() -> dict:
    """What the console needs to draw the bell."""
    s = settings()
    return {"configured": bool(s["command"].strip()), "enabled": s["enabled"]}


def clock(seconds: int) -> str:
    """Whole seconds as a clock, without padding the leading unit: 0:07, 9:59,
    10:00, 1:00:00, 9:59:59, 10:00:00. Leaving the most significant field
    unpadded is what produces M:SS / MM:SS / H:MM:SS / HH:MM:SS in turn."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{}:{:02d}".format(minutes, secs)


def clean_info(info) -> dict:
    if not isinstance(info, dict):
        return {}
    out = {k: str(info.get(k) or "")[:300] for k in PLACEHOLDERS if info.get(k)}
    # Always derived here from `duration`, never taken from the caller: one
    # implementation for local completions, console-reported remote ones and
    # the Test button alike, and older consoles gain it without sending it.
    out.pop("duration_hms", None)
    try:
        out["duration_hms"] = clock(int(float(out["duration"])))
    except (KeyError, TypeError, ValueError):
        pass
    return out


def expand(command: str, info: dict) -> str:
    """Substitute placeholders shell-quoted: values drop into an sh -c command
    as single words, so names with spaces or quotes cannot break it."""
    out = command
    for key in PLACEHOLDERS:
        out = out.replace("{%s}" % key, shlex.quote(str(info.get(key) or "")))
    return out


async def run_local(command: str, info: dict) -> dict:
    """Execute on this node. Also the body of the shared /api/notify/exec."""
    env = dict(os.environ)
    for key in PLACEHOLDERS:
        env["PUPPY_" + key.upper()] = str(info.get(key) or "")
    try:
        proc = await asyncio.create_subprocess_shell(
            command, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"ok": False, "error": "command timed out after %ds" % int(EXEC_TIMEOUT)}
    return {"ok": proc.returncode == 0, "rc": proc.returncode,
            "output": (out or b"").decode(errors="replace")[-1000:].strip()}


async def dispatch(info: dict, override: dict = None) -> dict:
    """Expand and run the configured (or supplied) command on its target."""
    s = dict(settings())
    if override:
        s.update({k: override[k] for k in ("backend", "command") if k in override})
    command = str(s.get("command") or "").strip()[:MAX_COMMAND]
    if not command:
        return {"ok": False, "error": "no command configured"}
    info = clean_info(info)
    expanded = expand(command, info)
    target = int(s.get("backend") or 0)
    if not target:
        return await run_local(expanded, info)
    from puppy import backends
    return await backends.notify_exec(target, expanded, info)


def session_finished(session: dict, status: str, duration_s: int) -> None:
    """Runner hook: fire-and-forget on this node's own completions."""
    if status == "interrupted" or session is None or not active():
        return
    info = {
        "backend": config.get("instance_name") or "local",
        "session": session.get("name") or "session %s" % session.get("id"),
        "engine": session.get("engine") or "",
        "model": session.get("model") or session.get("last_model") or "",
        "status": status,
        "duration": str(max(0, int(duration_s))),
        "cwd": session.get("cwd") or "",
        "id": str(session.get("id") or ""),
    }
    asyncio.ensure_future(_fire(info))


def accept_remote_fire(bid: int, sid: int) -> bool:
    """Consoles report remote completions; several may see the same one."""
    now = time.monotonic()
    key = (int(bid), int(sid))
    if now - _recent.get(key, -1e9) < DEDUPE_SECONDS:
        return False
    if len(_recent) > 512:
        cutoff = now - DEDUPE_SECONDS
        for old in [k for k, ts in _recent.items() if ts < cutoff]:
            _recent.pop(old, None)
    _recent[key] = now
    return True


async def _fire(info: dict) -> None:
    try:
        result = await dispatch(info)
        if not result.get("ok"):
            log.warning("completion command failed: %s",
                        result.get("error") or "rc=%s %s" % (result.get("rc"),
                                                             (result.get("output") or "")[:200]))
    except Exception:
        log.exception("completion command failed")
