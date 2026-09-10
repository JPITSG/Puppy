"""Codex CLI app-server driver.

Each Puppy turn owns one official ``codex app-server --stdio`` process. The
driver initializes that JSONL connection, starts or resumes the durable Codex
thread, starts one turn, consumes its notifications, and closes stdin after
``turn/completed``. Keeping the server turn-scoped preserves Puppy's process
ownership while exposing Codex's bidirectional active-turn protocol.

Contract verified against codex-cli 0.149.0 and 0.151.0:
  initialize -> initialized -> thread/start|thread/resume -> turn/start
  thread/started, turn/started, item/started|completed,
  thread/tokenUsage/updated, turn/completed

Fast contract verified against 0.153.2: ``model/list`` advertises per-model
``serviceTiers`` and stable ``turn/start`` accepts ``serviceTierForTurn``.
The catalog's semantic name selects the tier; its opaque id is never assumed.

Verified against 0.154.0: a stream the CLI re-establishes on its own arrives as
an ``error`` notification carrying ``willRetry`` (here
``codexErrorInfo.responseStreamDisconnected``, counting itself down as
"Reconnecting... 2/5"), and a ``warning`` once it settles on another transport.
Both are the CLI's own recovery, never the turn's outcome.

Puppy's Codex permission setting remains a sandbox choice. We explicitly use
``approvalPolicy: never``, matching the old non-interactive ``codex exec``
behavior: sandbox denials go back to the model rather than blocking on a UI
approval. Defensive request handling remains in place for managed policies
that can override the ordinary setting.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import re
import shutil
import time

from puppy import __version__, quota
from puppy.drivers import base as driver_base
from puppy.drivers.base import Driver, ToolUnavailable, tool_name
from puppy.user_paths import service_home

log = logging.getLogger("puppy.drivers.codex")

# codex wraps every command in `/bin/bash -lc "<script>"` (or -c); show the script
_SHELL_WRAP = re.compile(r"^\s*(?:\S*/)?(?:ba|z)?sh\s+-l?c\s+(.*)$", re.S)
_BROWSER_POLICY_OPEN = "<puppy_browser_policy>"
_BROWSER_POLICY_CLOSE = "</puppy_browser_policy>"
_TERMINAL_POLICY_OPEN = "<puppy_terminal_policy>"
_TERMINAL_POLICY_CLOSE = "</puppy_terminal_policy>"
_VNC_POLICY_OPEN = "<puppy_vnc_policy>"
_VNC_POLICY_CLOSE = "</puppy_vnc_policy>"
_SPAWN_POLICY_OPEN = "<puppy_spawn_policy>"
_SPAWN_POLICY_CLOSE = "</puppy_spawn_policy>"
_SYSTEM_PROMPT_OPEN = "<puppy_system_prompt>"
_SYSTEM_PROMPT_CLOSE = "</puppy_system_prompt>"

_ID_INITIALIZE = "puppy-initialize"
_ID_THREAD = "puppy-thread"
_ID_TURN = "puppy-turn"
_ID_INTERRUPT = "puppy-interrupt"
_ID_STEER_PREFIX = "puppy-steer:"
_ID_TOOL = "puppy-tool"
_MODEL_PAGE_LIMIT = 100
_MODEL_MAX_PAGES = 10
_MODEL_MAX_ITEMS = 1000
_APP_SERVER_OUTPUT_LIMIT = 8 * 1024 * 1024
_APP_SERVER_MESSAGE_LIMIT = 256
_EFFORT_FALLBACKS = {
    "low": "Fastest, minimal reasoning",
    "medium": "Balanced",
    "high": "More reasoning",
    "xhigh": "Extensive reasoning",
    "max": "Maximum reasoning",
}

# The CLI retries some failures on its own and narrates the attempts while it
# does: a response stream that has to fall back to another transport counts
# itself down as "Reconnecting... 2/5" before the model has said anything. For
# this long after a turn's own start that countdown is the engine coming up,
# not news the person who just pressed Send can act on, so the console keeps
# the starting word it already shows. A retry after the window carries the
# engine's own wording, because by then something is genuinely going wrong.
START_GRACE_SECONDS = 10.0
# The word the console itself shows from Send until the engine speaks, so a
# masked retry changes nothing on screen rather than swapping one word for
# another (app.js: setStatus("Starting…")).
_STARTING_STATUS = "Starting…"


def _rpc(request_id, method: str, params=None) -> dict:
    value = {"id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def _notification(method: str, params=None) -> dict:
    value = {"method": method}
    if params is not None:
        value["params"] = params
    return value


def _with_runtime_guidance(prompt: str, system_prompt: str, browser_mcp,
                           terminal_mcp=None, vnc_mcp=None, spawn_mcp=None,
                           session_mcp=None) -> str:
    """Add node and turn-scoped guidance without replacing native user config.

    Codex's developer_instructions config value is replacement-oriented. A
    tagged runtime preface keeps any user-configured developer instructions
    intact. The runner persists the original text before build_cmd is called,
    so this context never appears as part of the user's WebUI transcript.
    """
    blocks = []
    custom = str(system_prompt or "").strip()
    if custom:
        blocks.append("{}\n{}\n{}".format(
            _SYSTEM_PROMPT_OPEN, custom, _SYSTEM_PROMPT_CLOSE))
    browser = str((browser_mcp or {}).get("engine_guidance") or "").strip()
    if browser:
        blocks.append("{}\n{}\n{}".format(
            _BROWSER_POLICY_OPEN, browser, _BROWSER_POLICY_CLOSE))
    terminal = str((terminal_mcp or {}).get("engine_guidance") or "").strip()
    if terminal:
        blocks.append("{}\n{}\n{}".format(
            _TERMINAL_POLICY_OPEN, terminal, _TERMINAL_POLICY_CLOSE))
    screen = str((vnc_mcp or {}).get("engine_guidance") or "").strip()
    if screen:
        blocks.append("{}\n{}\n{}".format(
            _VNC_POLICY_OPEN, screen, _VNC_POLICY_CLOSE))
    spawn = str((spawn_mcp or {}).get("engine_guidance") or "").strip()
    if spawn:
        blocks.append("{}\n{}\n{}".format(
            _SPAWN_POLICY_OPEN, spawn, _SPAWN_POLICY_CLOSE))
    session_policy = str((session_mcp or {}).get("engine_guidance") or "").strip()
    if session_policy:
        blocks.append("<puppy_session_policy>\n" + session_policy + "\n</puppy_session_policy>")
    if not blocks:
        return prompt
    return "{}\n\n{}".format("\n\n".join(blocks), prompt)


def _codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(service_home(), ".codex")


def _quota_identity():
    """Only the selected ChatGPT account AND user identify a quota owner.

    The CLI's local ID-token claims are identity metadata, not an authentication
    decision. Neither token bytes nor raw account IDs leave this function.
    Keyring/external/API-key logins without this proof stay independent.
    """
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
        return None
    auth = quota.read_object(os.path.join(_codex_home(), "auth.json"))
    if auth.get("auth_mode") != "chatgpt" or auth.get("OPENAI_API_KEY"):
        return None
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        return None
    try:
        encoded = tokens["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        owner = claims["https://api.openai.com/auth"]
        if claims.get("iss") != "https://auth.openai.com" or \
                owner.get("chatgpt_account_id") != tokens.get("account_id"):
            return None
        return quota.account_key("openai-chatgpt", tokens.get("account_id"),
                                 owner.get("chatgpt_user_id"))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _effort_label(value: str) -> str:
    return "X-High" if value == "xhigh" else value.replace("_", " ").title()


def _reasoning_options(levels, value_key: str, description_key: str) -> list:
    options = [{"value": "", "label": "Default",
                "hint": "Codex model default effort"}]
    seen = set()
    for raw in levels or []:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get(value_key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        options.append({
            "value": value, "label": _effort_label(value),
            "hint": str(raw.get(description_key) or
                        _EFFORT_FALLBACKS.get(value, "Codex reasoning effort"))[:180],
        })
    return options


def _service_tier_options(tiers) -> list:
    """Bounded opaque service tiers from the CLI-owned model catalog."""
    options = []
    seen = set()
    for raw in tiers or []:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("id") or "").strip()[:120]
        label = str(raw.get("name") or "").strip()[:120]
        if not value or not label or value in seen:
            continue
        seen.add(value)
        options.append({
            "value": value, "label": label,
            "hint": str(raw.get("description") or "")[:240],
        })
    return options


def _default_model_option(options: list) -> dict:
    preferred = None
    for item in options:
        if item.pop("_default", False) and preferred is None:
            preferred = item
    efforts = preferred.get("effort_options") if preferred else None
    if not isinstance(efforts, list):
        seen = {}
        for model in options:
            for effort in model.get("effort_options") or []:
                if isinstance(effort, dict) and isinstance(effort.get("value"), str):
                    seen.setdefault(effort["value"], dict(effort))
        efforts = list(seen.values())
    default = {"value": "", "label": "Default",
               "hint": "Codex config.toml default model"}
    if efforts:
        default["effort_options"] = [dict(item) for item in efforts]
    # Only the model explicitly marked default can describe an omitted model.
    # Never union tiers across models: Fast may be unavailable on one of them.
    if preferred and isinstance(preferred.get("service_tiers"), list):
        default["service_tiers"] = [dict(item) for item in
                                    preferred["service_tiers"]]
    return default


def parse_model_catalog(models) -> list:
    """Normalize app-server ``model/list`` rows, preserving vendor order."""
    if not isinstance(models, list):
        raise RuntimeError("Codex model/list returned invalid data")
    options = []
    seen = set()
    for raw in models[:_MODEL_MAX_ITEMS]:
        if not isinstance(raw, dict) or raw.get("hidden") is True:
            continue
        value = str(raw.get("model") or raw.get("id") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        options.append({
            "value": value,
            "label": str(raw.get("displayName") or value).strip()[:120],
            "hint": str(raw.get("description") or "")[:240],
            "effort_options": _reasoning_options(
                raw.get("supportedReasoningEfforts"),
                "reasoningEffort", "description"),
            "service_tiers": _service_tier_options(raw.get("serviceTiers")),
            "_default": raw.get("isDefault") is True,
        })
    default = _default_model_option(options)
    return [default] + options


def _cache_file_model_options() -> list:
    """Validated cold-start fallback from the CLI-owned on-disk cache."""
    options = []
    try:
        with open(os.path.join(_codex_home(), "models_cache.json"), encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("models") or []:
            if not isinstance(m, dict) or m.get("visibility") != "list" or not m.get("slug"):
                continue
            options.append({
                "value": str(m["slug"]),
                "label": m.get("display_name") or m["slug"],
                "hint": str(m.get("description") or "")[:240],
                "effort_options": _reasoning_options(
                    m.get("supported_reasoning_levels"), "effort", "description"),
                "service_tiers": _service_tier_options(m.get("service_tiers")),
                "_priority": m.get("priority")
                if isinstance(m.get("priority"), int) else 999,
            })
        options.sort(key=lambda item: item.pop("_priority"))
    except Exception as e:
        log.warning("models_cache.json unreadable: %s", e)
        return []
    if not options:
        return []
    return [_default_model_option(options)] + options


def _static_model_options() -> list:
    levels = [{"effort": key, "description": value}
              for key, value in _EFFORT_FALLBACKS.items()]
    efforts = _reasoning_options(levels, "effort", "description")
    return [
        {"value": "", "label": "Default", "hint": "Codex config.toml default model",
         "effort_options": efforts},
        {"value": "gpt-5.6-sol", "label": "GPT-5.6 Sol",
         "hint": "Flagship coding model", "effort_options": efforts},
        {"value": "gpt-5.5", "label": "GPT-5.5", "hint": "",
         "effort_options": efforts},
        {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "hint": "Fast and cheap",
         "effort_options": efforts},
    ]


_quota_cache = {"ts": 0.0, "quota": None, "account_as_of": 0.0}


def _quota_with_meta(quota, as_of: float, source: str):
    if quota is None:
        return None
    value = dict(quota)
    value["as_of"] = as_of
    value["source"] = source
    return value


def _cache_live_quota(rate_limits) -> None:
    quota = _weekly_from_rl(rate_limits)
    if not quota:
        return
    now = time.time()
    _quota_cache.update(
        ts=now, quota=_quota_with_meta(quota, now, "live"), account_as_of=now)


def _weekly_quota():
    """Weekly usage from the newest rollout's rate_limits (window 10080 min).
    Codex reports these during turns; the newest rollout tail is the freshest
    figure available without spending tokens. 5-min cached."""
    now = time.time()
    if now - _quota_cache["ts"] < 300:
        return _quota_cache["quota"]
    quota = None
    newest_m = 0.0
    try:
        newest = None
        for dirpath, _dirs, files in os.walk(os.path.join(_codex_home(), "sessions")):
            for fn in files:
                if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                    p = os.path.join(dirpath, fn)
                    m = os.path.getmtime(p)
                    if m > newest_m:
                        newest, newest_m = p, m
        if newest:
            with open(newest, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 262144))
                tail = f.read().decode(errors="replace")
            for line in reversed(tail.splitlines()):
                if '"rate_limits"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                rl = (ev.get("payload") or {}).get("rate_limits") or ev.get("rate_limits") or {}
                quota = _weekly_from_rl(rl)
                if quota:
                    quota = _quota_with_meta(quota, newest_m, "rollout")
                    break
    except Exception as e:
        log.warning("weekly quota scan failed: %s", e)
    # Never replace a newer authoritative account read (including a read that
    # reported no weekly window) with an older rollout record.
    if newest_m <= float(_quota_cache.get("account_as_of") or 0.0):
        quota = _quota_cache["quota"]
    elif quota is None and _quota_cache["quota"] is not None:
        quota = _quota_cache["quota"]
    _quota_cache.update(ts=now, quota=quota)
    return _quota_cache["quota"]


def _weekly_from_rl(rl):
    if not isinstance(rl, dict):
        return None
    for win in (rl.get("primary"), rl.get("secondary")):
        if not isinstance(win, dict):
            continue
        duration = win.get("window_minutes")
        if duration is None:
            duration = win.get("windowDurationMins")
        used = win.get("used_percent")
        if used is None:
            used = win.get("usedPercent")
        if duration == 10080 and isinstance(used, (int, float)) and \
                not isinstance(used, bool) and math.isfinite(float(used)):
            return {"weekly_used_percent": float(used),
                    "resets_at": win.get("resets_at") or win.get("resetsAt") or
                    win.get("resets_in_seconds")}
    return None


async def _app_server_response(process, request_id, deadline: float,
                               purpose: str = "app-server request",
                               budget=None) -> dict:
    budget = budget if isinstance(budget, dict) else {"bytes": 0, "messages": 0}
    while budget["messages"] < _APP_SERVER_MESSAGE_LIMIT:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("{} timed out".format(purpose))
        line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
        if not line:
            raise RuntimeError("Codex exited before {} completed".format(purpose))
        budget["messages"] += 1
        budget["bytes"] += len(line)
        if budget["bytes"] > _APP_SERVER_OUTPUT_LIMIT:
            raise RuntimeError("Codex app-server response is too large")
        try:
            value = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("id") != request_id:
            continue
        if value.get("error"):
            error = value["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(message or "{} failed".format(purpose))
        result = value.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("{} returned an invalid response".format(purpose))
        return result
    raise RuntimeError("Codex returned too many unrelated app-server messages")


async def _stop_app_server(process) -> None:
    await driver_base.end_probe(process)


async def _read_account_rate_limits(binary: str, timeout: float = 12.0) -> dict:
    """Read the CLI account snapshot through its local JSONL app server."""
    process = await driver_base.start_probe(
        [binary, "app-server", "--stdio"], writable_stdin=True,
        line_limit=256 * 1024)
    deadline = time.monotonic() + timeout
    budget = {"bytes": 0, "messages": 0}

    async def send(value: dict) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        process.stdin.write(body)
        await process.stdin.drain()

    try:
        await send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "puppy", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        })
        await _app_server_response(
            process, 1, deadline, "account usage initialization", budget)
        await send({"method": "initialized"})
        await send({"id": 2, "method": "account/rateLimits/read", "params": None})
        result = await _app_server_response(
            process, 2, deadline, "account usage request", budget)
        # Newer app-server responses can carry several named limits. Prefer the
        # explicit Codex bucket; rateLimits is the backward-compatible shape in
        # the installed 0.149 contract.
        snapshot = None
        buckets = result.get("rateLimitsByLimitId")
        if isinstance(buckets, dict):
            snapshot = buckets.get("codex")
            if isinstance(snapshot, dict) and "limitId" not in snapshot:
                snapshot = {**snapshot, "limitId": "codex"}
            if not isinstance(snapshot, dict):
                snapshot = next((item for item in buckets.values()
                                 if isinstance(item, dict) and
                                 item.get("limitId") == "codex"), None)
        if not isinstance(snapshot, dict):
            snapshot = result.get("rateLimits")
        if not isinstance(snapshot, dict):
            raise RuntimeError("account usage service did not return rate limits")
        return snapshot
    finally:
        await _stop_app_server(process)


async def _read_model_catalog(binary: str, timeout: float = 14.0) -> list:
    """Read every visible model through the supported app-server protocol."""
    process = await driver_base.start_probe(
        [binary, "app-server", "--stdio"], writable_stdin=True)
    deadline = time.monotonic() + timeout
    budget = {"bytes": 0, "messages": 0}

    async def send(value: dict) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        process.stdin.write(body)
        await process.stdin.drain()

    try:
        await send({
            "id": "puppy-model-init", "method": "initialize",
            "params": {
                "clientInfo": {"name": "puppy", "version": __version__},
                "capabilities": {"experimentalApi": True},
            },
        })
        await _app_server_response(
            process, "puppy-model-init", deadline,
            "model catalog initialization", budget)
        await send({"method": "initialized"})
        cursor = None
        rows = []
        seen_cursors = set()
        for page in range(_MODEL_MAX_PAGES):
            request_id = "puppy-model-page-{}".format(page)
            params = {"limit": _MODEL_PAGE_LIMIT, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            await send({"id": request_id, "method": "model/list", "params": params})
            result = await _app_server_response(
                process, request_id, deadline, "model catalog request", budget)
            data = result.get("data")
            if not isinstance(data, list):
                raise RuntimeError("Codex model/list returned invalid data")
            rows.extend(data)
            if len(rows) > _MODEL_MAX_ITEMS:
                raise RuntimeError("Codex model catalog exceeds {} entries".format(
                    _MODEL_MAX_ITEMS))
            cursor = result.get("nextCursor")
            if cursor is None:
                return parse_model_catalog(rows)
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise RuntimeError("Codex model/list returned an invalid cursor")
            seen_cursors.add(cursor)
        raise RuntimeError("Codex model catalog exceeded {} pages".format(
            _MODEL_MAX_PAGES))
    finally:
        await _stop_app_server(process)


def _clean_cmd(cmd) -> str:
    if isinstance(cmd, list):
        if len(cmd) == 3 and str(cmd[0]).rsplit("/", 1)[-1] in ("bash", "sh", "zsh") \
                and str(cmd[1]) in ("-lc", "-c"):
            return str(cmd[2])
        return " ".join(str(x) for x in cmd)
    s = str(cmd or "").strip()
    m = _SHELL_WRAP.match(s)
    if not m:
        return s
    inner = m.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
        quote = inner[0]
        inner = inner[1:-1]
        if quote == '"':
            inner = re.sub(r'\\([\\"$`])', r"\1", inner)
    return inner


def _text_value(value, limit: int = 20000) -> str:
    """Readable, bounded text for structured tool results."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            text = str(value)
    return text[:limit]


