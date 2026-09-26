"""Claude Code driver.

Spawns the official `claude` binary per turn in headless stream-json mode:
  claude -p --output-format stream-json --input-format stream-json \
         --include-partial-messages --replay-user-messages --verbose \
         --permission-mode <mode> --permission-prompt-tool stdio \
         (--session-id <uuid> | --resume <uuid>) [--model <m>]

Interactive permission prompts arrive as control_request/can_use_tool on stdout
and are answered with control_response on stdin (verified against claude 2.1.219).

The AskUserQuestion tool rides that same channel (verified against claude
2.1.278): its can_use_tool carries the questions as the tool input, and the
allow's updatedInput carries the person's answers back, keyed by each
question's own text - which is all the CLI matches on. puppy.questions reads
the input into the rows the console draws and builds that map; a request it
cannot read stays an ordinary approval, and an allow without answers is the
CLI's own "The user did not answer the questions."

Side questions ("/btw") are a client-originated control_request, verified
against claude 2.1.258: request {subtype:"side_question", question, history?}
answered by exactly one control_response {response, synthetic, refusal_fallback?}
with system/control_request_progress rows (started, api_retry) in between, and
withdrawn with control_cancel_request. The CLI forks the live conversation for
a single tool-less answer that never enters its transcript, so the running turn
is untouched. history is ours to keep - the CLI ignores its own for SDK callers.
An unknown subtype is refused with an error control_response, never a crash.
Probed facts the runner depends on: it answers while an approval is pending and
during a background-task wait, and it can answer AFTER the turn's own result,
so stdin must stay open until it resolves.

Background work (Bash run_in_background, Monitor, backgrounded agents) is the
CLI's own, verified against claude 2.1.258: it reports the live set as
system/background_tasks_changed (REPLACE semantics, ambient housekeeping
flagged), the edges as task_started/task_updated/task_notification, and while
stdin stays open it wakes the model itself when a task ends - a fresh
system/init, a continuation, and another result in the same process. Closing
stdin ends every task (~5 s, reported as stopped) and exits; a later resume
first reports such a task as stale and runs an empty wake-up whose result
precedes the prompt's own. duration_api_ms accumulates over
the process while usage and num_turns are per result.

The model answering is read from system/init and from each assistant message
of the main conversation, as claude 2.1.283's own SDK message schema defines
them: a subagent's messages carry the parent_tool_use_id of its tool call and
its own model, and never move the conversation's (the CLI's own served-model
report skips them the same way). When the CLI moves the conversation to another
model mid-turn it says so first in system/model_fallback (or
model_consent_fallback) with fallback_model and readable content; that wording
rides on the report of the model once it answers, as the model action's note.
Compliance note: we only drive the unmodified official binary; auth stays inside
the CLI's own login (subscription OAuth), which is the vendor-sanctioned path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from puppy import questions, quota
from puppy.drivers import base as driver_base
from puppy.drivers import messages
from puppy.drivers.base import Driver, ToolUnavailable, stringify_content
from puppy.user_paths import service_home

log = logging.getLogger("puppy.drivers")


# Public SDKRateLimitInfo / SDKResultError codes. Unrecognized windows retain
# the general meaning of status without guessing a model or a time window.
_LIMIT_NAMES = {
    "five_hour": "five-hour usage limit",
    "seven_day": "weekly usage limit",
    "seven_day_opus": "weekly Opus usage limit",
    "seven_day_sonnet": "weekly Sonnet usage limit",
    "overage": "extra usage limit",
}
_OVERAGE_REASONS = {
    "overage_not_provisioned": "extra usage is not enabled",
    "org_level_disabled": "extra usage is disabled by your organization",
    "org_level_disabled_until": "extra usage is temporarily disabled by your organization",
    "out_of_credits": "extra usage credits have run out",
    "seat_tier_level_disabled": "extra usage is disabled for your seat",
    "member_level_disabled": "extra usage is disabled for your account",
    "seat_tier_zero_credit_limit": "your seat has no extra usage allowance",
    "group_zero_credit_limit": "your group has no extra usage allowance",
    "member_zero_credit_limit": "your account has no extra usage allowance",
    "org_service_level_disabled": "extra usage is disabled for this service",
    "no_limits_configured": "extra usage has no configured allowance",
    "fetch_error": "extra usage availability could not be checked",
    "unknown": "extra usage is unavailable",
}
_RESULT_ERRORS = {
    "error_during_execution": "Could not complete the request",
    "error_max_turns": "Stopped after reaching the turn limit",
    "error_max_budget_usd": "Stopped after reaching the configured spending limit",
    "error_max_structured_output_retries": "Could not produce the requested response format after retrying",
}


def _rate_notice(info, ctx):
    status = messages.text(info.get("status"))
    using_extra = info.get("isUsingOverage") is True or info.get("overageInUse") is True
    if status == "allowed" and not using_extra:
        return None
    if not status:
        return None
    window = messages.text(info.get("rateLimitType"))
    name = _LIMIT_NAMES.get(window, "usage limit")
    if window and window not in _LIMIT_NAMES:
        messages.unknown("usage window", window)
    extra = messages.text(info.get("overageStatus"))
    tone = "warn"
    if status == "allowed_warning":
        sentence = "Approaching your " + name
    elif status == "rejected":
        sentence = name[:1].upper() + name[1:] + " reached"
        if not using_extra and extra not in ("allowed", "allowed_warning"):
            tone = "bad"
    elif status == "allowed" and using_extra:
        sentence = "Using extra usage allowance"
    else:
        messages.unknown("usage status", status)
        return messages.once(ctx, messages.notice(
            messages.first_text(info.get("message"), info.get("text")) or
            "Usage status changed", "info"),
            ("usage", status, window))
    used = messages.number(info.get("utilization"), 0, 1)
    if used is not None and status in ("allowed_warning", "rejected"):
        sentence += " · {:g}% used".format(round(used * 100, 1))
    if using_extra and status != "allowed":
        sentence += " · using extra usage allowance"
    elif status == "rejected" and extra in ("allowed", "allowed_warning"):
        sentence += " · extra usage is available"
    elif status == "rejected" and extra == "rejected":
        reason = messages.text(info.get("overageDisabledReason"))
        sentence += " · " + _OVERAGE_REASONS.get(reason, "extra usage is unavailable")
        if reason and reason not in _OVERAGE_REASONS:
            messages.unknown("extra usage", reason)
    # An "using extra usage" notice does not identify an exhausted window.
    # Its included-allowance reset must not be presented as the extra one.
    reset = info.get("resetsAt") if status != "allowed" else None
    return messages.once(ctx, messages.notice(sentence, tone, reset),
                         ("usage", status, window, reset, using_extra, extra,
                          info.get("overageDisabledReason"), info.get("surpassedThreshold")))


def _retry_status(ev):
    parts = ["Retrying request"]
    attempt = messages.number(ev.get("attempt"), 1)
    maximum = messages.number(ev.get("max_retries"), 1)
    if attempt is not None and maximum is not None and attempt <= maximum:
        parts.append("attempt {:g} of {:g}".format(attempt, maximum))
    delay = messages.number(ev.get("retry_delay_ms"), 0)
    if delay is not None:
        parts.append("next attempt in {:g}s".format(round(delay / 1000, 1)))
    return " · ".join(parts)


def _result_error(ev):
    errors = ev.get("errors")
    detail = "\n".join(filter(None, map(messages.text, errors))) if isinstance(errors, list) else ""
    subtype = messages.text(ev.get("subtype"))
    fallback = _RESULT_ERRORS.get(subtype)
    if not fallback:
        messages.unknown("result error", subtype)
    return messages.first_text(ev.get("result"), detail, fallback) or "Could not complete the request"


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")


def _quota_account():
    """Read only the CLI's non-secret OAuth account metadata.

    Auth status / the turn's initialize reply must separately prove that this
    stored account is the active first-party subscription, not an API provider.
    """
    if any(os.environ.get(key) for key in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY")):
        return {}
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(service_home(), ".claude")
    if not os.path.exists(os.path.join(root, ".credentials.json")):
        return {}
    path = os.path.join(root, ".config.json")
    if not os.path.exists(path):
        path = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or service_home(),
                            ".claude.json")
    account = quota.read_object(path).get("oauthAccount")
    if not isinstance(account, dict):
        return {}
    key = quota.account_key("anthropic-subscription", account.get("accountUuid"),
                            account.get("organizationUuid"))
    if not key:
        return {}
    return {"key": key, "email": account.get("emailAddress"),
            "organization": account.get("organizationName"),
            "org_id": account.get("organizationUuid")}


_TASK_ENDED = ("completed", "failed", "killed", "stopped")
# Our request ids travel on the wire, so they are namespaced rather than
# trusted to be distinct from the CLI's own.
_ID_SQ_PREFIX = "puppy-sq:"
_MODEL_REQUEST_ID = "puppy-models"
_MODEL_OUTPUT_LIMIT = 8 * 1024 * 1024
_MODEL_MESSAGE_LIMIT = 128
# Claude's documented context selector is metadata on a model choice. The
# provider-facing assistant event can omit it even when system/init and the
# picker resolution retain it. Match the capacity syntax, never a model family
# or version, so future catalog names remain opaque to Puppy.
_CONTEXT_SELECTOR_RE = re.compile(r"\[[1-9][0-9]*(?:\.[0-9]+)?[km]\]$", re.I)
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")
# Unlisted request spellings the CLI is asked to resolve per catalog read.
MODEL_RESOLVE_LIMIT = 6
_EFFORT_HINTS = {
    "low": "Fastest, minimal reasoning",
    "medium": "Balanced",
    "high": "More reasoning",
    "xhigh": "Extensive reasoning",
    "max": "Maximum reasoning",
    "ultra": "Maximum reasoning with delegation",
}


def _effort_option(value: str) -> dict:
    label = "X-High" if value == "xhigh" else value.replace("_", " ").title()
    return {"value": value, "label": label,
            "hint": _EFFORT_HINTS.get(value, "Claude model effort")}


def _claude_efforts(values=None) -> list:
    options = [{"value": "", "label": "Default", "hint": "Claude model default"}]
    seen = set()
    normalized = []
    for raw in _EFFORT_ORDER[:-1] if values is None else values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    order = {value: index for index, value in enumerate(_EFFORT_ORDER)}
    normalized.sort(key=lambda value: (order.get(value, len(order)), value))
    for value in normalized:
        options.append(_effort_option(value))
    return options


def _model_aliases(value: str, resolved: str) -> list:
    """Request spellings covered by a named initialize row.

    Claude accepts its documented [1m] selector on aliases and full IDs even
    when the picker lists only the bare name (observed in 2.1.263). These are
    display/capability matches, not permission to rewrite a requested context
    window. Exact picker entries always take precedence over these aliases.
    """
    aliases = []
    for name in (value, resolved):
        if not name:
            continue
        forms = [name]
        selector = _CONTEXT_SELECTOR_RE.search(name)
        if selector is None:
            forms.append(name + "[1m]")
        elif selector.group().lower() == "[1m]":
            forms.append(name[:selector.start()])
        for form in forms:
            if form != value and form not in aliases:
                aliases.append(form)
    return aliases


def parse_model_catalog(models) -> list:
    """Normalize the initialize reply's model picker without guessing IDs."""
    if not isinstance(models, list):
        raise RuntimeError("Claude initialize response did not include models")
    options = []
    seen = set()
    for raw in models[:512]:
        if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
            continue
        native_value = raw["value"].strip()
        if not native_value:
            continue
        value = "" if native_value == "default" else native_value
        if value in seen:
            continue
        seen.add(value)
        label = str(raw.get("displayName") or native_value).strip()[:120]
        if not value and label.lower().startswith("default"):
            label = "Default"
        description = str(raw.get("description") or "").strip()
        resolved = str(raw.get("resolvedModel") or "").strip()
        hint = description
        if resolved and resolved not in hint:
            hint = "{}{}{}".format(hint, " · " if hint else "", resolved)
        option = {"value": value, "label": label or ("Default" if not value else value),
                  "hint": hint[:240]}
        if resolved:
            # Keep the engine's mapping as data. Model-move detection must not
            # reverse-engineer a changing alias or version from display text.
            option["resolved_model"] = resolved
        levels = raw.get("supportedEffortLevels")
        if raw.get("supportsEffort") is False:
            option["effort_options"] = _claude_efforts([])
        elif isinstance(levels, list):
            option["effort_options"] = _claude_efforts(levels)
        options.append(option)
    if "" not in seen:
        options.insert(0, {
            "value": "", "label": "Default", "hint": "Claude CLI default model",
            "effort_options": _claude_efforts(),
        })
    # A row explicitly resolving to an ID owns that spelling ahead of a
    # context variant inferred from another row. This keeps separate short
    # and extended entries' effort levels distinct, including full-ID picks.
    owners = {option["value"]: option for option in options}
    for option in options:
        if option["value"] and option.get("resolved_model"):
            owners.setdefault(option["resolved_model"], option)
    for option in options:
        if not option["value"]:
            continue
        option["aliases"] = [alias for alias in _model_aliases(
            option["value"], option.get("resolved_model", ""))
            if owners.setdefault(alias, option) is option]
    return options


