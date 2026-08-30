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
    {"a": "stdin", "data": {...}}                    continue a JSONL handshake
    {"a": "rate_limit", "info": {...}}
    {"a": "result", "data": {...}}                   turn finished (also persisted)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time

from puppy import cli_releases, cli_upgrade

log = logging.getLogger("puppy.drivers")

STATUS_TTL_SECONDS = 300
_status_cache = {}  # key -> (ts, dict)


# What an authentication failure looks like when a vendor's own surface says
# it: HTTP auth statuses, token lifecycle words, and the re-login instructions
# both CLIs print. Matched only against error channels (failed turns, failed
# account reads), never against model output.
AUTH_FAILURE_RE = re.compile(
    r"401|unauthorized|invalid bearer"
    r"|(?:access|refresh|oauth|authentication)[ _-]?token"
    r"|token (?:has )?(?:expired|been revoked|revoked|invalid)"
    r"|(?:revoked|expired|invalid) token"
    r"|authentication[ _-]?(?:error|failed)"
    r"|please (?:log ?in|sign ?in|run /login)"
    r"|log ?out and sign ?in", re.I)


def looks_like_auth_failure(text) -> bool:
    return bool(AUTH_FAILURE_RE.search(str(text or "")))


def _evidence_key(key: str) -> str:
    return "auth_evidence.{}".format(key)


def note_auth_failure(key: str, detail: str) -> None:
    """Record hard evidence that this engine's login no longer works: a real
    vendor response said so. Durable, so a puppy restart does not fall back to
    an optimistic local probe."""
    from puppy import db
    try:
        db.meta_set(_evidence_key(key), {
            "at": time.time(), "detail": str(detail or "")[:400]})
    except Exception as e:
        log.warning("could not record auth evidence for %s: %s", key, e)


def clear_auth_failure(key: str) -> None:
    from puppy import db
    try:
        db.meta_set(_evidence_key(key), None)
    except Exception:
        pass