def _completed_tool(tool: str, tool_input, result, item_id: str,
                    is_error: bool = False) -> list:
    """Normalize a completed one-shot CLI item into the shared tool pair."""
    return [
        {"a": "event", "kind": "tool_use",
         "data": {"tool": tool, "input": tool_input, "tool_use_id": item_id}},
        {"a": "event", "kind": "tool_result",
         "data": {"tool_use_id": item_id, "content": _text_value(result),
                  "is_error": bool(is_error)}},
    ]


def _web_search_input(item: dict) -> dict:
    action = item.get("action")
    out = dict(action) if isinstance(action, dict) else {}
    if action is not None and not isinstance(action, dict):
        out["action"] = action
    query = item.get("query")
    if query and not out.get("query") and not out.get("queries") and not out.get("url"):
        out["query"] = query
    return out


def _web_search_result(item: dict) -> str:
    results = item.get("results")
    if isinstance(results, list):
        blocks = []
        for result in results:
            if not isinstance(result, dict):
                blocks.append(_text_value(result, 2000))
                continue
            title = str(result.get("title") or result.get("name") or
                        result.get("ref_id") or "result")
            url = str(result.get("url") or "")
            snippet = str(result.get("snippet") or result.get("text") or "")
            lines = [title]
            if url:
                lines.append(url)
            if snippet:
                lines.append(snippet)
            blocks.append("\n".join(lines))
        if blocks:
            return "\n\n".join(blocks)[:20000]
    elif results is not None:
        return _text_value(results)
    for key in ("result", "output", "error", "message"):
        if item.get(key) is not None:
            return _text_value(item.get(key))
    return "(search completed)"