def _static_model_options() -> list:
    efforts = _claude_efforts()
    return [
        {"value": "", "label": "Default", "hint": "Claude CLI default model",
         "effort_options": efforts},
        {"value": "fable", "label": "Fable", "hint": "Latest Fable - most capable",
         "effort_options": efforts},
        {"value": "opus", "label": "Opus", "hint": "Latest Opus",
         "effort_options": efforts},
        {"value": "sonnet", "label": "Sonnet", "hint": "Latest Sonnet",
         "effort_options": efforts},
        {"value": "haiku", "label": "Haiku", "hint": "Latest Haiku - fast and cheap",
         "effort_options": efforts},
    ]


async def _read_model_catalog(binary: str, model: str = "") -> list:
    """The CLI's picker from a no-turn initialize exchange.

    With ``model`` the CLI is started as a turn would be for that request,
    and its picker then carries a row spelled exactly that way with the
    model it resolves to (observed on 2.1.273: a spelling it knows lands on
    that model's row, an unknown one becomes a row resolving to itself).
    """
    argv = [
        binary, "-p", "--output-format", "stream-json",
        "--input-format", "stream-json", "--verbose",
        "--no-session-persistence", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
    ]
    if model:
        argv += ["--model", model]
    process = await driver_base.start_probe(argv, writable_stdin=True)
    total = 0
    try:
        body = json.dumps({
            "type": "control_request", "request_id": _MODEL_REQUEST_ID,
            "request": {"subtype": "initialize", "hooks": None},
        }, separators=(",", ":")).encode("utf-8") + b"\n"
        process.stdin.write(body)
        await process.stdin.drain()
        for _ in range(_MODEL_MESSAGE_LIMIT):
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("Claude exited before reporting its models")
            total += len(line)
            if total > _MODEL_OUTPUT_LIMIT:
                raise RuntimeError("Claude model response is too large")
            try:
                event = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            response = event.get("response") if isinstance(event, dict) and \
                event.get("type") == "control_response" else None
            if not isinstance(response, dict) or \
                    response.get("request_id") != _MODEL_REQUEST_ID:
                continue
            if response.get("subtype") != "success":
                raise RuntimeError(str(response.get("error") or
                                       "Claude refused model discovery")[:400])
            payload = response.get("response")
            payload = payload if isinstance(payload, dict) else {}
            return parse_model_catalog(payload.get("models"))
        raise RuntimeError("Claude returned too many unrelated model messages")
    finally:
        await driver_base.end_probe(process)


