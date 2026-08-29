"""OpenCode driver using its native Agent Client Protocol (ACP) surface.

One ``opencode acp`` subprocess is started per Puppy turn.  Puppy initializes
the JSON-RPC stream, creates or resumes the OpenCode session, selects the
node-configured model/variant, then sends one prompt.  Provider authentication,
native history, project rules, plugins, and credentials remain entirely owned
by OpenCode.

The model catalog is intentionally discovered from ``opencode models
--verbose``. OpenCode can route many providers, so Puppy never ships provider
or model names of its own; every choice reported by the node feeds the same
per-session model control used by the other engines.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from puppy import __version__
from puppy.drivers.base import Driver, clean_env

log = logging.getLogger("puppy.drivers.opencode")

ACP_PROTOCOL = 1
PUPPY_AGENT = "puppy_console"
CATALOG_TTL_SECONDS = 300
CATALOG_TIMEOUT_SECONDS = 12
CATALOG_OUTPUT_LIMIT = 16 * 1024 * 1024

_ID_INITIALIZE = "puppy:initialize"
_ID_SESSION = "puppy:session"
_ID_LOAD = "puppy:load"
_ID_CONFIG = "puppy:config"
_ID_PROMPT = "puppy:prompt"


def _rpc(request_id, method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def _json_error(value) -> str:
    if not isinstance(value, dict):
        return str(value or "OpenCode protocol error")
    message = str(value.get("message") or "OpenCode protocol error")
    data = value.get("data")
    if data not in (None, "", {}):
        try:
            detail = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            detail = str(data)
        message += ": " + detail
    return message[:2000]


def _display_provider(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower() == "opencode":
        return "OpenCode"
    words = [part for part in re.split(r"[-_.\s]+", raw) if part]
    return " ".join(part[:1].upper() + part[1:] for part in words) or "Provider"


def _token_hint(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return ""
    value = int(value)
    if value >= 1000000:
        return "{:.1f}m context".format(value / 1000000.0).replace(".0m", "m")
    if value >= 1000:
        return "{:.0f}k context".format(value / 1000.0)
    return "{} context".format(value)


def _variant_options(model: dict) -> list:
    variants = model.get("variants")
    if isinstance(variants, dict):
        names = [str(key) for key in variants if str(key).strip()]
    elif isinstance(variants, list):
        names = [str(item) for item in variants if isinstance(item, str) and item.strip()]
    else:
        names = []
    order = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
    names = sorted(set(names), key=lambda item: (
        order.index(item.lower()) if item.lower() in order else len(order), item.lower()))
    options = [{"value": "", "label": "Default", "hint": "OpenCode model default"}]
    for name in names:
        label = "X-High" if name.lower() == "xhigh" else name.replace("_", " ").title()
        options.append({"value": name, "label": label, "hint": "OpenCode model variant"})
    return options


def parse_model_catalog(text: str) -> list:
    """Parse ``opencode models --verbose`` without knowing any provider.

    The CLI writes one full model ID followed by a pretty-printed JSON object.
    A small brace/string scanner is used rather than relying on indentation, so
    nested provider metadata and future formatting changes remain harmless.
    """
    lines = str(text or "").splitlines()
    catalog = []
    index = 0
    while index < len(lines):
        model_id = lines[index].strip()
        index += 1
        if not model_id or model_id.startswith(("[", "WARN ", "INFO ", "ERROR ")):
            continue
        while index < len(lines) and not lines[index].lstrip().startswith("{"):
            if lines[index].strip():
                break
            index += 1
        if index >= len(lines) or not lines[index].lstrip().startswith("{"):
            continue
        block = []
        depth = 0
        quoted = False
        escaped = False
        complete = False
        while index < len(lines):
            line = lines[index]
            block.append(line)
            index += 1
            for char in line + "\n":
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                elif char == '"':
                    quoted = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        complete = True
            if complete:
                break
        if not complete:
            break
        try:
            raw = json.loads("\n".join(block))
        except (TypeError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("providerID") or "").strip()
        bare_id = str(raw.get("id") or "").strip()
        full_id = model_id if "/" in model_id else "/".join(
            part for part in (provider, bare_id or model_id) if part)
        if not full_id:
            continue
        if not provider and "/" in full_id:
            provider = full_id.split("/", 1)[0]
        name = str(raw.get("name") or bare_id or full_id).strip()
        limits = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
        hints = [_token_hint(limits.get("context"))]
        capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
        inputs = capabilities.get("input")
        if isinstance(inputs, dict):
            extras = [str(key) for key, enabled in inputs.items()
                      if key != "text" and enabled is True]
            if extras:
                hints.append("{} input".format("/".join(extras)))
        entry = {
            "value": full_id,
            "label": name,
            "hint": " · ".join(part for part in hints if part) or "OpenCode model",
            "provider": provider,
            "provider_label": _display_provider(provider),
            "effort_options": _variant_options(raw),
        }
        catalog.append(entry)
    # The first copy wins if a plugin/provider prints a duplicate identifier.
    unique = {}
    for item in catalog:
        unique.setdefault(item["value"], item)
    return list(unique.values())


async def _read_catalog_process(process) -> bytes:
    """Read a catalog without ever buffering more than the advertised cap."""
    chunks = []
    total = 0
    while True:
        chunk = await process.stdout.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > CATALOG_OUTPUT_LIMIT:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise RuntimeError("model catalog exceeds {} MiB".format(
                CATALOG_OUTPUT_LIMIT // (1024 * 1024)))
        chunks.append(chunk)
    await process.wait()
    return b"".join(chunks)


def _permission_config(mode: str):
    if mode == "acceptEdits":
        return {"edit": "allow", "bash": "ask",
                "external_directory": "ask", "doom_loop": "ask"}
    if mode == "plan":
        # A subagent could otherwise evade the read-only boundary under its own
        # agent policy, so task delegation is blocked alongside writes/shells.
        return {"edit": "deny", "bash": "deny", "task": "deny",
                "external_directory": "ask", "doom_loop": "deny"}
    if mode == "manual":
        return {"*": "ask", "read": "allow", "glob": "allow", "grep": "allow",
                "lsp": "allow", "skill": "allow", "question": "allow"}
    if mode == "bypassPermissions":
        return "allow"
    # Standard mode deliberately respects the installation's own permission
    # policy (including its documented native defaults and project overrides).
    return None


def _content_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for part in (_content_text(item) for item in value) if part)
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "text":
            return str(value.get("text") or "")
        if kind == "diff":
            path = str(value.get("path") or "")
            old = str(value.get("oldText") or "")
            new = str(value.get("newText") or "")
            return "{}\n--- before\n{}\n+++ after\n{}".format(path, old, new).strip()
        if kind == "content":
            return _content_text(value.get("content"))
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return str(value)
    return str(value)


def _config_options(result) -> list:
    if not isinstance(result, dict):
        return []
    options = result.get("configOptions")
    return options if isinstance(options, list) else []


def _option(options: list, option_id: str):
    for item in options:
        if isinstance(item, dict) and item.get("id") == option_id:
            return item
    return None


class OpenCodeDriver(Driver):
    key = "opencode"
    label = "OpenCode"
    binary = "opencode"
    # The official curl installer uses this when no custom/XDG/HOME-bin target
    # wins, then updates interactive shell files. A service manager does not
    # source those files, so check the documented installation directly too.
    binary_fallbacks = ("~/.opencode/bin/opencode",)
    uses_stdin_stream = True
    availability_only = True
    dynamic_model_options = True
    allow_custom_model = False
    resume_requires_same_cwd = True
    release_source = {"kind": "npm", "package": "opencode-ai"}
    upgrade_source = {"kind": "self", "args": ["upgrade"]}

    def __init__(self):
        self._catalog = []
        self._catalog_error = ""
        self._catalog_ts = 0.0
        self._catalog_lock = None

    def permission_options(self):
        return [
            {"value": "auto", "label": "Standard",
             "hint": "Use this OpenCode installation's configured permissions"},
            {"value": "acceptEdits", "label": "Accept edits",
             "hint": "File edits allowed; commands and external paths ask"},
            {"value": "plan", "label": "Plan",
             "hint": "Read-only planning; edits, commands, and subagents denied"},
            {"value": "manual", "label": "Ask risky actions",
             "hint": "Reads stay automatic; other tools require approval"},
            {"value": "bypassPermissions", "label": "Full access",
             "hint": "All OpenCode permissions allowed - dangerous"},
        ]

    def default_permission(self) -> str:
        return "auto"

    async def refresh_model_options(self, force: bool = False) -> None:
        now = time.time()
        if not force and self._catalog_ts and now - self._catalog_ts < CATALOG_TTL_SECONDS:
            return
        binary = self.resolved_binary()
        if not binary:
            self._catalog = []
            self._catalog_error = "OpenCode binary not found"
            self._catalog_ts = now
            return
        if self._catalog_lock is None:
            self._catalog_lock = asyncio.Lock()
        async with self._catalog_lock:
            now = time.time()
            if not force and self._catalog_ts and now - self._catalog_ts < CATALOG_TTL_SECONDS:
                return
            try:
                env = clean_env(dict(os.environ))
                runtime_home = os.path.expanduser("~")
                if runtime_home and runtime_home != "~":
                    env.setdefault("HOME", runtime_home)
                process = await asyncio.create_subprocess_exec(
                    binary, "models", "--verbose", cwd="/", env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                try:
                    output = await asyncio.wait_for(
                        _read_catalog_process(process), timeout=CATALOG_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()
                    raise RuntimeError("model discovery timed out")
                except asyncio.CancelledError:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()
                    raise
                text = output.decode(errors="replace")
                if process.returncode:
                    detail = text.strip().splitlines()[-1] if text.strip() else \
                        "model discovery exited {}".format(process.returncode)
                    raise RuntimeError(detail[:400])
                catalog = parse_model_catalog(text)
                if not catalog:
                    raise RuntimeError("OpenCode reported no models")
                self._catalog = catalog
                self._catalog_error = ""
            except Exception as exc:
                # Keep a previously good catalog through a transient provider
                # or network failure so an established session control does not
                # suddenly lose every model choice.
                self._catalog_error = str(exc)[:400]
                log.warning("OpenCode model discovery failed: %s", exc)
            self._catalog_ts = time.time()

    def model_catalog_error(self) -> str:
        return self._catalog_error

    def model_catalog_loaded(self) -> bool:
        return bool(self._catalog_ts)

    def model_options(self):
        options = [{
            "value": "", "label": "Default", "hint": "OpenCode default model",
            "effort_options": [{"value": "", "label": "Default",
                                "hint": "OpenCode model default"}],
        }]
        for item in self._catalog:
            option = dict(item)
            provider = str(option.get("provider_label") or option.get("provider") or "").strip()
            label = str(option.get("label") or option.get("value") or "").strip()
            if provider:
                option["label"] = "{} · {}".format(provider, label)
            options.append(option)
        return options

    def default_model(self) -> str:
        return ""

    def effort_options_for_model(self, model: str):
        for item in self.model_options():
            if item.get("value") == model:
                options = item.get("effort_options")
                if isinstance(options, list):
                    return options
        return [{"value": "", "label": "Default", "hint": "OpenCode model default"}]

    def effort_options(self):
        seen = {"": {"value": "", "label": "Default", "hint": "OpenCode model default"}}
        order = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        for model in self.model_options():
            for item in model.get("effort_options") or []:
                if isinstance(item, dict) and isinstance(item.get("value"), str):
                    seen.setdefault(item["value"], dict(item))
        return sorted(seen.values(), key=lambda item: (
            -1 if not item["value"] else
            order.index(item["value"].lower()) if item["value"].lower() in order else 99,
            item["value"].lower()))

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt=""):
        return [self.resolved_binary() or self.binary,
                "acp", "--cwd", session["cwd"]]

    def build_env(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt=""):
        guidance = [str(system_prompt or "").strip()]
        if browser_mcp:
            guidance.append(str(browser_mcp.get("engine_guidance") or "").strip())
        agent = {
            "description": "Puppy managed interactive coding session",
            "mode": "primary",
        }
        joined = "\n\n".join(part for part in guidance if part)
        if joined:
            agent["prompt"] = joined
        permissions = _permission_config(
            session.get("permission_mode") or self.default_permission())
        if permissions is not None:
            agent["permission"] = permissions
        inline = {"agent": {PUPPY_AGENT: agent}, "default_agent": PUPPY_AGENT}
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(
            inline, ensure_ascii=False, separators=(",", ":"))}

    @staticmethod
    def _mcp_servers(browser_mcp) -> list:
        if not browser_mcp:
            return []
        environment = []
        for name, value in (browser_mcp.get("env") or {}).items():
            environment.append({"name": str(name), "value": str(value)})
        return [{
            "name": browser_mcp["name"],
            "command": browser_mcp["command"],
            "args": [str(value) for value in browser_mcp.get("args") or []],
            "env": environment,
        }]

    def turn_context(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                     system_prompt=""):
        return {
            "phase": "initialize",
            "first_turn": bool(first_turn),
            "prompt": prompt,
            "cwd": session["cwd"],
            "native_session_id": str(session.get("native_session_id") or ""),
            "model": str(session.get("model") or ""),
            "effort": str(session.get("effort") or ""),
            "mcp_servers": self._mcp_servers(browser_mcp),
            "session_method": "",
            "session_id": "",
            "setup": [],
            "setting": "",
            "config_options": [],
            "stream_block": "",
            "stream_text": "",
            "stream_thinking": "",
            "tools": {},
            "last_plan": "",
            "model_seen": "",
        }

    def initial_stdin(self, session, prompt):
        return [_rpc(_ID_INITIALIZE, "initialize", {
            "protocolVersion": ACP_PROTOCOL,
            "clientInfo": {"name": "puppy", "title": "Puppy", "version": __version__},
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        })]

    @staticmethod
    def _flush_stream(ctx: dict) -> list:
        actions = []
        thinking = ctx.get("stream_thinking") or ""
        text = ctx.get("stream_text") or ""
        # The active block normally makes only one side non-empty. Preserve a
        # deterministic order if an unusual server batches both before a tool.
        if thinking:
            actions.append({"a": "event", "kind": "thinking", "data": {"text": thinking}})
        if text:
            actions.append({"a": "event", "kind": "assistant", "data": {"text": text}})
        ctx["stream_thinking"] = ""
        ctx["stream_text"] = ""
        ctx["stream_block"] = ""
        return actions

    def _append_chunk(self, ctx: dict, block: str, text: str) -> list:
        if not text:
            return []
        actions = []
        active = ctx.get("stream_block") or ""
        if active and active != block:
            actions.extend(self._flush_stream(ctx))
        ctx["stream_block"] = block
        key = "stream_thinking" if block == "thinking" else "stream_text"
        ctx[key] = (ctx.get(key) or "") + text
        actions.append({"a": "transient", "msg": {
            "type": "delta", "block": block, "text": text}})
        return actions

    @staticmethod
    def _tool_name(update: dict) -> str:
        kind = str(update.get("kind") or "").strip()
        if kind and kind not in ("other", "unknown"):
            return kind
        title = str(update.get("title") or "tool").strip()
        return title.split(" ", 1)[0] or "tool"

    def _tool_update(self, update: dict, ctx: dict) -> list:
        actions = self._flush_stream(ctx)
        tool_id = str(update.get("toolCallId") or update.get("id") or "")
        if not tool_id:
            return actions
        tools = ctx.setdefault("tools", {})
        record = tools.setdefault(tool_id, {"used": False, "finished": False})
        tool = record.get("tool") or self._tool_name(update)
        record["tool"] = tool
        raw_input = update.get("rawInput")
        if raw_input is None:
            raw_input = record.get("input") or {}
        else:
            if isinstance(raw_input, dict) and tool in ("bash", "execute") and \
                    not raw_input.get("command") and update.get("title"):
                raw_input = dict(raw_input)
                raw_input["command"] = str(update.get("title"))
            record["input"] = raw_input
        if not record["used"]:
            actions.append({"a": "event", "kind": "tool_use", "data": {
                "tool": tool, "input": raw_input, "tool_use_id": tool_id}})
            record["used"] = True
        status = str(update.get("status") or "").lower()
        title = str(update.get("title") or tool).strip()
        if status in ("pending", "in_progress", "running"):
            actions.append({"a": "transient", "msg": {
                "type": "status", "text": title}})
        if status in ("completed", "failed", "error") and not record["finished"]:
            output = update.get("rawOutput")
            if output is None:
                output = update.get("content")
            content = _content_text(output)[:20000]
            actions.append({"a": "event", "kind": "tool_result", "data": {
                "tool": tool, "tool_use_id": tool_id, "content": content,
                "is_error": status in ("failed", "error")}})
            record["finished"] = True
        return actions

    @staticmethod
    def _usage(value) -> dict:
        if not isinstance(value, dict):
            return {}
        mapping = {
            "inputTokens": "input_tokens", "outputTokens": "output_tokens",
            "totalTokens": "total_tokens", "thoughtTokens": "thought_tokens",
            "cachedReadTokens": "cache_read_input_tokens",
            "cachedWriteTokens": "cache_creation_input_tokens",
        }
        return {target: value[source] for source, target in mapping.items()
                if isinstance(value.get(source), (int, float)) and
                not isinstance(value.get(source), bool)}

    @staticmethod
    def _current_model(options: list) -> str:
        model = _option(options, "model")
        return str((model or {}).get("currentValue") or "")

    def _advance_setup(self, ctx: dict) -> list:
        options = ctx.get("config_options") or []
        while ctx.get("setup"):
            setting = ctx["setup"].pop(0)
            desired = PUPPY_AGENT if setting == "mode" else str(ctx.get(setting) or "")
            if not desired:
                continue
            spec = _option(options, setting)
            if setting == "mode" and spec is None:
                # default_agent still selects the ephemeral agent; very old ACP
                # builds simply do not expose mode as a generic config option.
                continue
            current = str((spec or {}).get("currentValue") or "")
            if current == desired:
                if setting == "model" and current != ctx.get("model_seen"):
                    ctx["model_seen"] = current
                    return [{"a": "model", "model": current}] + self._advance_setup(ctx)
                continue
            ctx["setting"] = setting
            ctx["phase"] = "config"
            return [{"a": "stdin", "data": _rpc(_ID_CONFIG,
                "session/set_config_option", {
                    "sessionId": ctx["session_id"], "configId": setting,
                    "value": desired,
                })}]
        ctx["phase"] = "prompt"
        actions = []
        current = self._current_model(options)
        if current and current != ctx.get("model_seen"):
            ctx["model_seen"] = current
            actions.append({"a": "model", "model": current})
        actions.append({"a": "stdin", "data": _rpc(_ID_PROMPT, "session/prompt", {
            "sessionId": ctx["session_id"],
            "prompt": [{"type": "text", "text": ctx["prompt"]}],
        })})
        return actions

    def _protocol_failure(self, message: str) -> list:
        text = str(message or "OpenCode protocol error")[:2000]
        return [
            {"a": "event", "kind": "error", "data": {"text": text}},
            {"a": "result", "data": {"ok": False, "error": text,
                                       "stop_reason": "error"}},
        ]

    def _session_response(self, ev: dict, ctx: dict) -> list:
        if ev.get("error"):
            if ctx.get("session_method") == "session/resume" and not ctx.get("load_fallback"):
                ctx["load_fallback"] = True
                ctx["session_method"] = "session/load"
                return [{"a": "stdin", "data": _rpc(_ID_LOAD, "session/load", {
                    "sessionId": ctx["native_session_id"], "cwd": ctx["cwd"],
                    "mcpServers": ctx["mcp_servers"],
                })}]
            return self._protocol_failure(_json_error(ev.get("error")))
        result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
        session_id = str(result.get("sessionId") or ctx.get("native_session_id") or "")
        if not session_id:
            return self._protocol_failure("OpenCode did not return a session ID")
        ctx["session_id"] = session_id
        ctx["config_options"] = _config_options(result)
        ctx["setup"] = ["mode", "model", "effort"]
        return [{"a": "native_id", "id": session_id}] + self._advance_setup(ctx)

    def _session_update(self, update: dict, ctx: dict) -> list:
        if ctx.get("phase") != "prompt":
            # session/load may replay native history. Puppy already owns the
            # durable transcript, so pre-prompt updates must never duplicate it.
            return []
        kind = str(update.get("sessionUpdate") or update.get("type") or "")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            content = update.get("content")
            text = _content_text(content)
            return self._append_chunk(
                ctx, "thinking" if kind == "agent_thought_chunk" else "text", text)
        if kind in ("tool_call", "tool_call_update"):
            return self._tool_update(update, ctx)
        if kind == "plan":
            actions = self._flush_stream(ctx)
            entries = update.get("entries") or []
            lines = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                done = str(item.get("status") or "").lower() in ("completed", "done")
                text = str(item.get("content") or item.get("text") or "").strip()
                if text:
                    lines.append("[{}] {}".format("x" if done else " ", text))
            plan = "\n".join(lines)
            if plan and plan != ctx.get("last_plan"):
                ctx["last_plan"] = plan
                actions.extend([
                    {"a": "transient", "msg": {"type": "status", "text": "Plan updated"}},
                    {"a": "event", "kind": "info", "data": {
                        "subtype": "todo", "text": plan}},
                ])
            return actions
        if kind == "usage_update":
            used = update.get("used")
            if isinstance(used, (int, float)) and not isinstance(used, bool):
                return [{"a": "transient", "msg": {
                    "type": "thinking_tokens", "tokens": used}}]
        if kind in ("config_option_update", "current_model_update"):
            options = update.get("configOptions")
            if isinstance(options, list):
                ctx["config_options"] = options
            model = str(update.get("modelId") or self._current_model(
                ctx.get("config_options") or []) or "")
            if model and model != ctx.get("model_seen"):
                ctx["model_seen"] = model
                return [{"a": "model", "model": model}]
        return []

    def parse_line(self, line, ctx):
        try:
            ev = json.loads(line)
        except (TypeError, ValueError):
            return []
        if not isinstance(ev, dict):
            return []

        method = ev.get("method")
        if method == "session/update":
            params = ev.get("params") if isinstance(ev.get("params"), dict) else {}
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            return self._session_update(update, ctx)

        if method == "session/request_permission" and "id" in ev:
            params = ev.get("params") if isinstance(ev.get("params"), dict) else {}
            call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
            options = params.get("options") if isinstance(params.get("options"), list) else []
            suggestions = []
            if any(isinstance(item, dict) and item.get("kind") == "allow_always"
                   for item in options):
                suggestions.append({"type": "allowAlways", "label": "Always allow"})
            raw_input = call.get("rawInput") or {}
            if isinstance(raw_input, dict) and self._tool_name(call) in ("bash", "execute") and \
                    not raw_input.get("command") and call.get("title"):
                raw_input = dict(raw_input)
                raw_input["command"] = str(call.get("title"))
            return [{"a": "approval", "req": {
                "request_id": str(ev.get("id")), "_rpc_id": ev.get("id"),
                "_options": options,
                "tool_name": self._tool_name(call),
                "display_name": str(call.get("title") or self._tool_name(call)),
                "description": str(params.get("description") or ""),
                "input": raw_input,
                "tool_use_id": str(call.get("toolCallId") or ""),
                "suggestions": suggestions,
            }}]

        request_id = ev.get("id")
        if request_id == _ID_INITIALIZE:
            if ev.get("error"):
                return self._protocol_failure(_json_error(ev.get("error")))
            params = {"cwd": ctx["cwd"], "mcpServers": ctx["mcp_servers"]}
            if ctx.get("first_turn") or not ctx.get("native_session_id"):
                ctx["session_method"] = "session/new"
            else:
                ctx["session_method"] = "session/resume"
                params["sessionId"] = ctx["native_session_id"]
            ctx["phase"] = "session"
            return [{"a": "stdin", "data": _rpc(
                _ID_SESSION, ctx["session_method"], params)}]

        if request_id in (_ID_SESSION, _ID_LOAD):
            return self._session_response(ev, ctx)

        if request_id == _ID_CONFIG:
            if ev.get("error"):
                setting = ctx.get("setting") or "configuration"
                return self._protocol_failure("OpenCode rejected {}: {}".format(
                    setting, _json_error(ev.get("error"))))
            result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
            ctx["config_options"] = _config_options(result) or ctx.get("config_options") or []
            actions = []
            if ctx.get("setting") == "model":
                current = self._current_model(ctx["config_options"])
                if current and current != ctx.get("model_seen"):
                    ctx["model_seen"] = current
                    actions.append({"a": "model", "model": current})
            ctx["setting"] = ""
            actions.extend(self._advance_setup(ctx))
            return actions

        if request_id == _ID_PROMPT:
            actions = self._flush_stream(ctx)
            if ev.get("error"):
                actions.extend(self._protocol_failure(_json_error(ev.get("error"))))
                return actions
            result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
            stop = str(result.get("stopReason") or "end_turn")
            ok = stop not in ("refusal", "cancelled", "canceled", "error")
            actions.append({"a": "result", "data": {
                "ok": ok, "stop_reason": stop,
                "usage": self._usage(result.get("usage")),
                "error": "" if ok else "OpenCode stopped: {}".format(stop),
            }})
            ctx["phase"] = "done"
            return actions
        return []

    @staticmethod
    def _permission_option(request: dict, allow: bool, always: bool = False):
        options = request.get("_options") if isinstance(request, dict) else []
        wanted = ("allow_always",) if allow and always else \
            ("allow_once", "allow") if allow else ("reject_once", "reject_always", "reject")
        for kind in wanted:
            for item in options or []:
                if isinstance(item, dict) and item.get("kind") == kind and item.get("optionId"):
                    return item["optionId"]
        fallbacks = ("always",) if allow and always else \
            ("once", "allow") if allow else ("reject", "deny")
        available = {str(item.get("optionId")): item.get("optionId")
                     for item in options or [] if isinstance(item, dict) and item.get("optionId")}
        for fallback in fallbacks:
            if fallback in available:
                return available[fallback]
        return next(iter(available.values()), "once" if allow else "reject")

    def approval_payload(self, request_id, behavior, original_input, message="",
                         updated_permissions=None, request=None):
        request = request or {}
        always = any(isinstance(item, dict) and item.get("type") == "allowAlways"
                     for item in updated_permissions or [])
        option = self._permission_option(request, behavior == "allow", always)
        return {"jsonrpc": "2.0", "id": request.get("_rpc_id", request_id),
                "result": {"outcome": {"outcome": "selected", "optionId": option}}}

    def cancel_approval_payload(self, request: dict):
        option = self._permission_option(request, False)
        return {"jsonrpc": "2.0", "id": request.get("_rpc_id", request.get("request_id")),
                "result": {"outcome": {"outcome": "selected", "optionId": option}}}

    def interrupt_payload(self, session=None):
        native = str((session or {}).get("native_session_id") or "")
        if not native:
            return None
        return _notification("session/cancel", {"sessionId": native})