def _looks_like_tool_item(item: dict) -> bool:
    """Catch new CLI call types without turning lifecycle items into cards."""
    item_type = re.sub(r"[^a-z]", "", str(item.get("type") or "").lower())
    markers = ("tool", "call", "execution", "change", "search", "fetch",
               "browse", "view", "generation", "activity")
    if any(marker in item_type for marker in markers):
        return True
    return any(key in item for key in ("arguments", "input", "command", "path", "url"))


def _generic_tool(item: dict, item_id: str) -> list:
    result_keys = ("result", "results", "aggregatedOutput", "aggregated_output",
                   "output", "error", "message")
    omitted = {"type", "id", "name", "tool", "status", "arguments", "input", *result_keys}
    if "arguments" in item:
        tool_input = item.get("arguments")
    elif "input" in item:
        tool_input = item.get("input")
    else:
        tool_input = {key: value for key, value in item.items() if key not in omitted}
    if tool_input is None:
        tool_input = {}
    result = next((item.get(key) for key in result_keys if item.get(key) is not None), None)
    status = str(item.get("status") or "").lower()
    is_error = status in ("failed", "error") or bool(item.get("error"))
    if result is None:
        result = "({} completed)".format(str(item.get("type") or "tool").replace("_", " "))
    tool = str(item.get("name") or item.get("tool") or item.get("type") or "tool")
    return _completed_tool(tool, tool_input, result, item_id, is_error)