def _task_row(item) -> dict:
    """One live background task as the runner and consoles see it; empty for
    ambient housekeeping the CLI hides from user-visible activity."""
    if not isinstance(item, dict) or item.get("ambient") or item.get("skip_transcript"):
        return {}
    task_id = str(item.get("task_id") or "")
    if not task_id:
        return {}
    return {"id": task_id, "type": str(item.get("task_type") or ""),
            "description": str(item.get("description") or "")[:200]}


def _task_notice(ev) -> str:
    status = str(ev.get("status") or "")
    summary = str(ev.get("summary") or "").strip()
    if status == "completed" and summary:
        return summary
    label = "failed" if status == "failed" else \
        "completed" if status == "completed" else "stopped"
    return "Background task {}: {}".format(
        label, summary or ev.get("task_id") or "unknown task")


_MODEL_USAGE_FIELDS = (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                       ("cache_read_input_tokens", "cacheReadInputTokens"),
                       ("cache_creation_input_tokens", "cacheCreationInputTokens"))


def _model_usage(model_usage) -> dict:
    """The result's per-model breakdown (modelUsage) in the usage vocabulary,
    accumulated over the process, so the last result covers every wake-up of the turn -
    and every model the turn used, its subagents' and background calls'
    included, which the result's own usage (the main loop's) does not."""
    if not isinstance(model_usage, dict):
        return {}
    out = {}
    for model, entry in model_usage.items():
        if not isinstance(entry, dict) or not str(model or "").strip():
            continue
        row = {}
        for target, origin in _MODEL_USAGE_FIELDS:
            value = entry.get(origin)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                row[target] = int(value)
        if not any(row.values()):
            continue
        out[str(model).strip()[:200]] = row
    return out


def _context_window(model_usage, model) -> int:
    """The window of the model this turn ran on, from the result's per-model
    usage map (contextWindow per model id); the only or largest entry when
    the id seen mid-turn is not among them."""
    if not isinstance(model_usage, dict):
        return 0
    windows = {}
    for key, value in model_usage.items():
        if isinstance(value, dict):
            window = value.get("contextWindow")
            if isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0:
                windows[str(key)] = int(window)
    if not windows:
        return 0
    return windows.get(str(model or ""), max(windows.values()))


