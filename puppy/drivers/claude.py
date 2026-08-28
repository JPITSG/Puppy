"""Claude Code driver.

Spawns the official `claude` binary per turn in headless stream-json mode:
  claude -p --output-format stream-json --input-format stream-json \
         --include-partial-messages --verbose \
         --permission-mode <mode> --permission-prompt-tool stdio \
         (--session-id <uuid> | --resume <uuid>) [--model <m>]

Interactive permission prompts arrive as control_request/can_use_tool on stdout
and are answered with control_response on stdin (verified against claude 2.1.219).
Compliance note: we only drive the unmodified official binary; auth stays inside
the CLI's own login (subscription OAuth), which is the vendor-sanctioned path.
"""
from __future__ import annotations

import json
import os

from puppy.drivers.base import Driver, stringify_content


class ClaudeDriver(Driver):
    key = "claude"
    label = "Claude Code"
    binary = "claude"
    uses_stdin_stream = True
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

    def model_options(self):
        # aliases resolved by the CLI to the latest model of each tier (claude 2.1.219)
        return [
            {"value": "", "label": "Default", "hint": "CLI default model"},
            {"value": "fable", "label": "Fable", "hint": "Latest Fable - most capable"},
            {"value": "opus", "label": "Opus", "hint": "Latest Opus"},
            {"value": "sonnet", "label": "Sonnet", "hint": "Latest Sonnet"},
            {"value": "haiku", "label": "Haiku", "hint": "Latest Haiku - fast and cheap"},
        ]

    def effort_options(self):
        # claude --effort <low|medium|high|xhigh|max> (claude 2.1.219)
        return [
            {"value": "", "label": "Default", "hint": "CLI default effort"},
            {"value": "low", "label": "Low", "hint": "Fastest, minimal reasoning"},
            {"value": "medium", "label": "Medium", "hint": "Balanced"},
            {"value": "high", "label": "High", "hint": "More reasoning"},
            {"value": "xhigh", "label": "X-High", "hint": "Extensive reasoning"},
            {"value": "max", "label": "Max", "hint": "Maximum reasoning"},
        ]

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt=""):
        argv = [self.binary, "-p",
                "--output-format", "stream-json",
                "--input-format", "stream-json",
                "--include-partial-messages",
                "--verbose",
                "--permission-mode", session.get("permission_mode") or self.default_permission(),
                "--permission-prompt-tool", "stdio"]
        if browser_mcp:
            mcp_config = {"mcpServers": {browser_mcp["name"]: {
                "type": "stdio",
                "command": browser_mcp["command"],
                "args": list(browser_mcp.get("args") or []),
                "env": dict(browser_mcp.get("env") or {}),
            }}}
            argv += ["--mcp-config", json.dumps(mcp_config, separators=(",", ":"))]
        guidance = [str(system_prompt or "").strip()]
        if browser_mcp:
            guidance.append(str(browser_mcp.get("engine_guidance") or "").strip())
        guidance = "\n\n".join(part for part in guidance if part)
        if guidance:
            # Additive: preserve any system prompt the user or CLI already
            # supplies while making both configured layers model-visible.
            argv += ["--append-system-prompt", guidance]
        native = session.get("native_session_id") or ""
        if first_turn or not native:
            argv += ["--session-id", pinned_id]
        else:
            argv += ["--resume", native]
        model = (session.get("model") or "").strip()
        if model:
            argv += ["--model", model]
        effort = (session.get("effort") or "").strip()
        if effort:
            argv += ["--effort", effort]
        return argv

    def initial_stdin(self, session, prompt):
        return [
            {"type": "control_request", "request_id": "init_1",
             "request": {"subtype": "initialize", "hooks": None}},
            {"type": "user", "message": {"role": "user",
             "content": [{"type": "text", "text": prompt}]}},
        ]

    def approval_payload(self, request_id, behavior, original_input, message="",
                         updated_permissions=None, request=None):
        if behavior == "allow":
            resp = {"behavior": "allow", "updatedInput": original_input or {}}
            if updated_permissions:
                resp["updatedPermissions"] = updated_permissions
        else:
            resp = {"behavior": "deny", "message": message or "Denied by user"}
        return {"type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": resp}}

    def interrupt_payload(self, session=None):
        return {"type": "control_request", "request_id": "int_1",
                "request": {"subtype": "interrupt"}}

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
            if sub == "init":
                acts = [{"a": "native_id", "id": ev.get("session_id", "")},
                        {"a": "transient", "msg": {"type": "turn_init",
                                                   "model": ev.get("model", ""),
                                                   "tools": len(ev.get("tools") or [])}}]
                if ev.get("model"):
                    ctx["model_seen"] = ev["model"]
                    acts.append({"a": "model", "model": ev["model"]})
                return acts
            if sub == "status":
                return [{"a": "transient", "msg": {"type": "status", "text": ev.get("status") or ""}}]
            if sub == "thinking_tokens":
                return [{"a": "transient", "msg": {"type": "thinking_tokens",
                                                   "tokens": ev.get("estimated_tokens", 0)}}]
            return []

        if t == "assistant":
            acts = []
            msg = ev.get("message") or {}
            # per-response model id - catches mid-turn fallback (e.g. fable -> opus)
            mdl = msg.get("model") or ""
            if mdl and mdl != ctx.get("model_seen"):
                ctx["model_seen"] = mdl
                acts.append({"a": "model", "model": mdl})
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
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        acts.append({"a": "event", "kind": "tool_result",
                                     "data": {"tool_use_id": blk.get("tool_use_id", ""),
                                              "content": stringify_content(blk.get("content"))[:20000],
                                              "is_error": bool(blk.get("is_error"))}})
            return acts

        if t == "control_request":
            req = ev.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                return [{"a": "approval", "req": {
                    "request_id": ev.get("request_id", ""),
                    "tool_name": req.get("tool_name", "?"),
                    "display_name": req.get("display_name") or req.get("tool_name", "?"),
                    "input": req.get("input") or {},
                    "description": req.get("description", ""),
                    "suggestions": req.get("permission_suggestions") or [],
                    "tool_use_id": req.get("tool_use_id", ""),
                }}]
            return []

        if t == "control_cancel_request":
            return [{"a": "approval_cancel", "request_id": ev.get("request_id", "")}]

        if t == "rate_limit_event":
            return [{"a": "rate_limit", "info": ev.get("rate_limit_info") or {}}]

        if t == "result":
            usage = ev.get("usage") or {}
            return [{"a": "result", "data": {
                "ok": not ev.get("is_error", False),
                "duration_ms": ev.get("duration_api_ms"),
                "cost_usd": ev.get("total_cost_usd"),
                "stop_reason": ev.get("stop_reason", ""),
                "num_turns": ev.get("num_turns"),
                "usage": {k: usage.get(k) for k in
                          ("input_tokens", "output_tokens",
                           "cache_read_input_tokens", "cache_creation_input_tokens")
                          if usage.get(k) is not None},
                "error": (ev.get("result") or "")[:2000] if ev.get("is_error") else "",
            }}]

        return []

    def auth_touch_paths(self):
        home = os.environ.get("HOME", "/root")
        return [os.path.join(home, ".claude", ".credentials.json")]

    async def _auth_status(self):
        """`claude auth status --json` is the CLI's own verdict ({"loggedIn":
        bool}), which stays true when a credentials file exists but the login
        behind it has expired or been revoked. Only a parsed loggedIn field is
        treated as authoritative; any other outcome (older CLI without the
        verb, changed output) falls back to the credentials-file heuristic, so
        a wording change can never flip a working login to "missing"."""
        rc, out = await self._run_probe([self.binary, "auth", "status", "--json"])
        verdict = None
        try:
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
                verdict = data
        except (ValueError, TypeError):
            pass
        if verdict is not None:
            if verdict["loggedIn"]:
                bits = [str(verdict.get(k)) for k in ("authMethod", "subscriptionType")
                        if verdict.get(k) and verdict.get(k) != "none"]
                return {"auth": "ok",
                        "detail": "logged in" + (" · " + " · ".join(bits) if bits else "")}
            return {"auth": "missing", "detail": "run `claude login` as this user"}
        home = os.environ.get("HOME", "/root")
        cred = os.path.join(home, ".claude", ".credentials.json")
        if os.path.exists(cred):
            return {"auth": "ok", "detail": "credentials file present (auth verb unavailable)"}
        return {"auth": "missing", "detail": f"run `claude login` as this user ({cred} not found)"}