def _item_kind(item: dict) -> str:
    """Compare app-server camelCase item names without version punctuation."""
    return re.sub(r"[^a-z]", "", str(item.get("type") or "").lower())


def _error_text(value, fallback="Codex request failed") -> str:
    if isinstance(value, dict):
        message = value.get("message") or value.get("additionalDetails")
        if message:
            return str(message)[:4000]
        data = value.get("data")
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])[:4000]
    elif value:
        return str(value)[:4000]
    return fallback


_USAGE_FIELDS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
}


def _usage_breakdown(block) -> dict:
    """Normalize one camelCase token breakdown to Puppy's usage vocabulary."""
    if not isinstance(block, dict):
        return {}
    out = {}
    for target, origin in _USAGE_FIELDS.items():
        value = block.get(origin)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[target] = int(value)
    return out


def _accumulate_usage(value, ctx: dict) -> None:
    """Verified against codex-cli 0.152.1: thread/tokenUsage/updated fires once
    per model response, ``last`` being that response and ``total`` the
    thread's running sum. The turn's usage is the sum of ``total`` deltas
    since the turn's first notification (which contributes its own ``last``),
    so a repeated notification adds nothing and a resumed thread's history is
    never counted; the latest response's input plus output is the context in
    use against ``modelContextWindow``."""
    if not isinstance(value, dict):
        return
    last = _usage_breakdown(value.get("last"))
    total = _usage_breakdown(value.get("total"))
    previous = ctx.get("usage_total_prev")
    if previous is None or not total:
        delta = last
    else:
        delta = {key: total[key] - int(previous.get(key, 0)) for key in total}
    usage = dict(ctx.get("usage") or {})
    for key, amount in delta.items():
        usage[key] = int(usage.get(key, 0)) + amount
    ctx["usage"] = usage
    if total:
        ctx["usage_total_prev"] = total
    if last:
        ctx["context_used"] = int(last.get("input_tokens", 0)) + \
            int(last.get("output_tokens", 0))
    window = value.get("modelContextWindow")
    if isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0:
        ctx["context_window"] = int(window)


