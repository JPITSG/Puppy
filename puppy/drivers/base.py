"""Engine driver interface.

A driver translates between one CLI agent (claude, codex, ...) and puppy's
normalized world. Adding a new engine = subclass Driver, register it in
drivers/__init__.py. The runner is engine-agnostic.

Normalized transcript event kinds (persisted):
    user         {text}
    assistant    {text}
    thinking     {text}
    tool_use     {tool, input, tool_use_id}
    tool_result  {tool_use_id, content, is_error}
    info         {subtype, text, ...}
    result       {ok, usage?, cost_usd?, duration_ms?, stop_reason?, error?}
                 The transcript line reads outcome, duration, output tokens and
                 clock time for every engine; a driver that leaves duration_ms
                 unset is given the runner's wall-clock turn time.
    error        {text}
    engine_switch{from, to}

Actions returned by parse_line() (consumed by the runner):
    {"a": "event", "kind": ..., "data": {...}}      persist + broadcast
    {"a": "transient", "msg": {...}}                 broadcast only (deltas, status)
    {"a": "native_id", "id": "..."}                  store engine-native session id
    {"a": "model", "model": "..."}                   engine reported its model
    {"a": "approval", "req": {...}}                  interactive permission request
    {"a": "approval_cancel", "request_id": "..."}
    {"a": "rate_limit", "info": {...}}
    {"a": "result", "data": {...}}                   turn finished (also persisted)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from puppy import cli_releases, cli_upgrade

log = logging.getLogger("puppy.drivers")

STATUS_TTL_SECONDS = 300
_status_cache = {}  # key -> (ts, dict)


def invalidate_status(key=None) -> None:
    """Drop cached CLI probes so the next status() re-reads the installed truth."""
    if key is None:
        _status_cache.clear()
    else:
        _status_cache.pop(str(key), None)


class Driver:
    key = "base"
    label = "Base"
    binary = "false"
    # True: prompt + control messages flow over stdin as JSONL (claude style).
    # False: prompt is part of argv, stdin closed (codex style).
    uses_stdin_stream = False
    # Optional advisory source for latest-version checks. New registry kinds
    # belong in cli_releases; engine-specific package identity stays here.
    release_source = None
    # Optional fixed self-update verb for this CLI, e.g. {"kind": "self",
    # "args": ["update"]}. The vendor updater owns install-method detection;
    # cli_upgrade owns the bounded subprocess. Never build this from input.
    upgrade_source = None

    def permission_options(self):
        """[{value, label, hint}] - engine-specific permission/sandbox levels."""
        return []

    def default_permission(self) -> str:
        return ""

    def model_options(self):
        """[{value, label, hint}] - engine-specific model choices ('' = engine default).
        Free-text overrides are still allowed; this only feeds the picker UI."""
        return []

    def effort_options(self):
        """[{value, label, hint}] - reasoning effort levels ('' = engine default)."""
        return []

    def build_cmd(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                  browser_mcp=None) -> list:
        """argv for one turn. pinned_id: uuid the runner pre-generated for new sessions
        (engines that support pinning use it; others derive their own native id).
        browser_mcp is an optional per-turn stdio MCP server descriptor."""
        raise NotImplementedError

    def initial_stdin(self, session: dict, prompt: str) -> list:
        """JSON objects to write to stdin right after spawn (stdin-stream engines)."""
        return []

    def parse_line(self, line: str, ctx: dict) -> list:
        """One stdout line -> list of actions. ctx is a per-turn scratch dict."""
        raise NotImplementedError

    def approval_payload(self, request_id: str, behavior: str, original_input: dict,
                         message: str = "", updated_permissions=None) -> dict:
        """stdin JSON answering an approval request (stdin-stream engines)."""
        raise NotImplementedError

    def interrupt_payload(self):
        """stdin JSON requesting a graceful interrupt, or None (-> signal only)."""
        return None

    async def status(self) -> dict:
        """{installed, version, auth, detail, ...extras}; slow checks are cached."""
        now = time.time()
        cached = _status_cache.get(self.key)
        if cached and now - cached[0] < STATUS_TTL_SECONDS:
            st = dict(cached[1])
            probed_at = cached[0]
        else:
            st = {"installed": False, "version": "", "auth": "unknown", "detail": ""}
            if shutil.which(self.binary):
                st["installed"] = True
                st["version"] = await self._run_quick([self.binary, "--version"])
                st.update(await self._auth_status())
            _status_cache[self.key] = (now, dict(st))
            probed_at = now
        # Quotas can change between the relatively expensive version/auth
        # probes, so dynamic extras must never be trapped in the 5-minute cache.
        st["version_checked_at"] = probed_at
        st.update(cli_upgrade.state(self))
        st.update(cli_releases.status(self, st.get("version", "")))
        st.update(self._extra_status())
        return st

    def _extra_status(self) -> dict:
        """Engine-specific extras merged into status() (e.g. quota info)."""
        return {}

    async def refresh_usage(self):
        """Refresh account-limit data without starting a turn; None = unsupported."""
        return None

    async def _auth_status(self) -> dict:
        return {"auth": "unknown", "detail": ""}

    async def _run_quick(self, argv, timeout: float = 12.0) -> str:
        try:
            p = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
            return out.decode(errors="replace").strip().splitlines()[0] if out else ""
        except Exception as e:
            log.warning("%s quick cmd failed: %s", self.key, e)
            return ""


def clean_env(env: dict) -> dict:
    """Strip vars from a possibly-nested agent environment so spawned CLIs start clean."""
    out = dict(env)
    for k in list(out):
        if k.startswith("CLAUDE_") or k in ("CLAUDECODE", "ANTHROPIC_MODEL", "CODEX_HOME_OVERRIDE"):
            out.pop(k, None)
    return out


def stringify_content(content) -> str:
    """Claude tool_result content may be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(b))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else str(content)
