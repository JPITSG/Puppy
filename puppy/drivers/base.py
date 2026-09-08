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
                 engine_retry {attempt, delay} records that the prompt runs
                 again after a back-off because the engine reported a
                 transient failure (looks_transient_auth); only the final
                 attempt's outcome becomes the turn's result.
                 Engine background work uses three subtypes: background_wait
                 {tasks} when the model has answered but the engine still
                 owns background tasks whose end will wake it within this
                 same turn, task {status, task_id, tool_use_id?} when one of
                 them ends (the optional native tool id links its card), and
                 background_wait_stopped when the node ended such a wait
                 itself (turn timeout, or an engine that never continued).
    result       {ok, usage?, cost_usd?, engine_duration_ms?,
                  api_duration_ms?, stop_reason?, error?}
                 The runner adds duration_ms as comparable elapsed wall time.
                 The transcript line reads outcome, duration, input tokens
                 (input_tokens plus any cache_read/cache_creation keys, so a
                 driver whose input already counts cached tokens must not
                 also emit those keys), total output tokens (including any
                 reasoning_output_tokens subset) and clock time for every
                 engine. Engine-native or API-only measurements stay in their
                 explicitly scoped fields and are not displayed as wall time.
                 usage_scope="last_request"
                 explicitly labels an engine fallback that cannot recover a
                 whole-turn total. Drivers offering session
                 tools also stamp each prompt turn's result with the native
                 identities a later undo needs (native_session_id plus
                 claude's native_prompt_id/native_tail_id or codex's
                 native_turn_id); a tool turn's result carries tool=<name>.
                 context_used/context_window (tokens) describe the native
                 context at the END of the turn - the last model request's
                 input plus output against the model's window - and the
                 transcript line shows their ratio when both are known.
    error        {text}
                 subtype="engine_api_error" (with the vendor's error code)
                 is a synthetic API error message the CLI emitted in the
                 model's place; it is never assistant text.
    engine_switch{from, to}

Actions returned by parse_line() (consumed by the runner):
    {"a": "event", "kind": ..., "data": {...}}      persist + broadcast
    {"a": "transient", "msg": {...}}                 broadcast only (deltas, status)
    {"a": "native_id", "id": "..."}                  store engine-native session id
    {"a": "model", "model": "..."}                   engine-confirmed effective model;
                                                     the runner asks the driver
                                                     whether requested/reported
                                                     names are equivalent
    {"a": "approval", "req": {...}}                  interactive permission request
    {"a": "approval_cancel", "request_id": "..."}
    {"a": "stdin", "data": {...}}                    continue a JSONL handshake
    {"a": "rate_limit", "info": {...}}
    {"a": "result", "data": {...}}                   turn finished (also persisted)
    {"a": "background_tasks", "tasks": [...]}        live engine background tasks as
                                                     {id, type, description}; REPLACE
                                                     semantics, ambient work excluded
                                                     (also drives the header count).
                                                     Only emit a complete live set
                                                     from native task evidence, never
                                                     infer it from tool calls/text.
    {"a": "turn_pause", "data": {...}, "tasks": [...]}
                                                     the model answered but the engine
                                                     keeps running background tasks that
                                                     will wake it in this same process:
                                                     the runner keeps stdin open, and data
                                                     (the result so far) becomes the turn's
                                                     result only if the engine leaves before
                                                     a final "result"
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import shutil
import time

from puppy import cli_releases, cli_upgrade

log = logging.getLogger("puppy.drivers")

STATUS_TTL_SECONDS = 300
_status_cache = {}  # key -> (ts, dict)

MODEL_CATALOG_TTL_SECONDS = 300
MODEL_CATALOG_RETRY_SECONDS = (30, 60, 120, 300)
MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS = 5
MODEL_PROBE_LINE_LIMIT = 4 * 1024 * 1024


def _configured_timer_seconds(name: str, _fallback: float) -> float:
    # The second argument preserves the long-standing test seam while the
    # persisted setting is the runtime source of truth.
    from puppy import config
    return config.timer_seconds(name)


class ModelCatalogResult:
    """One successful vendor discovery, before it replaces last-known-good.

    ``source`` describes where the choices came from (normally ``engine``;
    Claude can also learn them from a real turn). ``note`` is a successful but
    important qualification, such as an older CLI that could only re-read its
    local cache when a person explicitly requested a network refresh.
    """

    def __init__(self, options: list, source: str = "engine", note: str = ""):
        self.options = options
        self.source = str(source or "engine")[:40]
        self.note = str(note or "")[:400]


class ModelCatalog:
    """Loop-safe, in-memory last-known-good state for a dynamic model list."""

    def __init__(self):
        self.options = []
        self.source = ""
        self.error = ""
        self.note = ""
        self.checked_at = None
        self.updated_at = None
        self.next_due_mono = 0.0
        self.completed_mono = 0.0
        self.attempt_forced = False
        self.force_started_mono = 0.0
        self.failures = 0
        self._lock = None
        self._lock_loop = None

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def due(self, now: float) -> bool:
        return not self.next_due_mono or now >= self.next_due_mono

    @staticmethod
    def _validated(options) -> list:
        if not isinstance(options, list):
            raise RuntimeError("model discovery returned an invalid catalog")
        kept = []
        seen = set()
        for raw in options:
            if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
                continue
            value = raw["value"]
            if value in seen:
                continue
            seen.add(value)
            kept.append(dict(raw))
        # A Default-only result is not a model catalog. Publishing it over a
        # previous good list would make every named choice disappear.
        if not any(item["value"] for item in kept):
            raise RuntimeError("engine reported no models")
        return kept

    def succeeded(self, result: ModelCatalogResult, forced: bool) -> None:
        now_mono = time.monotonic()
        now_wall = time.time()
        self.options = self._validated(result.options)
        self.source = result.source
        self.error = ""
        self.note = result.note
        self.checked_at = now_wall
        self.updated_at = now_wall
        self.completed_mono = now_mono
        self.attempt_forced = bool(forced)
        self.failures = 0
        self.next_due_mono = now_mono + _configured_timer_seconds(
            "model_catalog_minutes", MODEL_CATALOG_TTL_SECONDS)

    def failed(self, exc: Exception, forced: bool) -> None:
        now_mono = time.monotonic()
        self.failures += 1
        delay = MODEL_CATALOG_RETRY_SECONDS[
            min(self.failures - 1, len(MODEL_CATALOG_RETRY_SECONDS) - 1)]
        delay = min(delay, _configured_timer_seconds(
            "model_catalog_minutes", MODEL_CATALOG_TTL_SECONDS))
        self.error = (str(exc) or exc.__class__.__name__)[:400]
        self.note = ""
        self.checked_at = time.time()
        self.completed_mono = now_mono
        self.attempt_forced = bool(forced)
        self.next_due_mono = now_mono + delay

    def ingest(self, options: list, source: str = "turn") -> None:
        """Accept a catalog carried by an already-running native protocol."""
        self.succeeded(ModelCatalogResult(options, source=source), forced=False)


async def start_probe(argv: list, *, writable_stdin: bool = False,
                      cwd: str = "/", env=None,
                      line_limit: int = MODEL_PROBE_LINE_LIMIT):
    """Start a no-turn CLI probe in its own bounded process group."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if writable_stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=clean_env(dict(os.environ)) if env is None else env,
        limit=line_limit,
        start_new_session=True)


async def end_probe(process) -> None:
    """Close a probe and, if needed, stop every child it brought with it."""
    if process is None:
        return
    if process.stdin is not None:
        try:
            process.stdin.close()
        except (BrokenPipeError, ConnectionError):
            pass

    def group_alive() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    if process.returncode is not None and not group_alive():
        return
    for sig, timeout in ((signal.SIGINT, 0.75), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                if not group_alive():
                    return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            # The leader can exit on INT while a grandchild ignores it. The
            # group, rather than only process.returncode, is the cleanup proof.
            if not group_alive():
                if process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.2)
                    except asyncio.TimeoutError:
                        pass
                return


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


class ToolUnavailable(RuntimeError):
    """A session tool cannot run right now; the message is user-facing."""


_UNDO_STATE_KEY = "session_undo.{}"


def undo_state_get(session_id) -> dict:
    """A pending conversation rollback the engine applies on the session's
    next prompt (claude branches its transcript with that resume). Durable in
    meta so a restart between the undo and the prompt keeps the promise."""
    from puppy import db
    if session_id is None:
        return {}
    try:
        value = db.meta_get(_UNDO_STATE_KEY.format(int(session_id)))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def undo_state_set(session_id, state: dict) -> None:
    from puppy import db
    db.meta_set(_UNDO_STATE_KEY.format(int(session_id)), dict(state))


def undo_state_clear(session_id) -> None:
    from puppy import db
    try:
        db.meta_del(_UNDO_STATE_KEY.format(int(session_id)))
    except Exception:
        pass


def tool_name(tool) -> str:
    """The tool a build/context call is for, from the queue row fields the
    runner passes as ``tool`` (empty for an ordinary prompt turn)."""
    return str(tool.get("tool") or "") if isinstance(tool, dict) else ""


# A vendor's own "try again" wording: claude's OAuth refresh lock timeout
# ("another Claude Code process is refreshing it or exited mid-refresh. This is
# usually transient; retry in a minute"). The login is intact; only this
# process could not refresh the token in time.
TRANSIENT_AUTH_RE = re.compile(
    r"another [a-z ]*process is (?:refreshing|holding)"
    r"|exited mid-refresh|refresh lock|usually transient", re.I)


def looks_transient_auth(text) -> bool:
    return bool(TRANSIENT_AUTH_RE.search(str(text or "")))


def looks_like_auth_failure(text) -> bool:
    text = str(text or "")
    return bool(AUTH_FAILURE_RE.search(text)) and not looks_transient_auth(text)


def _evidence_key(key: str) -> str:
    return "auth_evidence.{}".format(key)


def _wake_engine_state() -> None:
    try:
        from puppy import state_stream
        state_stream.wake("engines")
    except Exception:
        pass


def note_auth_failure(key: str, detail: str) -> None:
    """Record hard evidence that this engine's login no longer works: a real
    vendor response said so. Durable, so a puppy restart does not fall back to
    an optimistic local probe."""
    from puppy import db
    try:
        db.meta_set(_evidence_key(key), {
            "at": time.time(), "detail": str(detail or "")[:400]})
        _wake_engine_state()
    except Exception as e:
        log.warning("could not record auth evidence for %s: %s", key, e)


def clear_auth_failure(key: str) -> None:
    from puppy import db
    try:
        evidence_key = _evidence_key(key)
        changed = db.meta_get(evidence_key) is not None
        db.meta_set(evidence_key, None)
        if changed:
            _wake_engine_state()
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
    _wake_engine_state()


class Driver:
    key = "base"
    label = "Base"
    binary = "false"
    # Vendor installers sometimes place a CLI in a documented per-user path
    # and add it only to interactive shell startup files. Services do not read
    # those files, so a driver may declare narrow, vendor-owned fallbacks while
    # ordinary PATH lookup remains authoritative.
    binary_fallbacks = ()
    # True: prompt/setup/control messages flow over a writable JSONL stdin.
    # False: the complete turn is supplied on argv and stdin stays closed.
    uses_stdin_stream = False
    # True only when the driver's pinned stdin protocol can add another user
    # message to the currently active turn. This is deliberately independent
    # of uses_stdin_stream: a writable protocol is necessary, but it does not
    # by itself prove same-turn steering semantics.
    supports_steering = False
    # Protocols with a distinct native acknowledgement of the steering
    # request set this. Other explicitly supported protocols use stdin drain
    # as their strongest available acceptance signal.
    steering_acknowledged = False
    # True only when the pinned protocol can put a question to the model
    # ALONGSIDE the running turn: answered from the same conversation context,
    # tool-less, one response, and never added to the engine's own transcript.
    # This is the opposite of steering - it must not change what the turn does.
    supports_side_questions = False
    # Some multi-provider CLIs deliberately leave authentication to whichever
    # provider/model a turn selects.  Their node health is binary availability,
    # not a single global login verdict.
    availability_only = False
    # Dynamic model catalogs are opt-in. Static drivers return their choices
    # immediately; dynamic drivers refresh before their model_options() are
    # served to the ordinary session controls.
    dynamic_model_options = False
    allow_custom_model = True
    # Engine-owned opt-in for a semantic Fast request. The driver resolves
    # that request through its live model catalog; callers never learn or
    # persist a vendor service-tier identifier.
    supports_fast_mode = False
    model_catalog_timeout_seconds = 15.0
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

    def tool_options(self):
        """Session tools this engine's headless protocol can run, as
        [{value, label, hint}]: "compact" summarizes the native conversation
        in place, "undo" drops the last prompt and its reply from the native
        conversation (files are never touched). Empty when unsupported."""
        return []

    def tool_plan(self, tool: str, session: dict, turns: list) -> dict:
        """Decide how one session tool runs now. ``turns`` lists the session's
        most recent completed turns, newest first, as {"kind": "prompt"|"tool",
        "tool": name, "text": prompt text, "result": persisted result data}.
        Return {"run": True, "params": {...}} for a tool turn the runner
        spawns (build_cmd/build_env/turn_context/initial_stdin then receive
        ``tool`` = the queue row fields), or {"run": False, "state": {...},
        "text": note} for a change the driver applies from durable
        per-session state on the next prompt. Either form may add
        "restore_text", a dropped prompt handed back to the composer. Raise
        ToolUnavailable with a user-facing reason otherwise."""
        raise ToolUnavailable("{} does not support {}".format(self.label, tool))

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
        state = self._model_catalog_state()
        options = state.options or self._fallback_model_options()
        seen = set()
        result = []
        for raw in options:
            if not isinstance(raw, dict) or not isinstance(raw.get("value"), str) or \
                    raw["value"] in seen:
                continue
            seen.add(raw["value"])
            result.append(dict(raw))
        return result

    def model_option(self, model: str, options=None):
        """Find a picker row, preferring exact values to driver-owned aliases."""
        options = self.model_options() if options is None else options
        for item in options:
            if isinstance(item, dict) and item.get("value") == model:
                return item
        for item in options:
            if isinstance(item, dict) and isinstance(item.get("aliases"), list) and \
                    model in item["aliases"]:
                return item
        return None

    def model_request_matches(self, requested: str, reported: str, ctx=None) -> bool:
        """Whether one engine report satisfies the model Puppy requested.

        The generic contract preserves the historical alias check: a short
        requested name may appear inside the engine's fully resolved id.
        Drivers whose catalog supplies an explicit alias mapping should use
        that mapping instead of teaching the runner vendor naming rules.
        """
        requested = str(requested or "").strip()
        reported = str(reported or "").strip()
        return not requested or requested.casefold() in reported.casefold()

    def models_equivalent(self, first: str, second: str, ctx=None) -> bool:
        """Whether two engine reports identify the same effective model.

        Exact equality is deliberately conservative. A driver may override
        this when its protocol emits multiple representations of one model.
        """
        return str(first or "") == str(second or "")

    def _model_catalog_state(self) -> ModelCatalog:
        state = getattr(self, "_model_catalog", None)
        if state is None:
            state = ModelCatalog()
            self._model_catalog = state
        return state

    def _fallback_model_options(self) -> list:
        return []

    def _fallback_model_source(self) -> str:
        return "static" if self._fallback_model_options() else "none"

    async def _discover_model_options(self, force: bool) -> ModelCatalogResult:
        raise RuntimeError("model discovery is not implemented")

    def model_catalog_timeout(self, force: bool) -> float:
        return float(self.model_catalog_timeout_seconds)

    async def refresh_model_options(self, force: bool = False) -> None:
        """Refresh a dynamic catalog without ever discarding last-known-good.

        Concurrent callers share an attempt. A force which arrived during an
        ordinary attempt still gets one forced attempt afterwards, because an
        engine such as OpenCode gives the forced form stronger cache semantics.
        """
        if not self.dynamic_model_options:
            return None
        state = self._model_catalog_state()
        requested_at = time.monotonic()
        if not force and not state.due(requested_at):
            return None
        async with state.lock():
            now = time.monotonic()
            if state.completed_mono >= requested_at and \
                    (not force or state.attempt_forced):
                return None
            if not force and not state.due(now):
                return None
            if force and state.force_started_mono and \
                    now - state.force_started_mono < \
                    MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS:
                return None
            # Never execute a package while its vendor updater is replacing it.
            if cli_upgrade.is_running(self.key):
                return None
            if force:
                state.force_started_mono = now
            try:
                result = await asyncio.wait_for(
                    self._discover_model_options(force),
                    timeout=self.model_catalog_timeout(force))
                if isinstance(result, list):
                    result = ModelCatalogResult(result)
                if not isinstance(result, ModelCatalogResult):
                    raise RuntimeError("model discovery returned an invalid result")
                state.succeeded(result, force)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, asyncio.TimeoutError):
                    exc = RuntimeError("model discovery timed out after {}s".format(
                        int(self.model_catalog_timeout(force))))
                state.failed(exc, force)
                log.warning("%s model discovery failed: %s", self.key, exc)
            try:
                from puppy import state_stream
                state_stream.wake("engines")
            except Exception:
                pass
        return None

    def model_catalog_error(self) -> str:
        return self._model_catalog_state().error

    def model_catalog_note(self) -> str:
        return self._model_catalog_state().note

    def model_catalog_source(self) -> str:
        return self._model_catalog_state().source or self._fallback_model_source()

    def model_catalog_checked_at(self):
        return self._model_catalog_state().checked_at

    def model_catalog_updated_at(self):
        return self._model_catalog_state().updated_at

    def invalidate_model_options(self) -> None:
        """Make the next discovery due while retaining visible last-known-good."""
        state = self._model_catalog_state()
        state.next_due_mono = 0.0
        state.force_started_mono = 0.0

    def model_catalog_loaded(self) -> bool:
        return not self.dynamic_model_options or \
            self._model_catalog_state().updated_at is not None

    def default_model(self) -> str:
        """Model used when a new/reseeded session does not name one."""
        return ""

    def effort_options(self):
        """[{value, label, hint}] - reasoning effort levels ('' = engine default)."""
        return []

    def effort_options_for_model(self, model: str):
        """Prefer capabilities attached to the selected catalog entry."""
        item = self.model_option(model)
        if item is not None and isinstance(item.get("effort_options"), list):
            return [dict(option) for option in item["effort_options"]
                    if isinstance(option, dict)]
        if self.allow_custom_model:
            return self.effort_options()
        return [{"value": "", "label": "Default", "hint": "Engine model default"}]

    def fast_mode_tier(self, model: str) -> str:
        """Opaque service-tier id implementing Fast for ``model``, or ``""``.

        Only a driver whose stable native protocol advertises this capability
        should override it. The persisted/UI setting stays a boolean; opaque
        ids are resolved afresh from the engine-owned model catalog for every
        turn so a CLI upgrade may change them without changing Puppy state.
        """
        return ""

    def fast_mode_hint(self, model: str) -> str:
        """Catalog-owned description of Fast for display, if available."""
        return ""

    def build_cmd(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                  browser_mcp=None, system_prompt: str = "",
                  terminal_mcp=None, vnc_mcp=None, spawn_mcp=None,
                  session_mcp=None, tool=None) -> list:
        """argv for one turn. pinned_id: uuid the runner pre-generated for new sessions
        (engines that support pinning use it; others derive their own native id).
        browser_mcp is an optional per-turn stdio MCP server descriptor. It may
        also carry engine_guidance that applies only while those tools exist.
        terminal_mcp, vnc_mcp and spawn_mcp are the equivalent descriptors
        for the shared terminal, remote screen and spawned-agent bridges;
        drivers may receive any combination in the same turn.
        system_prompt is the node owner's additive guidance for every turn.
        tool is the queue row fields ({engine, tool, ...params}) of a session
        tool turn; the runner passes it only to drivers with tool_options."""
        raise NotImplementedError

    def build_env(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                  browser_mcp=None, system_prompt: str = "",
                  terminal_mcp=None, vnc_mcp=None, spawn_mcp=None,
                  session_mcp=None, tool=None) -> dict:
        """Per-turn environment additions. Credentials remain CLI-owned."""
        return {}

    def turn_context(self, session: dict, first_turn: bool, prompt: str, pinned_id: str,
                     browser_mcp=None, system_prompt: str = "",
                     terminal_mcp=None, vnc_mcp=None, spawn_mcp=None,
                     session_mcp=None, tool=None) -> dict:
        """Driver scratch state shared across streamed protocol messages."""
        return {}

    def initial_stdin(self, session: dict, prompt: str, tool=None) -> list:
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

    def interrupt_payload(self, session=None, ctx=None):
        """stdin JSON requesting a graceful interrupt, or None (-> signal only).

        ``ctx`` is the live object returned by turn_context(). Protocols whose
        turn id is allocated during setup need it to target the exact turn.
        """
        return None

    def steer_payload(self, session: dict, ctx: dict, text: str,
                      request_id: str):
        """Return one active-turn user-input payload, or None until ready.

        ``ctx`` is the live object returned by turn_context(). Drivers must
        address the already-running native turn/session represented by that
        object; steering must never create a new turn or native session.
        """
        return None

    def steer_ready(self, session: dict, ctx: dict) -> bool:
        """Whether the live driver context can address its active turn."""
        return False

    def side_question_payload(self, session: dict, ctx: dict, question: str,
                              history: list, request_id: str):
        """Return one stdin side-question payload, or None until ready.

        ``history`` is this turn's earlier question/answer pairs, oldest
        first, which the driver replays so a follow-up reads as a thread.
        The payload must never create a turn, a native session, or a
        transcript entry the running turn can see.
        """
        return None

    def side_question_cancel_payload(self, session: dict, ctx: dict,
                                     request_id: str):
        """Return one stdin payload withdrawing an unanswered side question."""
        return None

    def side_question_ready(self, session: dict, ctx: dict) -> bool:
        """Whether the live driver context can carry a side question."""
        return False

    async def status(self) -> dict:
        """{installed, version, auth, detail, ...extras}; slow checks are cached."""
        now = time.time()
        cached = _status_cache.get(self.key)
        ttl = _configured_timer_seconds("cli_status_minutes", STATUS_TTL_SECONDS)
        if cached and now - cached[0] < ttl:
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
        p = None
        try:
            p = await start_probe(list(argv))
            out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
            return p.returncode, out.decode(errors="replace").strip()[:4000]
        except Exception as e:
            log.warning("%s probe %s failed: %s", self.key, argv[1:], e)
            return None, ""
        finally:
            await end_probe(p)

    async def _run_quick(self, argv, timeout: float = 12.0) -> str:
        p = None
        try:
            p = await start_probe(list(argv))
            out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
            return out.decode(errors="replace").strip().splitlines()[0] if out else ""
        except Exception as e:
            log.warning("%s quick cmd failed: %s", self.key, e)
            return ""
        finally:
            await end_probe(p)


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
                "OPENCODE_CONFIG_CONTENT",
                # A first-run bootstrap credential belongs only to the WebUI
                # process and must never reach an engine, terminal, updater,
                # or spawned-agent subprocess.
                "PUPPY_SETUP_CODE"):
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
