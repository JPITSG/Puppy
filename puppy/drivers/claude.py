"""Claude Code driver.

Spawns the official `claude` binary per turn in headless stream-json mode:
  claude -p --output-format stream-json --input-format stream-json \
         --include-partial-messages --replay-user-messages --verbose \
         --permission-mode <mode> --permission-prompt-tool stdio \
         (--session-id <uuid> | --resume <uuid>) [--model <m>]

Interactive permission prompts arrive as control_request/can_use_tool on stdout
and are answered with control_response on stdin (verified against claude 2.1.219).

Background work (Bash run_in_background, Monitor, backgrounded agents) is the
CLI's own, verified against claude 2.1.258: it reports the live set as
system/background_tasks_changed (REPLACE semantics, ambient housekeeping
flagged), the edges as task_started/task_updated/task_notification, and while
stdin stays open it wakes the model itself when a task ends - a fresh
system/init, a continuation, and another result in the same process. Closing
stdin ends every task (~5 s, reported as stopped) and exits; a later resume
first reports such a task as stale and runs an empty wake-up whose result
precedes the prompt's own. total_cost_usd and duration_api_ms accumulate over
the process while usage and num_turns are per result.
Compliance note: we only drive the unmodified official binary; auth stays inside
the CLI's own login (subscription OAuth), which is the vendor-sanctioned path.
"""
from __future__ import annotations

import json
import os

from puppy.drivers import base as driver_base
from puppy.drivers.base import Driver, ToolUnavailable, stringify_content
from puppy.user_paths import service_home


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")
_TASK_ENDED = ("completed", "failed", "killed", "stopped")


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

    def tool_options(self):
        return [
            {"value": "compact", "label": "Compact context",
             "hint": "Summarize the conversation so far into a shorter context"},
            {"value": "undo", "label": "Undo last turn",
             "hint": "Drop your last prompt and its reply from the conversation; "
                     "files are not changed"},
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
            "text": "Undid the last turn: the next prompt continues from before it "
                    "(files are unchanged)",
            "restore_text": str(last.get("text") or ""),
        }

    @staticmethod
    def _turn_text(prompt, tool):
        return "/compact" if driver_base.tool_name(tool) == "compact" else prompt

    def build_cmd(self, session, first_turn, prompt, pinned_id, browser_mcp=None,
                  system_prompt="", terminal_mcp=None, spawn_mcp=None, tool=None):
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
            [item for item in (browser_mcp, terminal_mcp, spawn_mcp) if item]
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
                  system_prompt="", terminal_mcp=None, spawn_mcp=None, tool=None):
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
                     spawn_mcp=None, tool=None):
        # --replay-user-messages gives a protocol-level acknowledgement for
        # each text message accepted from stdin. Do not expose steering until
        # the original prompt itself has been replayed, and retain the exact
        # FIFO identities needed to correlate later replays without putting a
        # Puppy request id into model-visible text.
        return {
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
                         updated_permissions=None, request=None):
        if behavior == "allow":
            resp = {"behavior": "allow", "updatedInput": original_input or {}}
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
                ctx["native_session_id"] = str(ev.get("session_id") or "")
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
                acts.append({"a": "event", "kind": "info", "data": {
                    "subtype": "task", "status": str(ev.get("status") or ""),
                    "task_id": str(ev.get("task_id") or ""),
                    "text": _task_notice(ev)}})
                return acts
            return []

        if t == "assistant":
            acts = []
            if ev.get("uuid"):
                ctx["tail_uuid"] = str(ev["uuid"])
            msg = ev.get("message") or {}
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if usage:
                ctx["context_used"] = sum(
                    int(usage.get(k) or 0) for k in
                    ("input_tokens", "cache_read_input_tokens",
                     "cache_creation_input_tokens", "output_tokens"))
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
                # duration_api_ms and total_cost_usd accumulate over the
                # process, so the last result already covers every wake-up
                "duration_ms": ev.get("duration_api_ms"),
                "cost_usd": ev.get("total_cost_usd"),
                "stop_reason": ev.get("stop_reason", ""),
                "num_turns": ctx.get("folded_turns"),
                "usage": dict(folded),
                "error": (ev.get("result") or "")[:2000] if ev.get("is_error") else "",
            }
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
        home = service_home()
        cred = os.path.join(home, ".claude", ".credentials.json")
        if os.path.exists(cred):
            return {"auth": "ok", "detail": "credentials file present (auth verb unavailable)"}
        return {"auth": "missing", "detail": f"run `claude login` as this user ({cred} not found)"}
