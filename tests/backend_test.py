#!/usr/bin/env python3
"""No-quota integration test for the separately deployable headless backend."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from puppy import __version__, protocol, upgrade_contract  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import CodexDriver, parse_model_catalog as parse_codex_catalog  # noqa: E402


def exercise_driver_normalization() -> None:
    claude = ClaudeDriver()
    assert claude.supports_steering is True
    assert claude.steering_acknowledged is True
    claude_ctx = claude.turn_context({}, True, "original direction", "pin")
    assert claude.steer_ready({}, claude_ctx) is False
    assert claude.steer_payload(
        {}, claude_ctx, "change direction", "steer-1") is None
    original_replay = claude.parse_line(json.dumps({
        "type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "original direction"}]},
    }), claude_ctx)
    assert original_replay == []
    assert claude.steer_ready({}, claude_ctx) is True
    assert claude.steer_payload(
        {}, claude_ctx, "change direction", "steer-1") == {
        "type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "change direction"}]}}
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "status", "status": "requesting",
    }), claude_ctx) == [{"a": "transient", "msg": {
        "type": "status", "text": "Requesting"}}]
    assert claude.parse_line(json.dumps({
        "type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "change direction"}]},
    }), claude_ctx) == [{
        "a": "steer_result", "request_id": "steer-1",
        "ok": True, "error": "",
    }]
    assert "--replay-user-messages" in claude.build_cmd(
        {}, True, "original direction", "pin")
    usage_ctx = claude.turn_context({}, True, "usage", "usage-pin")
    claude.parse_line(json.dumps({
        "type": "assistant", "message": {
            "model": "claude-test", "content": [], "usage": {
                "input_tokens": 10, "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 3, "output_tokens": 4,
            }}}), usage_ctx)
    usage_result = claude.parse_line(json.dumps({
        "type": "result", "session_id": "claude-session", "usage": {},
        "modelUsage": {"claude-test": {"contextWindow": 200000}},
    }), usage_ctx)[0]["data"]
    assert usage_result["context_used"] == 37
    assert usage_result["context_window"] == 200000

    # Background work (claude 2.1.258): a successful result with live tasks is
    # a pause the CLI will wake the model from in the same process, and the
    # final result folds every result of that process.
    bg_ctx = claude.turn_context({}, True, "start a watcher", "bg-pin")
    assert claude.parse_line(json.dumps({
        "type": "user", "uuid": "prompt-1", "message": {"role": "user", "content": [
            {"type": "text", "text": "start a watcher"}]},
    }), bg_ctx) == []
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "background_tasks_changed", "tasks": [
            {"task_id": "b1", "task_type": "local_bash", "description": "watch"},
            {"task_id": "b2", "task_type": "monitor_ws", "description": "sweep",
             "ambient": True}]}), bg_ctx) == [{
        "a": "background_tasks",
        "tasks": [{"id": "b1", "type": "local_bash", "description": "watch"}]}]
    # the edge for a task already in the set is not a change
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "task_started", "task_id": "b1",
        "is_backgrounded": True, "description": "watch",
        "task_type": "local_bash"}), bg_ctx) == []
    paused = claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 2, "duration_api_ms": 400,
        "total_cost_usd": 0.01, "stop_reason": "end_turn", "is_error": False,
        "usage": {"input_tokens": 10, "output_tokens": 4,
                  "cache_read_input_tokens": 100}, "modelUsage": {}}), bg_ctx)
    assert len(paused) == 1 and paused[0]["a"] == "turn_pause"
    assert paused[0]["tasks"] == [
        {"id": "b1", "type": "local_bash", "description": "watch"}]
    assert paused[0]["data"]["usage"] == {
        "input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 100}
    assert paused[0]["data"]["native_prompt_id"] == "prompt-1"
    assert paused[0]["data"]["num_turns"] == 2
    assert "wakeups" not in paused[0]["data"]
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "background_tasks_changed", "tasks": []}),
        bg_ctx) == [{"a": "background_tasks", "tasks": []}]
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "task_notification", "task_id": "b1",
        "status": "completed", "output_file": "/tmp/x",
        "summary": 'Background command "watch" completed (exit code 0)'}),
        bg_ctx) == [{"a": "event", "kind": "info", "data": {
            "subtype": "task", "status": "completed", "task_id": "b1",
            "text": 'Background command "watch" completed (exit code 0)'}}]
    assert claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 1, "duration_api_ms": 900,
        "total_cost_usd": 0.03, "stop_reason": "end_turn", "is_error": False,
        "usage": {"input_tokens": 5, "output_tokens": 6,
                  "cache_read_input_tokens": 50}, "modelUsage": {}}), bg_ctx) == [{
        "a": "result", "data": {
            "native_session_id": "s", "native_prompt_id": "prompt-1",
            "native_tail_id": "prompt-1", "ok": True, "api_duration_ms": 900,
            "cost_usd": 0.03, "stop_reason": "end_turn", "num_turns": 3,
            "usage": {"input_tokens": 15, "output_tokens": 10,
                      "cache_read_input_tokens": 150},
            "error": "", "wakeups": 1}}]

    # A task the previous process left running is reported before the prompt
    # is replayed; the empty wake-up result that follows is not the prompt's.
    stale_ctx = claude.turn_context({}, False, "and now?", "stale-pin")
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "task_notification", "task_id": "old",
        "status": "stopped", "output_file": "",
        "summary": "No completion record was found"}), stale_ctx) == []
    assert claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 0, "duration_api_ms": 0,
        "total_cost_usd": 0, "stop_reason": None, "is_error": False,
        "usage": {"input_tokens": 0, "output_tokens": 0}}), stale_ctx) == [{
        "a": "transient", "msg": {
            "type": "status",
            "text": "Catching up on an earlier background task..."}}]
    assert claude.parse_line(json.dumps({
        "type": "user", "uuid": "p2", "message": {"role": "user", "content": [
            {"type": "text", "text": "and now?"}]}}), stale_ctx) == []
    real = claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 1, "duration_api_ms": 300,
        "total_cost_usd": 0.02, "stop_reason": "end_turn", "is_error": False,
        "usage": {"input_tokens": 7, "output_tokens": 3}}), stale_ctx)
    assert real[0]["a"] == "result"
    assert real[0]["data"]["native_prompt_id"] == "p2"
    assert real[0]["data"]["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert real[0]["data"]["num_turns"] == 1
    # without a stale report, an early result is still the turn's result
    early_ctx = claude.turn_context({}, True, "x", "early-pin")
    early = claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "is_error": True,
        "result": "Not logged in", "usage": {}}), early_ctx)
    assert early[0]["a"] == "result" and early[0]["data"]["ok"] is False
    # an error never waits on background work
    error_ctx = claude.turn_context({}, True, "x", "error-pin")
    claude.parse_line(json.dumps({
        "type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "x"}]}}), error_ctx)
    claude.parse_line(json.dumps({
        "type": "system", "subtype": "background_tasks_changed", "tasks": [
            {"task_id": "b1", "task_type": "local_bash", "description": "watch"}]}),
        error_ctx)
    failed = claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "is_error": True,
        "result": "boom", "usage": {}}), error_ctx)
    assert failed[0]["a"] == "result" and failed[0]["data"]["ok"] is False
    # "/compact" is never replayed: the compaction acknowledges the request
    # through its replayed local-command output, after any stale wake-up
    compact_ctx = claude.turn_context(
        {}, False, "ignored", "compact-pin", tool={"tool": "compact"})
    assert compact_ctx["initial_prompt"] == "/compact"
    claude.parse_line(json.dumps({
        "type": "system", "subtype": "task_notification", "task_id": "old",
        "status": "stopped", "output_file": "", "summary": "stale"}), compact_ctx)
    assert claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 0, "is_error": False,
        "usage": {}}), compact_ctx)[0]["a"] == "transient"
    assert claude.parse_line(json.dumps({
        "type": "user", "uuid": "lc-1", "message": {
            "role": "user",
            "content": "<local-command-stdout>Compacted </local-command-stdout>"}}),
        compact_ctx) == []
    compacted = claude.parse_line(json.dumps({
        "type": "result", "session_id": "s", "num_turns": 0, "is_error": False,
        "usage": {}}), compact_ctx)
    assert compacted[0]["a"] == "result"
    assert compacted[0]["data"]["compacted"] is False
    assert compacted[0]["data"]["native_tail_id"] == "lc-1"

    # The CLI's OAuth refresh lock timeout is its own "retry in a minute":
    # never evidence of a lost login, and the synthetic message it emits in
    # the model's place is an error, not assistant text or a model move.
    from puppy.drivers import base as driver_base_module
    lock_text = ("Failed to refresh OAuth token: another Claude Code process is "
                 "refreshing it or exited mid-refresh. This is usually transient; "
                 "retry in a minute, and if it persists close other Claude Code "
                 "processes or sign in again")
    assert driver_base_module.looks_transient_auth(lock_text) is True
    assert driver_base_module.looks_like_auth_failure(lock_text) is False
    assert driver_base_module.looks_like_auth_failure(
        "OAuth token expired; please run /login") is True
    synthetic_ctx = claude.turn_context({}, True, "hello", "syn-pin")
    claude.parse_line(json.dumps({
        "type": "system", "subtype": "init", "session_id": "s",
        "model": "claude-fable-5-1", "tools": []}), synthetic_ctx)
    assert claude.parse_line(json.dumps({
        "type": "assistant", "uuid": "syn-1", "error": "server_error",
        "is_api_error_message": True, "message": {
            "model": "<synthetic>", "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": lock_text}],
            "usage": {"input_tokens": 0, "output_tokens": 0}}}), synthetic_ctx) == [{
        "a": "event", "kind": "error", "data": {
            "subtype": "engine_api_error", "code": "server_error",
            "text": lock_text}}]
    assert synthetic_ctx["model_seen"] == "claude-fable-5-1"
    assert synthetic_ctx.get("context_used") is None
    assert synthetic_ctx["tail_uuid"] == "syn-1"
    assert claude.parse_line(json.dumps({
        "type": "assistant", "message": {
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "No response requested."}]}}),
        synthetic_ctx) == []

    driver = CodexDriver()
    live_catalog = parse_codex_catalog([{
        "model": "gpt-test", "displayName": "Test", "isDefault": True,
        "supportedReasoningEfforts": [],
        "serviceTiers": [{"id": "catalog-fast", "name": "Fast"}],
    }])
    driver._model_catalog_state().options = live_catalog
    driver._model_catalog_state().source = "engine"
    assert driver.uses_stdin_stream is True
    assert driver.supports_steering is True
    assert driver.steering_acknowledged is True
    session = {
        "cwd": "/tmp", "native_session_id": "", "model": "gpt-test",
        "effort": "high", "permission_mode": "workspace-write",
    }
    context = driver.turn_context(
        session, True, "hello", "client-message-1",
        system_prompt="Node policy.")
    assert driver.steer_ready(session, context) is False
    assert driver.build_cmd(session, True, "hello", "client-message-1")[:3] == \
        ["codex", "app-server", "--stdio"]
    initialize = driver.initial_stdin(session, "hello")
    assert initialize == [{
        "id": "puppy-initialize", "method": "initialize",
        "params": {
            "clientInfo": {"name": "puppy", "title": "Puppy",
                           "version": __version__},
            "capabilities": {"experimentalApi": True},
        },
    }]
    setup = driver.parse_line(json.dumps({
        "id": "puppy-initialize", "result": {
            "userAgent": "codex", "codexHome": "/tmp/codex",
            "platformFamily": "unix", "platformOs": "linux",
        },
    }), context)
    assert setup[0] == {"a": "stdin", "data": {"method": "initialized"}}
    thread_request = setup[1]["data"]
    assert thread_request["method"] == "thread/start"
    assert thread_request["params"] == {
        "cwd": "/tmp", "approvalPolicy": "never",
        "sandbox": "workspace-write", "model": "gpt-test",
    }

    thread_actions = driver.parse_line(json.dumps({
        "id": "puppy-thread", "result": {
            "thread": {"id": "thread-1"}, "model": "gpt-test",
        },
    }), context)
    assert thread_actions[:2] == [
        {"a": "native_id", "id": "thread-1"},
        {"a": "model", "model": "gpt-test"},
    ]
    turn_request = thread_actions[2]["data"]
    assert turn_request["method"] == "turn/start"
    assert turn_request["params"]["threadId"] == "thread-1"
    assert turn_request["params"]["clientUserMessageId"] == "client-message-1"
    assert turn_request["params"]["input"][0]["text"].endswith("\n\nhello")
    assert "<puppy_system_prompt>\nNode policy." in \
        turn_request["params"]["input"][0]["text"]
    assert turn_request["params"]["effort"] == "high"
    assert turn_request["params"]["serviceTierForTurn"] == "default"

    legacy_driver = CodexDriver()
    legacy_driver._cache_file_options = [{"value": "", "label": "Default"}, {
        "value": "gpt-test", "label": "Test", "effort_options": []}]
    legacy_ctx = legacy_driver.turn_context(
        session, True, "hello", "legacy-message")
    assert "serviceTierForTurn" not in \
        legacy_driver._turn_request(legacy_ctx)["params"]

    # Fast stores no native spelling: an arbitrary id discovered for this
    # model is resolved into turn/start, while explicit off always asks for
    # the protocol's standard/default service.
    fast_driver = CodexDriver()
    fast_catalog = parse_codex_catalog([{
        "model": "future-model", "displayName": "Future", "isDefault": True,
        "supportedReasoningEfforts": [],
        "serviceTiers": [{"id": "opaque-speed-2040", "name": "Fast"}],
    }])
    fast_driver._model_catalog_state().options = fast_catalog
    fast_driver._model_catalog_state().source = "engine"
    fast_session = dict(session, model="future-model", fast_mode=True)
    fast_ctx = fast_driver.turn_context(
        fast_session, True, "hello", "future-message")
    assert fast_driver._turn_request(fast_ctx)["params"][
        "serviceTierForTurn"] == "opaque-speed-2040"

    started = driver.parse_line(json.dumps({
        "id": "puppy-turn", "result": {
            "turn": {"id": "turn-1", "status": "inProgress", "items": []},
        },
    }), context)
    assert started[0]["msg"]["text"] == "Thinking..."
    assert context["turn_id"] == "turn-1"
    assert driver.steer_ready(session, context) is True
    assert driver.steer_payload(
        session, context, "revised direction", "steer-2") == {
            "id": "puppy-steer:steer-2", "method": "turn/steer",
            "params": {
                "threadId": "thread-1",
                "clientUserMessageId": "steer-2",
                "input": [{"type": "text", "text": "revised direction"}],
                "expectedTurnId": "turn-1",
            },
        }
    assert driver.parse_line(json.dumps({
        "id": "puppy-steer:steer-2", "result": {"turnId": "turn-1"},
    }), context) == [{
        "a": "steer_result", "request_id": "steer-2",
        "ok": True, "error": "",
    }]
    rejected_steer = driver.parse_line(json.dumps({
        "id": "puppy-steer:steer-err", "error": {
            "code": -32600, "message": "turn already completed"},
    }), context)
    assert rejected_steer == [{
        "a": "steer_result", "request_id": "steer-err", "ok": False,
        "error": "turn already completed",
    }]

    search = driver.parse_line(json.dumps({
        "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 1,
            "item": {
            "type": "webSearch", "id": "search-1",
            "query": "python TLS support ...",
            "action": {"type": "search", "query": None,
                       "queries": ["python 3.9 TLS", "aiohttp certificate pin"]},
            "results": [
                {"title": "ssl documentation", "url": "https://docs.python.org/3/library/ssl.html",
                 "snippet": "TLS support in the standard library."},
                {"title": "aiohttp documentation", "url": "https://docs.aiohttp.org/",
                 "snippet": "Fingerprint verification."},
            ],
        }},
    }), context)
    assert [action.get("kind") for action in search] == ["tool_use", "tool_result"]
    assert search[0]["data"]["tool"] == "web_search"
    assert search[0]["data"]["input"]["queries"] == \
        ["python 3.9 TLS", "aiohttp certificate pin"]
    assert "https://docs.python.org/" in search[1]["data"]["content"]
    assert search[1]["data"]["is_error"] is False

    structured_search = driver.parse_line(json.dumps({
        "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 2,
            "item": {"type": "webSearch", "action": "open_page",
                     "results": {"url": "https://example.test/", "status": 200}}},
    }), context)
    assert structured_search[0]["data"]["input"] == {"action": "open_page"}
    assert structured_search[0]["data"]["tool_use_id"] == "codex-item-1"
    assert '"status": 200' in structured_search[1]["data"]["content"]

    image = driver.parse_line(json.dumps({
        "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 3,
            "item": {"type": "imageView", "id": "image-1",
                     "path": "/data/example.png"}},
    }), context)
    assert [action.get("kind") for action in image] == ["tool_use", "tool_result"]
    assert image[0]["data"]["input"] == {"path": "/data/example.png"}

    future_call = driver.parse_line(json.dumps({
        "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 4,
            "item": {"type": "collabAgentToolCall", "id": "call-1",
                     "name": "delegate", "arguments": {"task": "inspect"},
                     "result": {"status": "done"}}},
    }), context)
    assert [action.get("kind") for action in future_call] == ["tool_use", "tool_result"]
    assert future_call[0]["data"]["tool"] == "delegate"
    assert '"status": "done"' in future_call[1]["data"]["content"]

    lifecycle = driver.parse_line(json.dumps({
        "method": "item/completed", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "completedAtMs": 5,
            "item": {"type": "contextCompaction", "id": "compact-1"}},
    }), context)
    assert lifecycle == []

    # Restored usage from resume belongs to an older turn and must not leak
    # into this result; the matching live turn breakdown is authoritative.
    assert driver.parse_line(json.dumps({
        "method": "thread/tokenUsage/updated", "params": {
            "threadId": "thread-1", "turnId": "old-turn",
            "tokenUsage": {"last": {"inputTokens": 99, "outputTokens": 99}},
        },
    }), context) == []
    driver.parse_line(json.dumps({
        "method": "thread/tokenUsage/updated", "params": {
            "threadId": "thread-1", "turnId": "turn-1",
            "tokenUsage": {"last": {
                "inputTokens": 12, "outputTokens": 3,
                "cachedInputTokens": 4, "reasoningOutputTokens": 2,
                "totalTokens": 15,
            }},
        },
    }), context)

    approval = driver.parse_line(json.dumps({
        "id": 77, "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "thread-1", "turnId": "turn-1",
                   "itemId": "cmd-1", "startedAtMs": 1,
                   "command": "make test", "cwd": "/tmp"},
    }), context)[0]["req"]
    assert approval["input"]["command"] == "make test"
    assert driver.approval_payload(
        approval["request_id"], "allow", approval["input"],
        updated_permissions=[{"type": "allowAlways"}], request=approval) == \
        {"id": 77, "result": {"decision": "acceptForSession"}}
    assert driver.cancel_approval_payload(approval) == \
        {"id": 77, "result": {"decision": "cancel"}}
    assert driver.interrupt_payload(session, context) == {
        "id": "puppy-interrupt", "method": "turn/interrupt",
        "params": {"threadId": "thread-1", "turnId": "turn-1"},
    }
    assert driver.parse_line(json.dumps({
        "id": "puppy-interrupt", "error": {
            "code": -32600, "message": "turn already completed",
        },
    }), context) == []

    completed = driver.parse_line(json.dumps({
        "method": "turn/completed", "params": {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "completed", "items": [],
                     "durationMs": 1234},
        },
    }), context)
    assert completed == [{"a": "result", "data": {
        # the identities a later undo reverts to ride on every prompt result
        "native_session_id": "thread-1", "native_turn_id": "turn-1",
        # the last response's input+output is the context the model held
        "context_used": 15,
        "ok": True, "usage": {
            "input_tokens": 12, "output_tokens": 3,
            "cached_input_tokens": 4, "reasoning_output_tokens": 2,
        }, "engine_duration_ms": 1234, "stop_reason": "completed",
    }}]
    assert driver.steer_ready(session, context) is False
    assert driver.steer_payload(session, context, "too late", "steer-3") is None
    assert driver.interrupt_payload(session, context) is None

    resumed = dict(session, native_session_id="thread-existing")
    resume_ctx = driver.turn_context(
        resumed, False, "again", "client-message-2")
    resume_setup = driver.parse_line(json.dumps({
        "id": "puppy-initialize", "result": {
            "userAgent": "codex", "codexHome": "/tmp/codex",
            "platformFamily": "unix", "platformOs": "linux",
        },
    }), resume_ctx)
    resume_request = resume_setup[1]["data"]
    assert resume_request["method"] == "thread/resume"
    assert resume_request["params"]["threadId"] == "thread-existing"
    assert resume_request["params"]["excludeTurns"] is True


def exercise_side_question_contract() -> None:
    """The pinned claude 2.1.258 side-question control protocol.

    Probed against the real binary: the request rides the control channel
    (not a user message), the initialize reply is what makes it available, an
    unknown subtype is refused with an error control_response rather than a
    crash, and only answered non-synthetic exchanges may be replayed as
    history.
    """
    claude = ClaudeDriver()
    assert claude.supports_side_questions is True
    ctx = claude.turn_context({}, True, "do the work", "pin")

    # not until the control channel itself has answered
    assert claude.side_question_ready({}, ctx) is False
    assert claude.side_question_payload({}, ctx, "why?", [], "q1") is None
    assert claude.parse_line(json.dumps({
        "type": "control_response", "response": {
            "subtype": "success", "request_id": "init_1", "response": {}},
    }), ctx) == []
    assert claude.side_question_ready({}, ctx) is True

    # a tool turn addresses the native conversation with no live query to fork
    tool_ctx = claude.turn_context({}, False, "/compact", "pin",
                                   tool={"tool": "compact"})
    tool_ctx["control_ready"] = True
    assert claude.side_question_ready({}, tool_ctx) is False

    assert claude.side_question_payload({}, ctx, "why that file?", [], "q1") == {
        "type": "control_request", "request_id": "puppy-sq:q1",
        "request": {"subtype": "side_question", "question": "why that file?"}}
    # history is ours to keep: the CLI ignores its own for SDK callers, and a
    # half-formed pair is never replayed as if the model had said it
    assert claude.side_question_payload({}, ctx, "and then?", [
        {"question": "why that file?", "response": "It holds the parser."},
        {"question": "dropped", "response": ""},
    ], "q2")["request"]["history"] == [
        {"question": "why that file?", "response": "It holds the parser."}]
    assert claude.side_question_cancel_payload({}, ctx, "q2") == {
        "type": "control_cancel_request", "request_id": "puppy-sq:q2"}

    # progress and the single final answer, correlated by our namespaced id
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "control_request_progress",
        "request_id": "puppy-sq:q1", "status": "started",
    }), ctx) == [{"a": "side_question_progress", "request_id": "q1",
                  "status": "started", "attempt": None, "max_retries": None,
                  "retry_delay_ms": None}]
    # an id that is not ours is never interpreted as an answer
    assert claude.parse_line(json.dumps({
        "type": "system", "subtype": "control_request_progress",
        "request_id": "someone-else", "status": "started",
    }), ctx) == []
    assert claude.parse_line(json.dumps({
        "type": "control_response", "response": {
            "subtype": "success", "request_id": "puppy-sq:q1",
            "response": {"response": "It holds the parser.", "synthetic": False}},
    }), ctx) == [{"a": "side_question_result", "request_id": "q1", "ok": True,
                  "text": "It holds the parser.", "synthetic": False,
                  "fallback_model": "", "fallback_notice": ""}]
    # an older CLI refuses the subtype outright; its wording becomes the reason
    assert claude.parse_line(json.dumps({
        "type": "control_response", "response": {
            "subtype": "error", "request_id": "puppy-sq:q3",
            "error": "Unsupported control request subtype: side_question"},
    }), ctx) == [{"a": "side_question_result", "request_id": "q3", "ok": False,
                  "error": "Unsupported control request subtype: side_question"}]

    # engines without a native side-question channel offer nothing
    from puppy.drivers.opencode import OpenCodeDriver
    assert CodexDriver().supports_side_questions is False
    assert OpenCodeDriver().supports_side_questions is False
    assert protocol.SIDE_QUESTION_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.TIMER_SETTINGS_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.ENGINE_DEFAULTS_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.SESSION_PINNING_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.SESSION_FAST_MODE_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.NODE_STATE_STREAM_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.SESSION_CONTROL_WS_CAPABILITY in protocol.BASE_CAPABILITIES


async def exercise_codex_app_server_turn(root, runner, db) -> None:
    """Drive the real runner against a no-model fake app-server process.

    This pins process ownership, handshake ordering, native resume, normalized
    transcript events, token usage, stdin EOF, and the fact that prompts are
    carried in turn/start rather than process arguments.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fake = root / "codex"
    log_path = root / "requests.jsonl"
    fake.write_text(r'''#!/usr/bin/env python3
import json
import os
import sys
import time

seen = []

def read():
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("unexpected stdin EOF")
    value = json.loads(line)
    seen.append(value)
    return value

def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

if sys.argv[1:3] != ["app-server", "--stdio"]:
    raise SystemExit("wrong argv: {!r}".format(sys.argv[1:]))

initialize = read()
send({"id": initialize["id"], "result": {
    "userAgent": "fake-codex", "codexHome": "/tmp/fake-codex",
    "platformFamily": "unix", "platformOs": "linux"}})
if read().get("method") != "initialized":
    raise SystemExit("missing initialized notification")
thread_request = read()
thread_id = thread_request.get("params", {}).get("threadId") or "fake-thread"
send({"method": "thread/started", "params": {"thread": {"id": thread_id}}})
send({"id": thread_request["id"], "result": {
    "thread": {"id": thread_id}, "model": "gpt-fake"}})
turn_request = read()
turn_id = "fake-turn"
# Notifications are allowed to race the immediate request response.
send({"method": "turn/started", "params": {
    "threadId": thread_id,
    "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
send({"id": turn_request["id"], "result": {
    "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
prompt_text = turn_request["params"]["input"][0]["text"]
if prompt_text.endswith("interrupt fake turn") or \
        prompt_text.endswith("stop guard fake turn"):
    interrupt = read()
    if interrupt.get("method") != "turn/interrupt" or \
            interrupt.get("params") != {"threadId": thread_id, "turnId": turn_id}:
        raise SystemExit("bad interrupt request: {!r}".format(interrupt))
    send({"id": interrupt["id"], "result": {}})
    if prompt_text.endswith("stop guard fake turn"):
        time.sleep(0.15)
    send({"method": "turn/completed", "params": {
        "threadId": thread_id,
        "turn": {"id": turn_id, "status": "interrupted", "items": [],
                 "durationMs": 5}}})
else:
    answer = "fake app-server response"
    if prompt_text.endswith("steer fake turn") or \
            prompt_text.endswith("reject steer turn") or \
            prompt_text.endswith("unacknowledged steer turn"):
        steer = read()
        params = steer.get("params") or {}
        if steer.get("method") != "turn/steer" or \
                params.get("threadId") != thread_id or \
                params.get("expectedTurnId") != turn_id or \
                params.get("clientUserMessageId") is None or \
                not isinstance(params.get("input"), list):
            raise SystemExit("bad steer request: {!r}".format(steer))
        if prompt_text.endswith("reject steer turn"):
            send({"id": steer["id"], "error": {
                "code": -32600, "message": "active turn no longer accepts input"}})
        elif prompt_text.endswith("unacknowledged steer turn"):
            # Deliberately reach turn/completed without a response to the
            # turn/steer request. The runner must resolve its `sent` receipt.
            pass
        else:
            if params != {
                    "threadId": thread_id,
                    "clientUserMessageId": "runner-steer-1",
                    "input": [{"type": "text", "text": "updated direction"}],
                    "expectedTurnId": turn_id,
                }:
                raise SystemExit("bad steer request: {!r}".format(steer))
            send({"id": steer["id"], "result": {"turnId": turn_id}})
            answer = "steered: " + params["input"][0]["text"]
    if prompt_text.endswith("race source turn"):
        time.sleep(0.2)
    if prompt_text.endswith("result linger turn"):
        time.sleep(0.1)
    send({"method": "item/started", "params": {
        "threadId": thread_id, "turnId": turn_id, "startedAtMs": 1,
        "item": {"type": "agentMessage", "id": "answer-1", "text": ""}}})
    send({"method": "item/completed", "params": {
        "threadId": thread_id, "turnId": turn_id, "completedAtMs": 2,
        "item": {"type": "agentMessage", "id": "answer-1",
                 "text": answer, "phase": "final_answer"}}})
    send({"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread_id, "turnId": turn_id,
        "tokenUsage": {"last": {
            "inputTokens": 8, "outputTokens": 2, "cachedInputTokens": 3,
            "reasoningOutputTokens": 1, "totalTokens": 10},
            "total": {"inputTokens": 8, "outputTokens": 2,
                      "cachedInputTokens": 3, "reasoningOutputTokens": 1,
                      "totalTokens": 10}}}})
    send({"method": "turn/completed", "params": {
        "threadId": thread_id,
        "turn": {"id": turn_id, "status": "completed", "items": [],
                 "durationMs": 25}}})
    if prompt_text.endswith("result linger turn"):
        time.sleep(0.2)
for line in sys.stdin:
    if line.strip():
        seen.append(json.loads(line))
with open(os.environ["PUPPY_FAKE_CODEX_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "seen": seen}) + "\n")
''', encoding="utf-8")
    fake.chmod(0o755)

    from puppy.drivers import get_driver
    driver = get_driver("codex")
    original_binary = driver.binary
    original_log = os.environ.get("PUPPY_FAKE_CODEX_LOG")
    driver.binary = str(fake)
    os.environ["PUPPY_FAKE_CODEX_LOG"] = str(log_path)
    sid = db.create_session(
        "codex app-server fake", "codex", str(root), "", "",
        "#7aa2f7", "workspace-write")
    hub = runner.hub(sid)
    capture = None
    try:
        for index, prompt in enumerate(("first fake turn", "resumed fake turn")):
            assert hub.send_message(prompt) == {"queued": False}
            deadline = time.monotonic() + 10
            while hub.status != "idle":
                if time.monotonic() >= deadline:
                    raise AssertionError("fake Codex turn did not finish")
                await asyncio.sleep(0.02)
            if hub.turn_task is not None:
                await hub.turn_task
            assert db.get_session(sid)["native_session_id"] == "fake-thread"
            events = db.get_events(sid)
            assert sum(event["kind"] == "assistant" for event in events) == index + 1
            result = [event for event in events if event["kind"] == "result"][-1]
            assert result["data"]["ok"] is True
            assert result["data"]["usage"] == {
                "input_tokens": 8, "output_tokens": 2,
                "cached_input_tokens": 3, "reasoning_output_tokens": 1,
            }

        class Capture:
            def __init__(self):
                self.messages = []

            async def send_json(self, value):
                self.messages.append(value)

        capture = Capture()
        hub.attach(capture)

        assert (await hub.steer(
            "not running", "idle-steer", expected_turn_id="old-turn"))["error"] == \
            "there is no active turn to steer"
        assert hub.send_message("steer fake turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not (hub._proc_ready and isinstance(hub._driver_ctx, dict) and
                   hub._driver_ctx.get("phase") == "running"):
            if time.monotonic() >= deadline:
                raise AssertionError("fake Codex turn never became steerable")
            await asyncio.sleep(0.02)
        steering = hub.steering_state()
        assert steering["ready"] is True and steering["turn_id"]
        assert hub.snapshot()["steering"] == steering
        listed_steering = next(
            row["steering"] for row in runner.sessions_payload()["sessions"]
            if row["id"] == sid)
        assert listed_steering == steering
        wrong_turn = await hub.steer(
            "wrong destination", "runner-steer-wrong",
            expected_turn_id="not-the-active-turn")
        assert "active turn changed" in wrong_turn["error"]
        steered = await hub.steer(
            "updated direction", "runner-steer-1",
            expected_turn_id=steering["turn_id"])
        assert steered["ok"] is True
        assert steered["request_id"] == "runner-steer-1"
        assert steered["turn_id"] == steering["turn_id"]
        assert steered["status"] in ("sent", "accepted")
        duplicate = await hub.steer(
            "updated direction", "runner-steer-1",
            expected_turn_id=steering["turn_id"])
        assert duplicate["ok"] is True and duplicate["duplicate"] is True
        reused = await hub.steer(
            "different direction", "runner-steer-1",
            expected_turn_id=steering["turn_id"])
        assert "different text" in reused["error"]
        deadline = time.monotonic() + 10
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("steered fake Codex turn did not finish")
            await asyncio.sleep(0.02)
        if hub.turn_task is not None:
            await hub.turn_task
        events = db.get_events(sid)
        steer_event = next(event for event in events
                           if event["kind"] == "user" and
                           event["data"].get("request_id") == "runner-steer-1")
        assert steer_event["data"] == {
            "text": "updated direction", "steering": True,
            "request_id": "runner-steer-1", "turn_id": steering["turn_id"]}
        assert sum(event["kind"] == "user" and
                   event["data"].get("request_id") == "runner-steer-1"
                   for event in events) == 1
        assert hub._steer_receipts["runner-steer-1"]["status"] == "accepted"
        await asyncio.sleep(0)
        assert any(message.get("type") == "steer_status" and
                   message.get("request_id") == "runner-steer-1" and
                   message.get("status") == "accepted"
                   for message in capture.messages)
        assert any(event["kind"] == "assistant" and
                   event["data"].get("text") == "steered: updated direction"
                   for event in events)

        # A native negative acknowledgement is durable and idempotent: the
        # user message remains visible, its rejection is recorded once, and a
        # retry with the same identity cannot write it again.
        assert hub.send_message("reject steer turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not hub.steering_state()["ready"]:
            if time.monotonic() >= deadline:
                raise AssertionError("rejecting fake turn never became steerable")
            await asyncio.sleep(0.02)
        reject_turn = hub.steering_state()["turn_id"]
        rejected = await hub.steer(
            "rejected direction", "runner-steer-reject",
            expected_turn_id=reject_turn)
        assert rejected["status"] in ("sent", "rejected")
        deadline = time.monotonic() + 10
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("rejecting fake turn did not finish")
            await asyncio.sleep(0.02)
        assert hub._steer_receipts["runner-steer-reject"]["status"] == "rejected"
        events = db.get_events(sid)
        steering_errors = [event for event in events
                           if event["kind"] == "error" and
                           event["data"].get("subtype") == "steering_rejected" and
                           event["data"].get("request_id") == "runner-steer-reject"]
        assert len(steering_errors) == 1, steering_errors
        await asyncio.sleep(0)
        assert any(message.get("type") == "steer_status" and
                   message.get("request_id") == "runner-steer-reject" and
                   message.get("status") == "rejected"
                   for message in capture.messages)
        rejected_retry = await hub.steer(
            "rejected direction", "runner-steer-reject",
            expected_turn_id=reject_turn)
        assert rejected_retry["status"] == "rejected" and \
            rejected_retry["duplicate"] is True

        # A transport write is not native acceptance. If the engine completes
        # without acknowledging it, the receipt and transcript resolve to a
        # deterministic rejection instead of remaining `sent` forever.
        assert hub.send_message("unacknowledged steer turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not hub.steering_state()["ready"]:
            if time.monotonic() >= deadline:
                raise AssertionError("unacknowledged fake turn never became steerable")
            await asyncio.sleep(0.02)
        unacknowledged_turn = hub.steering_state()["turn_id"]
        unacknowledged = await hub.steer(
            "maybe too late", "runner-steer-unacknowledged",
            expected_turn_id=unacknowledged_turn)
        assert unacknowledged["status"] == "sent"
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("unacknowledged fake turn did not finish")
            await asyncio.sleep(0.02)
        assert hub._steer_receipts["runner-steer-unacknowledged"]["status"] == \
            "rejected"
        assert "completed before" in \
            hub._steer_receipts["runner-steer-unacknowledged"]["error"]

        # The result notification closes steering before process teardown.
        # This catches the window where status is still "running" but the
        # native turn has already become terminal.
        assert hub.send_message("result linger turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not hub.steering_state()["ready"]:
            if time.monotonic() >= deadline:
                raise AssertionError("lingering fake turn never became steerable")
            await asyncio.sleep(0.02)
        linger_turn = hub.steering_state()["turn_id"]
        while not hub._turn_result_seen:
            if time.monotonic() >= deadline:
                raise AssertionError("lingering fake turn produced no result")
            await asyncio.sleep(0.01)
        too_late = await hub.steer(
            "too late", "runner-steer-late", expected_turn_id=linger_turn)
        assert too_late["error"] == "the active turn has already completed"
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("lingering fake turn did not exit")
            await asyncio.sleep(0.02)

        # Stop wins before any later steering writer can enter the serialized
        # protocol stream.
        assert hub.send_message("stop guard fake turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not hub.steering_state()["ready"]:
            if time.monotonic() >= deadline:
                raise AssertionError("stop-guard fake turn never became steerable")
            await asyncio.sleep(0.02)
        stop_turn = hub.steering_state()["turn_id"]
        await hub.interrupt()
        stopped = await hub.steer(
            "ignore stop", "runner-steer-stopped", expected_turn_id=stop_turn)
        assert stopped["error"] == "the active turn is stopping"
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("stop-guard fake turn did not finish")
            await asyncio.sleep(0.02)

        # Hold the stdin serializer across one turn's completion and queued
        # successor startup. The late writer must compare the exposed turn id
        # again under the lock and must never land in the new process.
        assert hub.send_message("race source turn") == {"queued": False}
        deadline = time.monotonic() + 10
        while not hub.steering_state()["ready"]:
            if time.monotonic() >= deadline:
                raise AssertionError("race source never became steerable")
            await asyncio.sleep(0.02)
        race_turn = hub.steering_state()["turn_id"]
        race_generation = hub._turn_generation
        await hub._stdin_lock.acquire()
        try:
            assert hub.send_message("race successor turn") == {"queued": True}
            late_task = asyncio.create_task(hub.steer(
                "must not cross turns", "runner-steer-race",
                expected_turn_id=race_turn))
            await asyncio.sleep(0)
            while hub._turn_generation == race_generation or \
                    not hub._active_turn_id:
                if time.monotonic() >= deadline:
                    raise AssertionError("queued successor did not start")
                await asyncio.sleep(0.01)
        finally:
            hub._stdin_lock.release()
        raced = await late_task
        assert raced["error"] == \
            "the active turn changed before steering was delivered"
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("race successor did not finish")
            await asyncio.sleep(0.02)
        assert not any(event["kind"] == "user" and
                       event["data"].get("request_id") == "runner-steer-race"
                       for event in db.get_events(sid))

        # Stop immediately, before the subprocess has necessarily completed
        # initialize. The runner must defer the native request until the
        # app-server allocates its turn id, then close stdin on completion.
        assert hub.send_message("interrupt fake turn") == {"queued": False}
        await hub.interrupt()
        deadline = time.monotonic() + 10
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError("interrupted fake Codex turn did not finish")
            await asyncio.sleep(0.02)
        if hub.turn_task is not None:
            await hub.turn_task
        interrupted = [event for event in db.get_events(sid)
                       if event["kind"] == "info" and
                       event["data"].get("subtype") == "interrupted"]
        assert interrupted, db.get_events(sid)[-5:]

        invocations = [json.loads(line) for line in
                       log_path.read_text(encoding="utf-8").splitlines()]
        assert len(invocations) == 10, invocations
        first_thread = next(value for value in invocations[0]["seen"]
                            if value.get("id") == "puppy-thread")
        resumed_thread = next(value for value in invocations[1]["seen"]
                              if value.get("id") == "puppy-thread")
        assert first_thread["method"] == "thread/start"
        assert resumed_thread["method"] == "thread/resume"
        assert resumed_thread["params"]["threadId"] == "fake-thread"
        assert resumed_thread["params"]["excludeTurns"] is True
        for invocation, prompt in zip(invocations,
                                      ("first fake turn", "resumed fake turn")):
            assert prompt not in invocation["argv"]
            turn = next(value for value in invocation["seen"]
                        if value.get("id") == "puppy-turn")
            assert turn["params"]["input"][0]["text"].endswith(prompt)
        steer_invocation = next(invocation for invocation in invocations
            if any(value.get("method") == "turn/steer" and
                   value.get("params", {}).get("clientUserMessageId") ==
                       "runner-steer-1"
                   for value in invocation["seen"]))
        steer = next(value for value in steer_invocation["seen"]
                     if value.get("method") == "turn/steer")
        assert steer["id"] == "puppy-steer:runner-steer-1"
        interrupt_invocation = next(invocation for invocation in invocations
            if any(value.get("id") == "puppy-interrupt" for value in invocation["seen"])
            and any(value.get("method") == "turn/start" and
                    value.get("params", {}).get("input", [{}])[0].get("text", "")
                        .endswith("interrupt fake turn")
                    for value in invocation["seen"]))
        interrupt = next(value for value in interrupt_invocation["seen"]
                         if value.get("id") == "puppy-interrupt")
        assert interrupt == {
            "id": "puppy-interrupt", "method": "turn/interrupt",
            "params": {"threadId": "fake-thread", "turnId": "fake-turn"},
        }
    finally:
        if capture is not None:
            hub.detach(capture)
        driver.binary = original_binary
        if original_log is None:
            os.environ.pop("PUPPY_FAKE_CODEX_LOG", None)
        else:
            os.environ["PUPPY_FAKE_CODEX_LOG"] = original_log
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_claude_background_turn(root, runner, db, config) -> None:
    """Drive the real runner against a no-model fake claude whose background
    tasks follow the pinned stream-json contract.

    This pins the wait: stdin stays open across the model's answer while a
    task runs, the CLI's own wake-up continues the same turn, one folded result
    closes it, a stop or the turn timeout ends the wait through stdin EOF, a
    stale task reported by a resumed process never closes the prompt's turn
    early, and an error result never waits.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fake = root / "claude"
    log_path = root / "invocations.jsonl"
    fake.write_text(r'''#!/usr/bin/env python3
import json
import os
import select
import sys
import time

seen = []
flags = {}


def read():
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("unexpected stdin EOF")
    value = json.loads(line)
    seen.append(value)
    return value


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def stdin_eof(timeout):
    """True when the runner closes stdin within timeout seconds."""
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = select.select([sys.stdin], [], [], remaining)
        if not ready:
            return False
        line = sys.stdin.readline()
        if not line:
            return True
        if line.strip():
            seen.append(json.loads(line))


state = {"prompt": ""}


def finish(code=0):
    with open(os.environ["PUPPY_FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "seen": seen,
                                 "flags": flags, "prompt": state["prompt"]}) + "\n")
    raise SystemExit(code)


def prior_invocations(prompt):
    path = os.environ["PUPPY_FAKE_CLAUDE_LOG"]
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                if json.loads(line).get("prompt") == prompt:
                    count += 1
            except ValueError:
                pass
    return count


TRANSIENT = ("Failed to refresh OAuth token: another Claude Code process is "
             "refreshing it or exited mid-refresh. This is usually transient; retry "
             "in a minute, and if it persists close other Claude Code processes or "
             "sign in again")


argv = sys.argv[1:]
if argv[:1] != ["-p"] or "--input-format" not in argv:
    raise SystemExit("wrong argv: {!r}".format(argv))
if "--session-id" in argv:
    sid = argv[argv.index("--session-id") + 1]
else:
    sid = argv[argv.index("--resume") + 1]


def usage(inp, out):
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def result(num_turns, api_ms, cost, inp, out, text, is_error=False):
    send({"type": "result",
          "subtype": "error_during_execution" if is_error else "success",
          "is_error": is_error, "num_turns": num_turns, "duration_ms": 5,
          "duration_api_ms": api_ms, "total_cost_usd": cost,
          "stop_reason": "end_turn", "session_id": sid, "usage": usage(inp, out),
          "modelUsage": {"claude-fake": {"contextWindow": 200000}},
          "result": text})


def system(subtype, **fields):
    value = {"type": "system", "subtype": subtype, "session_id": sid}
    value.update(fields)
    send(value)


TASK = {"task_id": "btask1", "task_type": "local_bash",
        "description": "fake background command"}

init = read()
if init.get("type") != "control_request" or \
        init.get("request", {}).get("subtype") != "initialize":
    raise SystemExit("missing initialize: {!r}".format(init))
send({"type": "control_response", "response": {
    "subtype": "success", "request_id": init["request_id"], "response": {}}})
user = read()
prompt = user["message"]["content"][0]["text"]
state["prompt"] = prompt
if prompt.endswith("always transient fake turn") or \
        prompt.endswith("cancelled retry fake turn") or \
        (prompt.endswith("transient auth fake turn") and
         prior_invocations(prompt) == 0):
    # the CLI's OAuth refresh lock timeout: a synthetic error message in
    # the model's place, then an error result carrying the same text
    system("init", model="claude-fake", tools=[])
    send({"type": "user", "uuid": "u-t", "session_id": sid,
          "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
    send({"type": "assistant", "uuid": "a-t", "session_id": sid,
          "error": "server_error", "is_api_error_message": True, "message": {
              "model": "<synthetic>", "stop_reason": "stop_sequence",
              "content": [{"type": "text", "text": TRANSIENT}],
              "usage": usage(0, 0)}})
    send({"type": "result", "subtype": "success", "is_error": True,
          "num_turns": 1, "duration_ms": 5, "duration_api_ms": 0,
          "total_cost_usd": 0, "stop_reason": "stop_sequence", "session_id": sid,
          "usage": usage(0, 0), "modelUsage": {}, "result": TRANSIENT})
    for line in sys.stdin:
        if line.strip():
            seen.append(json.loads(line))
    finish()
if prompt.endswith("stale wakeup fake turn"):
    # a task the previous process left running is reported first, and its
    # wake-up query ends before the prompt is even replayed
    system("task_notification", task_id="bold1", status="stopped", output_file="",
           summary="No completion record was found for this background shell "
                   "command from the previous session.")
    system("init", model="claude-fake", tools=[])
    result(0, 0, 0.0, 0, 0, "")
    flags["early_close"] = stdin_eof(0.3)
system("init", model="claude-fake", tools=[])
send({"type": "user", "uuid": "u-1", "session_id": sid,
      "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
send({"type": "assistant", "uuid": "a-1", "session_id": sid, "message": {
    "model": "claude-fake", "content": [{"type": "text", "text": "first answer"}],
    "usage": usage(10, 2)}})
if prompt.endswith("error with tasks fake turn"):
    system("background_tasks_changed", tasks=[TASK])
    result(1, 100, 0.01, 10, 2, "boom", is_error=True)
    flags["closed_after_error"] = stdin_eof(3)
    finish()
if prompt.endswith("plain fake turn") or prompt.endswith("stale wakeup fake turn") \
        or prompt.endswith("transient auth fake turn"):
    result(1, 100, 0.01, 10, 2, "first answer")
else:
    system("background_tasks_changed", tasks=[TASK])
    system("task_started", task_id="btask1", tool_use_id="toolu_1",
           is_backgrounded=True, description=TASK["description"],
           task_type="local_bash")
    result(1, 100, 0.01, 10, 2, "first answer")
    if prompt.endswith("background fake turn"):
        if stdin_eof(0.5):
            # the runner gave up on the task: a real CLI would kill it now
            flags["early_close"] = True
            system("background_tasks_changed", tasks=[])
            system("task_notification", task_id="btask1", status="stopped",
                   output_file="", summary=TASK["description"])
            finish()
        system("background_tasks_changed", tasks=[])
        system("task_updated", task_id="btask1", patch={"status": "completed"})
        system("task_notification", task_id="btask1", tool_use_id="toolu_1",
               status="completed", output_file="/tmp/fake.output",
               summary='Background command "fake background command" '
                       'completed (exit code 0)')
        system("init", model="claude-fake", tools=[])
        send({"type": "assistant", "uuid": "a-2", "session_id": sid, "message": {
            "model": "claude-fake",
            "content": [{"type": "text", "text": "continued after task"}],
            "usage": usage(5, 3)}})
        result(1, 250, 0.02, 5, 3, "continued after task")
    else:
        # "stop while waiting" and "timeout while waiting": the task never ends
        flags["eof_while_waiting"] = stdin_eof(15)
        system("background_tasks_changed", tasks=[])
        system("task_notification", task_id="btask1", tool_use_id="toolu_1",
               status="stopped", output_file="", summary=TASK["description"])
        finish()
for line in sys.stdin:
    if line.strip():
        seen.append(json.loads(line))
finish()
''', encoding="utf-8")
    fake.chmod(0o755)

    from puppy.drivers import get_driver
    driver = get_driver("claude")
    original_binary = driver.binary
    original_log = os.environ.get("PUPPY_FAKE_CLAUDE_LOG")
    original_timeout = config.get("sessions.turn_timeout")
    original_retry = (runner.ENGINE_RETRY_DELAYS, runner.ENGINE_MAX_ATTEMPTS)
    driver.binary = str(fake)
    os.environ["PUPPY_FAKE_CLAUDE_LOG"] = str(log_path)
    sid = db.create_session(
        "claude background fake", "claude", str(root), "", "", "#7aa2f7", "auto")
    hub = runner.hub(sid)

    class Capture:
        def __init__(self):
            self.messages = []

        async def send_json(self, value):
            self.messages.append(value)

    capture = Capture()
    hub.attach(capture)

    async def finish_turn(label):
        deadline = time.monotonic() + 20
        while hub.status != "idle":
            if time.monotonic() >= deadline:
                raise AssertionError(label + " did not finish")
            await asyncio.sleep(0.02)
        if hub.turn_task is not None:
            await hub.turn_task

    def turn_events(prompt):
        events = db.get_events(sid)
        start = max(index for index, event in enumerate(events)
                    if event["kind"] == "user" and
                    event["data"].get("text") == prompt)
        return events[start:]

    def shape(events):
        return [(event["kind"], event["data"].get("subtype", ""))
                for event in events]

    def last_invocation():
        return json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])

    try:
        # Unlimited must run normally, including background wake-ups.
        config.set_timeouts({"turn_seconds": 0})
        # an ordinary turn establishes the native session
        assert hub.send_message("plain fake turn") == {"queued": False}
        await finish_turn("plain fake turn")
        assert shape(turn_events("plain fake turn")) == [
            ("user", ""), ("assistant", ""), ("result", "")]
        assert db.get_session(sid)["native_session_id"]

        # the CLI wakes the model for a finished task in the same process
        assert hub.send_message("background fake turn") == {"queued": False}
        await finish_turn("background fake turn")
        events = turn_events("background fake turn")
        assert shape(events) == [
            ("user", ""), ("assistant", ""), ("info", "background_wait"),
            ("info", "task"), ("assistant", ""), ("result", "")], shape(events)
        assert events[2]["data"]["text"] == \
            "Waiting for 1 background task: fake background command"
        assert events[2]["data"]["tasks"] == [{
            "id": "btask1", "type": "local_bash",
            "description": "fake background command"}]
        assert events[3]["data"]["status"] == "completed"
        assert events[3]["data"]["text"] == \
            'Background command "fake background command" completed (exit code 0)'
        assert events[4]["data"]["text"] == "continued after task"
        result = events[5]["data"]
        assert result["ok"] is True and result["wakeups"] == 1
        assert result["usage"] == {
            "input_tokens": 15, "output_tokens": 5,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        assert result["num_turns"] == 2 and result["api_duration_ms"] == 250
        # The unified duration includes the 0.5s background wait; Claude's
        # API-only 250ms remains separately available and is never mislabeled.
        assert result["duration_ms"] >= 450, result["duration_ms"]
        assert result["cost_usd"] == 0.02
        assert result["native_prompt_id"] == "u-1"
        assert result["native_tail_id"] == "a-2"
        assert hub.last_completion_status == "ok"
        waiting = [message for message in capture.messages
                   if message.get("type") == "background_tasks" and
                   message.get("waiting")]
        assert waiting and waiting[0]["tasks"][0]["id"] == "btask1"
        assert waiting[0]["text"] == \
            "Waiting for 1 background task: fake background command · Stop ends the wait"
        assert any(message.get("type") == "status" and
                   message.get("text") == waiting[0]["text"]
                   for message in capture.messages)
        assert any(message.get("type") == "background_tasks" and
                   not message.get("waiting") for message in capture.messages)
        assert last_invocation()["flags"].get("early_close") is not True

        # a stop during the wait ends the tasks through EOF; the answer stands
        capture.messages.clear()
        assert hub.send_message("stop while waiting fake turn") == {"queued": False}
        deadline = time.monotonic() + 20
        while hub._bg_wait_since is None:
            if time.monotonic() >= deadline:
                raise AssertionError("fake turn never paused on its task")
            await asyncio.sleep(0.02)
        snapshot = hub.snapshot()["background_tasks"]
        assert snapshot["waiting"] is True
        assert snapshot["tasks"][0]["id"] == "btask1"
        assert snapshot["text"].endswith("Stop ends the wait")
        assert hub.steering_state()["ready"] is False
        await hub.interrupt()
        await finish_turn("stop while waiting fake turn")
        events = turn_events("stop while waiting fake turn")
        assert shape(events) == [
            ("user", ""), ("assistant", ""), ("info", "background_wait"),
            ("info", "task"), ("info", "interrupted"), ("result", "")], shape(events)
        assert events[3]["data"]["status"] == "stopped"
        assert events[4]["data"]["text"] == \
            "Stopped waiting for background tasks; the engine ended them"
        assert events[5]["data"]["ok"] is True
        assert events[5]["data"]["usage"]["input_tokens"] == 10
        assert hub.last_completion_status == "interrupted"
        assert last_invocation()["flags"]["eof_while_waiting"] is True
        assert any(message.get("type") == "status" and
                   message.get("text") == "Ending the wait for background tasks..."
                   for message in capture.messages)

        # the turn timeout ends a wait gracefully instead of killing the engine
        config.set_value("sessions.turn_timeout", 2)
        try:
            assert hub.send_message("timeout while waiting fake turn") == \
                {"queued": False}
            await finish_turn("timeout while waiting fake turn")
        finally:
            config.set_value("sessions.turn_timeout", original_timeout)
        events = turn_events("timeout while waiting fake turn")
        assert shape(events) == [
            ("user", ""), ("assistant", ""), ("info", "background_wait"),
            ("info", "background_wait_stopped"), ("info", "task"),
            ("result", "")], shape(events)
        assert "turn timeout" in events[3]["data"]["text"]
        assert events[5]["data"]["ok"] is True
        assert hub.last_completion_status == "ok"
        assert last_invocation()["flags"]["eof_while_waiting"] is True

        # a stale task from the previous process never closes the prompt's turn
        assert hub.send_message("stale wakeup fake turn") == {"queued": False}
        await finish_turn("stale wakeup fake turn")
        events = turn_events("stale wakeup fake turn")
        assert shape(events) == [
            ("user", ""), ("assistant", ""), ("result", "")], shape(events)
        assert events[2]["data"]["native_prompt_id"] == "u-1"
        assert events[2]["data"]["num_turns"] == 1
        assert events[2]["data"]["usage"]["input_tokens"] == 10
        invocation = last_invocation()
        assert invocation["flags"]["early_close"] is False
        assert "--resume" in invocation["argv"]

        # an error result never waits on background work
        assert hub.send_message("error with tasks fake turn") == {"queued": False}
        await finish_turn("error with tasks fake turn")
        events = turn_events("error with tasks fake turn")
        assert shape(events) == [
            ("user", ""), ("assistant", ""), ("result", "")], shape(events)
        assert events[2]["data"]["ok"] is False
        assert last_invocation()["flags"]["closed_after_error"] is True
        assert hub.last_completion_status == "error"

        def invocations_for(prompt):
            rows = [json.loads(line) for line in
                    log_path.read_text(encoding="utf-8").splitlines()]
            return [row for row in rows if row.get("prompt") == prompt]

        # a transient engine failure retries on its own: no second user row,
        # no lost-login evidence, no model-move noise, one result at the end
        runner.ENGINE_RETRY_DELAYS = (0.3, 0.3)
        runner.ENGINE_MAX_ATTEMPTS = 3
        capture.messages.clear()
        assert hub.send_message("transient auth fake turn") == {"queued": False}
        await finish_turn("transient auth fake turn")
        events = turn_events("transient auth fake turn")
        assert shape(events) == [
            ("user", ""), ("error", "engine_api_error"), ("info", "engine_retry"),
            ("assistant", ""), ("result", "")], shape(events)
        assert events[1]["data"]["code"] == "server_error"
        assert "refresh OAuth token" in events[1]["data"]["text"]
        assert events[2]["data"]["attempt"] == 2
        assert events[4]["data"]["ok"] is True
        assert db.meta_get("auth_evidence.claude") is None
        assert hub.last_completion_status == "ok"
        assert any(message.get("type") == "status" and
                   str(message.get("text") or "").startswith("Retrying in")
                   for message in capture.messages)
        retried = invocations_for("transient auth fake turn")
        assert len(retried) == 2, retried
        assert "--resume" in retried[1]["argv"]

        # every attempt failing ends with the honest error, still not as lost login
        assert hub.send_message("always transient fake turn") == {"queued": False}
        await finish_turn("always transient fake turn")
        events = turn_events("always transient fake turn")
        assert shape(events) == [
            ("user", ""), ("error", "engine_api_error"), ("info", "engine_retry"),
            ("error", "engine_api_error"), ("info", "engine_retry"),
            ("error", "engine_api_error"), ("result", "")], shape(events)
        assert events[4]["data"]["attempt"] == 3
        assert events[6]["data"]["ok"] is False
        assert "refresh OAuth token" in events[6]["data"]["error"]
        assert db.meta_get("auth_evidence.claude") is None
        assert hub.last_completion_status == "error"
        assert len(invocations_for("always transient fake turn")) == 3

        # a stop during the back-off cancels the retry like any interrupted turn
        runner.ENGINE_RETRY_DELAYS = (5.0, 5.0)
        capture.messages.clear()
        assert hub.send_message("cancelled retry fake turn") == {"queued": False}
        deadline = time.monotonic() + 20
        while not any(message.get("type") == "status" and
                      str(message.get("text") or "").startswith("Retrying in")
                      for message in capture.messages):
            if time.monotonic() >= deadline:
                raise AssertionError("the retry back-off never started")
            await asyncio.sleep(0.02)
        await hub.interrupt()
        await finish_turn("cancelled retry fake turn")
        events = turn_events("cancelled retry fake turn")
        assert shape(events) == [
            ("user", ""), ("error", "engine_api_error"), ("info", "engine_retry"),
            ("info", "interrupted")], shape(events)
        assert events[3]["data"]["text"] == "Retry cancelled by user"
        assert hub.last_completion_status == "interrupted"
        assert len(invocations_for("cancelled retry fake turn")) == 1
    finally:
        hub.detach(capture)
        driver.binary = original_binary
        runner.ENGINE_RETRY_DELAYS, runner.ENGINE_MAX_ATTEMPTS = original_retry
        if original_log is None:
            os.environ.pop("PUPPY_FAKE_CLAUDE_LOG", None)
        else:
            os.environ["PUPPY_FAKE_CLAUDE_LOG"] = original_log
        config.set_value("sessions.turn_timeout", original_timeout)
        runner.drop_hub(sid)
        db.delete_session(sid)


def exercise_opencode_driver() -> None:
    """Pin catalog parsing and the full no-model ACP handshake/state machine."""
    from puppy import runner
    import puppy.drivers.opencode as opencode_module
    from puppy.drivers.opencode import OpenCodeDriver, _display_provider, parse_model_catalog
    catalog = parse_model_catalog("""provider/model-a
{
  "id": "model-a",
  "providerID": "provider",
  "name": "Model A",
  "limit": {"context": 128000, "output": 8192},
  "capabilities": {"input": {"text": true, "image": true}},
  "variants": {"high": {}, "max": {}}
}
second/model-b
{
  "id": "model-b",
  "providerID": "second",
  "name": "Model B",
  "variants": {}
}
""")
    assert [model["value"] for model in catalog] == \
        ["provider/model-a", "second/model-b"]
    assert catalog[0]["label"] == "Model A"
    assert "128k context" in catalog[0]["hint"] and "image input" in catalog[0]["hint"]
    assert [item["value"] for item in catalog[0]["effort_options"]] == \
        ["", "high", "max"]

    driver = OpenCodeDriver()
    driver._model_catalog_state().ingest(
        driver._catalog_options(catalog), source="engine")
    exposed = driver.model_options()
    assert [model["value"] for model in exposed] == \
        ["", "provider/model-a", "second/model-b"]
    assert exposed[0]["label"] == "Default"
    assert exposed[1]["label"] == "Provider · Model A"
    assert _display_provider("opencode") == "OpenCode"
    assert driver.default_model() == ""
    assert runner._starts_fresh_native_session(
        {"native_session_id": ""}, False, driver) is True
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, False, driver) is False
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, True, driver) is True
    assert runner._starts_fresh_native_session(
        {"native_session_id": "ses_existing"}, True, CodexDriver()) is False
    usage_root = Path(tempfile.mkdtemp(prefix="opencode-usage-"))
    previous_xdg = os.environ.get("XDG_DATA_HOME")
    try:
        usage_dir = usage_root / "opencode"
        usage_dir.mkdir()
        connection = sqlite3.connect(str(usage_dir / "opencode.db"))
        connection.execute(
            "CREATE TABLE session(id TEXT PRIMARY KEY,tokens_input INTEGER,"
            "tokens_output INTEGER,tokens_reasoning INTEGER,"
            "tokens_cache_read INTEGER,tokens_cache_write INTEGER)")
        connection.execute(
            "INSERT INTO session VALUES(?,?,?,?,?,?)",
            ("ses-counted", 11, 12, 13, 14, 15))
        connection.commit()
        connection.close()
        os.environ["XDG_DATA_HOME"] = str(usage_root)
        assert opencode_module._session_totals("ses-counted") == {
            "input_tokens": 11, "output_tokens": 25,
            "reasoning_output_tokens": 13,
            "cache_read_input_tokens": 14,
            "cache_creation_input_tokens": 15,
        }
        assert opencode_module._covers_request(
            {"input_tokens": 20, "output_tokens": 9},
            {"input_tokens": 7, "output_tokens": 9})
        assert not opencode_module._covers_request(
            {"input_tokens": 0, "output_tokens": 0},
            {"input_tokens": 7, "output_tokens": 9})
    finally:
        if previous_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = previous_xdg
        shutil.rmtree(usage_root, ignore_errors=True)
    session = {
        "cwd": "/tmp", "native_session_id": "", "model": "provider/model-a",
        "effort": "high", "permission_mode": "manual",
    }
    browser = {
        "name": "puppy_browser", "command": "/tmp/browser-agent",
        "args": ["--session", "9"], "env": {"PUPPY_SOCKET": "/tmp/socket"},
        "engine_guidance": "Use the shared browser.",
    }
    terminal_mcp = {
        "name": "puppy_terminal", "command": "/tmp/terminal-agent",
        "args": [], "env": {"PUPPY_TERMINAL_SOCKET": "/tmp/terminal-socket"},
        "engine_guidance": "Use the shared terminal only when requested.",
    }
    inline = json.loads(driver.build_env(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=terminal_mcp,
        system_prompt="Be concise.")["OPENCODE_CONFIG_CONTENT"])
    agent = inline["agent"]["puppy_console"]
    assert inline["default_agent"] == "puppy_console"
    assert "Be concise." in agent["prompt"] and "Use the shared browser." in agent["prompt"]
    assert "Use the shared terminal only when requested." in agent["prompt"]
    assert agent["permission"]["*"] == "ask" and agent["permission"]["read"] == "allow"

    ctx = driver.turn_context(session, True, "hello", "pin", browser_mcp=browser,
                              terminal_mcp=terminal_mcp)
    assert ctx["mcp_servers"] == [{
        "name": "puppy_browser", "command": "/tmp/browser-agent",
        "args": ["--session", "9"],
        "env": [{"name": "PUPPY_SOCKET", "value": "/tmp/socket"}],
    }, {
        "name": "puppy_terminal", "command": "/tmp/terminal-agent",
        "args": [],
        "env": [{"name": "PUPPY_TERMINAL_SOCKET", "value": "/tmp/terminal-socket"}],
    }]
    initial = driver.initial_stdin(session, "hello")[0]
    assert initial["method"] == "initialize" and initial["params"]["protocolVersion"] == 1
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:initialize", "result": {
            "agentCapabilities": {"loadSession": True}}}), ctx)
    assert actions[0]["data"]["method"] == "session/new"

    options = [
        {"id": "model", "currentValue": "provider/default",
         "options": [{"value": "provider/default"}, {"value": "provider/model-a"}]},
        {"id": "mode", "currentValue": "build",
         "options": [{"value": "build"}, {"value": "puppy_console"}]},
    ]
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:session",
        "result": {"sessionId": "ses_test", "configOptions": options}}), ctx)
    assert actions[0] == {"a": "native_id", "id": "ses_test"}
    assert actions[1]["data"]["params"]["configId"] == "mode"

    options[1]["currentValue"] = "puppy_console"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config", "result": {"configOptions": options}}), ctx)
    assert actions[-1]["data"]["params"] == {
        "sessionId": "ses_test", "configId": "model", "value": "provider/model-a"}

    model_options = [
        {"id": "model", "currentValue": "provider/model-a"},
        {"id": "effort", "currentValue": "none",
         "options": [{"value": "none"}, {"value": "high"}]},
        {"id": "mode", "currentValue": "puppy_console"},
    ]
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config",
        "result": {"configOptions": model_options}}), ctx)
    assert actions[0] == {"a": "model", "model": "provider/model-a"}
    assert actions[-1]["data"]["params"]["configId"] == "effort"
    model_options[1]["currentValue"] = "high"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:config",
        "result": {"configOptions": model_options}}), ctx)
    assert actions[-1]["data"]["method"] == "session/prompt"
    assert actions[-1]["data"]["params"]["prompt"] == [{"type": "text", "text": "hello"}]
    assert driver.supports_steering is True
    assert driver.steering_acknowledged is True
    assert driver.steer_ready(session, ctx) is True
    assert driver.steer_payload(
        session, ctx, "new constraint", "steer-oc-1") == {
            "jsonrpc": "2.0", "id": "puppy:steer:steer-oc-1",
            "method": "session/prompt", "params": {
                "sessionId": "ses_test",
                "prompt": [{"type": "text", "text": "new constraint"}],
            },
        }
    assert driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:steer:steer-oc-1",
        "result": {"stopReason": "end_turn"},
    }), ctx) == [{
        "a": "steer_result", "request_id": "steer-oc-1",
        "ok": True, "error": "",
    }]
    assert driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:steer:steer-oc-err",
        "error": {"code": -32000, "message": "session stopped"},
    }), ctx) == [{
        "a": "steer_result", "request_id": "steer-oc-err",
        "ok": False, "error": "session stopped",
    }]

    update = lambda value: json.dumps({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": "ses_test", "update": value}})
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "considering"}}), ctx)
    assert actions[-1]["msg"] == {"type": "delta", "block": "thinking",
                                   "text": "considering"}
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Before tool."}}), ctx)
    assert actions[0]["kind"] == "thinking"
    actions = driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": " More."}}), ctx)
    assert actions[-1]["msg"]["text"] == " More."
    actions = driver.parse_line(update({
        "sessionUpdate": "tool_call", "toolCallId": "call-1", "kind": "execute",
        "title": "Run check", "status": "in_progress", "rawInput": {"command": "true"}}), ctx)
    assert [action.get("kind") for action in actions if action.get("a") == "event"] == \
        ["assistant", "tool_use"]
    assert actions[0]["data"]["text"] == "Before tool. More."
    actions = driver.parse_line(update({
        "sessionUpdate": "tool_call_update", "toolCallId": "call-1", "kind": "execute",
        "title": "Run check", "status": "completed", "rawOutput": {"output": "ok"}}), ctx)
    assert actions[-1]["kind"] == "tool_result" and actions[-1]["data"]["is_error"] is False

    approval = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
        "params": {"sessionId": "ses_test", "toolCall": {
            "toolCallId": "call-2", "kind": "execute", "title": "Run command",
            "rawInput": {"command": "make test"}}, "options": [
                {"optionId": "once", "kind": "allow_once"},
                {"optionId": "always", "kind": "allow_always"},
                {"optionId": "reject", "kind": "reject_once"},
            ]}}), ctx)[0]["req"]
    assert approval["request_id"] == "7" and approval["suggestions"][0]["type"] == "allowAlways"
    reply = driver.approval_payload(
        "7", "allow", approval["input"], updated_permissions=[{"type": "allowAlways"}],
        request=approval)
    assert reply == {"jsonrpc": "2.0", "id": 7,
                     "result": {"outcome": {"outcome": "selected", "optionId": "always"}}}

    usage_action = driver.parse_line(update({
        "sessionUpdate": "usage_update", "used": 1000, "size": 2000}), ctx)
    assert usage_action == [{"a": "transient", "msg": {
        "type": "context_tokens", "tokens": 1000}}]

    driver.parse_line(update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "Done."}}), ctx)
    ctx["usage_baseline"] = {
        "input_tokens": 100, "output_tokens": 12,
        "reasoning_output_tokens": 2, "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 5,
    }
    original_totals = opencode_module._session_totals
    opencode_module._session_totals = lambda _sid: {
        "input_tokens": 130, "output_tokens": 23,
        "reasoning_output_tokens": 5, "cache_read_input_tokens": 590,
        "cache_creation_input_tokens": 7,
    }
    try:
        actions = driver.parse_line(json.dumps({
            "jsonrpc": "2.0", "id": "puppy:prompt", "result": {
                "stopReason": "end_turn", "usage": {
                    "inputTokens": 10, "outputTokens": 4,
                    "totalTokens": 14}}}), ctx)
    finally:
        opencode_module._session_totals = original_totals
    assert actions[0] == {"a": "event", "kind": "assistant", "data": {"text": "Done."}}
    assert actions[-1]["data"]["usage"] == {
        "input_tokens": 30, "output_tokens": 11,
        "reasoning_output_tokens": 3, "cache_read_input_tokens": 90,
        "cache_creation_input_tokens": 2}
    assert actions[-1]["data"]["usage_scope"] == "turn"
    assert actions[-1]["data"]["context_used"] == 1004
    assert actions[-1]["data"]["context_window"] == 2000
    assert driver.steer_ready(session, ctx) is False
    assert driver.steer_payload(session, ctx, "too late", "steer-oc-2") is None

    fallback_ctx = driver.turn_context(session, True, "limited", "pin-fallback")
    fallback_ctx.update(phase="prompt", session_id="definitely-not-a-session")
    limited = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:prompt", "result": {
            "stopReason": "max_tokens", "usage": {
                "inputTokens": 7, "outputTokens": 9,
                "thoughtTokens": 3, "totalTokens": 19}}}), fallback_ctx)[-1]["data"]
    assert limited["ok"] is False and limited["stop_reason"] == "max_tokens"
    assert limited["usage_scope"] == "last_request"
    assert limited["usage"] == {
        "input_tokens": 7, "output_tokens": 12, "total_tokens": 19,
        "reasoning_output_tokens": 3}

    resumed = dict(session, native_session_id="ses_existing", effort="")
    resume_ctx = driver.turn_context(resumed, False, "again", "pin")
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:initialize", "result": {}}), resume_ctx)
    assert actions[0]["data"]["method"] == "session/resume"
    actions = driver.parse_line(json.dumps({
        "jsonrpc": "2.0", "id": "puppy:session",
        "error": {"code": -32601, "message": "Method not found"}}), resume_ctx)
    assert actions[0]["data"]["method"] == "session/load"


async def exercise_opencode_binary_fallback(root) -> None:
    """The official per-user install must work outside a login-shell PATH."""
    from puppy import cli_upgrade
    from puppy.drivers import base as driver_base
    from puppy.drivers.opencode import OpenCodeDriver

    root = Path(root)
    home = root / "home"
    binary = home / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True)
    binary.write_text("""#!{python}
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("1.2.3")
elif sys.argv[1:] == ["models", "--verbose", "--refresh"]:
    print("fallback/model-a")
    print(json.dumps({{
        "id": "model-a", "providerID": "fallback", "name": "Model A",
        "variants": {{"high": {{}}}},
    }}))
else:
    raise SystemExit(2)
""".format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)

    saved_home = os.environ.get("HOME")
    saved_path = os.environ.get("PATH")
    try:
        os.environ["HOME"] = str(home)
        # Deliberately exclude the fake install: this is the systemd case.
        os.environ["PATH"] = "/usr/bin:/bin"
        driver = OpenCodeDriver()
        assert driver.resolved_binary() == str(binary)

        driver_base.invalidate_status("opencode")
        status = await driver.status()
        assert status["installed"] is True
        assert status["version"] == "1.2.3"
        assert status["auth"] == "ok" and status["detail"] == "binary available"

        await driver.refresh_model_options(force=True)
        assert [item["value"] for item in driver.model_options()] == \
            ["", "fallback/model-a"]
        session = {"cwd": "/tmp"}
        assert driver.build_cmd(session, True, "hello", "pin")[:2] == \
            [str(binary), "acp"]
        assert cli_upgrade._argv(driver) == [str(binary), "upgrade"]
    finally:
        driver_base.invalidate_status("opencode")
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
        if saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved_path


async def exercise_agent_notes(http, url, headers, pinned, session, cwd: Path) -> None:
    """AGENTS.md / CLAUDE.md in the session's working directory: listed on
    every session payload, read and replaced only by those two names, written
    through a symlink to its target, bounded, and removable."""
    sid = session["id"]
    notes_url = url + f"/api/sessions/{sid}/agent-notes"

    async def listed():
        async with http.get(url + "/api/sessions", headers=headers, ssl=pinned) as response:
            rows = (await response.json())["sessions"]
        return next(row["agent_notes"] for row in rows if row["id"] == sid)

    async with http.get(notes_url, headers=headers, ssl=pinned) as response:
        data = await response.json()
        assert response.status == 200, data
    assert [f["name"] for f in data["files"]] == ["AGENTS.md", "CLAUDE.md"]
    assert all(f["exists"] is False for f in data["files"]) and data["present"] == []
    assert await listed() == []
    assert session["agent_notes"] == []

    text = "# Project notes\n\nKeep answers brief.\n"
    async with http.put(notes_url, headers=headers, ssl=pinned,
                        json={"name": "AGENTS.md", "text": text}) as response:
        written = await response.json()
        assert response.status == 200, written
    assert written["present"] == ["AGENTS.md"]
    assert (cwd / "AGENTS.md").read_text(encoding="utf-8") == text
    assert await listed() == ["AGENTS.md"]
    async with http.get(url + f"/api/sessions/{sid}", headers=headers, ssl=pinned) as response:
        assert (await response.json())["session"]["agent_notes"] == ["AGENTS.md"]

    # a CLAUDE.md that links to AGENTS.md is a common layout: it reads through
    # and writes through, and the link itself survives the write
    (cwd / "CLAUDE.md").symlink_to("AGENTS.md")
    async with http.get(notes_url, headers=headers, ssl=pinned) as response:
        data = await response.json()
    linked = data["files"][1]
    assert linked["exists"] is True and linked["symlink"] == "AGENTS.md"
    assert linked["text"] == text and data["present"] == ["AGENTS.md", "CLAUDE.md"]
    async with http.put(notes_url, headers=headers, ssl=pinned,
                        json={"name": "CLAUDE.md", "text": "via the link\n"}) as response:
        assert response.status == 200, await response.text()
    assert (cwd / "CLAUDE.md").is_symlink()
    assert (cwd / "AGENTS.md").read_text(encoding="utf-8") == "via the link\n"

    for body, status in (({"name": "README.md", "text": "x"}, 400),
                         ({"name": "../AGENTS.md", "text": "x"}, 400),
                         ({"name": "AGENTS.md", "text": "x" * (256 * 1024 + 1)}, 413),
                         ({"name": "AGENTS.md", "text": 7}, 400),
                         ([1], 400)):
        async with http.put(notes_url, headers=headers, ssl=pinned, json=body) as response:
            assert response.status == status, (body if not isinstance(body, dict) else body.get("name"),
                                               response.status, await response.text())
    assert (cwd / "AGENTS.md").read_text(encoding="utf-8") == "via the link\n"

    # Sharing is detected from the files, split on Save, and re-created only
    # when no independent instructions would be lost. Old revisions conflict.
    async with http.get(notes_url, headers=headers, ssl=pinned) as response:
        shared = (await response.json())["shared"]
    assert shared["linked"] and shared["available"]
    async with http.put(notes_url, headers=headers, ssl=pinned, json={
            "shared": False, "expected_revision": shared["revision"],
            "texts": {"AGENTS.md": "via the link\n", "CLAUDE.md": "separate\n"}}) as response:
        split = await response.json()
        assert response.status == 200, split
    assert not (cwd / "CLAUDE.md").is_symlink()
    assert not split["shared"]["linked"] and not split["shared"]["available"]
    async with http.put(notes_url, headers=headers, ssl=pinned, json={
            "shared": True, "text": "replacement", "expected_revision": split["shared"]["revision"]}) as response:
        assert response.status == 409, await response.text()
    assert (cwd / "CLAUDE.md").read_text() == "separate\n"
    async with http.put(notes_url, headers=headers, ssl=pinned,
                        json={"name": "CLAUDE.md", "text": ""}) as response:
        empty = await response.json()
        assert response.status == 200, empty
    async with http.put(notes_url, headers=headers, ssl=pinned, json={
            "shared": True, "text": "replacement", "expected_revision": shared["revision"]}) as response:
        assert response.status == 409, await response.text()
    async with http.put(notes_url, headers=headers, ssl=pinned, json={
            "shared": True, "text": "via the link\n", "expected_revision": empty["shared"]["revision"]}) as response:
        joined = await response.json()
        assert response.status == 200, joined
    assert joined["shared"]["linked"] and (cwd / "CLAUDE.md").is_symlink()

    async with http.put(notes_url, headers=headers, ssl=pinned,
                        json={"name": "CLAUDE.md", "delete": True}) as response:
        removed = await response.json()
        assert response.status == 200, removed
    assert not (cwd / "CLAUDE.md").exists() and (cwd / "AGENTS.md").exists()
    assert removed["present"] == ["AGENTS.md"]
    async with http.put(notes_url, headers=headers, ssl=pinned,
                        json={"name": "AGENTS.md", "delete": True}) as response:
        assert (await response.json())["present"] == []
    assert await listed() == []
    async with http.get(url + "/api/sessions/999999/agent-notes",
                        headers=headers, ssl=pinned) as response:
        assert response.status == 404


def exercise_activity_blocks(session_hub_cls) -> None:
    """Queued turns retain one start time and become idle only after the tail."""
    hub = session_hub_cls(-1)
    started = time.time() - 42
    hub.status = "running"
    hub.active_since = started
    hub.queue = ["next queued turn"]
    assert hub._take_next_turn() == "next queued turn"
    assert hub.status == "running" and hub.active_since == started
    assert hub._take_next_turn() is None
    assert hub.status == "idle" and hub.active_since is None


def exercise_health_retry_bound(backends_module) -> None:
    """Offline recovery backs off, but never strands a revived node for a minute."""
    bid = -9103
    try:
        backends_module._clear_backend_health(bid)
        delays = []
        for _ in range(4):
            # Make each synthetic failure due; repeated in-flight failures are
            # separately coalesced by _mark_backend_offline's future deadline.
            backends_module._health_retry_after[bid] = 0
            started = time.monotonic()
            backends_module._mark_backend_offline(bid, "test node unavailable")
            delays.append(backends_module._health_retry_after[bid] - started)
        assert [round(delay) for delay in delays] == [2, 4, 8, 8], delays
        assert max(delays) < 8.1, delays
    finally:
        backends_module._clear_backend_health(bid)


def exercise_update_revision_epoch(runner_module) -> None:
    """Replacing a remote runtime never rewinds the controller topic clock."""
    runner_module.configure_updates("controller-epoch-test")
    first = runner_module.publish_state(
        {"type": "remote_state", "backend_id": 7,
         "event": {"type": "sessions"}},
        topic="remote:7:sessions")
    unchanged = runner_module.publish_state(
        {"type": "remote_state", "backend_id": 7,
         "event": {"type": "sessions"}},
        topic="remote:7:sessions")
    assert unchanged["state_revision"] == first["state_revision"]
    runner_module.clear_published_state("remote:7:")
    replacement = runner_module.publish_state(
        {"type": "remote_state", "backend_id": 7,
         "event": {"type": "sessions", "runtime_id": "replacement"}},
        topic="remote:7:sessions")
    assert replacement["runtime_id"] == "controller-epoch-test"
    assert replacement["state_topic"] == "remote:7:sessions"
    assert replacement["state_revision"] == first["state_revision"] + 1


def exercise_restore_stream_clear(backends_module, runner_module) -> None:
    """Restore drops cached state even for a currently disconnected backend."""
    runner_module.configure_updates("restore-cache-epoch")
    runner_module.publish_state(
        {"type": "remote_stream", "backend_id": 77, "connected": False},
        topic="remote:77:stream")
    runner_module.publish_state(
        {"type": "remote_state", "backend_id": 77,
         "event": {"type": "sessions", "sessions": []}},
        topic="remote:77:sessions")
    assert any(key.startswith("remote:77:") for key in runner_module._update_state)
    backends_module.reset_auto_upgrade_schedule()
    assert not any(key.startswith("remote:") for key in runner_module._update_state)


async def exercise_update_stream_ordering(runner_module) -> None:
    """Slow viewers coalesce state, but an edge remains an ordering barrier."""
    class SlowCapture:
        def __init__(self):
            self.messages = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_json(self, payload):
            self.messages.append(payload)
            if len(self.messages) == 1:
                self.started.set()
                await self.release.wait()

        async def close(self, **_kwargs):
            self.release.set()

    runner_module.configure_updates("queue-epoch-test")
    capture = SlowCapture()
    runner_module.updates_attach(capture)
    try:
        await asyncio.wait_for(capture.started.wait(), timeout=1)
        runner_module.publish_state({"type": "probe", "value": 1})
        runner_module.publish_state({"type": "probe", "value": 2})
        runner_module.broadcast_update({"type": "edge", "value": "between"})
        runner_module.publish_state({"type": "probe", "value": 3})
        capture.release.set()
        deadline = asyncio.get_event_loop().time() + 1
        while len(capture.messages) < 5 and \
                asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0)
        kinds = [message["type"] for message in capture.messages]
        assert kinds == ["updates_ready", "sessions", "probe", "edge", "probe"], \
            capture.messages
        probes = [message for message in capture.messages
                  if message["type"] == "probe"]
        assert [(item["value"], item["state_revision"])
                for item in probes] == [(2, 2), (3, 3)]
    finally:
        runner_module.updates_detach(capture)


async def exercise_state_topic_isolation(state_stream_module, runner_module) -> None:
    """A slow or failed probe cannot hold independent node state behind it."""
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    async def builder(_app, topics, _probes):
        topic = next(iter(topics))
        if topic == "engines":
            slow_started.set()
            await release_slow.wait()
        if topic == "broken":
            raise RuntimeError("synthetic isolated probe failure")
        return [{"type": topic, "value": topic}]

    runner_module.configure_updates("parallel-snapshot-epoch")
    publisher = state_stream_module._Publisher(
        {}, builder, lambda: 60,
        ("node", "engines", "browser_status", "broken"),
        ("node", "engines", "browser_status"))
    refresh = asyncio.create_task(publisher.refresh(None, probes=True))
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    for _ in range(20):
        if "node" in runner_module._update_state and \
                "browser_status" in runner_module._update_state:
            break
        await asyncio.sleep(0)
    assert "node" in runner_module._update_state
    assert "browser_status" in runner_module._update_state
    assert "engines" not in runner_module._update_state
    publisher.invalidate()
    release_slow.set()
    await asyncio.wait_for(refresh, timeout=1)
    assert "engines" not in runner_module._update_state

    await publisher.refresh({"engines"}, probes=True)
    assert runner_module._update_state["engines"]["value"] == "engines"


async def exercise_shutdown_broadcast(runner_module) -> None:
    """Both node and open-session watchers receive the same bounded notice."""
    class Capture:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    hub_id = -9002
    hub = runner_module.SessionHub(hub_id)
    updates = Capture()
    session = Capture()
    runner_module._hubs[hub_id] = hub
    runner_module.updates_attach(updates)
    hub.attach(session)
    try:
        await runner_module.announce_node_stopping("restart")
        for capture in (updates, session):
            notices = [message for message in capture.messages
                       if message.get("type") == "node_stopping"]
            assert len(notices) == 1, capture.messages
            notice = notices[0]
            assert notice["type"] == "node_stopping"
            assert notice["reason"] == "restart"
            assert isinstance(notice["server_time"], (int, float))
    finally:
        hub.detach(session)
        runner_module.updates_detach(updates)
        runner_module._hubs.pop(hub_id, None)


def exercise_host_cpu_math(host_metrics_module) -> None:
    previous = host_metrics_module._parse_cpu_stat(
        "intr 1\ncpu 100 10 20 400 50 5 6 9 1000 1000\n")
    current = host_metrics_module._parse_cpu_stat(
        "cpu 130 10 30 440 50 5 10 15 5000 5000\n")
    assert previous is not None and current is not None
    # guest counters are deliberately excluded because Linux already includes
    # them in user/nice. Of 90 elapsed ticks, 40 were idle.
    assert round(host_metrics_module._cpu_percent(previous, current), 1) == 55.6
    assert host_metrics_module._parse_cpu_stat("cpu invalid counters\n") is None
    assert host_metrics_module._cpu_percent(current, previous) is None


async def exercise_upgrade_readiness(upgrade_module, runner_module,
                                     terminal_module, temporary: Path) -> None:
    """Readiness and the POST gate must agree on workload and runtime blockers."""
    temporary.mkdir(parents=True, exist_ok=True)
    marker = temporary / "readiness-pending.json"
    runtime = {"enabled": True, "reason": "", "marker": marker}
    app = {"puppy_upgrade_draining": False}
    hub_id = -9001
    hub = runner_module.SessionHub(hub_id)
    old_terminal_count = terminal_module._active_terminals
    old_upload_count = upgrade_module.uploads._active_uploads
    old_runtime = upgrade_module._runtime
    runner_module._hubs[hub_id] = hub
    try:
        ready = upgrade_module._readiness(runtime, app)
        assert ready["ready"] is True and ready["state"] == "ready"
        assert ready["sessions"] == [] and ready["active_terminals"] == 0

        hub.status = "running"
        hub.active_since = time.time()
        hub.queue = ["queued behind the active turn"]
        terminal_module._active_terminals = 2
        busy = upgrade_module._readiness(runtime, app)
        assert busy["ready"] is False and busy["state"] == "busy"
        assert busy["sessions"][0]["id"] == hub_id
        assert busy["sessions"][0]["queued"] == 1
        assert busy["active_terminals"] == 2
        assert "running turn" in busy["reason"] and "queued message" in busy["reason"]
        assert "active terminals" in busy["reason"]

        upgrade_module._runtime = lambda tls_enabled=None: runtime
        rejected = await upgrade_module.h_upgrade(type("Request", (), {"app": app})())
        rejected_body = json.loads(rejected.text)
        assert rejected.status == 409
        assert rejected_body["readiness"]["state"] == "busy"

        app["puppy_upgrade_draining"] = True
        upgrading = upgrade_module._readiness(runtime, app)
        assert upgrading["ready"] is False and upgrading["state"] == "upgrading"
        app["puppy_upgrade_draining"] = False

        hub.status = "idle"
        hub.active_since = None
        hub.queue = []
        terminal_module._active_terminals = 0
        upgrade_module.uploads._active_uploads = 1
        uploading = upgrade_module._workload_readiness()
        assert uploading["ready"] is False and uploading["state"] == "busy"
        assert uploading["active_uploads"] == 1
        assert "active file upload" in uploading["reason"]
        upgrade_module.uploads._active_uploads = 0

        # A spawned agent is a live engine process of this node even without
        # a local session (a relayed job), and its verdict alone does not free
        # the node: the process may still be tearing down and writing files.
        from puppy import spawn_exec
        spawned = spawn_exec.SpawnJob({
            "engine": "codex", "model": "", "effort": "",
            "permission_mode": "", "prompt": "relayed work",
            "cwd": str(temporary), "idle_timeout_s": 600,
            "max_runtime_s": 7200}, ("remote",))
        spawned.task = asyncio.get_event_loop().create_future()
        spawn_exec.manager().jobs[spawned.id] = spawned
        try:
            spawning = upgrade_module._readiness(runtime, app)
            assert spawning["ready"] is False and spawning["state"] == "busy"
            assert spawning["active_spawns"] == 1 and spawning["sessions"] == []
            assert "1 running spawned agent" in spawning["reason"]
            rejected = await upgrade_module.h_upgrade(
                type("Request", (), {"app": app})())
            assert rejected.status == 409
            assert json.loads(rejected.text)["readiness"]["active_spawns"] == 1
            spawned._finish("done")
            tearing_down = upgrade_module._workload_readiness()
            assert tearing_down["ready"] is False
            assert tearing_down["active_spawns"] == 1
            spawned.task.set_result(None)
            settled = upgrade_module._workload_readiness()
            assert settled["ready"] is True and settled["active_spawns"] == 0
        finally:
            spawn_exec.manager().jobs.pop(spawned.id, None)
        marker.touch()
        blocked = upgrade_module._readiness(runtime, app)
        assert blocked["ready"] is False and blocked["state"] == "blocked"

        unsupported = upgrade_module._readiness({
            "enabled": False, "reason": "launcher unavailable", "marker": None,
        }, app)
        assert unsupported["state"] == "unsupported"
        assert unsupported["reason"] == "launcher unavailable"
    finally:
        upgrade_module._runtime = old_runtime
        runner_module._hubs.pop(hub_id, None)
        terminal_module._active_terminals = old_terminal_count
        upgrade_module.uploads._active_uploads = old_upload_count


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def previous_patch_version() -> str:
    major, minor, patch = upgrade_contract.version_key(__version__)
    assert patch > 0
    return f"{major}.{minor}.{patch - 1}"


def copy_with_version(source: Path, target: Path, version: str) -> None:
    with zipfile.ZipFile(source) as current, \
            zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as older:
        for info in current.infolist():
            payload = current.read(info.filename)
            if info.filename == "puppy/__init__.py":
                text = payload.decode("utf-8")
                text = text.replace(f'__version__ = "{__version__}"',
                                    f'__version__ = "{version}"')
                payload = text.encode("utf-8")
            older.writestr(info, payload)
    target.chmod(0o755)


def stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def receive_json_type(ws, kind: str, timeout: float = 3):
    """Read one WebSocket JSON object of *kind*, ignoring other live topics."""
    deadline = asyncio.get_event_loop().time() + timeout
    seen = []
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise AssertionError(
                "timed out waiting for {!r}; saw {}".format(kind, seen))
        # Bound each raw receive too. aiohttp consumes heartbeat frames inside
        # receive_json(), which can otherwise restart its own timeout forever
        # on a healthy socket that never emits the requested application type.
        try:
            frame = await ws.receive(timeout=min(1.0, remaining))
        except asyncio.TimeoutError:
            continue
        if frame.type != aiohttp.WSMsgType.TEXT:
            if frame.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                              aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise AssertionError(
                    "socket closed waiting for {!r}; saw {}".format(kind, seen))
            continue
        try:
            message = json.loads(frame.data)
        except (TypeError, ValueError):
            continue
        seen.append(message.get("type") if isinstance(message, dict) else None)
        if isinstance(message, dict) and message.get("type") == kind:
            return message


async def stop_process_with_notice(process: subprocess.Popen, url: str, token: str,
                                   fingerprint: str = "") -> None:
    """SIGTERM announces, closes live sockets, and exits without a 10s drain."""
    headers = {"X-Puppy-Token": token}
    async with aiohttp.ClientSession() as http:
        updates = await http.ws_connect(
            url + "/api/ws/updates", headers=headers, ssl=ssl_pin(fingerprint))
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "updates_ready"
        assert first["stream_version"] == 1 and first["runtime_id"]
        stop_started = time.monotonic()
        process.terminate()
        notice = await receive_json_type(updates, "node_stopping", timeout=3)
        assert notice["type"] == "node_stopping", notice
        assert notice["reason"] == "shutdown", notice
        assert isinstance(notice["server_time"], (int, float)), notice
        closing = await updates.receive(timeout=3)
        assert closing.type in (aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED), closing
        assert closing.type != aiohttp.WSMsgType.CLOSE or closing.data == 1012, closing
        await updates.close()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError("idle backend did not finish its prompt shutdown")
    elapsed = time.monotonic() - stop_started
    assert elapsed < 4, "idle backend shutdown took {:.2f}s".format(elapsed)


def ssl_pin(fingerprint: str):
    return aiohttp.Fingerprint(bytes.fromhex(fingerprint)) if fingerprint else True


async def wait_for_backend(url: str, process: subprocess.Popen,
                           fingerprint: str = "") -> None:
    pinned = ssl_pin(fingerprint)
    async with aiohttp.ClientSession() as http:
        for _ in range(100):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"backend exited during startup:\n{output[-3000:]}")
            try:
                async with http.get(url + "/api/ping", ssl=pinned) as response:
                    if response.status == 401:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.05)
    raise AssertionError("backend did not start")


async def exercise_node(url: str, token: str, expected_version: str,
                        upgrade_enabled: bool, data_dir: Path,
                        fingerprint: str = "") -> None:
    good = {"X-Puppy-Token": token}
    pinned = ssl_pin(fingerprint)
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", ssl=pinned) as response:
            assert response.status == 401
        async with http.get(url + "/api/ping",
                            headers={"X-Puppy-Token": "wrong-token-value"},
                            ssl=pinned) as response:
            assert response.status == 401
        async with http.get(url + "/api/ping", headers=good, ssl=pinned) as response:
            assert response.status == 200
            ping = await response.json()
        assert ping["role"] == "backend"
        assert ping["protocol"] == protocol.API_PROTOCOL
        assert ping["version"] == expected_version
        assert "sessions" in ping["capabilities"]
        assert "temporary-workspaces" in ping["capabilities"]
        assert "engine-usage-refresh" in ping["capabilities"]
        assert "engine-usage-refresh-manual" in ping["capabilities"]
        assert "engine-upgrade" in ping["capabilities"]
        assert "engine-model-selection" not in ping["capabilities"]
        assert "file-uploads" in ping["capabilities"]
        assert "queue-pause" in ping["capabilities"]
        assert "queue-edit" in ping["capabilities"]
        assert "queue-reorder" in ping["capabilities"]
        assert "queued-engine-switch" in ping["capabilities"]
        assert "queued-permission-config" in ping["capabilities"]
        assert "session-drafts" in ping["capabilities"]
        assert "active-turn-steering" in ping["capabilities"]
        assert "session-control-ws-v1" in ping["capabilities"]
        assert "node-state-stream-v1" in ping["capabilities"]
        assert "system-prompt" in ping["capabilities"]
        assert "spawn-exec" in ping["capabilities"]
        assert "spawn-progress-limits" in ping["capabilities"]
        assert "spawn-client-job-ids" in ping["capabilities"]
        assert "spawn-owner-lease" in ping["capabilities"]
        assert "session-search" in ping["capabilities"]
        assert "session-event-window" in ping["capabilities"]
        assert "session-tools" in ping["capabilities"]
        assert "session-fast-mode" in ping["capabilities"]
        assert "session-agent-notes" in ping["capabilities"]
        assert "session-pinning" in ping["capabilities"]
        assert "session-order-recency" in ping["capabilities"]
        assert "completion-events" in ping["capabilities"]
        assert "workspace-mirror-reset" in ping["capabilities"]
        assert "shutdown-notice" in ping["capabilities"]
        assert ping["shutting_down"] is False
        # browser surface: capability is static, enablement is node config
        # (off in this deployment), availability is probed on demand
        assert "browser" in ping["capabilities"]
        assert "browser-instances" in ping["capabilities"]
        assert "browser-handoff" in ping["capabilities"]
        assert "browser-file-workflows" in ping["capabilities"]
        assert "browser-shared-storage" in ping["capabilities"]
        assert ping["browser"] == {"enabled": False}
        assert ping["uploads"]["enabled"] is \
            (ping["uploads"]["max_file_size_mb"] > 0)
        # the packaged artifact serves the search surface: an empty index
        # answers cleanly and a broken query is a 400, never a 500
        async with http.get(url + "/api/search?q=zx-never-there", headers=good,
                            ssl=pinned) as response:
            found = await response.json()
            assert response.status == 200, found
            assert found["ok"] is True and found["total"] == 0, found
            assert found["sessions"] == [], found
        async with http.get(url + "/api/search?q=--", headers=good,
                            ssl=pinned) as response:
            assert response.status == 400
        async with http.get(url + "/api/search?q=zx", ssl=pinned) as response:
            assert response.status == 401
        async with http.get(url + "/api/system-prompt", headers=good,
                            ssl=pinned) as response:
            prompt_payload = await response.json()
            assert response.status == 200, prompt_payload
        prompt_defaults = prompt_payload["system_prompt"]
        assert prompt_defaults["custom"] == ""
        assert "coding engine runs on this backend" in \
            prompt_defaults["remote_workspace"]
        assert prompt_defaults["remote_workspace_default"] == \
            prompt_defaults["remote_workspace"]
        assert "shared, user-visible Puppy browser" in prompt_defaults["browser"]
        assert prompt_defaults["browser_default"] == prompt_defaults["browser"]
        assert "explicitly asks" in prompt_defaults["terminal"]
        assert prompt_defaults["terminal_default"] == prompt_defaults["terminal"]
        assert "one-shot" in prompt_defaults["spawn"]
        assert prompt_defaults["spawn_default"] == prompt_defaults["spawn"]
        assert prompt_defaults["max_chars"] == 32768
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "Use terse answers.",
                                    "remote_workspace":
                                        "Treat the working tree as a synchronized mirror.",
                                    "browser": "Use the visible browser first.",
                                    "terminal": "Use a shared terminal only on request.",
                                    "spawn": "Spawn delegate agents only on request."}) as response:
            saved_prompt = await response.json()
            assert response.status == 200, saved_prompt
        assert saved_prompt["system_prompt"]["custom"] == "Use terse answers."
        assert saved_prompt["system_prompt"]["remote_workspace"] == \
            "Treat the working tree as a synchronized mirror."
        assert saved_prompt["system_prompt"]["browser"] == "Use the visible browser first."
        assert saved_prompt["system_prompt"]["terminal"] == \
            "Use a shared terminal only on request."
        assert saved_prompt["system_prompt"]["spawn"] == \
            "Spawn delegate agents only on request."
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "x" * 32769}) as response:
            rejected_prompt = await response.json()
            assert response.status == 400, rejected_prompt
            assert "cannot exceed" in rejected_prompt["error"]
        async with http.get(url + "/api/system-prompt", ssl=pinned) as response:
            assert response.status == 401
        async with http.patch(url + "/api/system-prompt", headers=good, ssl=pinned,
                              json={"custom": "",
                                    "remote_workspace":
                                        prompt_defaults["remote_workspace_default"],
                                    "browser": prompt_defaults["browser_default"],
                                    "terminal": prompt_defaults["terminal_default"],
                                    "spawn": prompt_defaults["spawn_default"]}) as response:
            assert response.status == 200, await response.text()
        # spawned-agent execution is part of the shared surface on every node;
        # its start route validates before running anything.
        async with http.post(url + "/api/spawn", headers=good, ssl=pinned,
                             json={"engine": "no-such-engine",
                                   "prompt": "hi", "cwd": "/"}) as response:
            spawn_error = await response.json()
            assert response.status == 400, spawn_error
            assert "unknown engine" in spawn_error["error"]
        async with http.get(url + "/api/spawn/0123abcd", headers=good,
                            ssl=pinned) as response:
            assert response.status == 404, await response.text()
        async with http.patch(url + "/api/spawn/0123abcd", headers=good,
                              ssl=pinned,
                              json={"idle_timeout_s": 1200}) as response:
            assert response.status == 404, await response.text()
        async with http.patch(url + "/api/spawn/0123abcd", ssl=pinned,
                              json={"idle_timeout_s": 1200}) as response:
            assert response.status == 401, await response.text()
        async with http.post(url + "/api/spawn", ssl=pinned,
                             json={"engine": "claude", "prompt": "hi",
                                   "cwd": "/"}) as response:
            assert response.status == 401
        assert "terminal" not in ping["capabilities"]
        assert "terminal-instances" not in ping["capabilities"]
        assert "terminal-handoff" not in ping["capabilities"]
        # the completion-command endpoint is part of the shell surface: a node
        # deployed without a terminal must not run commands either
        assert "notify-exec" not in ping["capabilities"]
        async with http.post(url + "/api/notify/exec", headers=good, ssl=pinned,
                             json={"command": "true"}) as response:
            assert response.status == 404
        async with http.get(url + "/api/completions?after=0",
                            headers=good, ssl=pinned) as response:
            completions = await response.json()
            assert response.status == 200, completions
            assert completions["ok"] is True and completions["cursor"] >= 0
            assert len(completions["stream_id"]) == 32
        async with http.get(url + "/api/completions?after=-1",
                            headers=good, ssl=pinned) as response:
            assert response.status == 400
        async with http.get(url + "/api/completions?after=0",
                            ssl=pinned) as response:
            assert response.status == 401
        # The authenticated named-browser handoff routes are packaged in the
        # headless runtime even while Browser is off; an unknown logical ID is
        # a domain error, not an absent route.
        async with http.get(url + "/api/browser/instances/A1B2/binding",
                            headers=good, ssl=pinned) as response:
            missing_binding = await response.json()
            assert response.status == 404, missing_binding
            assert "closed or unknown" in missing_binding["error"]
        assert ("pinned-tls" in ping["capabilities"]) is bool(fingerprint)
        assert ping["transport"]["encrypted"] is bool(fingerprint)
        if fingerprint:
            assert ping["transport"]["certificate_sha256"] == fingerprint
        assert ("remote-upgrade" in ping["capabilities"]) is upgrade_enabled
        assert ping["upgrade"]["supported"] is upgrade_enabled
        assert ping["upgrade"]["api"] == "/api/node/upgrade"
        assert ping["upgrade"]["signing"] == "hmac-sha256"
        assert ping["upgrade"]["restart"] == "external-launcher"
        ping_readiness = ping["upgrade"]["readiness"]
        assert ping_readiness["ready"] is upgrade_enabled
        assert ping_readiness["state"] == \
            ("ready" if upgrade_enabled else "unsupported")
        assert ping["build"]["artifact"] == "zipapp"

        async with http.get(url + "/api/node", headers=good, ssl=pinned) as response:
            assert response.status == 200
        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            assert response.status == 200
            sessions_payload = await response.json()
            assert sessions_payload["sessions"] == []
            assert isinstance(sessions_payload["server_time"], (int, float))
        async with http.get(url + "/api/browser/status", headers=good, ssl=pinned) as response:
            assert response.status == 200
            browser_status = await response.json()
            assert browser_status["supported"] is True
            assert browser_status["enabled"] is False
            assert browser_status["running"] is False
            assert browser_status["instances"] == []
            assert isinstance(browser_status["available"], bool)
        async with http.get(url + "/api/engines", headers=good, ssl=pinned) as response:
            engine_payload = await response.json()
            assert response.status == 200, engine_payload
        assert engine_payload["timers"]["defaults"] == {
            "cli_release_minutes": 360,
            "model_catalog_minutes": 5,
            "cli_status_minutes": 5,
            "remote_session_seconds": 12,
            "remote_engine_seconds": 60,
            "completion_sync_seconds": 2,
        }
        assert set(engine_payload["timers"]["values"]) == set(
            engine_payload["timers"]["defaults"])
        by_key = {engine["key"]: engine for engine in engine_payload["engines"]}
        assert set(("claude", "codex", "opencode")).issubset(by_key)
        assert "engine-defaults" in ping["capabilities"]
        for key, engine in by_key.items():
            defaults_url = url + "/api/engines/{}/defaults".format(key)
            async with http.get(defaults_url, ssl=pinned) as response:
                assert response.status == 401
            async with http.get(defaults_url, headers=good, ssl=pinned) as response:
                read_defaults = await response.json()
                assert response.status == 200, read_defaults
            assert read_defaults["engine"]["session_defaults"] == engine["session_defaults"]
            assert set(engine["session_defaults"]) == {"permission_mode", "model", "effort"}
            chosen = {**engine["factory_defaults"],
                      "permission_mode": engine["permission_options"][0]["value"]}
            async with http.put(defaults_url, headers=good, ssl=pinned, json=chosen) as response:
                saved_defaults = await response.json()
                assert response.status == 200, saved_defaults
                assert saved_defaults["engine"]["session_defaults"] == chosen
            async with http.put(defaults_url, headers=good, ssl=pinned,
                                json={**chosen, "effort": "not-an-effort"}) as response:
                assert response.status == 400
            async with http.put(defaults_url, headers=good, ssl=pinned,
                                json=engine["factory_defaults"]) as response:
                assert response.status == 200, await response.text()
        opencode = by_key["opencode"]
        assert opencode["availability_only"] is True
        assert opencode["dynamic_model_options"] is True
        assert opencode["allow_custom_model"] is False
        assert opencode["model_options"][0]["value"] == ""
        assert opencode["model_options"][0]["label"] == "Default"
        assert len({model["value"] for model in opencode["model_options"]}) == \
            len(opencode["model_options"])
        if opencode["installed"]:
            assert opencode["auth"] == "ok"
            assert opencode["detail"] == "binary available"
        for engine in engine_payload["engines"]:
            assert isinstance(engine["dynamic_model_options"], bool)
            assert isinstance(engine["supports_fast_mode"], bool)
            assert isinstance(engine["model_catalog_loaded"], bool)
            assert isinstance(engine["model_catalog_error"], str)
            assert isinstance(engine["model_catalog_note"], str)
            assert engine["model_catalog_source"] in \
                ("engine", "turn", "cache-file", "static", "none")
            assert engine["model_catalog_checked_at"] is None or \
                isinstance(engine["model_catalog_checked_at"], (int, float))
            assert engine["model_catalog_updated_at"] is None or \
                isinstance(engine["model_catalog_updated_at"], (int, float))
            assert isinstance(engine["latest_version"], str)
            assert engine["update_available"] in (True, False, None)
            assert engine["latest_checked_at"] is None or \
                isinstance(engine["latest_checked_at"], (int, float))
            assert isinstance(engine["latest_check_error"], str)
            assert isinstance(engine["upgrade_supported"], bool)
            assert engine["upgrade_state"] in ("idle", "running")
            assert engine["upgrade_result"] is None or \
                isinstance(engine["upgrade_result"], dict)
            assert isinstance(engine["version_checked_at"], (int, float))
        assert by_key["codex"]["supports_fast_mode"] is True
        assert by_key["claude"]["supports_fast_mode"] is False
        assert by_key["opencode"]["supports_fast_mode"] is False
        for model in by_key["codex"]["model_options"]:
            assert "service_tiers" not in model
            assert isinstance(model["fast_mode_available"], bool)
            assert isinstance(model["fast_mode_hint"], str)
        # Discovered choices feed model_options directly; there is no separate
        # controller-owned or node-owned allow-list API.
        async with http.patch(url + "/api/engines/opencode/models", headers=good,
                              ssl=pinned, json={"models": []}) as response:
            assert response.status == 404, await response.text()
        # The headless surface serves the same version-refresh route as the
        # console; a node that cannot re-check must not advertise the button.
        async with http.post(url + "/api/engines/refresh",
                             headers=good, ssl=pinned) as response:
            rechecked = await response.json()
            assert response.status == 200, rechecked
        assert isinstance(rechecked["engines"], list)
        assert "usage_refresh" in rechecked
        assert "timers" in rechecked
        assert all("model_catalog_checked_at" in engine
                   for engine in rechecked["engines"])
        async with http.post(url + "/api/engines/not-an-engine/upgrade",
                             headers=good, ssl=pinned) as response:
            assert response.status == 404, await response.text()
        async with http.post(url + "/api/engines/not-an-engine/upgrade",
                             ssl=pinned) as response:
            assert response.status == 401, await response.text()
        async with http.get(url + "/api/engines/usage-refresh",
                            headers=good, ssl=pinned) as response:
            refresh = await response.json()
            assert response.status == 200, refresh
        assert refresh["usage_refresh"]["minutes"] == 0
        assert refresh["usage_refresh"]["enabled"] is False
        async with http.post(url + "/api/engines/usage-refresh",
                             headers=good, ssl=pinned) as response:
            manual_refresh = await response.json()
            assert response.status == 200, manual_refresh
        assert isinstance(manual_refresh["engines"], list)
        assert manual_refresh["usage_refresh"]["enabled"] is False
        async with http.get(url + "/api/timers", headers=good, ssl=pinned) as response:
            timer_settings = await response.json()
            assert response.status == 200, timer_settings
        assert timer_settings["timers"]["limits"]["model_catalog_minutes"] == {
            "min": 1, "max": 1440, "unit": "minutes"}
        async with http.patch(url + "/api/timers", headers=good, ssl=pinned,
                              json={"model_catalog_minutes": 9}) as response:
            changed_timers = await response.json()
            assert response.status == 200, changed_timers
        assert changed_timers["timers"]["values"]["model_catalog_minutes"] == 9
        async with http.patch(url + "/api/timers", headers=good, ssl=pinned,
                              json={"remote_session_seconds": 1}) as response:
            assert response.status == 400, await response.text()
        async with http.patch(url + "/api/timers", headers=good, ssl=pinned,
                              json={"not_a_timer": 3}) as response:
            assert response.status == 400, await response.text()
        async with http.get(url + "/api/timers", ssl=pinned) as response:
            assert response.status == 401, await response.text()
        async with http.patch(url + "/api/engines/usage-refresh",
                              headers=good, ssl=pinned,
                              json={"minutes": -1}) as response:
            assert response.status == 400
        async with http.get(url + "/api/uploads/settings",
                            headers=good, ssl=pinned) as response:
            upload_settings = await response.json()
            assert response.status == 200, upload_settings
        assert upload_settings["uploads"]["max_file_size_mb"] >= 0
        async with http.patch(url + "/api/uploads/settings", headers=good,
                              ssl=pinned, json={"max_file_size_mb": -1}) as response:
            assert response.status == 400, await response.text()
        async with http.patch(url + "/api/uploads/settings", headers=good,
                              ssl=pinned, json={"max_file_size_mb": 1}) as response:
            upload_settings = await response.json()
            assert response.status == 200, upload_settings
        assert upload_settings["uploads"]["max_file_size_bytes"] == 1024 * 1024
        updates = await http.ws_connect(url + "/api/ws/updates", headers=good, ssl=pinned)
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "updates_ready", first
        assert first["stream_version"] == 1 and first["runtime_id"]
        stream_runtime = first["runtime_id"]
        snapshots = {}
        deadline = asyncio.get_event_loop().time() + 45
        while len(snapshots) < 4 and asyncio.get_event_loop().time() < deadline:
            snapshot = await updates.receive_json(timeout=45)
            topic = snapshot.get("type")
            if topic not in ("sessions", "node", "engines", "browser_status"):
                continue
            assert snapshot["runtime_id"] == stream_runtime, snapshot
            assert snapshot["state_topic"] == topic, snapshot
            assert isinstance(snapshot["state_revision"], int), snapshot
            assert snapshot["state_revision"] >= 1, snapshot
            snapshots[topic] = snapshot
        assert set(snapshots) == {"sessions", "node", "engines", "browser_status"}, \
            snapshots
        session_snapshot = snapshots["sessions"]
        assert session_snapshot["sessions"] == []
        assert isinstance(session_snapshot["server_time"], (int, float))
        await updates.close()

        async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                "engine": "codex", "workspace_kind": "not-a-workspace",
        }) as response:
            assert response.status == 400
        async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                "engine": "codex", "workspace_kind": "temporary", "name": "scratch-test",
        }) as response:
            created = await response.json()
            assert response.status == 200, created
        scratch = created["session"]
        assert scratch["workspace_kind"] == "temporary"
        assert scratch["workspace_missing"] is False
        scratch_path = Path(scratch["cwd"])
        assert scratch_path.is_dir()
        assert scratch_path.name.startswith("session-")
        assert scratch_path.parent == data_dir.resolve() / "workspaces"
        assert scratch_path.stat().st_mode & 0o777 == 0o700
        (scratch_path / "throw-away.txt").write_text("disposable", encoding="utf-8")

        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            listed_payload = await response.json()
            assert response.status == 200, listed_payload
        listed_scratch = next(row for row in listed_payload["sessions"]
                              if row["id"] == scratch["id"])
        assert listed_scratch["status"] == "idle"
        assert listed_scratch["pinned"] is False
        assert listed_scratch["order_at"] == 0
        assert listed_scratch["active_since"] is None
        assert listed_scratch["steering"] == {
            "supported": True, "ready": False, "turn_id": ""}
        assert isinstance(listed_payload["server_time"], (int, float))

        # Pinning is strict, compare-protected, immediately returns the whole
        # canonical order, and shares the same route on a headless node.
        pin_updates = await http.ws_connect(
            url + "/api/ws/updates", headers=good, ssl=pinned)
        pin_initial = await pin_updates.receive_json(timeout=3)
        assert pin_initial["type"] == "updates_ready"
        pin_before = await receive_json_type(pin_updates, "sessions", timeout=3)
        pin_revision = pin_before["state_revision"]
        async with http.patch(
                url + f"/api/sessions/{scratch['id']}", headers=good, ssl=pinned,
                json={"pinned": "yes"}) as response:
            assert response.status == 400, await response.text()
        async with http.patch(
                url + f"/api/sessions/{scratch['id']}", headers=good, ssl=pinned,
                json={"pinned": True, "archived": True}) as response:
            assert response.status == 400, await response.text()
        async with http.patch(
                url + f"/api/sessions/{scratch['id']}", headers=good, ssl=pinned,
                json={"pinned": True, "expected_pinned": True}) as response:
            assert response.status == 409, await response.text()
        async with http.patch(
                url + f"/api/sessions/{scratch['id']}", headers=good, ssl=pinned,
                json={"pinned": True, "expected_pinned": False}) as response:
            pinned_payload = await response.json()
            assert response.status == 200, pinned_payload
        assert pinned_payload["session"]["pinned"] is True
        assert pinned_payload["sessions"][0]["id"] == scratch["id"]
        assert pinned_payload["sessions"][0]["pinned"] is True
        while True:
            pin_notice = await pin_updates.receive_json(timeout=3)
            if pin_notice.get("type") == "sessions" and \
                    pin_notice.get("state_revision", 0) > pin_revision:
                break
        assert pin_notice["sessions"][0]["pinned"] is True
        await pin_updates.close()
        for missing in ("expected_order", "expected_pinned"):
            body = {"order": [scratch["id"]], "expected_order": [scratch["id"]],
                    "expected_pinned": [scratch["id"]]}
            del body[missing]
            async with http.post(url + "/api/sessions/reorder", headers=good,
                                 ssl=pinned, json=body) as response:
                assert response.status == 400, await response.text()
        async with http.post(url + "/api/sessions/reorder", headers=good,
                             ssl=pinned, json={
                                 "order": [scratch["id"], scratch["id"]],
                             }) as response:
            assert response.status == 400, await response.text()
        async with http.post(url + "/api/sessions/reorder", headers=good,
                             ssl=pinned, json={
                                 "order": [scratch["id"]],
                                 "expected_order": [scratch["id"]],
                                 "expected_pinned": [],
                             }) as response:
            assert response.status == 409, await response.text()
        async with http.post(url + "/api/sessions/reorder", headers=good,
                             ssl=pinned, json={
                                 "order": [scratch["id"]],
                                 "expected_order": [scratch["id"]],
                                 "expected_pinned": [scratch["id"]],
                             }) as response:
            reordered_payload = await response.json()
            assert response.status == 200, reordered_payload
        assert reordered_payload["sessions"][0]["pinned"] is True
        for recency, status in (([0], 200), ([1], 409), ([True], 400),
                                ([10 ** 400], 400)):
            async with http.post(url + "/api/sessions/reorder", headers=good,
                                 ssl=pinned, json={
                                     "order": [scratch["id"]],
                                     "expected_order": [scratch["id"]],
                                     "expected_pinned": [scratch["id"]],
                                     "expected_recency": recency,
                                 }) as response:
                assert response.status == status, await response.text()

        # Model a workspace removed outside Puppy. The transcript/session stays,
        # advertises the missing files, and can be given a fresh private workspace.
        shutil.rmtree(scratch_path)
        async with http.get(url + f"/api/sessions/{scratch['id']}",
                            headers=good, ssl=pinned) as response:
            missing_payload = await response.json()
            missing = missing_payload["session"]
            assert response.status == 200
        assert missing_payload["steering"] == {
            "supported": True, "ready": False, "turn_id": ""}
        assert missing["workspace_missing"] is True
        async with http.post(url + f"/api/sessions/{scratch['id']}/workspace/reset",
                             headers=good, ssl=pinned) as response:
            reset = await response.json()
            assert response.status == 200, reset
        fresh_path = Path(reset["session"]["cwd"])
        assert fresh_path != scratch_path and fresh_path.is_dir()
        assert reset["session"]["workspace_missing"] is False
        async with http.get(url + f"/api/sessions/{scratch['id']}",
                            headers=good, ssl=pinned) as response:
            reset_events = (await response.json())["events"]
        assert any(event["kind"] == "info" and
                   event["data"].get("subtype") == "workspace_reset"
                   for event in reset_events)
        async with http.delete(url + f"/api/sessions/{scratch['id']}",
                               headers=good, ssl=pinned) as response:
            deleted = await response.json()
            assert response.status == 200 and deleted["workspace_removed"] is True, deleted
        assert not fresh_path.exists()
        async with http.get(url + "/api/sessions", headers=good, ssl=pinned) as response:
            assert (await response.json())["sessions"] == []

        normal_path = Path(tempfile.mkdtemp(prefix="puppy-normal-workspace-"))
        try:
            sentinel = normal_path / "must-survive-session-delete.txt"
            sentinel.write_text("persistent", encoding="utf-8")
            async with http.post(url + "/api/sessions", headers=good, ssl=pinned, json={
                    "engine": "codex", "cwd": str(normal_path), "name": "normal-test",
            }) as response:
                normal_created = await response.json()
                assert response.status == 200, normal_created
            normal = normal_created["session"]
            await exercise_agent_notes(http, url, good, pinned, normal, normal_path)
            assert normal["workspace_kind"] == "directory"

            # Malformed direct clients get bounded 4xx responses and cannot
            # turn a negative SQLite LIMIT into an unbounded transcript read.
            for payload in ([], {"text": ["not text"]}):
                async with http.post(
                        url + f"/api/sessions/{normal['id']}/message",
                        headers=good, json=payload, ssl=pinned) as response:
                    rejected_message = await response.json()
                    assert response.status == 400, rejected_message
            for payload in ([], {"text": ["not text"]}, {"text": "   "},
                            {"text": "valid"},
                            {"text": "valid", "request_id": ["not text"]},
                            {"text": "valid", "request_id": "bad id!",
                             "expected_turn_id": "turn"},
                            {"text": "x" * (128 * 1024 + 1)}):
                async with http.post(
                        url + f"/api/sessions/{normal['id']}/steer",
                        headers=good, json=payload, ssl=pinned) as response:
                    rejected_steer = await response.json()
                    assert response.status == 400, (payload if len(str(payload)) < 200
                                                    else "oversize", rejected_steer)
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/steer",
                    headers=good, json={"text": "valid direction"},
                    ssl=pinned) as response:
                idle_steer = await response.json()
                assert response.status == 400, idle_steer
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/steer",
                    headers=good, json={
                        "text": "valid direction",
                        "expected_turn_id": "idle-turn",
                    }, ssl=pinned) as response:
                idle_steer = await response.json()
                assert response.status == 409, idle_steer
            assert idle_steer["error"] == "there is no active turn to steer"
            # session tools: malformed requests are 400, a session that has
            # never run a turn has nothing to compact or undo (409), and no
            # engine is ever spawned by either answer
            for payload in ([], {"tool": 5}, {"tool": "explode"}):
                async with http.post(
                        url + f"/api/sessions/{normal['id']}/tool",
                        headers=good, json=payload, ssl=pinned) as response:
                    rejected_tool = await response.json()
                    assert response.status == 400, (payload, rejected_tool)
            for tool in ("compact", "undo"):
                async with http.post(
                        url + f"/api/sessions/{normal['id']}/tool",
                        headers=good, json={"tool": tool}, ssl=pinned) as response:
                    idle_tool = await response.json()
                    assert response.status == 409, (tool, idle_tool)
                    assert "has not run a turn" in idle_tool["error"], idle_tool
            for query in ("limit=nope", "limit=-1", "limit=0", "limit=501",
                          "before_seq=nope", "before_seq=0", "after_seq=nope",
                          "after_seq=-1", "before_seq=3&after_seq=1"):
                async with http.get(
                        url + f"/api/sessions/{normal['id']}/events?{query}",
                        headers=good, ssl=pinned) as response:
                    rejected_events = await response.json()
                    assert response.status == 400, (query, rejected_events)
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/events?limit=1",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/events?after_seq=0&limit=5",
                    headers=good, ssl=pinned) as response:
                forward = await response.json()
                assert response.status == 200, forward
                assert isinstance(forward["events"], list)

            session_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            first_snapshot = await session_ws.receive_json(timeout=3)
            assert first_snapshot["type"] == "snapshot"
            assert first_snapshot["steering"] == {
                "supported": True, "ready": False, "turn_id": ""}
            assert first_snapshot["draft"] == {
                "text": "", "revision": 0, "updated_at": None}
            # Correlated active-turn controls share the already-open session
            # socket on capable nodes.  Idle conflicts exercise the complete
            # transport and shared validation path without spending quota.
            await session_ws.send_json({
                "type": "steer", "text": "change direction",
                "request_id": "socket-steer-1", "expected_turn_id": "idle-turn",
            })
            steer_complete = await receive_json_type(
                session_ws, "steer_complete", timeout=3)
            assert steer_complete["request_id"] == "socket-steer-1"
            assert "error" in steer_complete, steer_complete
            await session_ws.send_json({
                "type": "ask", "question": "Why?",
                "request_id": "socket-ask-1", "expected_turn_id": "idle-turn",
            })
            ask_complete = await receive_json_type(
                session_ws, "ask_complete", timeout=3)
            assert ask_complete["request_id"] == "socket-ask-1"
            assert "error" in ask_complete, ask_complete
            peer_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            assert (await peer_ws.receive_json(timeout=3))["draft"]["revision"] == 0
            await session_ws.send_json({
                "type": "draft", "text": "Test", "client_id": "device-a",
                "client_seq": 1,
            })
            first_draft = await session_ws.receive_json(timeout=3)
            peer_draft = await peer_ws.receive_json(timeout=3)
            assert first_draft == peer_draft
            assert first_draft["type"] == "draft"
            assert first_draft["text"] == "Test" and first_draft["revision"] == 1
            assert first_draft["client_id"] == "device-a"
            assert first_draft["client_seq"] == 1
            await peer_ws.close()
            later_ws = await http.ws_connect(
                url + f"/api/ws/session/{normal['id']}", headers=good, ssl=pinned)
            later_snapshot = await later_ws.receive_json(timeout=3)
            assert later_snapshot["draft"]["text"] == "Test"
            assert later_snapshot["draft"]["revision"] == 1
            await later_ws.send_json({
                "type": "draft", "text": "", "client_id": "device-b",
                "client_seq": 9,
            })
            cleared_a = await session_ws.receive_json(timeout=3)
            cleared_b = await later_ws.receive_json(timeout=3)
            assert cleared_a == cleared_b
            assert cleared_a["text"] == "" and cleared_a["revision"] == 2
            await later_ws.close()
            await session_ws.send_json([])
            rejected_socket_object = await session_ws.receive_json(timeout=3)
            assert rejected_socket_object["type"] == "toast", rejected_socket_object
            await session_ws.send_json({"type": "message", "text": ["not text"]})
            rejected_socket_text = await session_ws.receive_json(timeout=3)
            assert rejected_socket_text["type"] == "toast", rejected_socket_text
            assert "must be text" in rejected_socket_text["text"]
            await session_ws.close()

            executable = b"MZ\x00arbitrary executable payload\n"
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/x-msdownload",
                        "X-Puppy-Filename": "%2E%2E%2Fprogram.exe",
                        "X-Puppy-Size": str(len(executable)),
                    }, data=executable, ssl=pinned) as response:
                uploaded = await response.json()
                assert response.status == 200, uploaded
            uploaded_path = Path(uploaded["path"])
            assert uploaded["name"] == "program.exe"
            assert uploaded["size"] == len(executable)
            assert uploaded_path.read_bytes() == executable
            assert uploaded_path.stat().st_mode & 0o777 == 0o600
            assert uploaded_path.parent.stat().st_mode & 0o777 == 0o700
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "too-large.bin",
                        "X-Puppy-Size": str(1024 * 1024 + 1),
                    }, data=b"x", ssl=pinned) as response:
                assert response.status == 413, await response.text()

            async def oversized_body():
                for _chunk in range(5):
                    yield b"z" * (256 * 1024)

            existing_uploads = set(uploaded_path.parent.parent.iterdir())
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "lied-about-size.bin",
                        "X-Puppy-Size": "1",
                    }, data=oversized_body(), ssl=pinned) as response:
                actual_limit = await response.json()
                assert response.status == 413, actual_limit
            assert actual_limit["uploads"]["max_file_size_mb"] == 1
            assert set(uploaded_path.parent.parent.iterdir()) == existing_uploads
            # An uploaded image reads back so a preview survives a page reload,
            # but only as a declared raster type with sniffing off - this route
            # must never become a general on-origin file server.
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{uploaded['upload_id']}",
                    headers=good, ssl=pinned) as response:
                served = await response.read()
                assert response.status == 415, (response.status, served)
            png = (b"\x89PNG\r\n\x1a\n" + b"preview-bytes")
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": "pasted.png",
                        "X-Puppy-Size": str(len(png)),
                    }, data=png, ssl=pinned) as response:
                image_upload = await response.json()
                assert response.status == 200, image_upload
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
                assert await response.read() == png
                assert response.headers["Content-Type"].startswith("image/png")
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert "no-store" not in response.headers.get("Cache-Control", "")
            # unauthenticated readers get nothing, and unknown ids are not found
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    ssl=pinned) as response:
                assert response.status == 401, await response.text()
            async with http.get(
                    url + f"/api/sessions/{normal['id']}/upload/1700000000000-abcdef0123",
                    headers=good, ssl=pinned) as response:
                assert response.status == 404, await response.text()
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{image_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{uploaded['upload_id']}",
                    headers=good, ssl=pinned) as response:
                discarded = await response.json()
                assert response.status == 200 and discarded["removed"] is True, discarded
            assert not uploaded_path.exists()
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "application/octet-stream",
                        "X-Puppy-Filename": ".empty", "X-Puppy-Size": "0",
                    }, data=b"", ssl=pinned) as response:
                empty_upload = await response.json()
                assert response.status == 200, empty_upload
            assert empty_upload["name"] == ".empty"
            assert Path(empty_upload["path"]).read_bytes() == b""
            async with http.delete(
                    url + f"/api/sessions/{normal['id']}/upload/{empty_upload['upload_id']}",
                    headers=good, ssl=pinned) as response:
                assert response.status == 200, await response.text()
            async with http.patch(url + "/api/uploads/settings", headers=good,
                                  ssl=pinned, json={"max_file_size_mb": 0}) as response:
                disabled_uploads = await response.json()
                assert response.status == 200, disabled_uploads
            async with http.post(
                    url + f"/api/sessions/{normal['id']}/upload", headers={
                        **good, "Content-Type": "text/plain",
                        "X-Puppy-Filename": "off.txt",
                    }, data=b"disabled", ssl=pinned) as response:
                rejected_upload = await response.json()
                assert response.status == 403, rejected_upload
            assert rejected_upload["uploads"]["enabled"] is False
            async with http.patch(url + "/api/uploads/settings", headers=good,
                                  ssl=pinned, json={"max_file_size_mb": 1}) as response:
                assert response.status == 200, await response.text()
            async with http.delete(url + f"/api/sessions/{normal['id']}",
                                   headers=good, ssl=pinned) as response:
                normal_deleted = await response.json()
                assert response.status == 200, normal_deleted
            assert normal_deleted["workspace_removed"] is False
            assert sentinel.read_text(encoding="utf-8") == "persistent"
        finally:
            shutil.rmtree(normal_path, ignore_errors=True)

        async with http.get(url + "/api/node/upgrade", headers=good, ssl=pinned) as response:
            status = await response.json()
            assert response.status == 200 and status["supported"] is upgrade_enabled
        assert status["readiness"]["ready"] is upgrade_enabled
        assert status["readiness"]["state"] == \
            ("ready" if upgrade_enabled else "unsupported")
        for path in ("/", "/static/app.js", "/api/settings", "/api/auth/status",
                     "/api/ws/term", "/api/terminal/instances",
                     "/api/ws/terminal/A1B2"):
            async with http.get(url + path, headers=good, ssl=pinned) as response:
                assert response.status == 404, (path, response.status)
        async with http.post(url + "/api/snapshot/export", headers=good,
                             json={"ui": {}}, ssl=pinned) as response:
            assert response.status == 404  # backup/restore is a full-WebUI surface
        async with http.post(url + "/api/settings/bind/prepare", headers=good,
                             json={"host": "127.0.0.1", "origin": url},
                             ssl=pinned) as response:
            assert response.status == 404  # browser-verified binding is controller-only
        async with http.get(url + "/api/settings/bind/verify/not-a-token",
                            headers=good, ssl=pinned) as response:
            assert response.status == 404
        async with http.post(url + "/api/settings/bind/activate", headers=good,
                             json={"token": "not-a-token", "browser_state": {}},
                             ssl=pinned) as response:
            assert response.status == 404
        async with http.get(url + "/api/settings/bind/handoff/not-a-token/ready",
                            headers=good, ssl=pinned) as response:
            assert response.status == 404
        if not upgrade_enabled:
            async with http.post(url + "/api/node/upgrade", headers=good,
                                 data=b"not-an-artifact", ssl=pinned) as response:
                assert response.status == 409
                rejected = await response.json()
            assert rejected["readiness"]["state"] == "unsupported"


async def reject_bad_signature(url: str, token: str, fingerprint: str = "") -> None:
    payload = b"signed body is intentionally not a zipapp"
    manifest = {
        "format": upgrade_contract.FORMAT_VERSION,
        "artifact": "zipapp",
        "version": __version__,
        "protocol": protocol.API_PROTOCOL,
        "size": len(payload),
        "sha256": upgrade_contract.artifact_sha256(payload),
        "nonce": "bad-signature-test-nonce",
        "created_at": int(time.time()),
    }
    headers = {
        "X-Puppy-Token": token,
        upgrade_contract.MANIFEST_HEADER: upgrade_contract.encode_manifest(manifest),
        upgrade_contract.SIGNATURE_HEADER: "0" * 64,
    }
    async with aiohttp.ClientSession() as http:
        async with http.post(url + "/api/node/upgrade", headers=headers,
                             data=payload, ssl=ssl_pin(fingerprint)) as response:
            assert response.status == 403, await response.text()


def exercise_current_peer_contract(backends) -> None:
    current = {"protocol": protocol.API_PROTOCOL, "role": "backend", "capabilities": []}
    assert backends._normalize_peer(current)["protocol"] == protocol.API_PROTOCOL
    for invalid in ({"role": "backend"}, {**current, "protocol": None},
                    {**current, "protocol": str(protocol.API_PROTOCOL)},
                    {**current, "protocol": True}, {**current, "protocol": 0},
                    {**current, "protocol": 1}, {**current, "role": "legacy-full"}):
        try:
            backends._normalize_peer(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted an outdated or malformed peer")


async def exercise_controller(url: str, token: str, backend_url: str,
                              backend_token: str, backend_fingerprint: str,
                              old_version: str, backend_state_dir: Path) -> None:
    headers = {"X-Puppy-Token": token}
    unavailable_url = backend_url.replace("127.0.0.1", "127.0.0.2")
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/ping", headers=headers) as response:
            full_ping = await response.json()
            assert response.status == 200
        assert full_ping["role"] == "full" and full_ping["protocol"] == protocol.API_PROTOCOL
        assert "terminal" in full_ping["capabilities"]
        assert "terminal-instances" in full_ping["capabilities"]
        assert "terminal-handoff" in full_ping["capabilities"]
        assert "queue-pause" in full_ping["capabilities"]
        assert "queue-edit" in full_ping["capabilities"]
        assert "queue-reorder" in full_ping["capabilities"]
        assert "session-pinning" in full_ping["capabilities"]
        assert "session-order-recency" in full_ping["capabilities"]
        assert "session-drafts" in full_ping["capabilities"]
        assert "active-turn-steering" in full_ping["capabilities"]
        assert "session-control-ws-v1" in full_ping["capabilities"]
        assert "node-state-stream-v1" in full_ping["capabilities"]
        assert "browser-handoff" in full_ping["capabilities"]
        assert "browser-file-workflows" in full_ping["capabilities"]
        assert "completion-events" in full_ping["capabilities"]
        assert "workspace-mirror-reset" in full_ping["capabilities"]
        assert "shutdown-notice" not in full_ping["capabilities"]

        updates = await http.ws_connect(url + "/api/ws/updates", headers=headers)
        first = await updates.receive_json(timeout=3)
        assert first["type"] == "updates_ready", first
        assert first["stream_version"] == 1 and first["runtime_id"]
        full_sessions = await receive_json_type(updates, "sessions", timeout=5)
        assert full_sessions["runtime_id"] == first["runtime_id"]
        full_terminals = await receive_json_type(
            updates, "terminal_instances", timeout=5)
        assert full_terminals["instances"] == []
        while True:
            metric = await updates.receive_json(timeout=5)
            if metric.get("type") == "host_metrics":
                break
        assert isinstance(metric["cpu_percent"], (int, float))
        assert 0 <= metric["cpu_percent"] <= 100
        assert isinstance(metric["sampled_at"], (int, float))
        await updates.close()

        async with http.post(url + "/api/backends", headers=headers, json={
                "url": backend_url, "token": backend_token}) as response:
            unpinned = await response.json()
            assert response.status == 400, unpinned
        assert "TLS certificate verification failed" in unpinned["error"]

        wrong_pin = ("0" if backend_fingerprint[0] != "0" else "1") + backend_fingerprint[1:]
        async with http.post(url + "/api/backends", headers=headers, json={
                "url": backend_url, "token": backend_token,
                "tls_fingerprint": wrong_pin}) as response:
            mismatched = await response.json()
            assert response.status == 400, mismatched
        assert "fingerprint mismatch" in mismatched["error"]

        async with http.post(url + "/api/backends", headers=headers, json={
                "name": "", "urls": [unavailable_url, backend_url],
                "token": backend_token,
                "tls_fingerprint": backend_fingerprint,
                "auto_upgrade": False}) as response:
            added = await response.json()
            assert response.status == 200, added
        assert added["remote"]["role"] == "backend"
        assert added["remote"]["protocol"] == protocol.API_PROTOCOL
        assert added["backend"]["id"] == added["id"]
        assert added["backend"]["availability"]["state"] == "online"
        assert "token" not in added["backend"]

        async with http.get(url + "/api/backends", headers=headers) as response:
            listed = (await response.json())["backends"]
            assert response.status == 200
        assert len(listed) == 1, listed
        stored = listed[0]
        assert stored["name"] == "backend-test-node"
        assert stored["protocol"] == protocol.API_PROTOCOL
        assert stored["role"] == "backend"
        assert "sessions" in stored["capabilities"]
        assert "temporary-workspaces" in stored["capabilities"]
        assert "engine-usage-refresh" in stored["capabilities"]
        assert "engine-upgrade" in stored["capabilities"]
        assert "file-uploads" in stored["capabilities"]
        assert "queue-pause" in stored["capabilities"]
        assert "queue-edit" in stored["capabilities"]
        assert "queue-reorder" in stored["capabilities"]
        assert "session-pinning" in stored["capabilities"]
        assert "session-drafts" in stored["capabilities"]
        assert "active-turn-steering" in stored["capabilities"]
        assert "session-control-ws-v1" in stored["capabilities"]
        assert "node-state-stream-v1" in stored["capabilities"]
        assert "browser-handoff" in stored["capabilities"]
        assert "browser-file-workflows" in stored["capabilities"]
        assert "shutdown-notice" in stored["capabilities"]
        assert "terminal" not in stored["capabilities"]
        assert "remote-upgrade" in stored["capabilities"]
        assert "pinned-tls" in stored["capabilities"]
        assert stored["remote_version"] == old_version
        assert stored["url"] == unavailable_url
        assert stored["urls"] == [unavailable_url, backend_url]
        assert stored["active_url"] == backend_url
        assert stored["tls_fingerprint"] == backend_fingerprint
        assert stored["auto_upgrade"] is False
        assert stored["upgrade_in_progress"] is False
        assert stored["availability"]["state"] == "online"
        assert stored["availability"]["reason"] == ""
        assert isinstance(stored["availability"]["checked_at"], (int, float))
        assert stored["last_known"]["version"] == 1
        assert len(stored["last_known"]["node_uuid"]) == 32
        assert stored["last_known"]["browser"] == {"enabled": False}
        assert stored["last_known"]["uploads"]["max_file_size_mb"] >= 0
        assert "token" not in stored

        # The controller owns one upstream subscription and fans revisioned
        # remote snapshots into its local update socket.  Consoles therefore
        # do not open one remote polling loop (or socket) apiece.
        fanout_updates = await http.ws_connect(
            url + "/api/ws/updates", headers=headers)
        fanout_ready = await fanout_updates.receive_json(timeout=3)
        assert fanout_ready["type"] == "updates_ready", fanout_ready
        fanout_topics = set()
        fanout_runtime = ""
        fanout_connected = False
        fanout_deadline = asyncio.get_event_loop().time() + 12
        while asyncio.get_event_loop().time() < fanout_deadline and \
                (not fanout_connected or fanout_topics != {
                    "sessions", "node", "engines", "browser_status"}):
            message = await fanout_updates.receive_json(timeout=12)
            if message.get("type") == "remote_stream" and \
                    message.get("backend_id") == stored["id"] and \
                    message.get("connected") is True:
                assert {"sessions", "node", "engines", "browser_status"}.issubset(
                    fanout_topics), (fanout_topics, message)
                fanout_connected = True
                fanout_runtime = message.get("node_runtime_id") or ""
            if message.get("type") != "remote_state" or \
                    message.get("backend_id") != stored["id"]:
                continue
            event = message.get("event") or {}
            topic = event.get("type")
            if topic not in ("sessions", "node", "engines", "browser_status"):
                continue
            assert event.get("state_topic") == topic, event
            assert isinstance(event.get("state_revision"), int), event
            assert event.get("runtime_id"), event
            if fanout_runtime:
                assert event["runtime_id"] == fanout_runtime, event
            fanout_topics.add(topic)
        assert fanout_connected, "controller did not connect its backend state stream"
        assert fanout_runtime
        assert fanout_topics == {
            "sessions", "node", "engines", "browser_status"}, fanout_topics
        await fanout_updates.close()

        # Node names must resolve unambiguously for spawned agents: a backend
        # may not reuse another backend's name (in any case), this
        # controller's instance name, a reserved local alias, or the #id
        # form - and the instance name may not shadow a backend either.
        for taken, needle in (("BACKEND-TEST-NODE", "already the name of backend"),
                              ("local", "reserved"), ("This Node", "reserved"),
                              ("#7", "cannot start with '#'")):
            async with http.post(url + "/api/backends", headers=headers, json={
                    "name": taken, "urls": [backend_url], "token": backend_token,
                    "tls_fingerprint": backend_fingerprint}) as response:
                refused = await response.json()
                assert response.status == 409, (taken, refused)
            assert needle in refused["error"], (taken, refused)
        async with http.get(url + "/api/settings", headers=headers) as response:
            instance_name = (await response.json())["instance_name"]
        async with http.post(url + "/api/backends", headers=headers, json={
                "name": instance_name.upper(), "urls": [backend_url],
                "token": backend_token,
                "tls_fingerprint": backend_fingerprint}) as response:
            refused = await response.json()
            assert response.status == 409, refused
        assert "own instance name" in refused["error"], refused
        # a second node reporting the same instance name gets no silent twin
        async with http.post(url + "/api/backends", headers=headers, json={
                "name": "", "urls": [backend_url], "token": backend_token,
                "tls_fingerprint": backend_fingerprint}) as response:
            refused = await response.json()
            assert response.status == 409, refused
        assert "pass a different name" in refused["error"], refused
        async with http.patch(url + "/api/backends/{}".format(stored["id"]),
                              headers=headers, json={"name": "Local"}) as response:
            refused = await response.json()
            assert response.status == 409 and "reserved" in refused["error"], refused
        async with http.patch(url + "/api/settings", headers=headers,
                              json={"instance_name": "Backend-Test-Node"}) as response:
            refused = await response.json()
            assert response.status == 409, refused
        assert "already the name of backend" in refused["error"], refused
        async with http.get(url + "/api/settings", headers=headers) as response:
            assert (await response.json())["instance_name"] == instance_name
        async with http.get(url + "/api/backends", headers=headers) as response:
            assert len((await response.json())["backends"]) == 1

        async with http.get(
                url + f"/api/b/{stored['id']}/browser/instances/A1B2/binding",
                headers=headers) as response:
            proxied_binding = await response.json()
            assert response.status == 404, proxied_binding
            assert "closed or unknown" in proxied_binding["error"]

        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": "yes"}) as response:
            assert response.status == 400, await response.text()

        # Display-only edits work without replacing the private token or
        # requiring a connection probe. A rejected credential edit is atomic:
        # the old pairing remains usable and no secret is returned to the UI.
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"name": "  Edited backend  "}) as response:
            renamed = await response.json()
            assert response.status == 200, renamed
        assert renamed["backend"]["name"] == "Edited backend", renamed
        assert renamed["connection_changed"] is False and renamed["remote"] is None
        assert "token" not in renamed["backend"]
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"token": "definitely-the-wrong-token"}) as response:
            rejected_edit = await response.json()
            assert response.status == 400, rejected_edit
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"tls_fingerprint": ""}) as response:
            unpinned_edit = await response.json()
            assert response.status == 400, unpinned_edit
        assert "TLS certificate verification failed" in unpinned_edit["error"]
        async with http.post(url + f"/api/backends/{stored['id']}/test",
                             headers=headers) as response:
            retained = await response.json()
            assert response.status == 200 and retained["ok"] is True, retained
        assert retained["backend"]["id"] == stored["id"]
        assert retained["backend"]["availability"]["state"] == "online"
        assert "token" not in retained["backend"]
        async with http.get(url + "/api/backends", headers=headers) as response:
            after_rejection = (await response.json())["backends"][0]
        assert after_rejection["name"] == "Edited backend", after_rejection
        assert after_rejection["urls"] == [unavailable_url, backend_url], after_rejection
        assert after_rejection["active_url"] == backend_url, after_rejection

        async with http.post(url + f"/api/backends/{stored['id']}/test",
                             headers=headers) as response:
            tested = await response.json()
            assert response.status == 200 and tested["ok"] is True, tested
        assert tested["backend"]["id"] == stored["id"]
        assert tested["backend"]["remote_version"] == old_version

        async with http.get(url + f"/api/b/{stored['id']}/sessions",
                            headers=headers) as response:
            proxied = await response.json()
            assert response.status == 200 and proxied["sessions"] == [], proxied
        assert isinstance(proxied["server_time"], (int, float))
        async with http.get(url + f"/api/b/{stored['id']}/node/upgrade",
                            headers=headers) as response:
            proxied_upgrade = await response.json()
            assert response.status == 200, proxied_upgrade
        assert proxied_upgrade["readiness"]["ready"] is True
        assert proxied_upgrade["readiness"]["state"] == "ready"

        # A backend-owned runtime blocker must disable readiness and be
        # propagated by the controller before it spends time building an artifact.
        pending_marker = backend_state_dir / "pending.json"
        pending_marker.write_text("{}\n", encoding="utf-8")
        try:
            async with http.get(url + f"/api/b/{stored['id']}/node/upgrade",
                                headers=headers) as response:
                blocked = await response.json()
                assert response.status == 200, blocked
            assert blocked["readiness"]["state"] == "blocked"
            async with http.post(url + f"/api/backends/{stored['id']}/upgrade",
                                 headers=headers) as response:
                rejected = await response.json()
                assert response.status == 409, rejected
            assert rejected["readiness"]["state"] == "blocked"
            async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                                  json={"auto_upgrade": True}) as response:
                enabled_while_blocked = await response.json()
                assert response.status == 200, enabled_while_blocked
            await asyncio.sleep(0.5)
            async with http.get(url + "/api/backends", headers=headers) as response:
                still_blocked = (await response.json())["backends"][0]
            assert still_blocked["remote_version"] == old_version
            assert still_blocked["upgrade_in_progress"] is False
            async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                                  json={"auto_upgrade": False}) as response:
                assert response.status == 200, await response.text()
        finally:
            pending_marker.unlink(missing_ok=True)
        async with http.get(url + f"/api/b/{stored['id']}/engines/usage-refresh",
                            headers=headers) as response:
            proxied_refresh = await response.json()
            assert response.status == 200, proxied_refresh
        assert proxied_refresh["usage_refresh"]["minutes"] == 0
        async with http.get(url + f"/api/b/{stored['id']}/engines",
                            headers=headers) as response:
            proxied_engines = await response.json()
            assert response.status == 200, proxied_engines
        assert isinstance(proxied_engines["engines"], list)
        assert proxied_engines["timers"]["values"]["model_catalog_minutes"] == 9
        assert proxied_engines["timeouts"]["defaults"] == {
            "turn_seconds": 7200, "spawn_runtime_seconds": 7200,
            "spawn_idle_seconds": 600, "terminal_idle_seconds": 900,
            "browser_idle_seconds": 900, "vnc_idle_seconds": 900}
        async with http.patch(url + f"/api/b/{stored['id']}/timeouts", headers=headers,
                              json={"browser_idle_seconds": 0}) as response:
            timeout_result = await response.json()
            assert response.status == 200, timeout_result
        assert timeout_result["timeouts"]["values"]["browser_idle_seconds"] == 0
        async with http.get(url + f"/api/b/{stored['id']}/timeouts", headers=headers) as response:
            assert (await response.json())["timeouts"]["values"]["browser_idle_seconds"] == 0
        async with http.patch(url + f"/api/b/{stored['id']}/timeouts", headers=headers,
                              json={"browser_idle_seconds": 900}) as response:
            assert response.status == 200

        async with http.patch(url + f"/api/b/{stored['id']}/timers", headers=headers,
                              json={"cli_status_minutes": 11}) as response:
            proxied_timers = await response.json()
            assert response.status == 200, proxied_timers
        assert proxied_timers["timers"]["values"]["cli_status_minutes"] == 11
        remote_updates = await http.ws_connect(
            url + f"/api/b/{stored['id']}/ws/updates", headers=headers)
        first = await remote_updates.receive_json(timeout=3)
        assert first["type"] == "updates_ready", first
        remote_sessions = await receive_json_type(
            remote_updates, "sessions", timeout=5)
        assert remote_sessions["sessions"] == []
        assert isinstance(remote_sessions["server_time"], (int, float))
        # A real connection edit is probed before it is stored, and closes the
        # affected backend's existing proxy channels so they reconnect through
        # the new URL/token/pin rather than remaining attached to the old peer.
        alternate_url = backend_url.replace("127.0.0.1", "localhost")
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"urls": [alternate_url]}) as response:
            moved = await response.json()
            assert response.status == 200, moved
        assert moved["connection_changed"] is True, moved
        assert moved["backend"]["url"] == alternate_url, moved
        close_deadline = asyncio.get_event_loop().time() + 5
        while True:
            closed = await remote_updates.receive(timeout=5)
            if closed.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                               aiohttp.WSMsgType.CLOSING):
                break
            assert asyncio.get_event_loop().time() < close_deadline, closed
        await remote_updates.close()
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"name": "backend-test-node",
                                    "urls": [unavailable_url, backend_url]}) as response:
            restored_connection = await response.json()
            assert response.status == 200, restored_connection
        assert restored_connection["connection_changed"] is True, restored_connection
        assert restored_connection["backend"]["name"] == "backend-test-node"

        # Prompt settings are node-owned and cross the same authenticated proxy
        # as sessions, so the controller can edit a headless node without
        # pretending the controller's own prompt applies remotely.
        async with http.get(url + f"/api/b/{stored['id']}/system-prompt",
                            headers=headers) as response:
            remote_prompt = await response.json()
            assert response.status == 200, remote_prompt
        async with http.patch(url + f"/api/b/{stored['id']}/system-prompt",
                              headers=headers,
                              json={"custom": "Controller-configured guidance.",
                                    "remote_workspace": remote_prompt["system_prompt"][
                                        "remote_workspace_default"],
                                    "browser": remote_prompt["system_prompt"]["browser_default"],
                                    "terminal": remote_prompt["system_prompt"]["terminal_default"]
                                    }) as response:
            remote_prompt = await response.json()
            assert response.status == 200, remote_prompt
        assert remote_prompt["system_prompt"]["custom"] == \
            "Controller-configured guidance."

        # Simulate the controller restarting without a remembered active URL.
        # A state-changing request may advance only after a pre-connect failure,
        # where replay cannot duplicate work on the backend.
        from puppy import backends as controller_backends
        controller_backends._active_urls.pop(stored["id"], None)
        async with http.post(url + f"/api/b/{stored['id']}/sessions", headers=headers, json={
                "engine": "codex", "workspace_kind": "temporary", "name": "proxied-scratch",
        }) as response:
            proxied_created = await response.json()
            assert response.status == 200, proxied_created
        proxied_scratch = proxied_created["session"]
        proxied_path = Path(proxied_scratch["cwd"])
        assert proxied_scratch["workspace_kind"] == "temporary" and proxied_path.is_dir()
        async with http.get(url + f"/api/b/{stored['id']}/sessions",
                            headers=headers) as response:
            proxied_sessions = await response.json()
            assert response.status == 200, proxied_sessions
        assert any(session["id"] == proxied_scratch["id"]
                   for session in proxied_sessions["sessions"])
        # Exceed both aiohttp applications' historical 8 MiB read ceiling. The
        # controller must stream the request to the node instead of buffering
        # it, while the receiving node remains the final limit authority.
        async with http.patch(
                url + f"/api/b/{stored['id']}/uploads/settings", headers=headers,
                json={"max_file_size_mb": 9}) as response:
            proxied_policy = await response.json()
            assert response.status == 200, proxied_policy
        assert proxied_policy["uploads"]["max_file_size_mb"] == 9
        async with http.get(url + "/api/backends", headers=headers) as response:
            cached_backend = (await response.json())["backends"][0]
            assert response.status == 200
        last_known = cached_backend["last_known"]
        assert last_known["version"] == 1
        assert last_known["usage_refresh"]["minutes"] == 0
        assert isinstance(last_known["engines"], list)
        assert isinstance(last_known["auto_upgrade"]["enabled"], bool)
        assert last_known["uploads"]["max_file_size_mb"] == 9
        assert last_known["browser"] == {"enabled": False}
        assert last_known["system_prompt"]["custom"] == \
            "Controller-configured guidance."
        assert any(session["id"] == proxied_scratch["id"]
                   for session in last_known["sessions"])
        proxied_file = b"MZ" + (b"x" * (8 * 1024 * 1024)) + b"streamed-through-controller"
        controller_backends._active_urls.pop(stored["id"], None)
        async with http.post(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}/upload",
                headers={
                    **headers, "Content-Type": "application/x-msdownload",
                    "X-Puppy-Filename": "deploy.exe",
                    "X-Puppy-Size": str(len(proxied_file)),
                }, data=proxied_file) as response:
            proxied_upload = await response.json()
            assert response.status == 200, proxied_upload
        proxied_upload_path = Path(proxied_upload["path"])
        assert proxied_upload["size"] == len(proxied_file)
        assert controller_backends._active_urls[stored["id"]] == backend_url
        assert proxied_upload_path.stat().st_size == len(proxied_file)
        assert proxied_upload_path.read_bytes() == proxied_file
        async with http.delete(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}/upload/" +
                proxied_upload["upload_id"], headers=headers) as response:
            assert response.status == 200, await response.text()

        # New controllers allocate a node-owned, identified PTY before opening
        # its viewer socket. The process survives a viewer reconnect and keeps
        # one four-character identity for both the user and terminal MCP tools.
        terminal_updates = await http.ws_connect(
            url + "/api/ws/updates", headers=headers)
        assert (await terminal_updates.receive_json(timeout=3))["type"] == \
            "updates_ready"
        terminal_before = await receive_json_type(
            terminal_updates, "terminal_instances", timeout=5)
        terminal_revision = terminal_before["state_revision"]
        async with http.post(url + "/api/terminal/instances", headers=headers,
                             json={"command": "/bin/bash", "cols": 80,
                                   "rows": 24}) as response:
            created_terminal = await response.json()
            assert response.status == 201, created_terminal
        terminal_id = created_terminal["terminal"]["id"]
        assert len(terminal_id) == 4 and terminal_id.isalnum() and \
            terminal_id == terminal_id.upper(), terminal_id
        while True:
            terminal_pushed = await receive_json_type(
                terminal_updates, "terminal_instances", timeout=5)
            if terminal_pushed["state_revision"] > terminal_revision and any(
                    item["id"] == terminal_id
                    for item in terminal_pushed["instances"]):
                break
        terminal_revision = terminal_pushed["state_revision"]
        async with http.get(url + "/api/terminal/instances",
                            headers=headers) as response:
            terminal_list = await response.json()
            assert response.status == 200, terminal_list
        assert any(item["id"] == terminal_id and item["running"]
                   for item in terminal_list["instances"]), terminal_list
        shared_terminal = await http.ws_connect(
            url + "/api/ws/terminal/" + terminal_id, headers=headers)
        terminal_status = await shared_terminal.receive_json(timeout=3)
        terminal_binding = await shared_terminal.receive_json(timeout=3)
        assert terminal_status["type"] == "status" and \
            terminal_status["terminal_id"] == terminal_id
        assert terminal_binding["type"] == "binding" and \
            terminal_binding["session_id"] is None
        await shared_terminal.send_bytes(b"echo PUPPY_SHARED_TERMINAL_OK\nexit\n")
        shared_output = b""
        deadline = asyncio.get_event_loop().time() + 5
        while b"PUPPY_SHARED_TERMINAL_OK" not in shared_output and \
                asyncio.get_event_loop().time() < deadline:
            message = await shared_terminal.receive(timeout=2)
            if message.type == aiohttp.WSMsgType.BINARY:
                shared_output += message.data
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        assert b"PUPPY_SHARED_TERMINAL_OK" in shared_output, shared_output[-200:]
        await shared_terminal.close()
        async with http.delete(url + "/api/terminal/instances/" + terminal_id,
                               headers=headers) as response:
            assert response.status == 200, await response.text()
        while True:
            terminal_pushed = await receive_json_type(
                terminal_updates, "terminal_instances", timeout=5)
            if terminal_pushed["state_revision"] > terminal_revision and not any(
                    item["id"] == terminal_id
                    for item in terminal_pushed["instances"]):
                break
        await terminal_updates.close()

        for retired in ("/api/ws/term", "/api/ws/browser", "/api/notify/fire"):
            async with http.get(url + retired, headers=headers) as response:
                assert response.status == 404, retired

        # Enabling the controller-owned policy wakes its background worker.
        # The node's live readiness remains authoritative, then the exact same
        # signed/restart/rollback pipeline used by the manual action runs.
        lifecycle_fanout = await http.ws_connect(
            url + "/api/ws/updates", headers=headers)
        assert (await lifecycle_fanout.receive_json(timeout=3))["type"] == \
            "updates_ready"
        old_stream_runtime = ""
        old_stream_revision = 0
        fanout_deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < fanout_deadline:
            message = await lifecycle_fanout.receive_json(timeout=10)
            if message.get("type") == "remote_stream" and \
                    message.get("backend_id") == stored["id"] and \
                    message.get("connected") is True:
                old_stream_runtime = message.get("node_runtime_id") or ""
                old_stream_revision = int(message.get("state_revision") or 0)
                break
        assert old_stream_runtime and old_stream_revision > 0
        lifecycle_updates = await http.ws_connect(
            url + f"/api/b/{stored['id']}/ws/updates", headers=headers)
        lifecycle_snapshot = await lifecycle_updates.receive_json(timeout=3)
        assert lifecycle_snapshot["type"] == "updates_ready"
        async with http.patch(url + f"/api/backends/{stored['id']}", headers=headers,
                              json={"auto_upgrade": True}) as response:
            toggled = await response.json()
            assert response.status == 200, toggled
        assert toggled["backend"]["auto_upgrade"] is True

        lifecycle_notice = None
        notice_deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < notice_deadline:
            message = await lifecycle_updates.receive_json(timeout=90)
            if message.get("type") == "node_stopping":
                lifecycle_notice = message
                break
        assert lifecycle_notice is not None, "remote restart sent no lifecycle notice"
        assert lifecycle_notice["reason"] == "restart", lifecycle_notice
        async with http.get(url + "/api/backends", headers=headers) as response:
            stopping_backend = (await response.json())["backends"][0]
        assert stopping_backend["availability"]["state"] == "offline", stopping_backend
        assert "restarting" in stopping_backend["availability"]["reason"].lower()
        assert stopping_backend["last_known"]["uploads"]["max_file_size_mb"] == 9
        assert stopping_backend["last_known"]["usage_refresh"]["minutes"] == 0
        assert stopping_backend["last_known"]["system_prompt"]["custom"] == \
            "Controller-configured guidance."
        assert any(session["id"] == proxied_scratch["id"]
                   for session in stopping_backend["last_known"]["sessions"])

        # The controller must replace its cached connected verdict even when
        # the upstream ended deliberately.  Otherwise a console attaching in
        # this restart window can suppress fallback against an offline node.
        disconnected_revision = 0
        disconnect_deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < disconnect_deadline:
            message = await lifecycle_fanout.receive_json(timeout=10)
            if message.get("type") == "remote_stream" and \
                    message.get("backend_id") == stored["id"] and \
                    message.get("connected") is False:
                disconnected_revision = int(message.get("state_revision") or 0)
                break
        assert disconnected_revision > old_stream_revision
        await lifecycle_updates.close()

        deadline = asyncio.get_event_loop().time() + 120
        refreshed = None
        while asyncio.get_event_loop().time() < deadline:
            async with http.get(url + "/api/backends", headers=headers) as response:
                refreshed = (await response.json())["backends"][0]
                assert response.status == 200
            if refreshed["remote_version"] == __version__ and \
                    not refreshed["upgrade_in_progress"]:
                break
            await asyncio.sleep(0.25)
        assert refreshed is not None and refreshed["remote_version"] == __version__, refreshed
        assert refreshed["availability"]["state"] == "online", refreshed
        assert refreshed["auto_upgrade"] is True
        assert "remote-upgrade" in refreshed["capabilities"]

        # The controller process did not restart, so its outer topic revision
        # stays monotonic while the nested backend runtime/revisions restart.
        # An already-open console must accept, not discard, the replacement.
        new_stream_runtime = ""
        new_stream_revision = 0
        saw_current_node = False
        fanout_deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < fanout_deadline and \
                (not new_stream_runtime or not saw_current_node):
            message = await lifecycle_fanout.receive_json(timeout=15)
            if message.get("type") == "remote_stream" and \
                    message.get("backend_id") == stored["id"] and \
                    message.get("connected") is True and \
                    message.get("node_runtime_id") != old_stream_runtime:
                new_stream_runtime = message.get("node_runtime_id") or ""
                new_stream_revision = int(message.get("state_revision") or 0)
            if message.get("type") == "remote_state" and \
                    message.get("backend_id") == stored["id"]:
                event = message.get("event") or {}
                if event.get("type") == "node" and \
                        event.get("runtime_id") != old_stream_runtime and \
                        event.get("version") == __version__:
                    saw_current_node = True
        assert new_stream_runtime and new_stream_runtime != old_stream_runtime
        assert new_stream_revision > disconnected_revision
        assert saw_current_node
        await lifecycle_fanout.close()
        async with http.delete(
                url + f"/api/b/{stored['id']}/sessions/{proxied_scratch['id']}",
                headers=headers) as response:
            assert response.status == 200, await response.text()
        assert not proxied_path.exists()


async def exercise_redirect_rejection() -> None:
    redirected_tokens = []

    async def redirect(_request):
        raise web.HTTPFound(location="/capture")

    async def capture(request):
        redirected_tokens.append(request.headers.get("X-Puppy-Token"))
        return web.Response(text="captured")

    app = web.Application()
    app.router.add_get("/api/ping", redirect)
    app.router.add_get("/api/ws/updates", redirect)
    app.router.add_get("/capture", capture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    token = "redirect-test-token-0123456789abcdef"
    try:
        from puppy import backends

        probed = await backends.probe_backend(origin, token)
        assert probed["ok"] is False
        assert redirected_tokens == []
        try:
            await backends.client().ws_connect(
                f"ws://127.0.0.1:{port}/api/ws/updates",
                headers={"X-Puppy-Token": token})
            raise AssertionError("backend WebSocket redirect was accepted")
        except (aiohttp.ClientError, RuntimeError):
            pass
        assert redirected_tokens == []
    finally:
        await runner.cleanup()


async def exercise_proxy_recovery(controller_url: str, controller_token: str) -> None:
    """HTTP and WebSocket proxies follow a node between configured origins."""
    from puppy import backends as controller_backends

    backend_token = "recovery-backend-token-0123456789abcdef"
    ports = [free_port(), free_port()]
    backend_urls = [f"http://127.0.0.1:{port}" for port in ports]
    request_counts = {port: {"ping": 0, "sessions": 0, "updates": 0} for port in ports}

    async def start_backend(port: int) -> web.AppRunner:
        async def authorized(request):
            if request.headers.get("X-Puppy-Token") != backend_token:
                return web.json_response({"error": "unauthorized"}, status=401)
            return None

        async def ping(request):
            request_counts[port]["ping"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({
                "ok": True, "name": "recovery-node", "version": __version__,
                "protocol": protocol.API_PROTOCOL, "role": "backend",
                "capabilities": ["sessions"],
                "transport": {"encrypted": False},
            })

        async def sessions(request):
            request_counts[port]["sessions"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({"type": "sessions", "sessions": []})

        async def engines(request):
            denied = await authorized(request)
            if denied is not None:
                return denied
            return web.json_response({"engines": []})

        async def updates(request):
            request_counts[port]["updates"] += 1
            denied = await authorized(request)
            if denied is not None:
                return denied
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "sessions", "sessions": []})
            async for _message in ws:
                pass
            return ws

        app = web.Application()
        app.router.add_get("/api/ping", ping)
        app.router.add_get("/api/sessions", sessions)
        app.router.add_get("/api/engines", engines)
        app.router.add_get("/api/ws/updates", updates)
        runner = web.AppRunner(app, shutdown_timeout=0.2)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        return runner

    headers = {"X-Puppy-Token": controller_token}
    backend_runner = await start_backend(ports[0])
    backend_id = None
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(controller_url + "/api/backends", headers=headers, json={
                    "name": "recovery-node", "urls": backend_urls, "token": backend_token,
            }) as response:
                added = await response.json()
                assert response.status == 200, added
            backend_id = added["id"]
            assert "active-turn-steering" not in \
                (added["remote"].get("capabilities") or [])
            proxy_url = controller_url + f"/api/b/{backend_id}"

            async with http.patch(controller_url + f"/api/backends/{backend_id}",
                                  headers=headers, json={"auto_upgrade": True}) as response:
                unsupported = await response.json()
                assert response.status == 409, unsupported
            assert "upgrade-capable headless backend" in unsupported["error"]

            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"

            await backend_runner.cleanup()
            backend_runner = None
            closed = await updates.receive(timeout=2)
            assert closed.type in (
                aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                               aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR), closed
            await updates.close()

            # The same authenticated node appears at its alternate address.
            # The stale primary remains first in configuration, but both HTTP
            # and WebSocket handshakes must advance to the live candidate and
            # publish that exact address as active.
            backend_runner = await start_backend(ports[1])
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            async with http.get(controller_url + "/api/backends", headers=headers) as response:
                moved = (await response.json())["backends"]
            active = next(item for item in moved if item["id"] == backend_id)
            assert active["urls"] == backend_urls and active["active_url"] == backend_urls[1]
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"

            await backend_runner.cleanup()
            backend_runner = None
            closed = await updates.receive(timeout=2)
            assert closed.type in (
                aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR), closed
            await updates.close()
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                unavailable = await response.json()
                assert response.status == 503, unavailable
                assert unavailable["availability"]["state"] == "offline", unavailable
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("dead backend completed the proxied WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status == 503, exc.status

            backend_runner = await start_backend(ports[0])
            # Starting a listener does not let browser traffic rediscover it.
            # Until the controller's authenticated health probe succeeds, the
            # proxy gate responds locally and touches no backend API route.
            sessions_before = request_counts[ports[0]]["sessions"]
            updates_before = request_counts[ports[0]]["updates"]
            started = time.monotonic()
            for _attempt in range(3):
                async with http.get(proxy_url + "/sessions", headers=headers) as response:
                    assert response.status == 503, await response.text()
            try:
                await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
                raise AssertionError("offline gate completed a WebSocket handshake")
            except aiohttp.WSServerHandshakeError as exc:
                assert exc.status == 503, exc.status
            assert time.monotonic() - started < 0.5
            assert request_counts[ports[0]]["sessions"] == sessions_before
            assert request_counts[ports[0]]["updates"] == updates_before

            # Force the normally backoff-scheduled recovery probe due now and
            # wait for its controller-owned availability broadcast/state.
            controller_backends._health_retry_after[backend_id] = 0
            controller_backends._wake_health()
            recovered = False
            for _attempt in range(50):
                async with http.get(controller_url + "/api/backends",
                                    headers=headers) as response:
                    listed = (await response.json())["backends"]
                current = next(item for item in listed if item["id"] == backend_id)
                recovered = current["availability"]["state"] == "online"
                if recovered:
                    break
                await asyncio.sleep(0.1)
            assert recovered, "controller health probe did not recover the backend"
            assert request_counts[ports[0]]["ping"] >= 1
            async with http.get(proxy_url + "/sessions", headers=headers) as response:
                assert response.status == 200, await response.text()
            async with http.get(controller_url + "/api/backends", headers=headers) as response:
                returned = (await response.json())["backends"]
            active = next(item for item in returned if item["id"] == backend_id)
            assert active["active_url"] == backend_urls[0]
            updates = await http.ws_connect(proxy_url + "/ws/updates", headers=headers)
            assert (await updates.receive_json(timeout=2))["type"] == "sessions"
            await updates.close()
    finally:
        if backend_id is not None:
            async with aiohttp.ClientSession() as http:
                async with http.delete(
                        controller_url + f"/api/backends/{backend_id}", headers=headers) as response:
                    assert response.status == 200
        if backend_runner is not None:
            await backend_runner.cleanup()


async def exercise_launcher_rollback(artifact: Path, launcher: Path, state_dir: Path,
                                     data_dir: Path, backend_url: str,
                                     backend_token: str,
                                     backend_fingerprint: str) -> subprocess.Popen:
    backup = artifact.with_name(artifact.stem + ".previous" + artifact.suffix)
    shutil.copy2(artifact, backup)
    previous_sha = upgrade_contract.artifact_sha256(backup.read_bytes())
    bad_payload = b"import sys\nsys.exit(23)\n"
    artifact.write_bytes(bad_payload)
    artifact.chmod(0o755)
    status_path = state_dir / "status.json"
    marker = state_dir / "pending.json"
    marker.write_text(json.dumps({
        "format": upgrade_contract.LAUNCHER_PROTOCOL,
        "artifact": str(artifact.resolve()),
        "backup": str(backup.resolve()),
        "status_path": str(status_path.resolve()),
        "data_dir": str(data_dir.resolve()),
        "previous_version": __version__,
        "previous_sha256": previous_sha,
        "target_version": "9.9.9",
        "target_sha256": upgrade_contract.artifact_sha256(bad_payload),
        "health": {"host": "127.0.0.1", "port": int(backend_url.rsplit(":", 1)[1]),
                   "tls": True, "certificate_sha256": backend_fingerprint},
    }), encoding="utf-8")
    process = subprocess.Popen([
        sys.executable, str(launcher), "--artifact", str(artifact),
        "--state-dir", str(state_dir), "--", "serve", "--data-dir", str(data_dir),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    await wait_for_backend(backend_url, process, backend_fingerprint)
    async with aiohttp.ClientSession() as http:
        async with http.get(backend_url + "/api/ping",
                            headers={"X-Puppy-Token": backend_token},
                            ssl=ssl_pin(backend_fingerprint)) as response:
            ping = await response.json()
            assert response.status == 200 and ping["version"] == __version__, ping
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "rolled-back", status
    assert not marker.exists()
    assert upgrade_contract.artifact_sha256(artifact.read_bytes()) == previous_sha
    return process


async def check_notify_placeholders() -> None:
    """The completion-command contract both runtimes expand and export.

    Imported here, not at module scope: notify pulls in puppy.config, which
    binds its data path at import time and must not load before PUPPY_DATA.
    """
    from puppy import backends, db, notify
    # the leading unit is never zero-padded, so the shape follows the span
    for seconds, expected in [
            (0, "0:00"), (7, "0:07"), (59, "0:59"), (60, "1:00"),
            (599, "9:59"),          # M:SS below ten minutes
            (600, "10:00"), (3599, "59:59"),        # MM:SS below the hour
            (3600, "1:00:00"), (35999, "9:59:59"),  # H:MM:SS below ten hours
            (36000, "10:00:00"), (86399, "23:59:59"),   # HH:MM:SS thereafter
            (360000, "100:00:00")]:
        assert notify.clock(seconds) == expected, (seconds, notify.clock(seconds))
    assert notify.clock(-5) == "0:00"
    assert "duration_hms" in notify.PLACEHOLDERS

    info = notify.clean_info({"session": "my app", "duration": "3661"})
    assert info["duration_hms"] == "1:01:01", info
    assert notify.expand("{session} {duration_hms} {duration}", info) == \
        "'my app' 1:01:01 3661"
    # derived on the node, so a reporting console cannot supply its own
    hostile = notify.clean_info({"duration": "90", "duration_hms": "$(touch /tmp/x)"})
    assert hostile["duration_hms"] == "1:30", hostile
    # and a completion with no duration simply has neither
    assert "duration_hms" not in notify.clean_info({"session": "s"})

    # A deliberate stop is not completed work. Test the hook itself so this
    # stays true even if a caller accidentally passes an interrupted outcome
    # while notification settings are armed.
    fired = []
    original_active, original_fire = notify.active, notify._fire

    async def capture_fire(payload):
        fired.append(payload)

    try:
        notify.active = lambda: True
        notify._fire = capture_fire
        session = {"id": 9, "name": "stopped", "engine": "codex",
                   "model": "configured", "last_model": "actual-model",
                   "cwd": "/private/mirror/project",
                   "workspace": json.dumps({
                       "uid": "abcd1234", "root": "/authoritative/project",
                       "node": "remote", "label": "remote project"})}
        notify.session_finished(session, "interrupted", 12)
        await asyncio.sleep(0)
        assert fired == [], fired
        notify.session_finished(session, "ok", 13)
        await asyncio.sleep(0)
        assert len(fired) == 1 and fired[0]["status"] == "ok", fired
        assert fired[0]["model"] == "actual-model"
        assert fired[0]["cwd"] == "/authoritative/project"
        history = notify.completion_events(0)
        assert len(history["stream_id"]) == 32 and history["cursor"] >= 2
        assert history["completions"][-1]["cwd"] == "/authoritative/project"

        # The controller establishes a baseline once, then consumes durable
        # remote records itself. It advances only after attempting delivery;
        # interrupted work and a replaced/restored stream never ring.
        bid = 987
        notify.forget_backend(bid)
        stream = "a" * 32

        def record(seq, status="ok"):
            return {
                "seq": seq, "completion_id": "{:032x}".format(seq),
                "session_id": 44, "session": "remote session",
                "engine": "codex", "model": "actual", "status": status,
                "duration": 5, "cwd": "/remote/project",
                "started_at": 10.0, "finished_at": 15.0,
            }

        replies = [
            {"ok": True, "stream_id": stream, "cursor": 1,
             "truncated": False, "completions": [record(1)]},
            {"ok": True, "stream_id": stream, "cursor": 2,
             "truncated": False, "completions": [record(2)]},
            {"ok": True, "stream_id": stream, "cursor": 3,
             "truncated": False,
             "completions": [record(3, "interrupted")]},
            {"ok": True, "stream_id": "b" * 32, "cursor": 1,
             "truncated": False, "completions": [record(1)]},
            # A same-stream gap is rejected; the cursor must not advance past
            # an event the node failed to return.
            {"ok": True, "stream_id": "b" * 32, "cursor": 3,
             "truncated": False, "completions": [record(3)]},
        ]
        requested = []
        original_fetch = backends.fetch_completion_events

        async def fetch(_bid, after):
            requested.append(after)
            return replies.pop(0)

        backends.fetch_completion_events = fetch
        fired.clear()
        try:
            backend = {"id": bid, "name": "remote"}
            for _ in range(5):
                await notify._poll_backend(backend)
        finally:
            backends.fetch_completion_events = original_fetch
            notify.forget_backend(bid)
        assert requested == [0, 1, 2, 3, 1], requested
        assert [item["seq"] for item in fired] == [2], fired
    finally:
        notify.active, notify._fire = original_active, original_fire


def exercise_auth_evidence(root, db) -> None:
    """Local login verbs are optimistic - codex answers "Logged in" from its
    file while the refresh token behind it is revoked. Hard evidence from the
    vendor's own responses (a 401 account read, a failed turn) overrides an ok
    probe, survives restarts, and lifts on re-login or any authed success."""
    from puppy.drivers import base as driver_base
    from puppy.drivers.codex import CodexDriver
    root = Path(root)
    home = root / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    saved = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    try:
        for text, want in [
            ("Your access token could not be refreshed because your refresh "
             "token was revoked. Please log out and sign in again.", True),
            ("GET https://chatgpt.com/backend-api/wham/usage failed: "
             "401 Unauthorized", True),
            ("OAuth token has expired · Please run /login", True),
            ("engine exited without a result (exit 1)", False),
            ("stream error: connection reset by peer", False),
        ]:
            assert driver_base.looks_like_auth_failure(text) is want, text

        codex = CodexDriver()
        auth = home / "auth.json"
        auth.write_text("{}")
        os.utime(auth, (time.time() - 3600,) * 2)
        ok = {"installed": True, "auth": "ok", "detail": "Logged in using ChatGPT"}
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
        driver_base.note_auth_failure("codex", "401 Unauthorized")
        overlaid = driver_base.apply_auth_evidence(codex, dict(ok))
        assert overlaid["auth"] == "expired" and "401" in overlaid["detail"]
        # a probe already negative keeps its own words
        kept = driver_base.apply_auth_evidence(
            codex, {"installed": True, "auth": "missing", "detail": "Not logged in"})
        assert kept["auth"] == "missing"
        # re-login rewrites the credential file: evidence lifts by itself
        os.utime(auth, None)
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
        assert db.meta_get("auth_evidence.codex") is None
        # authenticated success is the other way out
        driver_base.note_auth_failure("codex", "401")
        os.utime(auth, (time.time() - 3600,) * 2)
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "expired"
        driver_base.clear_auth_failure("codex")
        assert driver_base.apply_auth_evidence(codex, dict(ok))["auth"] == "ok"
    finally:
        if saved is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = saved


async def exercise_auth_probes(root) -> None:
    """The ready/no-auth word must come from each CLI's own auth verb, judged
    by exit code and structure - never by substring. "Not logged in" CONTAINS
    "logged in", which once reported a logged-out codex as ready."""
    from puppy.drivers.claude import ClaudeDriver
    from puppy.drivers.codex import CodexDriver
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def stub(name, script):
        path = root / name
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(0o755)
        return str(path)

    codex = CodexDriver()
    for script, expected in [
        ('echo "Logged in using ChatGPT"; exit 0', "ok"),
        ('echo "Not logged in"; exit 1', "missing"),               # the build-node.lan case
        ('echo "WARNING: preamble"; echo "Not logged in"; exit 1', "missing"),
        ('echo "You are signed out"; exit 1', "missing"),          # future rewording
        ('echo "Session active"; exit 0', "ok"),                   # rc stays the contract
    ]:
        codex.binary = stub("codex-case", script)
        got = (await codex._auth_status())["auth"]
        assert got == expected, (script, got)
    codex.binary = str(root / "codex-absent")
    assert (await codex._auth_status())["auth"] == "unknown"

    claude = ClaudeDriver()
    home = os.environ.get("HOME")
    scratch = root / "home"
    (scratch / ".claude").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(scratch)
    try:
        for script, creds, expected in [
            ("echo '{\"loggedIn\": true, \"authMethod\": \"claude.ai\"}'; exit 0",
             False, "ok"),
            ("echo '{\"loggedIn\": false}'; exit 1", True, "missing"),  # stale creds file
            ("echo \"error: unknown command 'auth'\" >&2; exit 1", True, "ok"),
            ("echo \"error: unknown command 'auth'\" >&2; exit 1", False, "missing"),
        ]:
            cred = scratch / ".claude" / ".credentials.json"
            if creds:
                cred.write_text("{}")
            elif cred.exists():
                cred.unlink()
            claude.binary = stub("claude-case", script)
            got = (await claude._auth_status())["auth"]
            assert got == expected, (script, creds, got)
    finally:
        if home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = home


def exercise_session_show_meta(runner, db) -> None:
    """The head strip is a per-session flag on the shared session payload: on by
    default, a real boolean on the wire so a console can trust it."""
    sid = db.create_session("meta", "claude", "/tmp", "", "", "blue", "auto")
    hub = runner.hub(sid)

    def listed():
        rows = [x for x in runner.sessions_payload()["sessions"] if x["id"] == sid]
        assert rows, "session missing from the list payload"
        return rows[0]

    try:
        assert runner.session_payload(db.get_session(sid))["show_meta"] is True
        db.touch_session(sid, show_meta=0)
        assert runner.session_payload(db.get_session(sid))["show_meta"] is False
        db.touch_session(sid, show_meta=1)
        assert runner.session_payload(db.get_session(sid))["show_meta"] is True
        # BOTH payloads carry it. The sidebar menu reads the LIST, and is the
        # only way back once the head is hidden - serving it without the field
        # left that menu stuck showing "on", so every click hid it again.
        assert listed()["show_meta"] is True
        db.touch_session(sid, show_meta=0)
        assert listed()["show_meta"] is False
        # The same list and per-session snapshot expose why an activity block
        # went idle. This is transient wire state, not a persisted session flag.
        hub.last_completion_status = "interrupted"
        assert listed()["completion_status"] == "interrupted"
        assert hub.snapshot()["completion_status"] == "interrupted"
        hub.status = "running"
        assert listed()["completion_status"] == ""
        assert hub.snapshot()["completion_status"] == ""
        private_cwd = "/private/data/mirrors/abcd1234/project"
        public_cwd = "/srv/authoritative/project"
        db.touch_session(sid, cwd=private_cwd, workspace=json.dumps({
            "uid": "abcd1234", "root": public_cwd,
            "node": "files", "label": "files:" + public_cwd,
        }, separators=(",", ":")))
        assert runner.session_payload(db.get_session(sid))["cwd"] == public_cwd
        assert listed()["cwd"] == public_cwd
        assert hub.snapshot()["session"]["cwd"] == public_cwd
        from puppy import search
        title = search._title_body(db.query_one(
            "SELECT id,name,cwd,workspace,created_at FROM sessions WHERE id=?",
            (sid,)))
        assert public_cwd in title and private_cwd not in title
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_queue_persistence(runner, db) -> None:
    """Queued prompts belong to the user: they survive a kill and a restart as
    held items, come back only on an explicit re-send, and never run twice."""
    def assert_raises(kind, call):
        try:
            call()
        except kind:
            return
        raise AssertionError("expected " + kind.__name__)

    sid = db.create_session("queue survival", "claude", "/tmp", "", "", "blue", "auto")
    try:
        h = runner.hub(sid)
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        h.send_message("first")
        for text in ("second", "third"):
            h.send_message(text)
        assert h.queue == ["second", "third"]
        assert db.meta_get("session_queue.{}".format(sid)) == {
            "queue": ["second", "third"], "held": [], "paused": []}

        # a deploy kill parks the queue durably instead of discarding it
        await h.kill()
        assert h.queue == [] and h.held == ["second", "third"]

        # a restart restores everything as held; a second restart adds nothing
        runner.drop_hub(sid)
        h = runner.hub(sid)
        assert h.queue == [] and h.held == ["second", "third"]
        runner.drop_hub(sid)
        h = runner.hub(sid)
        assert h.held == ["second", "third"]

        # held items move only on explicit instruction, guarded by their text
        assert "error" in h.requeue_held(0, "not the item")
        restarted = []
        h._start_turn = lambda text: (restarted.append(text),
                                      setattr(h, "status", "running"))
        assert h.requeue_held(0, "second") == {"queued": False}
        assert restarted == ["second"]
        assert h.requeue_held(0, "third") == {"queued": True}
        assert h.queue == ["third"] and h.held == []

        # during shutdown the queue is left for kill() to park, not consumed
        runner._draining = True
        try:
            assert h._take_next_turn() is None
            assert h.queue == ["third"] and h.status == "idle"
        finally:
            runner._draining = False
        await h.kill()
        assert h.held == ["third"]
        assert h.discard_held(0, "third") == {"ok": True}
        assert db.meta_get("session_queue.{}".format(sid)) is None

        # Restart preserves current item identities and refuses invalid state
        # before rewriting any queued work.
        from puppy.drivers import get_driver
        eng_fields = {"engine": "codex", "model": "gpt-x", "effort": "",
                      "permission_mode": get_driver("codex").default_permission(),
                      "fast_mode": "off"}
        fields = {"model": "claude-x", "engine": "claude"}
        queued = {"kind": "engine", "fields": eng_fields,
                  "key": runner._queued_engine_key(eng_fields)}
        held = {"kind": "config", "fields": fields,
                "key": runner._queued_config_key(fields)}
        current = {"queue": [queued, "paused prompt"], "held": [held], "paused": [1]}
        runner.drop_hub(sid)
        db.meta_set("session_queue.{}".format(sid), current)
        runner.validate_persisted_queues(db.connect())
        h = runner.hub(sid)
        assert h.held == [queued, "paused prompt", held] and not h.queue
        assert not h.paused_queue
        assert db.meta_get("session_queue.{}".format(sid)) == {
            "queue": [], "held": h.held, "paused": []}
        for invalid in (
                {"queue": [queued], "held": []},
                {"queue": [dict(queued, key="stale")], "held": [], "paused": []},
                {"queue": ["keep", {"kind": "config", "fields": {"model": "old"}}],
                 "held": [], "paused": []},
                {"queue": ["keep"], "held": [], "paused": [2]},
                {"queue": [queued], "held": [], "paused": [0]},
                {"queue": [], "held": [], "paused": []}):
            runner.drop_hub(sid)
            db.meta_set("session_queue.{}".format(sid), invalid)
            before = db.query_one("SELECT value FROM meta WHERE key=?",
                                 ("session_queue.{}".format(sid),))["value"]
            for operation in (lambda: runner.hub(sid),
                              lambda: runner.validate_persisted_queues(db.connect())):
                assert_raises(ValueError, operation)
            assert db.query_one("SELECT value FROM meta WHERE key=?",
                                ("session_queue.{}".format(sid),))["value"] == before
        db.meta_del("session_queue.{}".format(sid))
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)
    assert db.meta_get("session_queue.{}".format(sid)) is None


async def exercise_queue_pause(runner, db) -> None:
    """Pausing skips only that prompt, stays durable/stale-safe, and remains
    distinct from held work."""
    sid = db.create_session("queue pause", "claude", "/tmp", "", "", "blue", "auto")
    try:
        h = runner.hub(sid)
        pending_config = {
            "kind": "config", "fields": {"model": "paused-model"},
            "key": 'config:{"model": "paused-model"}',
        }
        h.status = "running"
        h.active_since = time.time() - 5
        h.queue = ["second", pending_config, "third", "fourth"]
        h._broadcast_queue()

        # Only prompts can be paused, and the same stale text guard used by X
        # prevents a shifted index from changing some other prompt.
        assert "error" in h.set_queue_paused(1, pending_config["key"], True)
        assert "error" in h.set_queue_paused(2, "not third", True)
        assert h.set_queue_paused(2, "third", True) == {"ok": True, "paused": True}
        assert h.snapshot()["paused"] == [2]
        assert db.meta_get("session_queue.{}".format(sid))["paused"] == [2]

        # Earlier work still runs. Consuming it shifts the paused index; the
        # later unpaused prompt then passes it and sees the intervening config.
        assert h._take_next_turn() == "second"
        assert h.queue == [pending_config, "third", "fourth"]
        assert h._paused_wire() == [1]
        assert h._take_next_turn() == "fourth"
        assert h.queue == ["third"] and h._paused_wire() == [0]
        assert db.get_session(sid)["model"] == "paused-model"
        assert h._take_next_turn() is None and h.status == "idle"

        # Play resumes the only remaining prompt immediately.
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h.set_queue_paused(0, "third", False) == {"ok": True, "paused": False}
        assert started == ["third"]
        assert h.queue == [] and h._paused_wire() == []
        assert db.get_session(sid)["model"] == "paused-model"

        # Pausing the front of an idle queue starts the later prompt rather
        # than stranding it. Removing the paused row clears its sidecar state.
        h.status = "idle"
        h.queue = ["remove me", "fifth"]
        assert h.set_queue_paused(0, "remove me", True) == {
            "ok": True, "paused": True}
        assert started == ["third", "fifth"]
        assert h.queue == ["remove me"] and h._paused_wire() == [0]
        assert h.unqueue(0, "remove me") == {"ok": True}
        assert h.queue == [] and h._paused_wire() == []

        # Edit is one guarded queue -> durable-draft operation. It cannot move
        # a shifted neighbor or a pending configuration row, and popping the
        # prompt remaps pause state exactly like every other queue mutation.
        h.status = "running"
        h.queue = ["edit this", pending_config, "keep paused"]
        h.paused_queue = {0, 2}
        await h.update_draft("replace this composer")
        assert "error" in await h.edit_queued(0, "not edit this")
        assert "cannot be edited" in (await h.edit_queued(
            1, pending_config["key"]))["error"]
        edited = await h.edit_queued(0, "edit this")
        assert edited["ok"] is True and edited["started"] is False
        assert edited["draft"]["type"] == "draft"
        assert edited["draft"]["text"] == "edit this"
        assert db.get_session_draft(sid)["text"] == "edit this"
        assert h.queue == [pending_config, "keep paused"]
        assert h._paused_wire() == [1]
        assert db.meta_get("session_queue.{}".format(sid)) == {
            "queue": [pending_config, "keep paused"],
            "held": [], "paused": [1],
        }

        # If the active turn ended just before the guarded edit arrived, the
        # edited row is skipped and the next runnable prompt proceeds.
        h.status = "idle"
        h.queue = ["edit after finish", "run after edit"]
        h.paused_queue.clear()
        edited_idle = await h.edit_queued(0, "edit after finish")
        assert edited_idle["ok"] is True and edited_idle["started"] is True
        assert started[-1] == "run after edit" and h.queue == []
        assert db.get_session_draft(sid)["text"] == "edit after finish"

        # A kill instead moves prose to held, where resend/discard already
        # supply the only state transition and pause must not leak through.
        h.status = "running"
        h.queue = ["held after stop"]
        assert h.set_queue_paused(0, "held after stop", True) == {
            "ok": True, "paused": True}
        await h.kill()
        assert h.queue == [] and h.held == ["held after stop"]
        assert h.snapshot()["paused"] == []
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_session_order(runner, db) -> None:
    """Pins and activity/manual ordering form one canonical node-owned list."""
    assert db.list_sessions(include_archived=True) == []
    ids = [db.create_session("order {}".format(name), "claude", "/tmp", "", "",
                             "blue", "auto") for name in ("a", "b", "c")]
    a, b, c = ids

    def order():
        return [s["id"] for s in db.list_sessions(include_archived=True)]

    def numbering():
        return [s["sort_order"] for s in db.list_sessions(include_archived=True)]

    def pinned_ids():
        return [s["id"] for s in db.list_sessions(include_archived=True)
                if s["pinned"]]

    def recencies():
        return [s["order_at"] for s in db.list_sessions(include_archived=True)]

    def assert_canonical():
        count = len(pinned_ids())
        assert numbering() == list(range(-count, 0)) + \
            list(range(1, len(order()) - count + 1)), numbering()
        values = recencies()
        assert values[:count] == [0] * count
        assert values[count:] == sorted(values[count:], reverse=True)
        db.require_current_schema(db.connect())

    def assert_raises(kind, call):
        try:
            call()
        except kind:
            return
        raise AssertionError("{} was not raised".format(kind.__name__))

    hubs = {}
    tasks = []
    try:
        assert order() == [a, b, c]
        assert recencies() == [0, 0, 0]
        for sid in ids:
            hub = runner.SessionHub(sid)
            runner._hubs[sid] = hub

            async def no_turn(_item):
                return None
            hub._run_turn = no_turn   # the transition is what is under test
            hubs[sid] = hub
        hubs[c]._start_turn("first prompt")
        tasks.append(hubs[c].turn_task)
        assert order() == [c, a, b], order()
        first_recency = recencies()
        assert first_recency[0] > 0 and first_recency[1:] == [0, 0]
        # a queued continuation within the same activity block stays put
        hubs[c]._start_turn("queued prompt")
        tasks.append(hubs[c].turn_task)
        assert order() == [c, a, b], order()
        assert recencies() == first_recency
        hubs[b]._start_turn("later prompt")
        tasks.append(hubs[b].turn_task)
        assert order() == [b, c, a], order()
        # finishing changes nothing; the next activation does
        hubs[b].status = "idle"
        hubs[b].active_since = None
        assert order() == [b, c, a], order()
        before_completion = recencies()
        db.touch_session(b, status="idle", name="renamed")
        db.add_event(b, "result", {"status": "ok"})
        assert recencies() == before_completion
        hubs[a]._start_turn("newest prompt")
        tasks.append(hubs[a].turn_task)
        assert order() == [a, b, c], order()
        assert_canonical()
        # a manual drag edits the same order the activations produced
        slots = recencies()
        db.reorder_sessions([c, a, b], expected_order=[a, b, c],
                            expected_pinned=[])
        assert order() == [c, a, b], order()
        assert recencies() == slots
        assert_canonical()
        hubs[a].status = "idle"
        hubs[a].active_since = None
        hubs[a]._start_turn("again")
        tasks.append(hubs[a].turn_task)
        assert order() == [a, c, b], order()

        # Already-first activity must still get a new global position. A
        # compare token catches this change even though IDs/pins are unchanged.
        before = recencies()
        from unittest.mock import patch
        with patch.object(db.time, "time", return_value=1.0):
            db.bump_session_to_top(a)
        assert order() == [a, c, b]
        assert recencies()[0] > before[0]
        assert recencies()[1:] == before[1:]
        assert_raises(db.SessionOrderConflict, lambda: db.reorder_sessions(
            [c, a, b], expected_order=order(), expected_pinned=[],
            expected_recency=before))
        assert order() == [a, c, b]
        assert_canonical()
        # Recency is durable, not a runner/browser cache.
        saved = recencies()
        with db._lock:
            db._conn.close()
            db._conn = None
        assert recencies() == saved
        # Invalid persisted recencies fail before any reorder can overwrite
        # them. An operator must repair the ledger explicitly.
        key = "session_order_at." + str(c)
        original = db.meta_get(key)
        db.meta_set(key, saved[0] + 1)
        corrupt = recencies()
        try:
            assert_raises(db.SchemaMismatchError,
                          lambda: db.require_current_schema(db.connect()))
            assert_raises(db.SchemaMismatchError, lambda: db.reorder_sessions(
                [c, a, b], expected_order=order(), expected_pinned=[]))
            assert recencies() == corrupt
            assert order() == [a, c, b]
        finally:
            db.meta_set(key, original)

        # New pins join below established pins. Repeating the same PATCH is a
        # true no-op, and activity never disturbs their manual priority.
        assert db.set_session_pinned(c, True, expected=False) is True
        assert order() == [c, a, b]
        assert db.set_session_pinned(b, True, expected=False) is True
        assert order() == [c, b, a]
        assert db.set_session_pinned(b, True, expected=True) is False
        pin_recency = recencies()
        db.bump_session_to_top(b)
        assert order() == [c, b, a]
        assert recencies() == pin_recency
        assert pinned_ids() == [c, b]
        assert_canonical()

        # A client asking to cross the boundary can only
        # reorder within the two authoritative cohorts.
        db.reorder_sessions([a, b, c], expected_order=order(),
                            expected_pinned=pinned_ids())
        assert order() == [b, c, a]
        db.reorder_sessions([c, b, a], expected_order=[b, c, a],
                            expected_pinned=[b, c])
        assert order() == [c, b, a]
        assert db.set_session_pinned(c, False, expected=True) is True
        assert order() == [b, c, a]
        db.bump_session_to_top(a)
        assert order() == [b, a, c]
        assert_canonical()

        # Archived rows remain members of both compare tokens and the pin
        # partition even while the ordinary sidebar view hides them.
        db.touch_session(b, archived=1)
        assert [s["id"] for s in db.list_sessions()] == [a, c]
        assert order() == [b, a, c]

        # All-pinned is a special create edge: the new ordinary row must be 1,
        # never zero (which is legacy-unpinned but non-canonical).
        d = db.create_session("order d", "claude", "/tmp", "", "", "blue", "auto")
        ids.append(d)
        for sid in (a, c, d):
            assert db.set_session_pinned(sid, True, expected=False) is True
        assert order() == [b, a, c, d]
        e = db.create_session("order e", "claude", "/tmp", "", "", "blue", "auto")
        ids.append(e)
        assert order() == [b, a, c, d, e]
        assert numbering() == [-4, -3, -2, -1, 1]

        # Syntax failures are 400 at the HTTP layer; stale membership, order,
        # cohort, and pin expectations are conflicts and never write.
        assert_raises(ValueError,
                      lambda: db.reorder_sessions([b, a, a, c, d, e],
                          expected_order=order(), expected_pinned=pinned_ids()))
        assert_raises(db.SessionOrderConflict,
                      lambda: db.reorder_sessions([b, a, c, d],
                          expected_order=order(), expected_pinned=pinned_ids()))
        assert_raises(db.SessionOrderConflict, lambda: db.reorder_sessions(
            [b, a, c, d, e], expected_order=[e, b, a, c, d],
            expected_pinned=[b, a, c, d]))
        assert_raises(db.SessionOrderConflict, lambda: db.reorder_sessions(
            [b, a, c, d, e], expected_order=[b, a, c, d, e],
            expected_pinned=[a, b, c, d]))
        assert_raises(db.SessionOrderConflict,
                      lambda: db.set_session_pinned(e, True, expected=True))
        assert order() == [b, a, c, d, e]

        # A valid request may arrange either cohort, but never interleave them.
        db.reorder_sessions([e, d, c, b, a],
                            expected_order=[b, a, c, d, e],
                            expected_pinned=[b, a, c, d])
        assert order() == [d, c, b, a, e]
        assert pinned_ids() == [d, c, b, a]
        assert_canonical()
        payload = runner.sessions_payload()["sessions"]
        assert [s["id"] for s in payload] == [d, c, b, a, e]
        assert [s["pinned"] for s in payload] == [True, True, True, True, False]
        assert [s["order_at"] for s in payload] == recencies()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for sid in ids:
            runner._hubs.pop(sid, None)
            db.delete_session(sid)
        assert not db.query("SELECT key FROM meta WHERE key GLOB 'session_order_at.*'")


async def exercise_queue_reorder(runner, db) -> None:
    """A revision-guarded drag holds dequeue, remaps pause indexes, and resumes
    from the user's chosen order when the hold is released."""
    sid = db.create_session("queue reorder", "claude", "/tmp", "", "",
                            "blue", "auto")
    try:
        h = runner.hub(sid)
        pending_config = {
            "kind": "config", "fields": {"model": "later-model"},
            "key": 'config:{"model": "later-model"}',
        }
        h.status = "running"
        h.queue = ["one", pending_config, "two", "three"]
        h.paused_queue = {0, 3}
        h._broadcast_queue()
        revision = h.queue_revision
        owner = object()
        other = object()

        assert "error" in h.begin_queue_reorder(owner, "stale", revision - 1)
        assert h.begin_queue_reorder(owner, "drag-1", revision) == {
            "ok": True, "queue_revision": revision}
        assert "error" in h.begin_queue_reorder(other, "drag-2", revision)

        # The turn finishes while the row is in flight. No prompt can start
        # until the acknowledged holder commits or cancels.
        h.status = "idle"
        h.active_since = time.time() - 8
        h._queue_waiting_completion = (h.active_since, False, "ok")
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h._start_queue_if_ready() is False
        assert h.queue[0] == "one"

        result = h.reorder_queue(
            owner, "drag-1", revision, [2, 1, 0, 3])
        assert result["ok"] is True and result["started"] is True
        assert started == ["two"]
        assert h.queue == [pending_config, "one", "three"]
        assert h._paused_wire() == [1, 2]
        assert h._queue_reorder is None
        assert h._queue_waiting_completion is None

        # A malformed drop releases the lease but never mutates the queue.
        h.status = "running"
        h._broadcast_queue()
        revision = h.queue_revision
        assert h.begin_queue_reorder(owner, "drag-3", revision)["ok"] is True
        before = list(h.queue)
        rejected = h.reorder_queue(owner, "drag-3", revision, [0, 0, 1])
        assert rejected["error"] == "invalid queue order"
        assert h.queue == before and h._queue_reorder is None

        # If every remaining prompt is paused, releasing the transition ends
        # the deferred activity block instead of leaving it half-complete.
        h.status = "idle"
        h.queue = ["paused one", "paused two"]
        h.paused_queue = {0, 1}
        h.active_since = time.time() - 4
        h._queue_waiting_completion = (h.active_since, False, "ok")
        h._broadcast_queue()
        revision = h.queue_revision
        assert h.begin_queue_reorder(owner, "drag-4", revision)["ok"] is True
        settled = h.finish_queue_reorder(owner, "drag-4")
        assert settled["ok"] is True and settled["started"] is False
        assert h.active_since is None and h.last_completion_status == "ok"
        assert h._queue_waiting_completion is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_session_drafts(runner, db, uploads, config) -> None:
    """Drafts are ordered, durable, conflict-safe, and own staged uploads."""
    sid = db.create_session("draft sync", "claude", "/tmp", "", "", "blue", "auto")
    session_root = Path(config.DATA_DIR).resolve() / "uploads" / str(sid)

    class Watcher:
        def __init__(self):
            self.messages = []

        async def send_json(self, value):
            self.messages.append(value)

    first = Watcher()
    second = Watcher()
    h = runner.hub(sid)
    h.attach(first)
    h.attach(second)
    try:
        assert h.snapshot()["draft"] == {
            "text": "", "revision": 0, "updated_at": None}
        assert h.snapshot()["draft_max_chars"] == db.MAX_DRAFT_CHARS
        saved = await h.update_draft("Test", "device-a", 1)
        assert saved["revision"] == 1 and saved["text"] == "Test"
        assert first.messages[-1] == second.messages[-1] == saved
        assert db.get_session_draft(sid)["text"] == "Test"

        newer = await h.update_draft("edited elsewhere", "device-b", 4)
        stale = await h.consume_draft("Test", "device-a", 2, recipient=first)
        assert stale["consumed"] is False
        assert stale["text"] == "edited elsewhere"
        assert stale["revision"] == newer["revision"] == 2
        assert db.get_session_draft(sid)["text"] == "edited elsewhere"

        too_large = await h.update_draft("x" * (db.MAX_DRAFT_CHARS + 1))
        assert "cannot exceed" in too_large["error"]
        assert db.get_session_draft(sid)["revision"] == 2

        class GateWatcher:
            def __init__(self):
                self.messages = []
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def send_json(self, value):
                self.messages.append(value)
                if value.get("type") == "snapshot":
                    self.started.set()
                    await self.release.wait()

        joining = GateWatcher()
        attach_task = asyncio.create_task(h.attach_with_snapshot(joining))
        await joining.started.wait()
        ordered_task = asyncio.create_task(
            h.update_draft("ordered after snapshot", "device-b", 5))
        await asyncio.sleep(0)
        assert not ordered_task.done()
        joining.release.set()
        await attach_task
        ordered = await ordered_task
        assert [item["type"] for item in joining.messages] == ["snapshot", "draft"]
        assert joining.messages[0]["draft"]["revision"] == 2
        assert joining.messages[1]["revision"] == ordered["revision"] == 3
        h.detach(joining)

        def uploaded(name: str):
            directory = uploads._new_upload_directory(sid)
            path = directory / name
            path.write_bytes(b"draft attachment")
            path.chmod(0o600)
            marker = "[file attached: {} ({}, 16 B) — inspect it with your file tools]".format(
                path, name)
            return path, marker

        abandoned_path, abandoned_marker = uploaded("draft-only.txt")
        await h.update_draft(abandoned_marker, "device-a", 3)
        assert abandoned_path.exists()
        await h.update_draft("", "device-a", 4)
        # A full-document update from another socket may already be in flight
        # with this marker. Keep draft-only bytes until session deletion rather
        # than accepting that later revision with a broken attachment.
        assert abandoned_path.exists()

        retained_path, retained_marker = uploaded("also-queued.txt")
        await h.update_draft(retained_marker, "device-a", 5)
        h.status = "running"  # clearing must not start the retained prompt
        h.queue = [retained_marker]
        await h.update_draft("", "device-a", 6)
        assert retained_path.exists()
        assert h.clear_queue() == 1
        assert not retained_path.exists()
        h.status = "idle"

        # Moving a queued prompt to the composer transfers ownership of its
        # attachment marker instead of treating the queue removal as cancel.
        replaced_path, replaced_marker = uploaded("replaced-composer.txt")
        edited_path, edited_marker = uploaded("edited-from-queue.txt")
        await h.update_draft(replaced_marker)
        h.status = "running"
        h.queue = [edited_marker]
        moved = await h.edit_queued(0, edited_marker)
        assert moved["ok"] is True and moved["draft"]["text"] == edited_marker
        assert h.queue == [] and db.get_session_draft(sid)["text"] == edited_marker
        assert not replaced_path.exists()
        assert edited_path.exists()
        h.status = "idle"

        durable = await h.update_draft("survives a restart", "device-a", 7)
        h.detach(first)
        h.detach(second)
        runner.drop_hub(sid)
        await asyncio.sleep(0)
        h = runner.hub(sid)
        assert h.snapshot()["draft"] == {
            "text": "survives a restart", "revision": durable["revision"],
            "updated_at": durable["updated_at"],
        }
    finally:
        h.status = "idle"
        h.queue.clear()
        h.held.clear()
        h.paused_queue.clear()
        h._persist_queue()
        runner.drop_hub(sid)
        shutil.rmtree(session_root, ignore_errors=True)
        db.delete_session(sid)
    assert db.query_one(
        "SELECT 1 FROM session_drafts WHERE session_id=?", (sid,)) is None


def exercise_abandoned_upload_cleanup(runner, db, uploads, config) -> None:
    """Queue cancellation removes only uploads with no surviving reference."""
    sid = db.create_session("upload cleanup", "claude", "/tmp", "", "", "blue", "auto")
    session_root = Path(config.DATA_DIR).resolve() / "uploads" / str(sid)

    def uploaded(name: str):
        directory = uploads._new_upload_directory(sid)
        path = directory / name
        path.write_bytes(b"private attachment")
        path.chmod(0o600)
        marker = "[file attached: {} ({}, 18 B) — inspect it with your file tools]".format(
            path, name)
        return path, marker

    h = runner.hub(sid)
    h.status = "running"  # cancellation must not auto-start a remaining prompt
    try:
        abandoned_path, abandoned_marker = uploaded("abandoned.txt")
        h.queue = [abandoned_marker]
        assert h.unqueue(0, abandoned_marker) == {"ok": True}
        assert not abandoned_path.exists()

        shared_path, shared_marker = uploaded("shared.txt")
        h.queue = [shared_marker, shared_marker]
        assert h.unqueue(0, shared_marker) == {"ok": True}
        assert shared_path.exists()  # the second queued prompt still owns it
        assert h.unqueue(0, shared_marker) == {"ok": True}
        assert not shared_path.exists()

        sent_path, sent_marker = uploaded("sent.txt")
        db.add_event(sid, "user", {"text": sent_marker})
        h.queue = [sent_marker]
        assert h.unqueue(0, sent_marker) == {"ok": True}
        assert sent_path.exists()  # durable transcript previews must survive

        active_path, active_marker = uploaded("active.txt")
        h._active_prompt_text = active_marker
        h.queue = [active_marker]
        assert h.unqueue(0, active_marker) == {"ok": True}
        assert active_path.exists()
        h._active_prompt_text = ""
        assert uploads.discard_abandoned(sid, [active_marker]) == 1
        assert not active_path.exists()

        held_path, held_marker = uploaded("held.txt")
        h.held = [held_marker]
        assert h.discard_held(0, held_marker) == {"ok": True}
        assert not held_path.exists()

        first_path, first_marker = uploaded("first.txt")
        second_path, second_marker = uploaded("second.txt")
        h.queue = [first_marker, second_marker]
        assert h.clear_queue() == 2
        assert not first_path.exists() and not second_path.exists()

        prose_path, _marker = uploaded("plain-prose.txt")
        prose = "A path mentioned as prose must stay: {}".format(prose_path)
        h.queue = [prose]
        assert h.unqueue(0, prose) == {"ok": True}
        assert prose_path.exists()
    finally:
        h.status = "idle"
        h.queue.clear()
        h.held.clear()
        h.paused_queue.clear()
        h._active_prompt_text = ""
        h._persist_queue()
        runner.drop_hub(sid)
        shutil.rmtree(session_root, ignore_errors=True)
        db.delete_session(sid)


def exercise_auth_hardening(auth) -> None:
    """Unknown accounts pay the real work factor and limiter state stays bounded."""
    captured = []
    original_verify = auth.verify_password
    auth.verify_password = lambda _password, stored: captured.append(stored) or False
    try:
        assert auth.check_login("definitely-missing-user", "wrong") is False
    finally:
        auth.verify_password = original_verify
    assert captured and int(captured[0].split("$")[1]) == auth.PBKDF2_ITERS

    auth._attempts.clear()
    auth._attempts_last_prune = time.monotonic() - auth.RATE_LIMIT_PRUNE_SECONDS
    auth._attempts["expired"] = [time.monotonic() - 301]
    assert auth._rate_limited("fresh", limit=2, window=300) is False
    assert "expired" not in auth._attempts
    assert auth._rate_limited("fresh", limit=2, window=300) is False
    assert auth._rate_limited("fresh", limit=2, window=300) is True
    for index in range(auth.RATE_LIMIT_MAX_KEYS + 20):
        auth._rate_limited("peer-{}".format(index), limit=2, window=300)
    assert len(auth._attempts) <= auth.RATE_LIMIT_MAX_KEYS
    auth._attempts.clear()
    auth._attempts_last_prune = time.monotonic()


async def exercise_auth_endpoint(url: str, auth) -> None:
    """A caller cannot mint limiter identities with a Forwarded header."""
    seen = []
    original = auth._rate_limited
    auth._rate_limited = lambda key: seen.append(key) or True
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url + "/api/auth/login",
                                 headers={"X-Forwarded-For": "198.51.100.77"},
                                 json={"username": "user", "password": "wrong"}) as response:
                assert response.status == 429, await response.text()
            async with http.post(url + "/api/auth/login", json=[]) as response:
                assert response.status == 400, await response.text()
    finally:
        auth._rate_limited = original
    assert seen == ["127.0.0.1"], seen


async def exercise_engine_switch_queue(url: str, token: str, runner, db) -> None:
    """An engine switch queues behind pending work, applies in order, and one
    engine's validated settings can never reach another engine."""
    from puppy.drivers import get_driver
    codex_default = get_driver("codex").default_model()
    codex_permission = get_driver("codex").default_permission()
    codex_custom_permission = "danger-full-access"
    claude_permission = get_driver("claude").default_permission()
    sid = db.create_session("switch queue", "claude", "/tmp", "old-model", "high",
                            "blue", "auto")
    h = runner.hub(sid)
    claude_cfg = {
        "kind": "config", "fields": {"model": "claude-only", "engine": "claude"},
        "key": runner._queued_config_key({"model": "claude-only", "engine": "claude"}),
    }
    h.queue = [claude_cfg, "paused prompt"]
    h.paused_queue = {1}
    h.held = ["held prompt"]
    h._persist_queue()
    try:
        async with aiohttp.ClientSession() as http:
            headers = {"X-Puppy-Token": token}
            for payload in ([], {"engine": ["codex"]}):
                async with http.post(
                        url + "/api/sessions/{}/switch".format(sid), headers=headers,
                        json=payload) as response:
                    assert response.status == 400, await response.text()
            # Work is pending: the switch holds its place at the queue tail
            # instead of applying, and nothing pending is discarded.
            async with http.post(
                    url + "/api/sessions/{}/switch".format(sid), headers=headers,
                    json={"engine": "codex"}) as response:
                switched = await response.json()
                assert response.status == 200, switched
            # The permission picker now follows the queued target just like
            # model/effort: valid target values fold into the switch row and a
            # value belonging only to the live engine is rejected.
            async with http.patch(
                    url + "/api/sessions/{}".format(sid), headers=headers,
                    json={"permission_mode": codex_custom_permission}) as response:
                permission_changed = await response.json()
                assert response.status == 200, permission_changed
            async with http.patch(
                    url + "/api/sessions/{}".format(sid), headers=headers,
                    json={"permission_mode": "plan"}) as response:
                assert response.status == 400, await response.text()
        assert switched["queued"] is True
        assert permission_changed["queued_config"] is True
        assert switched["session"]["engine"] == "claude"
        assert h.queue[:2] == [claude_cfg, "paused prompt"]
        assert runner._is_queued_engine(h.queue[2])
        assert h.queue[2]["fields"] == {
            "engine": "codex", "model": codex_default, "effort": "",
            "permission_mode": codex_custom_permission, "fast_mode": "off"}
        assert h._paused_wire() == [1] and h.held == ["held prompt"]
        # an engine upgrade must treat the queued switch target as busy work
        assert any(b["id"] == sid for b in runner.engine_blockers("codex"))
        assert any(b["id"] == sid for b in runner.engine_blockers("claude"))

        # A model picked while the switch waits belongs to its target and
        # folds into the same switch row; a stale validation tag is refused.
        assert h.queue_config({"model": "o-mini", "engine": "codex"}) == \
            {"handled": True}
        assert h.queue[2]["fields"]["model"] == "o-mini"
        assert "belongs to" in h.queue_config(
            {"model": "sonnet", "engine": "claude"})["error"]
        assert h.pending_config() == {
            "engine": "codex", "model": "o-mini", "effort": "",
            "permission_mode": codex_custom_permission, "fast_mode": "off"}

        # Re-picking collapses into the same pending row, resetting its
        # complete configuration to the newly chosen target's defaults.
        assert h.request_engine_switch(
            "claude", "", "", claude_permission) == {"queued": True}
        assert len(h.queue) == 3 and h.queue[2]["fields"] == {
            "engine": "claude", "model": "", "effort": "",
            "permission_mode": claude_permission, "fast_mode": "off"}
        assert h.request_engine_switch(
            "codex", codex_default, "", codex_permission) == {"queued": True}
        assert h.queue_config({
            "permission_mode": codex_custom_permission, "engine": "codex",
        }) == {"handled": True}

        # A new prompt into the idle, fully paused queue starts itself: the
        # claude model change applies while the session is still claude, the
        # switch then lands on codex with a fresh native conversation, and the
        # paused prompt keeps its place for later.
        started = []
        h._start_turn = lambda text: (started.append(text),
                                      setattr(h, "status", "running"))
        assert h.send_message("run now") == {"queued": True}
        assert started == ["run now"]
        assert h.queue == ["paused prompt"] and h._paused_wire() == [0]
        session = db.get_session(sid)
        assert session["engine"] == "codex"
        assert session["model"] == codex_default and session["effort"] == ""
        assert session["native_session_id"] == "" and session["last_model"] == ""
        assert session["used_config"] == ""
        assert session["permission_mode"] == codex_custom_permission
        divider = [e for e in db.get_events(sid) if e["kind"] == "engine_switch"][-1]
        assert divider["data"] == {"from": "claude", "to": "codex",
                                   "from_model": "claude-only", "from_effort": "high"}

        # A row whose engine switch was cancelled is skipped with a note, not
        # applied to whatever engine is current now.
        h.status = "idle"
        h._apply_queued_config({"model": "claude-x", "engine": "claude"})
        assert db.get_session(sid)["model"] == codex_default
        skipped = db.get_events(sid)[-1]
        assert skipped["kind"] == "info" and \
            skipped["data"]["subtype"] == "config_skipped"
        h._apply_queued_config({
            "permission_mode": "read-only", "engine": "codex",
        })
        assert db.get_session(sid)["permission_mode"] == "read-only"

        # Held work survives switches for an explicit decision: a held switch
        # re-applies (or re-queues) on demand, and a held setting validated by
        # a different engine is refused rather than misapplied.
        assert h.unqueue(0, "paused prompt") == {"ok": True}
        eng_fields = {
            "engine": "claude", "model": "", "effort": "",
            "permission_mode": claude_permission,
            "fast_mode": "off",
        }
        eng_row = {"kind": "engine", "fields": dict(eng_fields),
                   "key": runner._queued_engine_key(eng_fields)}
        h.held = [eng_row]
        assert h.requeue_held(0, eng_row["key"]) == {"ok": True}
        assert h.held == [] and db.get_session(sid)["engine"] == "claude"
        stale_fields = {"model": "gpt-x", "engine": "codex"}
        stale_cfg = {"kind": "config", "fields": dict(stale_fields),
                     "key": runner._queued_config_key(stale_fields)}
        h.held = [stale_cfg]
        assert "belongs to" in h.requeue_held(0, stale_cfg["key"])["error"]
        assert h.held == [stale_cfg]
        assert h.discard_held(0, stale_cfg["key"]) == {"ok": True}

        # With nothing pending the route still switches immediately.
        async with aiohttp.ClientSession() as http:
            async with http.post(
                    url + "/api/sessions/{}/switch".format(sid),
                    headers={"X-Puppy-Token": token},
                    json={"engine": "codex"}) as response:
                switched = await response.json()
                assert response.status == 200, switched
        assert switched["queued"] is False
        assert switched["session"]["engine"] == "codex"

        # Deleting a session discards its queue before the asynchronous hub
        # kill runs, so the durable queue record cannot be resurrected.
        h.queue = ["deleted with the session"]
        h._persist_queue()
        assert db.meta_get("session_queue.{}".format(sid)) is not None
        runner.drop_hub(sid)
        db.delete_session(sid)
        for _ in range(4):
            await asyncio.sleep(0)
        assert db.meta_get("session_queue.{}".format(sid)) is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_fast_mode(url: str, token: str, runner, db) -> None:
    """Fast is model-advertised, boolean on disk, and ordered in the queue."""
    from puppy.drivers import get_driver

    driver = get_driver("codex")
    sentinel = object()
    previous = {name: driver.__dict__.get(name, sentinel) for name in
                ("refresh_model_options", "model_options", "fast_mode_tier")}

    async def no_refresh(force=False):
        return None

    options = [
        {"value": "", "label": "Default", "fast_mode_available": True},
        {"value": "future-fast", "label": "Future Fast",
         "fast_mode_available": True},
        {"value": "future-standard", "label": "Future Standard",
         "fast_mode_available": False},
    ]
    driver.refresh_model_options = no_refresh
    driver.model_options = lambda: [dict(item) for item in options]
    driver.fast_mode_tier = lambda model: \
        "catalog-tier-99" if str(model or "") in ("", "future-fast") else ""
    sid = db.create_session(
        "fast mode", "codex", "/tmp", "future-fast", "", "blue",
        driver.default_permission())
    hub = runner.hub(sid)
    headers = {"X-Puppy-Token": token}
    try:
        async with aiohttp.ClientSession() as http:
            async def patch(body):
                async with http.patch(
                        url + "/api/sessions/{}".format(sid),
                        headers=headers, json=body) as response:
                    return response.status, await response.json()

            status, payload = await patch({"fast_mode": "yes"})
            assert status == 400 and "true or false" in payload["error"], \
                (status, payload)

            status, payload = await patch({"fast_mode": True})
            assert status == 200, payload
            assert payload["session"]["fast_mode"] is True
            assert db.get_session(sid)["fast_mode"] is True
            listed = next(item for item in runner.sessions_payload()["sessions"]
                          if item["id"] == sid)
            assert listed["fast_mode"] is True

            # A catalog row without Fast cannot be enabled, and moving onto
            # that row turns an existing request off atomically.
            status, payload = await patch({"model": "future-standard"})
            assert status == 200, payload
            assert payload["session"]["model"] == "future-standard"
            assert payload["session"]["fast_mode"] is False
            status, payload = await patch({"fast_mode": True})
            assert status == 400 and "does not offer Fast" in payload["error"]

            status, payload = await patch({"model": "future-fast"})
            assert status == 200, payload
            hub.status = "running"
            status, payload = await patch({"fast_mode": True})
            assert status == 200 and payload["queued_config"] is True, payload
            assert hub.queue[0]["fields"] == {
                "engine": "codex", "fast_mode": "on"}
            assert hub.pending_config()["fast_mode"] == "on"

        # Applying the settings-only queue performs the same boolean storage
        # conversion as an immediate PATCH.
        hub.status = "idle"
        assert hub._take_next_turn() is None
        assert db.get_session(sid)["fast_mode"] is True

        # A subsequent engine reseed always starts with Fast off.
        claude = get_driver("claude")
        assert hub._apply_engine_switch({
            "engine": "claude", "model": claude.default_model(), "effort": "",
            "permission_mode": claude.default_permission(), "fast_mode": "off",
        }) is True
        assert db.get_session(sid)["fast_mode"] is False
        async with aiohttp.ClientSession() as http:
            async with http.patch(
                    url + "/api/sessions/{}".format(sid), headers=headers,
                    json={"fast_mode": True}) as response:
                refused = await response.json()
                assert response.status == 400, refused
                assert "not available for Claude" in refused["error"]
    finally:
        hub.status = "idle"
        runner.drop_hub(sid)
        db.delete_session(sid)
        for name, value in previous.items():
            if value is sentinel:
                driver.__dict__.pop(name, None)
            else:
                driver.__dict__[name] = value


def exercise_effective_model_provenance(runner, db) -> None:
    """Requested picker history stays separate from engine-confirmed routing."""
    from puppy.drivers import get_driver

    sid = db.create_session("model provenance", "claude", "/tmp",
                            "requested-a", "high", "blue", "auto")
    hub = runner.hub(sid)
    try:
        db.touch_session(
            sid, used_config=json.dumps({"model": "requested-a", "effort": "high"}),
            last_model="served-effective-a", model="requested-b", effort="max")
        session = db.get_session(sid)
        hub._note_turn_config(session)
        divider = db.get_events(sid)[-1]
        assert divider["kind"] == "info" and \
            divider["data"]["subtype"] == "config_change", divider
        assert divider["data"]["from_model"] == "served-effective-a", divider
        assert divider["data"]["to_model"] == "requested-b", divider
        assert runner.parse_used_config(db.get_session(sid)["used_config"]) == {
            "model": "requested-b", "effort": "max"}
        assert db.get_session(sid)["last_model"] == ""

        # The provider reroutes the new request. An outgoing engine divider
        # names that confirmed model, never requested-b.
        db.touch_session(sid, last_model="served-fallback-b")
        target = get_driver("codex")
        assert hub._apply_engine_switch({
            "engine": "codex", "model": target.default_model(), "effort": "",
            "permission_mode": target.default_permission(),
            "fast_mode": "off",
        }) is True
        moved = [event for event in db.get_events(sid)
                 if event["kind"] == "engine_switch"][-1]
        assert moved["data"]["from_model"] == "served-fallback-b", moved
        assert moved["data"]["from_effort"] == "max", moved
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


def exercise_claude_model_alias_provenance(runner, db) -> None:
    """Claude catalog aliases compare by resolution, not hardcoded names."""
    sid = db.create_session("model alias provenance", "claude", "/tmp",
                            "future-long[2m]", "high", "blue", "auto")
    hub = runner.hub(sid)
    driver = ClaudeDriver()
    try:
        session = db.get_session(sid)
        ctx = driver.turn_context(session, True, "hello", "pin")
        assert driver.parse_line(json.dumps({
            "type": "control_response",
            "response": {"subtype": "success", "request_id": "init_1",
                         "response": {"models": [
                             {"value": "default", "displayName": "Default"},
                             {"value": "future-long[2m]", "displayName": "Future",
                              "resolvedModel": "vendor-future-9[2m]"},
                         ]}},
        }), ctx) == []

        before = len(db.get_events(sid))
        init_actions = driver.parse_line(json.dumps({
            "type": "system", "subtype": "init", "session_id": "native",
            "model": "vendor-future-9[2m]", "tools": [],
        }), ctx)
        report = next(action for action in init_actions if action["a"] == "model")
        hub._note_effective_model(session, driver, ctx, report["model"])
        assert len(db.get_events(sid)) == before
        assert db.get_session(sid)["last_model"] == "vendor-future-9[2m]"

        # The provider-facing assistant form drops the capacity selector. It
        # is neither another model action nor last_model churn.
        assert driver.parse_line(json.dumps({
            "type": "assistant", "message": {
                "model": "vendor-future-9", "content": []},
        }), ctx) == []
        assert len(db.get_events(sid)) == before
        assert db.get_session(sid)["last_model"] == "vendor-future-9[2m]"

        # A persisted provider form from an earlier response is equivalent to
        # the next turn's system/init form and must not churn back and forth.
        session["last_model"] = "vendor-future-9"
        db.touch_session(sid, last_model="vendor-future-9")
        hub._note_effective_model(
            session, driver, ctx, "vendor-future-9[2m]")
        assert len(db.get_events(sid)) == before
        assert db.get_session(sid)["last_model"] == "vendor-future-9"

        # A genuinely different resolved id is still detected and retained.
        hub._note_effective_model(session, driver, ctx, "vendor-other-1")
        warning = db.get_events(sid)[-1]
        assert warning["kind"] == "info"
        assert warning["data"] == {
            "subtype": "model_switch",
            "text": "requested model 'future-long[2m]' but engine is serving "
                    "vendor-other-1",
        }
        assert db.get_session(sid)["last_model"] == "vendor-other-1"
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)


async def exercise_queue_pause_websocket(url: str, token: str, runner, db) -> None:
    """The authenticated socket carries pause and revision-guarded reorder."""
    sid = db.create_session("queue pause socket", "claude", "/tmp", "", "",
                            "blue", "auto")
    h = runner.hub(sid)
    h.status = "running"
    h.queue = ["socket queued prompt", "socket second prompt"]
    h._broadcast_queue()
    ws = None
    try:
        async with aiohttp.ClientSession() as http:
            ws = await http.ws_connect(
                url + "/api/ws/session/{}".format(sid),
                headers={"X-Puppy-Token": token})
            snapshot = await ws.receive_json(timeout=3)
            assert snapshot["queued"] == [
                "socket queued prompt", "socket second prompt"]
            assert snapshot["paused"] == []
            assert isinstance(snapshot["queue_revision"], int)

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": True})
            paused = await ws.receive_json(timeout=3)
            assert paused["type"] == "queued" and paused["paused"] == [0], paused

            await ws.send_json({
                "type": "begin_queue_reorder", "request_id": "socket-drag",
                "queue_revision": paused["queue_revision"],
            })
            ready = await ws.receive_json(timeout=3)
            assert ready == {
                "type": "queue_reorder_ready", "request_id": "socket-drag",
                "ok": True, "queue_revision": paused["queue_revision"],
            }, ready
            await ws.send_json({
                "type": "reorder_queue", "request_id": "socket-drag",
                "queue_revision": paused["queue_revision"], "order": [1, 0],
            })
            frames = [await ws.receive_json(timeout=3),
                      await ws.receive_json(timeout=3)]
            queued = next(frame for frame in frames if frame["type"] == "queued")
            complete = next(frame for frame in frames
                            if frame["type"] == "queue_reorder_complete")
            assert queued["queued"] == [
                "socket second prompt", "socket queued prompt"]
            assert queued["paused"] == [1]
            assert complete["ok"] is True and complete["started"] is False
            assert complete["queued"] == queued["queued"]

            await ws.send_json({
                "type": "edit_queue", "request_id": "stale-edit",
                "index": 0, "text": "not the queued prompt",
            })
            rejected_edit = await ws.receive_json(timeout=3)
            assert rejected_edit["type"] == "queue_edit_complete", rejected_edit
            assert rejected_edit["request_id"] == "stale-edit"
            assert "error" in rejected_edit

            await ws.send_json({
                "type": "edit_queue", "request_id": "socket-edit",
                "index": 0, "text": "socket second prompt",
            })
            edit_frames = [await ws.receive_json(timeout=3) for _ in range(3)]
            edited_queue = next(frame for frame in edit_frames
                                if frame["type"] == "queued")
            edited_draft = next(frame for frame in edit_frames
                                if frame["type"] == "draft")
            edit_complete = next(frame for frame in edit_frames
                                 if frame["type"] == "queue_edit_complete")
            assert edited_queue["queued"] == ["socket queued prompt"]
            assert edited_queue["paused"] == [0]
            assert edited_draft["text"] == "socket second prompt"
            assert edit_complete["request_id"] == "socket-edit"
            assert edit_complete["ok"] is True and edit_complete["started"] is False
            assert edit_complete["draft"] == edited_draft
            assert db.get_session_draft(sid)["text"] == "socket second prompt"

            h.queue.append("socket third prompt")
            h._broadcast_queue()
            disconnect_queue = await ws.receive_json(timeout=3)
            assert disconnect_queue["type"] == "queued"
            assert disconnect_queue["queued"] == [
                "socket queued prompt", "socket third prompt"]

            # A holder is released immediately when its owning socket leaves;
            # the 30-second lease is only a last-resort fail-open path.
            await ws.send_json({
                "type": "begin_queue_reorder", "request_id": "disconnect-drag",
                "queue_revision": disconnect_queue["queue_revision"],
            })
            ready = await ws.receive_json(timeout=3)
            assert ready["ok"] is True, ready

            await ws.send_json({"type": "set_queue_paused", "index": 0,
                                "text": "socket queued prompt", "paused": "yes"})
            rejected = await ws.receive_json(timeout=3)
            assert rejected["type"] == "toast" and \
                "true or false" in rejected["text"], rejected
            await ws.close()
            ws = None
            await asyncio.sleep(0)
            assert h._queue_reorder is None
    finally:
        if ws is not None:
            await ws.close()
        h.queue.clear()
        h.paused_queue.clear()
        h.status = "idle"
        h._persist_queue()
        runner.drop_hub(sid)
        db.delete_session(sid)


async def main() -> None:
    private_tests = BASE / "data" / "tests"
    private_tests.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_tests.chmod(0o700)
    temp_root = Path(tempfile.mkdtemp(prefix="backend-", dir=str(private_tests)))
    process = None
    disabled_process = None
    controller_runner = None
    try:
        exercise_driver_normalization()
        exercise_side_question_contract()
        release_artifact = temp_root / "release" / "puppy-backend.pyz"
        subprocess.run([sys.executable, str(BASE / "backend" / "build.py"),
                        "--output", str(release_artifact)], cwd=str(BASE), check=True)
        generated_launcher = release_artifact.with_name("puppy-backend-launcher.py")
        generated_license = release_artifact.with_name("LICENSE")
        assert generated_launcher.is_file() and os.access(generated_launcher, os.X_OK)
        assert generated_license.read_bytes() == (BASE / "LICENSE").read_bytes()
        with zipfile.ZipFile(release_artifact) as archive:
            names = archive.namelist()
        assert any(name.startswith("puppy/drivers/") for name in names)
        assert "puppy/browser_agent.py" in names
        assert "puppy/terminal_agent.py" in names
        for name in ("session_agent", "session_links", "session_actions", "session_coordination"):
            assert "puppy/{}.py".format(name) in names
        assert "LICENSE" in names
        assert not any(name.startswith("puppy/static/") for name in names)
        mcp_env = dict(os.environ)
        mcp_env["PYTHONPATH"] = str(release_artifact)
        mcp_env["PUPPY_DATA"] = str(temp_root / "mcp-data")
        mcp_init = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }) + "\n"
        mcp_output = subprocess.check_output(
            [sys.executable, "-m", "puppy.browser_agent"], input=mcp_init,
            env=mcp_env, cwd=str(temp_root), text=True, timeout=5)
        mcp_result = json.loads(mcp_output.strip())
        assert mcp_result["result"]["serverInfo"]["name"] == "Puppy managed browser"
        assert "four uppercase" in mcp_result["result"]["instructions"]
        assert "default for interactive web navigation" in \
            mcp_result["result"]["instructions"]
        assert "repository's own browser test suite" in \
            mcp_result["result"]["instructions"]
        terminal_mcp_output = subprocess.check_output(
            [sys.executable, "-m", "puppy.terminal_agent"], input=mcp_init,
            env=mcp_env, cwd=str(temp_root), text=True, timeout=5)
        terminal_mcp_result = json.loads(terminal_mcp_output.strip())
        assert terminal_mcp_result["result"]["serverInfo"]["name"] == \
            "Puppy shared terminal"
        assert "only when the user specifically asks" in \
            terminal_mcp_result["result"]["instructions"]
        from tests.mcp_startup_test import exercise_mcp_startup
        exercise_mcp_startup(release_artifact, temp_root / "mcp-driver-startup")
        self_test = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "self-test", "--data-dir",
            str(temp_root / "self-test-data"),
        ], text=True).splitlines()[-1])
        assert self_test["ok"] is True and self_test["version"] == __version__
        assert "/api/terminal/instances" in self_test["routes"]
        assert "/api/session-links/node" in self_test["routes"]
        assert "/api/session-links/action" in self_test["routes"]
        assert "/api/ws/session-links" in self_test["routes"]
        assert "/api/ws/terminal/{terminal_id}" in self_test["routes"]

        # Configuration alone is insufficient: a directly launched zipapp must
        # keep remote upgrades disabled because no external rollback exists.
        disabled_data = temp_root / "disabled-data"
        disabled_port = free_port()
        disabled_url = f"http://127.0.0.1:{disabled_port}"
        backend_token = "backend-test-token-0123456789abcdef"
        subprocess.check_output([
            sys.executable, str(release_artifact), "pairing", "--data-dir", str(disabled_data),
            "--name", "disabled-node", "--bind", "127.0.0.1", "--port", str(disabled_port),
            "--advertise-url", disabled_url, "--api-token", backend_token,
            "--disable-terminal", "--enable-remote-upgrade", "--disable-tls",
            "--usage-refresh-minutes", "0", "--max-upload-size-mb", "2",
        ], text=True)
        disabled_process = subprocess.Popen([
            sys.executable, str(release_artifact), "serve", "--data-dir", str(disabled_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_for_backend(disabled_url, disabled_process)
        await exercise_node(disabled_url, backend_token, __version__, upgrade_enabled=False,
                            data_dir=disabled_data)
        await stop_process_with_notice(disabled_process, disabled_url, backend_token)
        disabled_process = None

        old_version = previous_patch_version()
        artifact = temp_root / "node" / "puppy-backend.pyz"
        artifact.parent.mkdir()
        copy_with_version(release_artifact, artifact, old_version)
        backend_data = temp_root / "backend-data"
        backend_port = free_port()
        backend_url = f"https://127.0.0.1:{backend_port}"
        pairing_raw = subprocess.check_output([
            sys.executable, str(artifact), "pairing", "--data-dir", str(backend_data),
            "--name", "backend-test-node", "--bind", "127.0.0.1",
            "--port", str(backend_port), "--advertise-url", backend_url,
            "--api-token", backend_token, "--disable-terminal", "--enable-remote-upgrade",
            "--auto-tls", "--usage-refresh-minutes", "0", "--max-upload-size-mb", "3",
        ], text=True)
        pairing = json.loads(pairing_raw)
        assert pairing["url"] == backend_url and pairing["token"] == backend_token
        backend_fingerprint = pairing["tls_sha256"]
        assert len(backend_fingerprint) == 64
        assert "pinned-tls" in pairing["capabilities"]
        assert "terminal" not in pairing["capabilities"]
        assert "remote-upgrade" not in pairing["capabilities"]  # pairing command is not launcher-managed
        assert "shutdown-notice" in pairing["capabilities"]
        assert "queue-pause" in pairing["capabilities"]
        assert "queue-edit" in pairing["capabilities"]
        assert "queue-reorder" in pairing["capabilities"]
        assert "session-pinning" in pairing["capabilities"]
        assert "session-drafts" in pairing["capabilities"]
        assert "active-turn-steering" in pairing["capabilities"]
        assert "session-control-ws-v1" in pairing["capabilities"]
        assert "node-state-stream-v1" in pairing["capabilities"]
        assert "engine-model-selection" not in pairing["capabilities"]
        assert pairing["max_upload_size_mb"] == 3
        assert (backend_data / "config.json").stat().st_mode & 0o777 == 0o600
        identity_manifest = json.loads(
            (backend_data / "tls" / "identity.json").read_text(encoding="utf-8"))
        assert (backend_data / "tls").stat().st_mode & 0o777 == 0o700
        assert ((backend_data / "tls" / identity_manifest["private_key"])
                .stat().st_mode & 0o777) == 0o600

        enabled_pairing = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "pairing",
            "--data-dir", str(temp_root / "enabled-data"),
            "--bind", "127.0.0.1", "--port", str(backend_port),
            "--api-token", backend_token, "--usage-refresh-minutes", "30",
            "--max-upload-size-mb", "4",
        ], text=True))
        assert "terminal" in enabled_pairing["capabilities"]
        assert "terminal-instances" in enabled_pairing["capabilities"]
        assert "terminal-handoff" in enabled_pairing["capabilities"]
        assert "engine-model-selection" not in enabled_pairing["capabilities"]
        assert enabled_pairing["usage_refresh_minutes"] == 30
        assert enabled_pairing["max_upload_size_mb"] == 4
        assert "pinned-tls" in enabled_pairing["capabilities"]
        assert len(enabled_pairing["tls_sha256"]) == 64
        repeated_pairing = json.loads(subprocess.check_output([
            sys.executable, str(release_artifact), "pairing",
            "--data-dir", str(temp_root / "enabled-data"),
        ], text=True))
        assert repeated_pairing["tls_sha256"] == enabled_pairing["tls_sha256"]
        assert repeated_pairing["usage_refresh_minutes"] == 30
        assert repeated_pairing["max_upload_size_mb"] == 4

        state_dir = backend_data / "upgrade"
        legacy_launcher_env = dict(os.environ)
        legacy_launcher_env.update({
            "PUPPY_BACKEND_LAUNCHER_PROTOCOL": str(upgrade_contract.LAUNCHER_PROTOCOL),
            "PUPPY_BACKEND_MANAGED_ARTIFACT": str(artifact.resolve()),
            "PUPPY_BACKEND_UPGRADE_MARKER": str((state_dir / "pending.json").resolve()),
            "PUPPY_BACKEND_UPGRADE_STATUS": str((state_dir / "status.json").resolve()),
        })
        process = subprocess.Popen([
            sys.executable, str(artifact), "serve", "--data-dir", str(backend_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=legacy_launcher_env)
        await wait_for_backend(backend_url, process, backend_fingerprint)
        await exercise_node(backend_url, backend_token, old_version, upgrade_enabled=False,
                            data_dir=backend_data, fingerprint=backend_fingerprint)
        await stop_process_with_notice(
            process, backend_url, backend_token, backend_fingerprint)
        process = None

        process = subprocess.Popen([
            sys.executable, str(BASE / "backend" / "launcher.py"),
            "--artifact", str(artifact), "--state-dir", str(state_dir), "--",
            "serve", "--data-dir", str(backend_data),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_for_backend(backend_url, process, backend_fingerprint)
        await exercise_node(backend_url, backend_token, old_version, upgrade_enabled=True,
                            data_dir=backend_data, fingerprint=backend_fingerprint)
        await reject_bad_signature(backend_url, backend_token, backend_fingerprint)

        # Import the full application only after its independent data path is set.
        controller_data = temp_root / "controller-data"
        controller_data.mkdir()
        os.environ["PUPPY_DATA"] = str(controller_data)
        # config binds its data path at import time, so an earlier import of it
        # anywhere above would silently point this whole test at the real
        # instance's data directory. Fail loudly instead of writing there.
        assert "puppy.config" not in sys.modules, \
            "puppy.config was imported before the test data path was set"
        from puppy import (auth, backends as controller_backends, config, db,
                           host_metrics, runner, state_stream, terminal, uploads)
        from backend.puppy_backend import upgrade as backend_upgrade
        from puppy import web as puppy_web
        from puppy.web import build_app

        await check_notify_placeholders()

        config.load()
        exercise_opencode_driver()
        await exercise_opencode_binary_fallback(temp_root / "opencode-fallback")
        controller_token = "controller-test-token-0123456789abcdef"
        config.set_value("auth.api_token", controller_token)
        config.set_value("engines.usage_refresh_minutes", 0)
        db.connect()
        await exercise_codex_app_server_turn(
            temp_root / "codex-app-server", runner, db)
        await exercise_claude_background_turn(
            temp_root / "claude-background", runner, db, config)
        exercise_current_peer_contract(controller_backends)
        exercise_auth_hardening(auth)
        exercise_activity_blocks(runner.SessionHub)
        exercise_health_retry_bound(controller_backends)
        exercise_restore_stream_clear(controller_backends, runner)
        exercise_update_revision_epoch(runner)
        await exercise_update_stream_ordering(runner)
        await exercise_state_topic_isolation(state_stream, runner)
        await exercise_shutdown_broadcast(runner)
        await exercise_queue_persistence(runner, db)
        await exercise_queue_pause(runner, db)
        await exercise_queue_reorder(runner, db)
        await exercise_session_order(runner, db)
        await exercise_session_drafts(runner, db, uploads, config)
        exercise_abandoned_upload_cleanup(runner, db, uploads, config)
        exercise_session_show_meta(runner, db)
        exercise_effective_model_provenance(runner, db)
        exercise_claude_model_alias_provenance(runner, db)
        await exercise_auth_probes(temp_root / "auth-probes")
        exercise_auth_evidence(temp_root / "auth-evidence", db)
        exercise_host_cpu_math(host_metrics)
        await exercise_upgrade_readiness(
            backend_upgrade, runner, terminal, temp_root / "readiness")
        app = build_app()
        controller_runner = web.AppRunner(app)
        await controller_runner.setup()
        site = web.TCPSite(controller_runner, "127.0.0.1", 0)
        await site.start()
        sock = site._server.sockets[0]
        controller_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        await exercise_auth_endpoint(controller_url, auth)
        await exercise_queue_pause_websocket(
            controller_url, controller_token, runner, db)
        await exercise_engine_switch_queue(
            controller_url, controller_token, runner, db)
        await exercise_fast_mode(
            controller_url, controller_token, runner, db)
        await exercise_controller(controller_url, controller_token, backend_url,
                                  backend_token, backend_fingerprint, old_version, state_dir)
        await exercise_proxy_recovery(controller_url, controller_token)
        await exercise_redirect_rejection()
        upgrade_status = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
        assert upgrade_status["state"] == "succeeded", upgrade_status
        assert artifact.with_name(artifact.stem + ".previous" + artifact.suffix).is_file()

        await controller_runner.cleanup()
        controller_runner = None
        stop_process(process)
        process = None
        process = await exercise_launcher_rollback(
            artifact, BASE / "backend" / "launcher.py", state_dir, backend_data,
            backend_url, backend_token, backend_fingerprint)
        print("backend package, engine releases, pinned TLS, signed upgrade, restart, and rollback passed")
    finally:
        if controller_runner is not None:
            await controller_runner.cleanup()
        if disabled_process is not None:
            stop_process(disabled_process)
        if process is not None:
            stop_process(process)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