def apply_auth_evidence(driver, st: dict) -> dict:
    """Overlay recorded failure evidence onto a probe that says ok. The local
    verbs are optimistic - `codex login status` reads its file without trying
    the token, so a revoked login still answers "Logged in". Evidence expires
    the moment a credential file is rewritten (the user logged in again) and
    is cleared by any authenticated success, so it can never wedge."""
    from puppy import db
    if st.get("auth") != "ok":
        return st
    try:
        record = db.meta_get(_evidence_key(driver.key))
    except Exception:
        record = None
    if not isinstance(record, dict) or not record.get("at"):
        return st
    for path in driver.auth_touch_paths():
        try:
            # slack because file mtimes lag the wall clock by a few ms on some
            # filesystems; a real re-login comes minutes after the failure
            if os.path.getmtime(path) > float(record["at"]) - 2.0:
                clear_auth_failure(driver.key)
                return st
        except OSError:
            continue
    st["auth"] = "expired"
    st["detail"] = record.get("detail") or "the engine reported an authentication failure"
    return st


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
    # Vendor installers sometimes place a CLI in a documented per-user path
    # and add it only to interactive shell startup files. Services do not read
    # those files, so a driver may declare narrow, vendor-owned fallbacks while
    # ordinary PATH lookup remains authoritative.
    binary_fallbacks = ()
    # True: prompt + control messages flow over stdin as JSONL (claude style).
    # False: prompt is part of argv, stdin closed (codex style).
    uses_stdin_stream = False
    # Some multi-provider CLIs deliberately leave authentication to whichever
    # provider/model a turn selects.  Their node health is binary availability,
    # not a single global login verdict.
    availability_only = False
    # Dynamic model catalogs are opt-in. Static drivers return their choices
    # immediately; dynamic drivers refresh before their model_options() are
    # served to the ordinary session controls.
    dynamic_model_options = False
    allow_custom_model = True
    # Loading a native session in a replacement temporary workspace is unsafe
    # for engines which bind permissions and tool routing to the creation cwd.
    resume_requires_same_cwd = False
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

    def resolved_binary(self) -> str:
        """Executable used by probes, turns, discovery, and upgrades.

        Fallbacks must expand to absolute paths. This prevents an engine from
        accidentally treating Puppy's working directory as an executable
        search path while still supporting documented per-user installers.
        """
        found = shutil.which(self.binary)
        if found:
            return found
        for raw in self.binary_fallbacks:
            if not isinstance(raw, str) or not raw:
                continue
            candidate = os.path.expanduser(raw)
            if not os.path.isabs(candidate):
                continue
            found = shutil.which(candidate)
            if found:
                return found
        return ""

    def default_permission(self) -> str:
        return ""

    def model_options(self):
        """[{value, label, hint}] - engine-specific model choices ('' = engine default).
        ``allow_custom_model`` decides whether the picker also offers free text."""
        return []

    async def refresh_model_options(self, force: bool = False) -> None:
        """Refresh a driver-owned dynamic catalog. Static drivers do nothing."""
        return None

    def model_catalog_error(self) -> str:
        return ""

    def model_catalog_loaded(self) -> bool:
        return True

    def default_model(self) -> str:
        """Model used when a new/reseeded session does not name one."""
        return ""

    def effort_options(self):
        """[{value, label, hint}] - reasoning effort levels ('' = engine default)."""
        return []

    def effort_options_for_model(self, model: str):
        """A dynamic driver may expose variants specific to one model."""
        return self.effort_options()

    def build_cmd(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                  browser_mcp=None, system_prompt: str = "") -> list:
        """argv for one turn. pinned_id: uuid the runner pre-generated for new sessions
        (engines that support pinning use it; others derive their own native id).
        browser_mcp is an optional per-turn stdio MCP server descriptor. It may
        also carry engine_guidance that applies only while those tools exist.
        system_prompt is the node owner's additive guidance for every turn."""
        raise NotImplementedError

    def build_env(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                  browser_mcp=None, system_prompt: str = "") -> dict:
        """Per-turn environment additions. Credentials remain CLI-owned."""
        return {}

    def turn_context(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                     browser_mcp=None, system_prompt: str = "") -> dict:
        """Driver scratch state shared across streamed protocol messages."""
        return {}

    def initial_stdin(self, session: dict, prompt: str) -> list:
        """JSON objects to write to stdin right after spawn (stdin-stream engines)."""
        return []

    def parse_line(self, line: str, ctx: dict) -> list:
        """One stdout line -> list of actions. ctx is a per-turn scratch dict."""
        raise NotImplementedError

    def approval_payload(self, request_id: str, behavior: str, original_input: dict,
                         message: str = "", updated_permissions=None,
                         request=None) -> dict:
        """stdin JSON answering an approval request (stdin-stream engines)."""
        raise NotImplementedError

    def cancel_approval_payload(self, request: dict):
        """Protocol response for a pending approval cancelled with its turn."""
        return None

    def interrupt_payload(self, session=None):
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
            binary = self.resolved_binary()
            if binary:
                st["installed"] = True
                st["version"] = await self._run_quick([binary, "--version"])
                if self.availability_only:
                    st.update(auth="ok", detail="binary available")
                else:
                    st.update(await self._auth_status())
            _status_cache[self.key] = (now, dict(st))
            probed_at = now
        # Quotas can change between the relatively expensive version/auth
        # probes, so dynamic extras must never be trapped in the 5-minute cache.
        st["version_checked_at"] = probed_at
        st.update(cli_upgrade.state(self))
        st.update(cli_releases.status(self, st.get("version", "")))
        st.update(self._extra_status())
        # outside the cache: evidence must land and lift without waiting 5 min
        return st if self.availability_only else apply_auth_evidence(self, st)

    def _extra_status(self) -> dict:
        """Engine-specific extras merged into status() (e.g. quota info)."""
        return {}

    def auth_touch_paths(self) -> list:
        """Credential files whose rewrite means "the user logged in again".
        Only their mtime is ever read - never their contents."""
        return []

    async def refresh_usage(self):
        """Refresh account-limit data without starting a turn; None = unsupported."""
        return None

    async def _auth_status(self) -> dict:
        return {"auth": "unknown", "detail": ""}

    async def _run_probe(self, argv, timeout: float = 15.0):
        """(exit_code, full_output) for a local no-quota probe; (None, "") when
        the command could not run at all. Auth verbs speak through their exit
        code as much as their wording, and warnings can precede the line that
        matters, so unlike _run_quick nothing is thrown away."""
        try:
            p = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
            return p.returncode, out.decode(errors="replace").strip()[:4000]
        except Exception as e:
            log.warning("%s probe %s failed: %s", self.key, argv[1:], e)
            return None, ""

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
        if k.startswith("CLAUDE_") or k in (
                "CLAUDECODE", "ANTHROPIC_MODEL", "CODEX_HOME_OVERRIDE",
                # Puppy adds this back for exactly one case: a root-owned
                # Claude turn whose selected mode explicitly bypasses all
                # permission checks. Never inherit a broader service setting.
                "IS_SANDBOX",
                # A Puppy process may itself have been launched by OpenCode.
                # Its turn-scoped inline config must never leak into a child
                # engine; the OpenCode driver installs its own value explicitly.
                "OPENCODE_CONFIG_CONTENT"):
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
