"""Codex CLI driver.

Spawns the official `codex` binary per turn in non-interactive JSONL mode:
  codex exec --json --skip-git-repo-check --color never -C <cwd> -s <sandbox> \
       [-m model] (<prompt> | resume <thread_id> <prompt>)

Event schema (verified against codex-cli 0.149.0):
  thread.started {thread_id}  turn.started  item.started/updated/completed {item}
  turn.completed {usage}      turn.failed {error}
Item types: agent_message, reasoning, command_execution, file_change,
mcp_tool_call, web_search, todo_list, error.
exec mode has no interactive approvals - the sandbox policy is the control.
New completed item types that look call-shaped are preserved as generic tool
pairs so CLI additions do not silently disappear from the transcript.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import time

from puppy.drivers.base import Driver, clean_env

log = logging.getLogger("puppy.drivers.codex")

# codex wraps every command in `/bin/bash -lc "<script>"` (or -c); show the script
_SHELL_WRAP = re.compile(r"^\s*(?:\S*/)?(?:ba|z)?sh\s+-l?c\s+(.*)$", re.S)


def _codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.environ.get("HOME", "/root"), ".codex")


_models_cache = {"ts": 0.0, "models": None}


def _cached_models() -> list:
    """Models the CLI itself knows about (models_cache.json), 5-min cached."""
    now = time.time()
    if _models_cache["models"] is not None and now - _models_cache["ts"] < 300:
        return _models_cache["models"]
    models = []
    try:
        with open(os.path.join(_codex_home(), "models_cache.json"), encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("models") or []:
            if not isinstance(m, dict) or m.get("visibility") != "list" or not m.get("slug"):
                continue
            models.append({
                "slug": m["slug"],
                "label": m.get("display_name") or m["slug"],
                "hint": (m.get("description") or "")[:90],
                "efforts": [(lv.get("effort"), (lv.get("description") or "")[:90])
                            for lv in m.get("supported_reasoning_levels") or []
                            if isinstance(lv, dict) and lv.get("effort")],
                "priority": m.get("priority") if isinstance(m.get("priority"), int) else 999,
            })
        models.sort(key=lambda m: m["priority"])
    except Exception as e:
        log.warning("models_cache.json unreadable: %s", e)
        models = []
    _models_cache.update(ts=now, models=models)
    return models


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


async def _app_server_response(process, request_id: int, deadline: float) -> dict:
    for _ in range(64):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("account usage request timed out")
        line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
        if not line:
            raise RuntimeError("account usage service exited before responding")
        try:
            value = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("id") != request_id:
            continue
        if value.get("error"):
            error = value["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(message or "account usage request failed")
        result = value.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("account usage service returned an invalid response")
        return result
    raise RuntimeError("account usage service returned too many unrelated messages")


async def _stop_app_server(process) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except (BrokenPipeError, ConnectionError):
            pass
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def _read_account_rate_limits(binary: str, timeout: float = 12.0) -> dict:
    """Read the CLI account snapshot through its local JSONL app server."""
    process = await asyncio.create_subprocess_exec(
        binary, "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=256 * 1024,
        env=clean_env(dict(os.environ)))
    deadline = time.monotonic() + timeout

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
        await _app_server_response(process, 1, deadline)
        await send({"method": "initialized"})
        await send({"id": 2, "method": "account/rateLimits/read", "params": None})
        result = await _app_server_response(process, 2, deadline)
        # Newer app-server responses can carry several named limits. Prefer the
        # explicit Codex bucket; rateLimits is the backward-compatible shape in
        # the installed 0.149 contract.
        snapshot = None
        buckets = result.get("rateLimitsByLimitId")
        if isinstance(buckets, dict):
            snapshot = buckets.get("codex")
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
    item_type = str(item.get("type") or "").lower()
    markers = ("tool", "call", "execution", "change", "search", "fetch",
               "browse", "view", "generation", "activity")
    if any(marker in item_type for marker in markers):
        return True
    return any(key in item for key in ("arguments", "input", "command", "path", "url"))


def _generic_tool(item: dict, item_id: str) -> list:
    result_keys = ("result", "results", "aggregated_output", "output", "error", "message")
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


class CodexDriver(Driver):
    key = "codex"
    label = "Codex"
    binary = "codex"
    uses_stdin_stream = False
    release_source = {"kind": "npm", "package": "@openai/codex"}
    upgrade_source = {"kind": "self", "args": ["update"]}

    def permission_options(self):
        return [
            {"value": "read-only", "label": "Read only", "hint": "Sandbox: no writes, no network"},
            {"value": "workspace-write", "label": "Workspace write", "hint": "Sandbox: writes inside the session dir"},
            {"value": "danger-full-access", "label": "Full access", "hint": "No sandbox - dangerous"},
        ]

    def default_permission(self) -> str:
        return "workspace-write"

    def model_options(self):
        # derived from the CLI's own models_cache.json; static fallback if unreadable
        opts = [{"value": "", "label": "Default", "hint": "config.toml default model"}]
        models = _cached_models()
        if models:
            opts += [{"value": m["slug"], "label": m["label"], "hint": m["hint"]} for m in models]
        else:
            opts += [{"value": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "hint": "Flagship coding model"},
                     {"value": "gpt-5.5", "label": "GPT-5.5", "hint": ""},
                     {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "hint": "Fast and cheap"}]
        return opts

    def effort_options(self):
        # union of supported_reasoning_levels across cached models (canonical order)
        opts = [{"value": "", "label": "Default", "hint": "config.toml default effort"}]
        seen = {}
        for m in _cached_models():
            for effort, desc in m["efforts"]:
                if effort and effort not in seen:
                    seen[effort] = desc
        if not seen:
            seen = {"low": "Fastest, minimal reasoning", "medium": "Balanced", "high": "More reasoning",
                    "xhigh": "Extensive reasoning", "max": "Maximum reasoning"}
        order = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        for lv in sorted(seen, key=lambda x: order.index(x) if x in order else 99):
            opts.append({"value": lv, "label": lv.capitalize() if lv != "xhigh" else "X-High",
                         "hint": seen[lv]})
        return opts

    def _extra_status(self):
        return {"quota": _weekly_quota()}

    async def refresh_usage(self):
        if not shutil.which(self.binary):
            return None
        snapshot = await _read_account_rate_limits(self.binary)
        now = time.time()
        quota = _weekly_from_rl(snapshot)
        _quota_cache.update(
            ts=now, quota=_quota_with_meta(quota, now, "account"),
            account_as_of=now)
        return True

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None):
        argv = [self.binary, "exec", "--json", "--skip-git-repo-check", "--color", "never",
                "-C", session["cwd"],
                "-s", session.get("permission_mode") or self.default_permission()]
        model = (session.get("model") or "").strip()
        if model:
            argv += ["-m", model]
        effort = (session.get("effort") or "").strip()
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        if browser_mcp:
            prefix = "mcp_servers." + browser_mcp["name"]
            argv += ["-c", prefix + ".command=" + json.dumps(browser_mcp["command"]),
                     "-c", prefix + ".args=" + json.dumps(
                         list(browser_mcp.get("args") or []), separators=(",", ":")),
                     "-c", prefix + ".enabled=true",
                     "-c", prefix + ".startup_timeout_sec=10",
                     "-c", prefix + ".tool_timeout_sec=70"]
            for key, value in sorted((browser_mcp.get("env") or {}).items()):
                argv += ["-c", "{}.env.{}={}".format(
                    prefix, key, json.dumps(str(value)))]
        native = session.get("native_session_id") or ""
        if first_turn or not native:
            argv += [prompt]
        else:
            argv += ["resume", native, prompt]
        return argv

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except Exception:
            return []
        if not isinstance(ev, dict):
            return []
        t = ev.get("type")

        if t == "thread.started":
            return [{"a": "native_id", "id": ev.get("thread_id", "")}]

        if t == "turn.started":
            return [{"a": "transient", "msg": {"type": "status", "text": "thinking..."}}]

        if t in ("item.started", "item.updated"):
            item = ev.get("item") or {}
            if not isinstance(item, dict):
                return []
            it = item.get("type", "")
            if it == "command_execution":
                return [{"a": "transient", "msg": {"type": "status",
                                                   "text": f"$ {_clean_cmd(item.get('command'))[:120]}"}}]
            if it == "agent_message" and item.get("text"):
                return [{"a": "transient", "msg": {"type": "status", "text": "writing..."}}]
            if it == "web_search":
                search_input = _web_search_input(item)
                query = search_input.get("query") or search_input.get("url") or ""
                if not query and isinstance(search_input.get("queries"), list):
                    query = next((value for value in search_input["queries"] if value), "")
                text = "web search" + (": " + str(query)[:100] if query else "...")
                return [{"a": "transient", "msg": {"type": "status", "text": text}}]
            return []

        if t == "item.completed":
            item = ev.get("item") or {}
            if not isinstance(item, dict):
                return []
            it = item.get("type", "")
            iid = str(item.get("id") or "")
            if not iid:
                ctx["item_seq"] = int(ctx.get("item_seq", 0)) + 1
                iid = "codex-item-{}".format(ctx["item_seq"])
            if it == "agent_message":
                return [{"a": "event", "kind": "assistant", "data": {"text": item.get("text", "")}}]
            if it == "reasoning":
                return [{"a": "event", "kind": "thinking", "data": {"text": item.get("text", "")}}]
            if it == "command_execution":
                out = item.get("aggregated_output") or item.get("output") or ""
                code = item.get("exit_code")
                return _completed_tool(
                    "shell", {"command": _clean_cmd(item.get("command"))}, out, iid,
                    code not in (None, 0, "0"))
            if it == "file_change":
                changes = item.get("changes") or []
                if not isinstance(changes, list):
                    changes = [changes]
                summary = "\n".join(
                    "{}: {}".format(c.get("kind", "edit"), c.get("path", "?"))
                    if isinstance(c, dict) else str(c) for c in changes) or "(no changes)"
                return _completed_tool("file_change", {"changes": changes}, summary, iid)
            if it == "mcp_tool_call":
                tool = ".".join(str(x) for x in
                                (item.get("server", ""), item.get("tool", "")) if x) or "mcp"
                result = item.get("result") if item.get("result") is not None else item.get("status", "")
                return _completed_tool(tool, item.get("arguments") or {}, result, iid,
                                       item.get("status") == "failed")
            if it == "web_search":
                status = str(item.get("status") or "").lower()
                return _completed_tool(
                    "web_search", _web_search_input(item), _web_search_result(item), iid,
                    status in ("failed", "error") or bool(item.get("error")))
            if it == "todo_list":
                items = item.get("items") or []
                txt = "\n".join(("[x] " if x.get("completed") else "[ ] ") +
                                str(x.get("text", ""))
                                for x in items if isinstance(x, dict))
                return [{"a": "transient", "msg": {"type": "status", "text": "plan updated"}},
                        {"a": "event", "kind": "info", "data": {"subtype": "todo", "text": txt}}] if txt else []
            if it == "error":
                return [{"a": "event", "kind": "error", "data": {"text": item.get("message", "codex error")}}]
            if _looks_like_tool_item(item):
                return _generic_tool(item, iid)
            return []

        if t == "token_count":
            # not observed in exec --json yet, but codex records these in rollouts;
            # capture live if the stream ever carries them
            rl = ev.get("rate_limits") or (ev.get("info") or {}).get("rate_limits")
            if rl:
                _cache_live_quota(rl)
                return [{"a": "rate_limit", "info": rl}]
            return []

        if t == "turn.completed":
            usage = ev.get("usage") or {}
            return [{"a": "result", "data": {
                "ok": True,
                "usage": {k: usage.get(k) for k in
                          ("input_tokens", "output_tokens", "cached_input_tokens",
                           "reasoning_output_tokens") if usage.get(k) is not None},
            }}]

        if t == "turn.failed":
            err = (ev.get("error") or {}).get("message", "turn failed")
            return [{"a": "event", "kind": "error", "data": {"text": err}},
                    {"a": "result", "data": {"ok": False, "error": err}}]

        if t == "error":
            return [{"a": "event", "kind": "error", "data": {"text": ev.get("message", "codex error")}}]

        return []

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