class ClaudeDriver(Driver):
    key = "claude"
    label = "Claude Code"
    binary = "claude"
    uses_stdin_stream = True
    supports_steering = True
    steering_acknowledged = True
    supports_side_questions = True
    dynamic_model_options = True
    release_source = {"kind": "npm", "package": "@anthropic-ai/claude-code"}
    upgrade_source = {"kind": "self", "args": ["update"]}

    def permission_options(self):
        return [
            {"value": "auto", "label": "Auto", "hint": "Standard rules - asks for risky actions (interactive approvals)"},
            {"value": "acceptEdits", "label": "Accept edits", "hint": "File edits auto-approved, still asks for commands"},
            {"value": "plan", "label": "Plan", "hint": "Read-only planning mode"},
            {"value": "manual", "label": "Ask everything", "hint": "Every tool use needs approval"},
            {"value": "dontAsk", "label": "Don't ask", "hint": "Skips prompts; disallowed actions are denied"},
            {"value": "bypassPermissions", "label": "Bypass all", "hint": "No permission checks at all - dangerous"},
        ]

    def default_permission(self) -> str:
        return "auto"

    def _fallback_model_options(self):
        # Stable family aliases remain useful on older CLIs which predate the
        # initialize catalog. A successful discovery replaces this whole list.
        return _static_model_options()

    async def _discover_model_options(self, force: bool):
        binary = self.resolved_binary()
        if not binary:
            raise RuntimeError("Claude binary not found")
        return driver_base.ModelCatalogResult(
            await _read_model_catalog(binary), source="engine")

    async def cover_model_spellings(self, spellings) -> None:
        """Ask the CLI which listed model each unlisted spelling names.

        The picker spells a model as it was last requested, so a saved
        default such as ``fable[1m]`` can be absent from a list that names
        the same model ``claude-fable-5-1[1m]``. Puppy never guesses that
        family from the text: the CLI started with ``--model <spelling>``
        reports the spelling's resolved model in its own picker, and only a
        spelling resolving to a model the catalog already lists becomes that
        row's alias. Each spelling is asked once per catalog, at most
        ``MODEL_RESOLVE_LIMIT`` per read, under the catalog's own lock.
        """
        state = self._model_catalog_state()
        if not state.options:
            return
        wanted = []
        for raw in spellings or ():
            spelling = str(raw or "").strip()
            if spelling and spelling not in wanted and \
                    spelling not in state.spelling_attempts and \
                    self.model_option(spelling, state.options) is None:
                wanted.append(spelling)
        if not wanted:
            return
        binary = self.resolved_binary()
        if not binary:
            return
        async with state.lock():
            for spelling in wanted[:MODEL_RESOLVE_LIMIT]:
                if not state.options or spelling in state.spelling_attempts or \
                        self.model_option(spelling, state.options) is not None:
                    continue
                if driver_base.cli_upgrade.is_running(self.key):
                    return
                state.spelling_attempts.add(spelling)
                try:
                    rows = await asyncio.wait_for(
                        _read_model_catalog(binary, model=spelling),
                        timeout=self.model_catalog_timeout(False))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.info("claude could not resolve model %r: %s", spelling, exc)
                    continue
                echo = next((row for row in rows
                             if row["value"].casefold() == spelling.casefold()), None)
                resolved = str((echo or {}).get("resolved_model") or "")
                if not resolved:
                    log.info("claude did not resolve model %r", spelling)
                elif state.learn_spelling(spelling, resolved):
                    log.info("claude resolves model %r to %s", spelling, resolved)
                else:
                    log.info("claude resolves model %r to unlisted %s", spelling, resolved)

    def effort_options(self):
        seen = {}
        for model in self.model_options():
            for option in model.get("effort_options") or []:
                if isinstance(option, dict) and isinstance(option.get("value"), str):
                    seen.setdefault(option["value"], dict(option))
        if len(seen) <= 1:
            return _claude_efforts()
        order = {value: index for index, value in enumerate(("",) + _EFFORT_ORDER)}
        return sorted(seen.values(), key=lambda item: (
            order.get(item["value"], len(order)), item["value"]))

    @staticmethod
    def _reported_model_forms(value: str) -> set:
        """Comparable forms of a Claude-reported model id.

        Claude Code may keep a capacity selector in its picker/system model
        while stripping it from the provider-facing assistant model. Nothing
        else is parsed: family names and version components remain opaque.
        """
        value = str(value or "").strip()
        if not value:
            return set()
        without_selector = _CONTEXT_SELECTOR_RE.sub("", value)
        return {value, without_selector}

    def models_equivalent(self, first: str, second: str, ctx=None) -> bool:
        return bool(self._reported_model_forms(first) &
                    self._reported_model_forms(second))

    def model_request_matches(self, requested: str, reported: str, ctx=None) -> bool:
        requested = str(requested or "").strip()
        if not requested:
            return True
        options = ctx.get("model_options") if isinstance(ctx, dict) else None
        if not isinstance(options, list):
            options = self.model_options()
        # Values are engine-owned and normally compared exactly. The casefold
        # fallback retains the CLI's long-standing case-insensitive aliases
        # without making provider-specific ids case-insensitive generally.
        option = self.model_option(requested, options)
        if option is None:
            folded = requested.casefold()
            option = next((item for item in options
                           if isinstance(item, dict) and
                           str(item.get("value") or "").casefold() == folded), None)
        resolved = str((option or {}).get("resolved_model") or "").strip()
        if resolved:
            return self.models_equivalent(resolved, reported, ctx)
        # Older CLIs and explicit full ids may not have a catalog row. Retain
        # the generic short-alias behavior, but compare forms after removing
        # only Claude's documented capacity selector.
        requested_forms = self._reported_model_forms(requested)
        reported_forms = self._reported_model_forms(reported)
        return any(left.casefold() in right.casefold()
                   for left in requested_forms for right in reported_forms)

    def _served_model(self, ctx, model) -> list:
        """One report of the model answering the main conversation, or none
        when it is the model already reported. The CLI's own words for a
        switch to it (system/model_fallback) ride along as the report's note."""
        model = str(model or "").strip()
        if not model or model == "<synthetic>":
            return []
        switch = ctx.get("model_switch")
        switched = switch and (self.models_equivalent(switch["model"], model, ctx) or
                               self.model_request_matches(switch["model"], model, ctx))
        if not switched and self.models_equivalent(model, ctx.get("model_seen"), ctx):
            return []
        ctx["model_seen"] = model
        action = {"a": "model", "model": model}
        if switched:
            ctx.pop("model_switch", None)
            action["note"] = switch["note"]
        return [action]

    def tool_options(self):
        return [
            {"value": "compact", "label": "Compact context",
             "hint": "Summarize the conversation so far into a shorter context"},
            {"value": "undo", "label": "Undo last turn",
             "hint": "Revert the engine's context to before your last prompt; "
                     "Puppy's transcript and files stay unchanged"},
        ]

    def tool_plan(self, tool, session, turns):
        """Verified against claude 2.1.258. Compaction is the ordinary
        stream-json turn whose user message is the local command "/compact".
        Undo cannot run on its own in print mode: the NEXT prompt resumes with
        --resume-session-at <last message of the turn before the dropped one>
        --resume-drops-turn <dropped prompt uuid>, which appends that prompt
        under the earlier message and leaves the dropped turn orphaned in the
        transcript for every later plain resume. Headless file rewinding is
        not enabled, so files stay as they are."""
        if tool == "compact":
            return {"run": True, "params": {}}
        if tool != "undo":
            raise ToolUnavailable("{} does not support {}".format(self.label, tool))
        if driver_base.undo_state_get(session.get("id")):
            raise ToolUnavailable(
                "The last undo is still pending - send a prompt before undoing again")
        last = turns[0] if turns else None
        if last is None:
            raise ToolUnavailable("Nothing to undo yet - this session has not run a turn")
        if last.get("kind") != "prompt":
            raise ToolUnavailable(
                "The last thing that ran was a {}, which cannot be undone".format(
                    "compaction" if last.get("tool") == "compact" else "session tool"))
        result = last.get("result") or {}
        native = str(session.get("native_session_id") or "")
        if not result:
            raise ToolUnavailable("The last turn did not finish, so it cannot be undone")
        if not result.get("native_prompt_id") or \
                str(result.get("native_session_id") or "") != native:
            raise ToolUnavailable(
                "The last turn predates undo support here and cannot be undone")
        previous = (turns[1].get("result") or {}) if len(turns) > 1 else {}
        resume_at = str(previous.get("native_tail_id") or "")
        if not resume_at or str(previous.get("native_session_id") or "") != native:
            raise ToolUnavailable("Only turns after the first can be undone")
        return {
            "run": False,
            "state": {"engine": self.key, "resume_at": resume_at,
                      "drops": str(result["native_prompt_id"])},
            "text": "Engine context reverted: the next prompt continues from before "
                    "the last turn (Puppy's transcript and files are unchanged)",
            "restore_text": str(last.get("text") or ""),
        }

    @staticmethod
    def _turn_text(prompt, tool):
        return "/compact" if driver_base.tool_name(tool) == "compact" else prompt

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt="", terminal_mcp=None, vnc_mcp=None,
                  spawn_mcp=None, session_mcp=None, tool=None):
        tool_kind = driver_base.tool_name(tool)
        argv = [self.binary, "-p",
                "--output-format", "stream-json",
                "--input-format", "stream-json",
                "--include-partial-messages",
                "--replay-user-messages",
                "--verbose",
                "--permission-mode", session.get("permission_mode") or self.default_permission(),
                "--permission-prompt-tool", "stdio"]
        # a tool turn only ever addresses the existing native conversation:
        # no agent bridges, no guidance, and never a fresh session
        mcps = [] if tool_kind else \
            [item for item in (browser_mcp, terminal_mcp, vnc_mcp, spawn_mcp,
                               session_mcp) if item]
        if mcps:
            mcp_config = {"mcpServers": {item["name"]: {
                "type": "stdio", "command": item["command"],
                "args": list(item.get("args") or []),
                "env": dict(item.get("env") or {}),
            } for item in mcps}}
            argv += ["--mcp-config", json.dumps(mcp_config, separators=(",", ":"))]
        guidance = [] if tool_kind else [str(system_prompt or "").strip()]
        guidance.extend(str(item.get("engine_guidance") or "").strip()
                        for item in mcps)
        guidance = "\n\n".join(part for part in guidance if part)
        if guidance:
            # Additive: preserve any system prompt the user or CLI already
            # supplies while making both configured layers model-visible.
            argv += ["--append-system-prompt", guidance]
        native = session.get("native_session_id") or ""
        if tool_kind:
            argv += ["--resume", native]
        elif first_turn or not native:
            argv += ["--session-id", pinned_id]
        else:
            argv += ["--resume", native]
            pending = driver_base.undo_state_get(session.get("id"))
            if pending.get("engine") == self.key and \
                    pending.get("native_session_id") == native and \
                    pending.get("resume_at") and pending.get("drops"):
                argv += ["--resume-session-at", str(pending["resume_at"]),
                         "--resume-drops-turn", str(pending["drops"])]
        model = (session.get("model") or "").strip()
        if model:
            argv += ["--model", model]
        effort = (session.get("effort") or "").strip()
        if effort:
            argv += ["--effort", effort]
        return argv

    def build_env(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt="", terminal_mcp=None, vnc_mcp=None,
                  spawn_mcp=None, session_mcp=None, tool=None):
        # Claude normally refuses bypassPermissions when its effective user is
        # root. The vendor's sandbox marker is deliberately turn-scoped: the
        # runner starts every turn with clean_env(), then calls this method for
        # the freshly spawned process. Changing modes therefore removes or
        # restores the marker on the very next turn without persistent state.
        mode = session.get("permission_mode") or self.default_permission()
        if mode == "bypassPermissions" and getattr(os, "geteuid", lambda: -1)() == 0:
            return {"IS_SANDBOX": "1"}
        return {}

    def initial_stdin(self, session, prompt, tool=None):
        return [
            {"type": "control_request", "request_id": "init_1",
             "request": {"subtype": "initialize", "hooks": None}},
            {"type": "user", "message": {"role": "user",
             "content": [{"type": "text", "text": self._turn_text(prompt, tool)}]}},
        ]

    def turn_context(self, session, first_turn, prompt, pinned_id,
                     browser_mcp=None, system_prompt="", terminal_mcp=None,
                     vnc_mcp=None, spawn_mcp=None, session_mcp=None, tool=None):
        # --replay-user-messages gives a protocol-level acknowledgement for
        # each text message accepted from stdin. Do not expose steering until
        # the original prompt itself has been replayed, and retain the exact
        # FIFO identities needed to correlate later replays without putting a
        # Puppy request id into model-visible text.
        return {
            "quota_account": _quota_account(),
            "quota_identity": None,
            "initial_prompt": self._turn_text(prompt, tool),
            "initial_user_replayed": False,
            "pending_steers": [],
            "tool": driver_base.tool_name(tool),
            # transcript identities a later undo needs: this turn's prompt and
            # its last chain entry (user/assistant/compaction messages carry
            # the uuid the CLI persists; results do not)
            "native_session_id": "",
            "prompt_uuid": "",
            "tail_uuid": "",
            "compacted": False,
            # the last API request's prompt size: what the model actually held
            "context_used": None,
            # a local command (compaction) acknowledges the request through its
            # replayed output rather than a replay of the "/compact" text
            "local_command_seen": False,
            # the initialize control_response: proof that the control channel
            # itself is live, which is what a side question rides on. The
            # user-message replay proves only that the query accepts prompts.
            "control_ready": False,
            # A turn's initialize mapping is authoritative for its own alias.
            # Start with last-known-good so an older CLI that omits the list
            # still benefits without coupling concurrent turns to later ingest.
            "model_options": self.model_options(),
            # background work the CLI still owns: REPLACE semantics from
            # background_tasks_changed, task edges as the fallback
            "background_tasks": [],
            # wake-up queries for tasks of a PREVIOUS process run ahead of our
            # own request; their results are folded, never taken as ours
            "stale_wakeups": 0,
            # every result of this process adds to the prompt's totals
            "folded_usage": {},
            "folded_turns": None,
            "wakeups": 0,
        }

    @staticmethod
    def _request_acknowledged(ctx) -> bool:
        return bool(ctx.get("initial_user_replayed") or ctx.get("compacted") or
                    ctx.get("local_command_seen"))

    @staticmethod
    def _set_tasks(ctx, tasks) -> list:
        if tasks == list(ctx.get("background_tasks") or []):
            return []
        ctx["background_tasks"] = tasks
        return [{"a": "background_tasks", "tasks": [dict(row) for row in tasks]}]

    def _drop_task(self, ctx, task_id) -> list:
        task_id = str(task_id or "")
        return self._set_tasks(ctx, [row for row in (ctx.get("background_tasks") or [])
                                     if row.get("id") != task_id])

    def approval_payload(self, request_id, behavior, original_input, message="",
                         updated_permissions=None, request=None, answers=None):
        if behavior == "allow":
            resp = {"behavior": "allow", "updatedInput": original_input or {}}
            if answers is not None and isinstance(request, dict) and \
                    request.get("kind") == "question":
                # the person's answers, keyed the way the CLI reads them
                resp["updatedInput"] = questions.reply_input(
                    original_input, request.get("questions"), answers)
            if updated_permissions:
                resp["updatedPermissions"] = updated_permissions
        else:
            resp = {"behavior": "deny", "message": message or "Denied by user"}
        return {"type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": resp}}

    def interrupt_payload(self, session=None, ctx=None):
        return {"type": "control_request", "request_id": "int_1",
                "request": {"subtype": "interrupt"}}

    def steer_payload(self, session, ctx, text, request_id):
        # stream-json input remains open for the whole invocation. Additional
        # user messages are incorporated by the active query at its next safe
        # processing boundary; no separate turn or session is created.
        if not self.steer_ready(session, ctx):
            return None
        ctx.setdefault("pending_steers", []).append({
            "request_id": request_id, "text": text,
        })
        return {"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": text}]}}

    def steer_ready(self, session, ctx):
        # A pipe drain proves only that bytes reached the CLI process. Its
        # replay of the original user message proves that the live query has
        # actually accepted stream input and can receive additional guidance.
        return bool(ctx.get("initial_user_replayed")) and not ctx.get("tool")

    def side_question_payload(self, session, ctx, question, history, request_id):
        # A control_request, not a user message: the CLI answers it from a
        # one-shot tool-less fork of the same context and never shows it to
        # the running turn. Only answered exchanges are replayed, so a thread
        # never carries a placeholder forward as if the model had said it.
        if not self.side_question_ready(session, ctx):
            return None
        request = {"subtype": "side_question", "question": question}
        rows = []
        for item in history or []:
            row = {"question": str(item.get("question") or ""),
                   "response": str(item.get("response") or "")}
            if not row["question"] or not row["response"]:
                continue
            notice = str(item.get("fallback_notice") or "")
            if notice:
                row["fallback_notice"] = notice
            rows.append(row)
        if rows:
            request["history"] = rows
        return {"type": "control_request",
                "request_id": _ID_SQ_PREFIX + request_id, "request": request}

    def side_question_cancel_payload(self, session, ctx, request_id):
        return {"type": "control_cancel_request",
                "request_id": _ID_SQ_PREFIX + request_id}

    def side_question_ready(self, session, ctx):
        # The initialize reply is the control channel's own acknowledgement.
        # A tool turn addresses the native conversation with no live query to
        # fork, so it is never offered one.
        return bool(ctx.get("control_ready")) and not ctx.get("tool")

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except Exception:
            return []
        if not isinstance(ev, dict):
            return []
        t = ev.get("type")

        if t == "stream_event":
            inner = ev.get("event") or {}
            it = inner.get("type")
            if it == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta":
                    return [{"a": "transient", "msg": {"type": "delta", "block": "text",
                                                      "text": delta.get("text", "")}}]
                if delta.get("type") == "thinking_delta":
                    return [{"a": "transient", "msg": {"type": "delta", "block": "thinking",
                                                      "text": delta.get("thinking", "")}}]
            elif it == "content_block_start":
                blk = (inner.get("content_block") or {})
                if blk.get("type") == "tool_use":
                    return [{"a": "transient", "msg": {"type": "status",
                                                      "text": f"Using {blk.get('name', 'tool')}..."}}]
            return []

        if t == "system":
            sub = ev.get("subtype")
            if sub == "control_request_progress":
                # the only long-running client request we make; other ids are
                # not ours to interpret
                rid = str(ev.get("request_id") or "")
                if not rid.startswith(_ID_SQ_PREFIX):
                    return []
                return [{"a": "side_question_progress",
                         "request_id": rid[len(_ID_SQ_PREFIX):],
                         "status": str(ev.get("status") or ""),
                         "attempt": ev.get("attempt"),
                         "max_retries": ev.get("max_retries"),
                         "retry_delay_ms": ev.get("retry_delay_ms")}]
            if sub == "init":
                ctx["native_session_id"] = str(ev.get("session_id") or "")
                acts = [{"a": "native_id", "id": ev.get("session_id", "")},
                        {"a": "transient", "msg": {"type": "turn_init",
                                                   "model": ev.get("model", ""),
                                                   "tools": len(ev.get("tools") or [])}}]
                acts.extend(self._served_model(ctx, ev.get("model")))
                return acts
            if sub in ("model_fallback", "model_consent_fallback"):
                # The CLI moved the conversation to another model (claude
                # 2.1.283's SDK schema: availability or a consent gate, with
                # fallback_model and its own readable content). The move
                # counts once that model answers; until then only its wording
                # is kept, for the report of that model.
                model = messages.text(ev.get("fallback_model"))
                note = messages.text(ev.get("content"))
                if model and note:
                    ctx["model_switch"] = {"model": model, "note": note}
                return []
            if sub == "status":
                code = messages.text(ev.get("status"))
                status = {"requesting": "Requesting", "compacting": "Compacting context", "": ""}.get(code)
                if status is None:
                    messages.unknown("activity status", code)
                    status = "Working…"
                return [{"a": "transient", "msg": {"type": "status", "text": status}}]
            if sub == "api_retry":
                return [{"a": "transient", "msg": {"type": "status", "text": _retry_status(ev)}}]
            if sub == "notification":
                tone = {"error": "bad", "warning": "warn", "success": "ok"}.get(messages.text(ev.get("color")), "info")
                return messages.actions(ctx, messages.notice(ev.get("text"), tone))
            if sub == "informational":
                text = messages.text(ev.get("content"))
                if not text:
                    return []
                if ev.get("level") == "info":
                    return [{"a": "event", "kind": "info", "data": {"subtype": "engine_info", "text": text}}]
                tone = "warn" if ev.get("level") in ("warning", "suggestion") or ev.get("prevent_continuation") is True else "info"
                return messages.actions(ctx, messages.notice(text, tone))
            if sub == "thinking_tokens":
                return [{"a": "transient", "msg": {"type": "thinking_tokens",
                                                   "tokens": ev.get("estimated_tokens", 0)}}]
            if sub == "compact_boundary":
                ctx["compacted"] = True
                if ev.get("uuid"):
                    ctx["tail_uuid"] = str(ev["uuid"])
                meta = ev.get("compact_metadata") if isinstance(
                    ev.get("compact_metadata"), dict) else {}
                pre = meta.get("pre_tokens")
                text = "Context compacted"
                if isinstance(pre, (int, float)) and not isinstance(pre, bool) and pre > 0:
                    text += " · {:,} tokens before".format(int(pre))
                return [{"a": "event", "kind": "info",
                         "data": {"subtype": "compact", "text": text}}]
            if sub == "background_tasks_changed":
                rows = [_task_row(item) for item in (ev.get("tasks") or [])]
                return self._set_tasks(ctx, [row for row in rows if row])
            if sub == "task_started":
                row = _task_row(ev)
                if row and ev.get("is_backgrounded") and all(
                        item.get("id") != row["id"]
                        for item in ctx.get("background_tasks") or []):
                    return self._set_tasks(
                        ctx, list(ctx.get("background_tasks") or []) + [row])
                return []
            if sub == "task_updated":
                patch = ev.get("patch") if isinstance(ev.get("patch"), dict) else {}
                if patch.get("status") in _TASK_ENDED:
                    return self._drop_task(ctx, ev.get("task_id"))
                return []
            if sub == "task_notification":
                acts = self._drop_task(ctx, ev.get("task_id"))
                if not self._request_acknowledged(ctx):
                    # a task of the previous process, reported before our own
                    # request: the CLI wakes the model for it ahead of the prompt
                    ctx["stale_wakeups"] = int(ctx.get("stale_wakeups") or 0) + 1
                    return acts
                if ev.get("skip_transcript") or ev.get("ambient"):
                    return acts
                data = {
                    "subtype": "task", "status": str(ev.get("status") or ""),
                    "task_id": str(ev.get("task_id") or ""),
                    "text": _task_notice(ev)}
                # Native provenance, when supplied: never guess a tool from
                # its command or task description. Some notices have no tool.
                if ev.get("tool_use_id"):
                    data["tool_use_id"] = str(ev["tool_use_id"])
                acts.append({"a": "event", "kind": "info", "data": data})
                return acts
            return []

        if t == "assistant":
            acts = []
            if ev.get("uuid"):
                ctx["tail_uuid"] = str(ev["uuid"])
            msg = ev.get("message") or {}
            if msg.get("model") == "<synthetic>":
                # The CLI speaks in the model's place: an API error (the
                # wrapper carries is_api_error_message/error) or a local
                # notice. Neither is a model move nor a request the model
                # held, and an error is reported as one.
                if ev.get("is_api_error_message") or ev.get("error"):
                    text = "\n".join(
                        blk.get("text") for blk in msg.get("content") or []
                        if isinstance(blk, dict) and blk.get("type") == "text"
                        and blk.get("text")).strip()
                    if text:
                        acts.append({"a": "event", "kind": "error", "data": {
                            "subtype": "engine_api_error",
                            "code": str(ev.get("error") or ""),
                            "text": text}})
                return acts
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if usage:
                ctx["context_used"] = sum(
                    int(usage.get(k) or 0) for k in
                    ("input_tokens", "cache_read_input_tokens",
                     "cache_creation_input_tokens", "output_tokens"))
            # Per-response model id: catches a mid-turn fallback. Only the
            # main conversation's - a subagent answers on its own model inside
            # its tool call (parent_tool_use_id names that call), which is
            # exactly how the CLI tells its own served model apart.
            if not ev.get("parent_tool_use_id"):
                acts.extend(self._served_model(ctx, msg.get("model")))
            for blk in msg.get("content") or []:
                bt = blk.get("type")
                if bt == "text" and blk.get("text"):
                    acts.append({"a": "event", "kind": "assistant", "data": {"text": blk["text"]}})
                elif bt == "thinking" and blk.get("thinking"):
                    acts.append({"a": "event", "kind": "thinking", "data": {"text": blk["thinking"]}})
                elif bt == "tool_use":
                    acts.append({"a": "event", "kind": "tool_use",
                                 "data": {"tool": blk.get("name", "?"),
                                          "input": blk.get("input") or {},
                                          "tool_use_id": blk.get("id", "")}})
            return acts

        if t == "user":
            acts = []
            if ev.get("uuid"):
                ctx["tail_uuid"] = str(ev["uuid"])
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and "<local-command-stdout>" in content:
                ctx["local_command_seen"] = True
            if isinstance(content, list):
                texts = [blk.get("text") for blk in content
                         if isinstance(blk, dict) and
                         blk.get("type") == "text" and
                         isinstance(blk.get("text"), str)]
                for text in texts:
                    if "<local-command-stdout>" in text:
                        ctx["local_command_seen"] = True
                    if not ctx.get("initial_user_replayed") and \
                            text == ctx.get("initial_prompt"):
                        ctx["initial_user_replayed"] = True
                        ctx["prompt_uuid"] = str(ev.get("uuid") or "")
                        continue
                    pending = ctx.get("pending_steers") or []
                    match = next((index for index, item in enumerate(pending)
                                  if item.get("text") == text), None)
                    if match is not None:
                        item = pending.pop(match)
                        acts.append({"a": "steer_result",
                                     "request_id": item["request_id"],
                                     "ok": True, "error": ""})
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        acts.append({"a": "event", "kind": "tool_result",
                                     "data": {"tool_use_id": blk.get("tool_use_id", ""),
                                              "content": stringify_content(blk.get("content"))[:20000],
                                              "is_error": bool(blk.get("is_error"))}})
            return acts

        if t == "control_response":
            resp = ev.get("response") or {}
            rid = str(resp.get("request_id") or "")
            if rid == "init_1":
                # the control channel answered: side questions can ride it
                ctx["control_ready"] = resp.get("subtype") == "success"
                if ctx["control_ready"] and isinstance(resp.get("response"), dict):
                    account = resp["response"].get("account")
                    expected = ctx.get("quota_account") or {}
                    if isinstance(account, dict) and expected.get("key") and \
                            account.get("apiProvider") == "firstParty" and \
                            account.get("subscriptionType") and \
                            not account.get("apiKeySource") and \
                            account.get("tokenSource") in (None, "claude.ai") and \
                            account.get("email") == expected.get("email") and \
                            account.get("organization") == expected.get("organization") and \
                            expected == _quota_account():
                        ctx["quota_identity"] = expected["key"]
                        self._quota_login = expected["key"]
                    try:
                        options = parse_model_catalog(resp["response"].get("models"))
                        ctx["model_options"] = options
                        self._model_catalog_state().ingest(options, source="turn")
                    except Exception:
                        # Picker metadata is optional and cannot affect the
                        # control channel's readiness for this real turn.
                        pass
                return []
            if not rid.startswith(_ID_SQ_PREFIX):
                return []
            request_id = rid[len(_ID_SQ_PREFIX):]
            if resp.get("subtype") != "success":
                # an older CLI refuses the subtype outright; the wording is the
                # engine's own and is shown as the reason
                return [{"a": "side_question_result", "request_id": request_id,
                         "ok": False, "error": str(
                             resp.get("error") or "the engine refused the question")}]
            body = resp.get("response")
            body = body if isinstance(body, dict) else {}
            fallback = body.get("refusal_fallback")
            fallback = fallback if isinstance(fallback, dict) else {}
            return [{"a": "side_question_result", "request_id": request_id,
                     "ok": True, "text": str(body.get("response") or ""),
                     # the CLI's own placeholder for an API error or a model
                     # that tried to call a tool: shown, never threaded
                     "synthetic": bool(body.get("synthetic")),
                     "fallback_model": str(fallback.get("fallback_model") or ""),
                     "fallback_notice": str(fallback.get("content") or "")}]

        if t == "control_request":
            req = ev.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                approval = {
                    "request_id": ev.get("request_id", ""),
                    "tool_name": req.get("tool_name", "?"),
                    "display_name": req.get("display_name") or req.get("tool_name", "?"),
                    "input": req.get("input") or {},
                    "description": req.get("description", ""),
                    "suggestions": req.get("permission_suggestions") or [],
                    "tool_use_id": req.get("tool_use_id", ""),
                }
                # a question to the person, drawn as a form rather than a
                # permission card; anything unreadable stays an approval
                asked = questions.questions_from(approval["tool_name"], approval["input"])
                if asked is not None:
                    approval["kind"] = "question"
                    approval["questions"], approval["question_title"] = asked
                return [{"a": "approval", "req": approval}]
            return []

        if t == "control_cancel_request":
            return [{"a": "approval_cancel", "request_id": ev.get("request_id", "")}]

        if t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if isinstance(info, dict):
                windows = info.get("unifiedWindows")
                week = windows.get("seven_day") if isinstance(windows, dict) else None
                if not isinstance(week, dict) and info.get("rateLimitType") == "seven_day":
                    week = info
                if isinstance(week, dict) and quota.finite(week.get("utilization")):
                    self._quota_monitor().observe(self._quota_identity(),
                        ctx.get("quota_identity"), quota.weekly_sample(
                            week["utilization"] * 100, week.get("resetsAt")))
            if not isinstance(info, dict):
                return []
            return [{"a": "rate_limit", "info": info, "notice": _rate_notice(info, ctx)}]

        if t == "result":
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            folded = ctx.setdefault("folded_usage", {})
            for key in USAGE_KEYS:
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    folded[key] = int(folded.get(key) or 0) + int(value)
            turns = ev.get("num_turns")
            if isinstance(turns, (int, float)) and not isinstance(turns, bool):
                ctx["folded_turns"] = int(ctx.get("folded_turns") or 0) + int(turns)
            if not self._request_acknowledged(ctx) and \
                    int(ctx.get("stale_wakeups") or 0) > 0:
                # the wake-up for a previous process's task ran ahead of our
                # request: its result is not the prompt's
                ctx["stale_wakeups"] = int(ctx["stale_wakeups"]) - 1
                return [{"a": "transient", "msg": {
                    "type": "status",
                    "text": "Catching up on an earlier background task..."}}]
            identity = {
                "native_session_id": ctx.get("native_session_id") or
                                     str(ev.get("session_id") or ""),
                "native_prompt_id": ctx.get("prompt_uuid") or "",
                "native_tail_id": ctx.get("tail_uuid") or "",
            }
            if ctx.get("tool") == "compact":
                identity["compacted"] = bool(ctx.get("compacted"))
            window = _context_window(ev.get("modelUsage"), ctx.get("model_seen"))
            if ctx.get("context_used") is not None and window:
                identity["context_used"] = int(ctx["context_used"])
                identity["context_window"] = window
            data = {
                **identity,
                "ok": not ev.get("is_error", False),
                # duration_api_ms accumulates over the
                # process, so the last result already covers every wake-up.
                # API time is not wall time: the runner supplies the latter.
                "api_duration_ms": ev.get("duration_api_ms"),
                "stop_reason": ev.get("stop_reason", ""),
                "num_turns": ctx.get("folded_turns"),
                "usage": dict(folded),
                "error": _result_error(ev)[:2000] if ev.get("is_error") else "",
            }
            model_usage = _model_usage(ev.get("modelUsage"))
            if model_usage:
                data["model_usage"] = model_usage
            if ctx.get("wakeups"):
                data["wakeups"] = int(ctx["wakeups"])
            if data["ok"] and ctx.get("background_tasks") and not ctx.get("tool"):
                # the model has answered, but its background work is still
                # running and the CLI will wake it when that ends: keep the
                # process alive and treat this result as provisional
                ctx["wakeups"] = int(ctx.get("wakeups") or 0) + 1
                return [{"a": "turn_pause", "data": data,
                         "tasks": [dict(row) for row in ctx["background_tasks"]]}]
            return [{"a": "result", "data": data}]

        return []

    def auth_touch_paths(self):
        home = service_home()
        return [os.path.join(home, ".claude", ".credentials.json")]

    def _quota_identity(self):
        key = _quota_account().get("key")
        return key if key == getattr(self, "_quota_login", None) else None

    def _quota_monitor(self):
        if not hasattr(self, "_account_quota"):
            self._account_quota = quota.Monitor("anthropic-subscription", "seven_day")
        return self._account_quota

    def _extra_status(self):
        return {"usage_monitor": self._quota_monitor().payload(self._quota_identity())}

    async def _auth_status(self):
        """`claude auth status --json` is the CLI's own verdict ({"loggedIn":
        bool}), which stays true when a credentials file exists but the login
        behind it has expired or been revoked. Only a parsed loggedIn field is
        treated as authoritative; any other outcome (older CLI without the
        verb, changed output) falls back to the credentials-file heuristic, so
        a wording change can never flip a working login to "missing"."""
        expected = _quota_account()
        rc, out = await self._run_probe([self.binary, "auth", "status", "--json"])
        self._quota_login = None
        verdict = None
        try:
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
                verdict = data
        except (ValueError, TypeError):
            pass
        if verdict is not None:
            if verdict["loggedIn"]:
                if verdict.get("authMethod") == "claude.ai" and \
                        verdict.get("apiProvider") == "firstParty" and \
                        expected.get("key") and expected == _quota_account() and \
                        verdict.get("orgId") == expected.get("org_id") and \
                        verdict.get("email") == expected.get("email"):
                    self._quota_login = expected["key"]
                bits = [str(verdict.get(k)) for k in ("authMethod", "subscriptionType")
                        if verdict.get(k) and verdict.get(k) != "none"]
                return {"auth": "ok",
                        "detail": "logged in" + (" · " + " · ".join(bits) if bits else "")}
            return {"auth": "missing", "detail": "run `claude auth login` as this user"}
        home = service_home()
        cred = os.path.join(home, ".claude", ".credentials.json")
        if os.path.exists(cred):
            return {"auth": "ok", "detail": "credentials file present (auth verb unavailable)"}
        return {"auth": "missing", "detail": f"run `claude auth login` as this user ({cred} not found)"}