class CodexDriver(Driver):
    key = "codex"
    label = "Codex"
    binary = "codex"
    uses_stdin_stream = True
    supports_steering = True
    steering_acknowledged = True
    dynamic_model_options = True
    supports_fast_mode = True
    release_source = {"kind": "npm", "package": "@openai/codex"}
    upgrade_source = {"kind": "self", "args": ["update"]}

    def __init__(self):
        self._cache_file_options = None

    def permission_options(self):
        return [
            {"value": "read-only", "label": "Read only", "hint": "Sandbox: no writes, no network"},
            {"value": "workspace-write", "label": "Workspace write", "hint": "Sandbox: writes inside the session dir"},
            {"value": "danger-full-access", "label": "Full access", "hint": "No sandbox - dangerous"},
        ]

    def default_permission(self) -> str:
        return "workspace-write"

    def _fallback_model_options(self):
        if self._cache_file_options is None:
            self._cache_file_options = _cache_file_model_options()
        return self._cache_file_options or _static_model_options()

    def _fallback_model_source(self) -> str:
        self._fallback_model_options()
        return "cache-file" if self._cache_file_options else "static"

    async def _discover_model_options(self, force: bool):
        binary = self.resolved_binary()
        if not binary:
            raise RuntimeError("Codex binary not found")
        return driver_base.ModelCatalogResult(
            await _read_model_catalog(binary), source="engine")

    def effort_options(self):
        seen = {"": {"value": "", "label": "Default",
                     "hint": "Codex config.toml default effort"}}
        for model in self.model_options():
            for option in model.get("effort_options") or []:
                if isinstance(option, dict) and isinstance(option.get("value"), str):
                    seen.setdefault(option["value"], dict(option))
        if len(seen) == 1:
            for value, hint in _EFFORT_FALLBACKS.items():
                seen[value] = {"value": value, "label": _effort_label(value), "hint": hint}
        order = ["", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        return sorted(seen.values(), key=lambda item: (
            order.index(item["value"]) if item["value"] in order else len(order),
            item["value"]))

    def _fast_mode_option(self, model: str):
        # A cache file can outlive the binary that wrote it (notably after a
        # downgrade).  Only a successful exchange with the currently running
        # app-server proves that its turn protocol and catalog agree about
        # service tiers.  A previous live success remains last-known-good
        # across transient refresh failures, as the shared catalog owns that
        # policy.
        state = self._model_catalog_state()
        if state.source != "engine":
            return None
        model = str(model or "")
        for option in state.options:
            if str(option.get("value") or "") != model:
                continue
            for tier in option.get("service_tiers") or []:
                if isinstance(tier, dict) and \
                        str(tier.get("label") or "").strip().casefold() == "fast":
                    return tier
            return None
        return None

    def fast_mode_tier(self, model: str) -> str:
        """Resolve Fast exactly as the native client does: by the catalog's
        semantic tier name, then carry its opaque id into this turn only."""
        option = self._fast_mode_option(model)
        return str((option or {}).get("value") or "").strip()

    def fast_mode_hint(self, model: str) -> str:
        option = self._fast_mode_option(model)
        return str((option or {}).get("hint") or "")

    def _service_tiers_available(self) -> bool:
        """Whether this installed CLI's catalog proves the turn field exists.

        Older app-server builds know nothing about service tiers. Omitting the
        additive field there preserves their turns; a non-empty advertised
        tier is the forward-compatible feature probe for explicit default/off.
        """
        state = self._model_catalog_state()
        return state.source == "engine" and \
            any(option.get("service_tiers") for option in state.options)

    def _extra_status(self):
        return {"quota": _weekly_quota(),
                "usage_monitor": self._quota_monitor().payload(_quota_identity())}

    def _quota_monitor(self):
        if not hasattr(self, "_account_quota"):
            self._account_quota = quota.Monitor("openai-chatgpt", "codex")
        return self._account_quota

    def _observe_quota(self, limits, identity, observed_at=None):
        # A weekly duration alone does not identify a bucket (e.g. a separate
        # model allowance can also be weekly). Only explicit Codex limits join.
        if not isinstance(limits, dict) or limits.get("limitId", limits.get("limit_id")) != "codex":
            return
        week = _weekly_from_rl(limits)
        if week:
            self._quota_monitor().observe(_quota_identity(), identity,
                quota.weekly_sample(week["weekly_used_percent"],
                                    week["resets_at"], observed_at))

    async def refresh_usage(self):
        if not shutil.which(self.binary):
            return None
        identity = _quota_identity()
        observed_at = time.time()
        snapshot = await _read_account_rate_limits(self.binary)
        now = time.time()
        self._observe_quota(snapshot, identity, observed_at)
        quota = _weekly_from_rl(snapshot)
        _quota_cache.update(
            ts=now, quota=_quota_with_meta(quota, now, "account"),
            account_as_of=now)
        return True

    def tool_options(self):
        return [
            {"value": "compact", "label": "Compact context",
             "hint": "Summarize the conversation so far into a shorter context"},
            {"value": "undo", "label": "Undo last turn",
             "hint": "Revert the engine's context to before your last prompt; "
                     "Puppy's transcript and files stay unchanged"},
        ]

    def tool_plan(self, tool, session, turns):
        """Verified against codex-cli 0.152.1: thread/compact/start runs the
        compaction as its own turn (a contextCompaction item, then
        turn/completed); thread/revert {threadId, beforeTurnId} drops that
        turn and everything after it from the thread and answers with the
        thread, then notifies thread/reverted. thread/rollback is deprecated
        and refused for paginated threads. Neither touches files."""
        if tool == "compact":
            return {"run": True, "params": {}}
        if tool != "undo":
            raise ToolUnavailable("{} does not support {}".format(self.label, tool))
        last = turns[0] if turns else None
        if last is None:
            raise ToolUnavailable("Nothing to undo yet - this session has not run a turn")
        if last.get("kind") != "prompt":
            raise ToolUnavailable(
                "The last thing that ran was a {}, which cannot be undone".format(
                    "compaction" if last.get("tool") == "compact" else "session tool"))
        result = last.get("result") or {}
        if not result:
            raise ToolUnavailable("The last turn did not finish, so it cannot be undone")
        if not result.get("native_turn_id") or \
                str(result.get("native_session_id") or "") != \
                str(session.get("native_session_id") or ""):
            raise ToolUnavailable(
                "The last turn predates undo support here and cannot be undone")
        return {"run": True,
                "params": {"before_turn_id": str(result["native_turn_id"])},
                "restore_text": str(last.get("text") or "")}

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt="", terminal_mcp=None, vnc_mcp=None,
                  spawn_mcp=None, session_mcp=None, tool=None):
        argv = [self.binary, "app-server", "--stdio"]
        # a tool turn only addresses the existing thread: no agent bridges
        bridges = () if tool_name(tool) else (browser_mcp, terminal_mcp, vnc_mcp,
                                              spawn_mcp, session_mcp)
        for mcp in (item for item in bridges if item):
            prefix = "mcp_servers." + mcp["name"]
            argv += ["-c", prefix + ".command=" + json.dumps(mcp["command"]),
                     "-c", prefix + ".args=" + json.dumps(
                         list(mcp.get("args") or []), separators=(",", ":")),
                     "-c", prefix + ".enabled=true",
                     "-c", prefix + ".startup_timeout_sec=10",
                     "-c", prefix + ".tool_timeout_sec=70"]
            for key, value in sorted((mcp.get("env") or {}).items()):
                argv += ["-c", "{}.env.{}={}".format(
                    prefix, key, json.dumps(str(value)))]
        return argv

    def turn_context(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                     system_prompt="", terminal_mcp=None, vnc_mcp=None,
                     spawn_mcp=None, session_mcp=None, tool=None):
        return {
            "quota_identity": _quota_identity(),
            "tool": tool_name(tool),
            "tool_params": dict(tool) if isinstance(tool, dict) else {},
            "phase": "initialize",
            # the runner builds this context immediately before spawning the
            # process, so it dates the turn itself for the retry grace
            "started_at": time.monotonic(),
            "first_turn": bool(first_turn),
            "native_session_id": str(session.get("native_session_id") or ""),
            "thread_id": "",
            "turn_id": "",
            "client_message_id": str(pinned_id),
            "cwd": str(session["cwd"]),
            "model": str(session.get("model") or "").strip(),
            "effort": str(session.get("effort") or "").strip(),
            # "default" is the protocol's explicit off state. An enabled
            # session resolves the opaque tier from the current model catalog;
            # no vendor id is persisted or hardcoded here.
            "service_tier": (
                self.fast_mode_tier(session.get("model") or "")
                if session.get("fast_mode") else
                "default" if self._service_tiers_available() else None),
            "sandbox": str(session.get("permission_mode") or
                           self.default_permission()),
            "prompt": _with_runtime_guidance(
                prompt, system_prompt, browser_mcp, terminal_mcp, vnc_mcp,
                spawn_mcp, session_mcp),
            "usage": {},
            "items": {},
            "item_seq": 0,
            "last_error": "",
            "completed": False,
        }

    def initial_stdin(self, session, prompt, tool=None):
        return [_rpc(_ID_INITIALIZE, "initialize", {
            "clientInfo": {
                "name": "puppy", "title": "Puppy", "version": __version__,
            },
            "capabilities": {"experimentalApi": True},
        })]

    def steer_payload(self, session, ctx, text, request_id):
        if not self.steer_ready(session, ctx):
            return None
        ctx = ctx if isinstance(ctx, dict) else {}
        thread_id = str(ctx.get("thread_id") or "")
        turn_id = str(ctx.get("turn_id") or "")
        return _rpc(_ID_STEER_PREFIX + request_id, "turn/steer", {
            "threadId": thread_id,
            "clientUserMessageId": request_id,
            "input": [{"type": "text", "text": text}],
            "expectedTurnId": turn_id,
        })

    def steer_ready(self, session, ctx):
        ctx = ctx if isinstance(ctx, dict) else {}
        return ctx.get("phase") == "running" and not ctx.get("completed") and \
            not ctx.get("tool") and \
            bool(ctx.get("thread_id")) and bool(ctx.get("turn_id"))

    @staticmethod
    def _thread_request(ctx: dict) -> dict:
        params = {
            "cwd": ctx["cwd"],
            # codex exec was deliberately non-interactive. Keep the same
            # contract instead of silently adopting a user's TUI approval
            # default merely because app-server can request approvals.
            "approvalPolicy": "never",
            "sandbox": ctx["sandbox"],
        }
        if ctx.get("model"):
            params["model"] = ctx["model"]
        if ctx.get("first_turn"):
            method = "thread/start"
        else:
            method = "thread/resume"
            params["threadId"] = ctx["native_session_id"]
            # Puppy already owns the rendered transcript. Hydrating an entire
            # native history into one JSONL response wastes memory and can
            # exceed the subprocess stream limit on an old conversation.
            params["excludeTurns"] = True
        return _rpc(_ID_THREAD, method, params)

    @staticmethod
    def _turn_request(ctx: dict) -> dict:
        params = {
            "threadId": ctx["thread_id"],
            "clientUserMessageId": ctx["client_message_id"],
            "input": [{"type": "text", "text": ctx["prompt"]}],
        }
        if ctx.get("model"):
            params["model"] = ctx["model"]
        if ctx.get("effort"):
            params["effort"] = ctx["effort"]
        if ctx.get("service_tier") is not None:
            params["serviceTierForTurn"] = ctx.get("service_tier") or "default"
        return _rpc(_ID_TURN, "turn/start", params)

    @staticmethod
    def _protocol_failure(message: str) -> list:
        text = str(message or "Codex app-server protocol error")[:4000]
        return [
            {"a": "event", "kind": "error", "data": {"text": text}},
            {"a": "result", "data": {"ok": False, "error": text,
                                       "stop_reason": "error"}},
        ]

    @staticmethod
    def _starting(ctx: dict) -> bool:
        """Is this turn still inside its start-up window?

        A context without the stamp (an older resumed turn, a caller that
        builds its own) is simply not in the window: the engine's own wording
        is the safe answer.
        """
        started = ctx.get("started_at") if isinstance(ctx, dict) else None
        if not isinstance(started, (int, float)) or isinstance(started, bool):
            return False
        return (time.monotonic() - float(started)) < START_GRACE_SECONDS

    @staticmethod
    def _active_params(params: dict, ctx: dict, require_turn=False) -> bool:
        thread_id = str(params.get("threadId") or "")
        if thread_id and ctx.get("thread_id") and thread_id != ctx["thread_id"]:
            return False
        turn_id = str(params.get("turnId") or "")
        if require_turn and ctx.get("turn_id") and turn_id and \
                turn_id != ctx["turn_id"]:
            return False
        return True

    def _response(self, ev: dict, ctx: dict) -> list:
        request_id = ev.get("id")
        if isinstance(request_id, str) and request_id.startswith(_ID_STEER_PREFIX):
            steer_id = request_id[len(_ID_STEER_PREFIX):]
            if ev.get("error"):
                return [{"a": "steer_result", "request_id": steer_id,
                         "ok": False, "error": _error_text(
                             ev.get("error"), "Codex rejected steering")}]
            result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
            accepted_turn = str(result.get("turnId") or "")
            expected_turn = str(ctx.get("turn_id") or "")
            if not accepted_turn or accepted_turn != expected_turn:
                return [{"a": "steer_result", "request_id": steer_id,
                         "ok": False,
                         "error": "Codex acknowledged steering for an unexpected turn"}]
            return [{"a": "steer_result", "request_id": steer_id,
                     "ok": True, "error": ""}]
        if request_id not in (_ID_INITIALIZE, _ID_THREAD, _ID_TURN,
                              _ID_INTERRUPT, _ID_TOOL):
            return []
        if ev.get("error"):
            # Completion can win the race with a user interrupt. In that case
            # app-server may reject turn/interrupt because the turn is already
            # terminal; the authoritative turn/completed notification still
            # decides the outcome, and the runner's signal fallback remains
            # armed if no completion follows.
            if request_id == _ID_INTERRUPT:
                return []
            return self._protocol_failure(_error_text(
                ev.get("error"), "Codex app-server request failed"))

        result = ev.get("result")
        if request_id == _ID_INITIALIZE:
            if not isinstance(result, dict):
                return self._protocol_failure(
                    "Codex app-server returned an invalid initialize response")
            ctx["phase"] = "thread"
            return [
                {"a": "stdin", "data": _notification("initialized")},
                {"a": "stdin", "data": self._thread_request(ctx)},
            ]

        if request_id == _ID_THREAD:
            result = result if isinstance(result, dict) else {}
            thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
            thread_id = str(thread.get("id") or "")
            if not thread_id:
                return self._protocol_failure(
                    "Codex app-server did not return a thread id")
            ctx["thread_id"] = thread_id
            ctx["phase"] = "turn"
            actions = [{"a": "native_id", "id": thread_id}]
            model = str(result.get("model") or "")
            if model:
                actions.append({"a": "model", "model": model})
            if ctx.get("tool") == "compact":
                actions.append({"a": "stdin", "data": _rpc(
                    _ID_TOOL, "thread/compact/start", {"threadId": thread_id})})
                actions.append({"a": "transient", "msg": {
                    "type": "status", "text": "Compacting context..."}})
            elif ctx.get("tool") == "undo":
                actions.append({"a": "stdin", "data": _rpc(_ID_TOOL, "thread/revert", {
                    "threadId": thread_id,
                    "beforeTurnId": str((ctx.get("tool_params") or {}).get(
                        "before_turn_id") or ""),
                })})
            else:
                actions.append({"a": "stdin", "data": self._turn_request(ctx)})
            return actions

        if request_id == _ID_TOOL:
            if ctx.get("tool") == "undo":
                # the thread answered already rewritten; thread/reverted and
                # the status churn that follow are not this turn's business
                ctx["completed"] = True
                return [{"a": "result", "data": {
                    "ok": True, "stop_reason": "reverted", "usage": {}}}]
            # compaction runs as its own turn: turn/started names it and
            # turn/completed decides the outcome, like any other turn
            ctx["phase"] = "running"
            return []

        if request_id == _ID_TURN:
            result = result if isinstance(result, dict) else {}
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                return self._protocol_failure(
                    "Codex app-server did not return a turn id")
            ctx["turn_id"] = turn_id
            ctx["phase"] = "running"
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Thinking..."}}]

        # turn/interrupt acknowledgement; turn/completed is authoritative.
        return []

    @staticmethod
    def _remember_item(item: dict, ctx: dict) -> dict:
        item_id = str(item.get("id") or "")
        if not item_id:
            return item
        previous = ctx.setdefault("items", {}).get(item_id)
        merged = dict(previous) if isinstance(previous, dict) else {}
        merged.update(item)
        ctx["items"][item_id] = merged
        return merged

    def _item_started(self, item: dict, ctx: dict) -> list:
        item = self._remember_item(item, ctx)
        kind = _item_kind(item)
        if kind == "commandexecution":
            return [{"a": "transient", "msg": {"type": "status",
                "text": "$ {}".format(_clean_cmd(item.get("command"))[:120])}}]
        if kind == "agentmessage":
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Writing..."}}]
        if kind == "reasoning":
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Thinking..."}}]
        if kind == "filechange":
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Applying changes..."}}]
        if kind == "contextcompaction":
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Compacting context..."}}]
        if kind == "mcptoolcall":
            tool = ".".join(str(value) for value in
                            (item.get("server"), item.get("tool")) if value)
            return [{"a": "transient", "msg": {"type": "status",
                "text": "Using {}...".format(tool or "MCP tool")}}]
        if kind == "websearch":
            query = item.get("query") or ""
            return [{"a": "transient", "msg": {"type": "status",
                "text": "Web search{}".format(
                    ": " + str(query)[:100] if query else "...")}}]
        return []

    def _item_completed(self, item: dict, ctx: dict) -> list:
        item = self._remember_item(item, ctx)
        kind = _item_kind(item)
        iid = str(item.get("id") or "")
        if not iid:
            ctx["item_seq"] = int(ctx.get("item_seq", 0)) + 1
            iid = "codex-item-{}".format(ctx["item_seq"])

        if kind == "agentmessage":
            return [{"a": "event", "kind": "assistant",
                     "data": {"text": str(item.get("text") or "")}}]
        if kind == "reasoning":
            text = item.get("text")
            if not text:
                values = item.get("summary") or item.get("content") or []
                if isinstance(values, list):
                    text = "\n".join(str(value) for value in values if value)
                else:
                    text = str(values or "")
            return ([{"a": "event", "kind": "thinking",
                      "data": {"text": str(text)}}] if text else [])
        if kind == "commandexecution":
            output = item.get("aggregatedOutput")
            if output is None:
                output = item.get("aggregated_output") or item.get("output") or ""
            code = item.get("exitCode")
            if code is None:
                code = item.get("exit_code")
            failed = str(item.get("status") or "").lower() in \
                ("failed", "declined", "error") or code not in (None, 0, "0")
            return _completed_tool(
                "shell", {"command": _clean_cmd(item.get("command"))},
                output, iid, failed)
        if kind == "filechange":
            changes = item.get("changes") or []
            if not isinstance(changes, list):
                changes = [changes]
            summary = "\n".join(
                "{}: {}".format(change.get("kind", "edit"),
                                change.get("path", "?"))
                if isinstance(change, dict) else str(change)
                for change in changes) or "(no changes)"
            failed = str(item.get("status") or "").lower() in \
                ("failed", "declined", "error")
            return _completed_tool(
                "file_change", {"changes": changes}, summary, iid, failed)
        if kind == "mcptoolcall":
            tool = ".".join(str(value) for value in
                            (item.get("server"), item.get("tool")) if value) or "mcp"
            result = item.get("result")
            if result is None:
                result = item.get("error") or item.get("status") or ""
            failed = str(item.get("status") or "").lower() in \
                ("failed", "error") or bool(item.get("error"))
            return _completed_tool(
                tool, item.get("arguments") or {}, result, iid, failed)
        if kind == "websearch":
            return _completed_tool(
                "web_search", _web_search_input(item),
                _web_search_result(item), iid, bool(item.get("error")))
        if kind in ("plan", "todolist"):
            text = str(item.get("text") or "")
            if not text:
                values = item.get("items") or []
                text = "\n".join(
                    ("[x] " if value.get("completed") else "[ ] ") +
                    str(value.get("text") or "")
                    for value in values if isinstance(value, dict))
            return ([
                {"a": "transient", "msg": {"type": "status",
                                             "text": "Plan updated"}},
                {"a": "event", "kind": "info",
                 "data": {"subtype": "todo", "text": text}},
            ] if text else [])
        if kind == "error":
            return [{"a": "event", "kind": "error", "data": {
                "text": _error_text(item, "Codex error")}}]
        if _looks_like_tool_item(item):
            return _generic_tool(item, iid)
        return []

    @staticmethod
    def _request_error(request_id, message: str) -> dict:
        return {"id": request_id, "error": {
            "code": -32601, "message": str(message)[:1000]}}

    def _server_request(self, method: str, request_id, params: dict,
                        ctx: dict) -> list:
        params = params if isinstance(params, dict) else {}
        thread_id = str(params.get("threadId") or
                        params.get("conversationId") or "")
        if thread_id and ctx.get("thread_id") and thread_id != ctx["thread_id"]:
            return [{"a": "stdin", "data": self._request_error(
                request_id, "Puppy cannot approve a child-thread request")}]

        if method in ("item/commandExecution/requestApproval",
                      "execCommandApproval"):
            command = params.get("command")
            if isinstance(command, list):
                command = " ".join(str(value) for value in command)
            suggestions = [{"type": "allowAlways",
                            "label": "Allow for this Codex session"}]
            return [{"a": "approval", "req": {
                "request_id": str(request_id), "_rpc_id": request_id,
                "_method": method, "_params": params,
                "tool_name": "shell", "display_name": "Shell command",
                "description": str(params.get("reason") or ""),
                "input": {"command": str(command or ""),
                          "cwd": str(params.get("cwd") or "")},
                "tool_use_id": str(params.get("itemId") or
                                   params.get("callId") or ""),
                "suggestions": suggestions,
            }}]

        if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            file_changes = params.get("fileChanges")
            if not isinstance(file_changes, dict):
                remembered = ctx.get("items", {}).get(str(params.get("itemId") or ""))
                file_changes = ((remembered or {}).get("changes")
                                if isinstance(remembered, dict) else None)
            input_value = {"file_changes": file_changes or []}
            if params.get("grantRoot"):
                input_value["file_path"] = params["grantRoot"]
            return [{"a": "approval", "req": {
                "request_id": str(request_id), "_rpc_id": request_id,
                "_method": method, "_params": params,
                "tool_name": "file_change", "display_name": "File changes",
                "description": str(params.get("reason") or ""),
                "input": input_value,
                "tool_use_id": str(params.get("itemId") or
                                   params.get("callId") or ""),
                "suggestions": [{"type": "allowAlways",
                                 "label": "Allow for this Codex session"}],
            }}]

        if method == "item/permissions/requestApproval":
            return [{"a": "approval", "req": {
                "request_id": str(request_id), "_rpc_id": request_id,
                "_method": method, "_params": params,
                "tool_name": "permissions", "display_name": "Additional permissions",
                "description": str(params.get("reason") or ""),
                "input": params.get("permissions") or {},
                "tool_use_id": str(params.get("itemId") or ""),
                "suggestions": [{"type": "allowAlways",
                                 "label": "Allow for this Codex session"}],
            }}]

        if method == "mcpServer/elicitation/request":
            # Puppy's approval card cannot safely collect arbitrary typed MCP
            # form values. Explicit cancellation is preferable to a request
            # that silently blocks the whole turn forever.
            return [{"a": "stdin", "data": {
                "id": request_id, "result": {"action": "cancel"}}}]

        return [{"a": "stdin", "data": self._request_error(
            request_id, "Puppy does not support app-server request {}".format(method))}]

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except Exception:
            return []
        if not isinstance(ev, dict):
            return []
        method = ev.get("method")
        if not method:
            return self._response(ev, ctx) if "id" in ev else []

        params = ev.get("params") if isinstance(ev.get("params"), dict) else {}
        if "id" in ev:
            return self._server_request(str(method), ev.get("id"), params, ctx)

        if method == "thread/started":
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            thread_id = str(thread.get("id") or "")
            if not thread_id or (ctx.get("thread_id") and
                                 thread_id != ctx["thread_id"]):
                return []
            ctx["thread_id"] = thread_id
            return [{"a": "native_id", "id": thread_id}]

        if method == "turn/started":
            if not self._active_params(params, ctx):
                return []
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or params.get("turnId") or "")
            if turn_id:
                ctx["turn_id"] = turn_id
            ctx["phase"] = "running"
            return [{"a": "transient", "msg": {
                "type": "status", "text": "Thinking..."}}]

        if method in ("item/started", "item/completed"):
            if not self._active_params(params, ctx, require_turn=True):
                return []
            if not ctx.get("turn_id") and params.get("turnId"):
                ctx["turn_id"] = str(params["turnId"])
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            return (self._item_started(item, ctx) if method == "item/started"
                    else self._item_completed(item, ctx))

        if method == "thread/tokenUsage/updated":
            if not ctx.get("turn_id") or \
                    str(params.get("turnId") or "") != ctx["turn_id"]:
                return []
            _accumulate_usage(params.get("tokenUsage"), ctx)
            return []

        if method == "account/rateLimits/updated":
            limits = params.get("rateLimits")
            if isinstance(limits, dict):
                self._observe_quota(limits, ctx.get("quota_identity"))
                _cache_live_quota(limits)
                return [{"a": "rate_limit", "info": limits}]
            return []

        if method == "model/rerouted":
            if not self._active_params(params, ctx, require_turn=True):
                return []
            model = str(params.get("toModel") or "")
            return [{"a": "model", "model": model}] if model else []

        if method == "error":
            if not self._active_params(params, ctx, require_turn=True):
                return []
            text = _error_text(params.get("error"), "Codex turn error")
            if params.get("willRetry"):
                if self._starting(ctx):
                    return [{"a": "transient", "msg": {
                        "type": "status", "text": _STARTING_STATUS}}]
                return [{"a": "transient", "msg": {
                    "type": "status", "text": "Retrying: {}".format(text[:160])}}]
            ctx["last_error"] = text
            return [{"a": "event", "kind": "error", "data": {"text": text}}]

        if method == "turn/completed":
            if not self._active_params(params, ctx, require_turn=True) or \
                    ctx.get("completed"):
                return []
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or params.get("turnId") or "")
            if ctx.get("turn_id") and turn_id and turn_id != ctx["turn_id"]:
                return []
            ctx["completed"] = True
            status = str(turn.get("status") or "").lower()
            duration = turn.get("durationMs")
            # what a later undo reverts to: this turn's id on the thread it
            # ran in (a tool turn records nothing - it is not undoable)
            identity = {
                "native_session_id": str(ctx.get("thread_id") or ""),
                "native_turn_id": "" if ctx.get("tool") else turn_id,
            }
            if ctx.get("context_used") is not None:
                identity["context_used"] = int(ctx["context_used"])
                if ctx.get("context_window"):
                    identity["context_window"] = int(ctx["context_window"])
            if status == "completed":
                return [{"a": "result", "data": {
                    **identity,
                    "ok": True, "usage": dict(ctx.get("usage") or {}),
                    "engine_duration_ms": duration, "stop_reason": "completed",
                }}]
            error = _error_text(turn.get("error"),
                                "Codex turn {}".format(status or "failed"))
            result = {"a": "result", "data": {
                **identity,
                "ok": False, "error": error,
                "usage": dict(ctx.get("usage") or {}),
                "engine_duration_ms": duration,
                "stop_reason": "cancelled" if status == "interrupted" else
                               (status or "error"),
            }}
            if error == ctx.get("last_error") or status == "interrupted":
                return [result]
            return [{"a": "event", "kind": "error", "data": {"text": error}},
                    result]

        # Delta/progress/config notifications are intentionally not persisted;
        # their completed item or final turn notification is authoritative.
        return []

    @staticmethod
    def _approval_decision(request: dict, behavior: str,
                           updated_permissions=None, message="", cancel=False) -> dict:
        request_id = request.get("_rpc_id", request.get("request_id"))
        method = str(request.get("_method") or "")
        always = any(isinstance(value, dict) and
                     value.get("type") == "allowAlways"
                     for value in updated_permissions or [])
        allow = behavior == "allow" and not cancel

        if method in ("item/commandExecution/requestApproval",
                      "item/fileChange/requestApproval"):
            decision = ("cancel" if cancel else
                        ("acceptForSession" if allow and always else
                         "accept" if allow else "decline"))
            return {"id": request_id, "result": {"decision": decision}}

        if method in ("execCommandApproval", "applyPatchApproval"):
            if cancel:
                decision = "abort"
            elif allow:
                decision = "approved_for_session" if always else "approved"
            else:
                decision = {"denied": {
                    "rejection": str(message or "Denied by user")[:1000]}}
            return {"id": request_id, "result": {"decision": decision}}

        if method == "item/permissions/requestApproval" and allow:
            params = request.get("_params") or {}
            return {"id": request_id, "result": {
                "permissions": params.get("permissions") or {},
                "scope": "session" if always else "turn",
            }}

        return {"id": request_id, "error": {
            "code": -32600,
            "message": str(message or "Permission request denied by user")[:1000],
        }}

    def approval_payload(self, request_id, behavior, original_input, message="",
                         updated_permissions=None, request=None):
        return self._approval_decision(
            request or {"request_id": request_id}, behavior,
            updated_permissions=updated_permissions, message=message)

    def cancel_approval_payload(self, request: dict):
        return self._approval_decision(
            request or {}, "deny", message="Turn interrupted", cancel=True)

    def interrupt_payload(self, session=None, ctx=None):
        ctx = ctx or {}
        thread_id = str(ctx.get("thread_id") or
                        (session or {}).get("native_session_id") or "")
        turn_id = str(ctx.get("turn_id") or "")
        if not thread_id or not turn_id or ctx.get("completed"):
            return None
        return _rpc(_ID_INTERRUPT, "turn/interrupt", {
            "threadId": thread_id, "turnId": turn_id,
        })

    def auth_touch_paths(self):
        return [os.path.join(_codex_home(), "auth.json")]

    async def _auth_status(self):
        """`codex login status` speaks primarily through its exit code (0 =
        authenticated), which survives wording changes. The phrase check is a
        second lock: "Not logged in" CONTAINS "logged in", so a substring
        match once reported a logged-out codex as ready."""
        rc, out = await self._run_probe([self.binary, "login", "status"])
        text = " ".join(out.split())
        if rc is None:
            return {"auth": "unknown", "detail": text or "login status probe failed"}
        if rc != 0 or re.search(r"not\s+logged\s*in|logged\s*out", text, re.I):
            return {"auth": "missing",
                    "detail": text or "run `codex login` as this user"}
        return {"auth": "ok", "detail": text}
