#!/usr/bin/env python3
"""Readable native notices and safe fallbacks, without engine runs or quota.

Fixtures follow Claude Code 2.1.282 / SDK message types, the JSON schema
exported by codex-cli 0.157.0, and OpenCode 1.18.32's ACP adapter. See
docs/engine-messages.md for their source contracts and upgrade checks.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("engine-messages-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer
from puppy import config, db, runner
from puppy.drivers import get_driver, messages
from puppy.drivers.claude import ClaudeDriver
from puppy.drivers.codex import CodexDriver
from puppy.drivers.opencode import OpenCodeDriver
from puppy.web import build_app
from backend.puppy_backend.app import build_app as backend_app

RESET = 1900000000


def parse(driver, frame, ctx=None):
    return driver.parse_line(json.dumps(frame), ctx if ctx is not None else {})


def claude_checks():
    driver, ctx = ClaudeDriver(), {}
    info = {"status": "allowed_warning", "rateLimitType": "seven_day",
            "utilization": .8, "resetsAt": RESET, "surpassedThreshold": .75,
            "unifiedWindows": {"seven_day": {"utilization": .8, "resetsAt": RESET}},
            "isUsingOverage": False, "future_field": {"ignored": True}}

    def rate(value, context=None):
        return parse(driver, {"type": "rate_limit_event", "rate_limit_info": value}, context)

    action = rate(info, ctx)[0]
    assert action["info"] == info, "formatting must not rewrite quota data"
    assert action["notice"] == {"text": "Approaching your weekly usage limit · 80% used",
                                "tone": "warn", "resets_at": RESET}
    assert rate(dict(info, utilization=.81), ctx)[0]["notice"] is None
    assert rate(dict(info, status="allowed"), ctx)[0]["notice"] is None
    assert rate(dict(info, status="rejected"), ctx)[0]["notice"]["tone"] == "bad"
    # An included limit may be exhausted while extra usage is available.
    extra = rate(dict(info, status="rejected", overageStatus="allowed"))[0]["notice"]
    assert extra["tone"] == "warn" and "extra usage is available" in extra["text"]
    extra = rate(dict(info, status="allowed", isUsingOverage=True))[0]["notice"]
    assert extra["text"] == "Using extra usage allowance" and "resets_at" not in extra
    extra = rate(dict(info, status="rejected", overageStatus="rejected",
                      overageDisabledReason="org_level_disabled"))[0]["notice"]
    assert "disabled by your organization" in extra["text"]
    generic = rate(dict(info, rateLimitType="future_bucket"))[0]["notice"]
    assert "Approaching your usage limit" in generic["text"] and "future_bucket" not in generic["text"]
    unknown = rate(dict(info, status="future_state"))[0]["notice"]
    assert "future_state" not in unknown["text"] and "reached" not in unknown["text"]
    assert rate(dict(info, status="future_state", message="Usage information is being refreshed"))[0]["notice"]["text"] == "Usage information is being refreshed"
    for bad in (None, True, "0.8", -1, 2, float("nan"), {}, []):
        value = rate(dict(info, utilization=bad, resetsAt=bad))[0]["notice"]
        assert "%" not in value["text"]
        assert "resets_at" not in value or (isinstance(bad, (float, int)) and not isinstance(bad, bool) and bad >= 1)
    assert rate(["invalid"]) == []
    assert rate({})[0]["notice"] is None

    for subtype, field in (("notification", "text"), ("informational", "content")):
        context = {}
        frame = {"type": "system", "subtype": subtype, field: "Provider is busy; trying again", "level": "warning", "color": "warning"}
        assert parse(driver, frame, context)[0]["notice"]["text"] == frame[field]
        assert parse(driver, frame, context) == [], "same turn does not repeat identical notices"
        assert parse(driver, dict(frame, **{field: {"raw": "data"}})) == []
    status = parse(driver, {"type": "system", "subtype": "api_retry", "attempt": 2,
                            "max_retries": 5, "retry_delay_ms": 1500, "error": "server_error"})
    assert status == [{"a": "transient", "msg": {"type": "status", "text":
        "Retrying request · attempt 2 of 5 · next attempt in 1.5s"}}]
    status = parse(driver, {"type": "system", "subtype": "status", "status": "future_status"})
    assert status[0]["msg"]["text"] == "Working…"
    context = driver.turn_context({}, True, "test", "pin")
    result = parse(driver, {"type": "result", "is_error": True, "subtype": "error_max_turns",
                            "errors": ["The configured turn limit was reached", {"private": "not display text"}]}, context)
    assert result[-1]["data"]["error"] == "The configured turn limit was reached"
    result = parse(driver, {"type": "result", "is_error": True, "subtype": "future_error", "errors": None}, {})
    assert result[-1]["data"]["error"] == "Could not complete the request"


def codex_checks():
    driver = CodexDriver()
    ctx = {"thread_id": "thread-a", "turn_id": "turn-a", "phase": "running"}
    warning = {"method": "warning", "params": {"threadId": "thread-a", "message": "Switched to another transport"}}
    assert parse(driver, warning, ctx)[0]["notice"] == {"text": "Switched to another transport", "tone": "warn"}
    assert parse(driver, warning, ctx) == []
    assert parse(driver, {"method": "warning", "params": {"threadId": "thread-b", "message": "Not ours"}}, ctx) == []
    notice = parse(driver, {"method": "configWarning", "params": {"summary": "Setting ignored", "details": "Choose a supported value", "path": "/private/path"}}, ctx)[0]["notice"]
    assert notice["text"] == "Setting ignored · Choose a supported value"
    assert "/private" not in str(notice)
    for retry in (True, False):
        actions = parse(driver, {"method": "error", "params": {
            "threadId": "thread-a", "turnId": "turn-a", "willRetry": retry,
            "error": {"message": "A readable provider explanation", "codexErrorInfo": "future_code"}}}, ctx)
        if retry:
            assert actions[0]["a"] == "transient" and "Retrying" in actions[0]["msg"]["text"]
        else:
            assert actions[0]["kind"] == "error" and actions[0]["data"]["text"] == "A readable provider explanation"
    for code, expected in (("usageLimitExceeded", "Your usage limit has been reached"),
                           ({"responseStreamDisconnected": {"httpStatusCode": 503}}, "The response connection was interrupted"),
                           ("future_code", "Codex turn error")):
        actions = parse(driver, {"method": "error", "params": {"error": {"codexErrorInfo": code}}}, {})
        assert actions[0]["data"]["text"] == expected
    limits = {"limitId": "account-bucket", "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": RESET},
              "rateLimitReachedType": "rate_limit_reached"}
    sample = lambda value: {"method": "account/rateLimits/updated", "params": {"rateLimits": value}}
    rate_ctx = {}
    rate = parse(driver, sample(limits), rate_ctx)[0]
    assert "100% of 5-hour allowance used" in rate["notice"]["text"]
    assert rate["notice"]["resets_at"] == RESET and rate["info"] == limits
    assert parse(driver, sample(limits), rate_ctx)[0]["notice"] is None
    assert parse(driver, sample({"primary": {"usedPercent": 99}}), rate_ctx)[0]["notice"] is None
    unknown = parse(driver, sample(dict(limits, rateLimitReachedType="future_limit")), rate_ctx)[0]["notice"]
    assert "future_limit" not in unknown["text"] and "resets_at" not in unknown
    missing = parse(driver, sample(dict(limits, primary={"usedPercent": 100})), {})[0]["notice"]
    assert missing["text"] == "Usage limit reached" and "resets_at" not in missing
    several = dict(limits, secondary={"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": RESET + 100})
    assert parse(driver, sample(several))[0]["notice"]["resets_at"] == RESET + 100
    several["secondary"].pop("resetsAt")
    assert "resets_at" not in parse(driver, sample(several))[0]["notice"]


def opencode_checks():
    driver = OpenCodeDriver()
    def result(value):
        return parse(driver, {"id": "puppy:prompt", **value}, {"phase": "prompt"})[-1]["data"]
    with patch("puppy.drivers.opencode._session_totals", return_value=None):
        for stop, expected in (("max_tokens", "response length limit"), ("max_turn_requests", "limit on requests"),
                               ("refusal", "declined"), ("cancelled", "cancelled"),
                               ("future_stop", "without confirming completion"), (None, "without confirming completion")):
            value = result({"result": {"stopReason": stop}})
            assert value["ok"] is False and expected in value["error"]
            assert "future_stop" not in value["error"]
        assert result({"result": {"stopReason": "end_turn"}})["ok"] is True
        value = result({"error": {"code": -32603, "message": "Provider temporarily unavailable",
                                 "data": {"service": "session", "errorName": "APIError", "token": "private"}}})
        assert value["error"] == "Provider temporarily unavailable"
        value = result({"error": {"code": -32000, "data": {"private": "not a message"}}})
        assert value["error"] == "Sign in to the model provider to continue"
        value = result({"error": {"code": ["malformed"], "message": {"private": "not a message"}}})
        assert value["error"] == "Could not complete the request"


async def wire_contract(factory):
    """A real native frame -> runner -> authenticated WebSocket on each runtime."""
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    driver = get_driver("claude")
    frames = [
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "utilization": .8,
             "rateLimitType": "seven_day", "resetsAt": RESET}},
        {"type": "system", "subtype": "notification", "text": "A readable engine notice", "color": "warning"},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Finished"},
    ]
    script = ROOT / "native-fixture.py"
    script.write_text("import sys\nsys.stdin.readline()\n" +
                      "\n".join("print(" + repr(json.dumps(f)) + ", flush=True)" for f in frames) +
                      "\nfor line in sys.stdin: pass\n")
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client, AsyncExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", return_value=[sys.executable, str(script)]))
        sid = db.create_session("Engine notice fixture", "claude", str(ROOT), "", "", "", "default")
        hub = runner.hub(sid)
        try:
            ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
            assert (await ws.receive_json(timeout=3))["type"] == "snapshot"
            assert hub.send_message("test") == {"queued": False}
            received = []
            while True:
                frame = await ws.receive_json(timeout=8)
                received.append(frame)
                if frame["type"] == "turn_done":
                    break
            await hub.turn_task
            rate = next(row for row in received if row["type"] == "rate_limit")
            assert rate["engine"] == "claude" and "80% used" in rate["notice"]["text"]
            assert rate["info"]["status"] == "allowed_warning" and rate["info"]["captured_at"] > 0
            notice = next(row for row in received if row["type"] == "engine_notice")
            assert notice["engine"] == "claude" and notice["notice"]["text"] == "A readable engine notice"
            assert db.get_events(sid)[-1]["data"]["ok"] is True
            await ws.close()
        finally:
            if hub.turn_task and not hub.turn_task.done():
                await hub.interrupt()
                await hub.turn_task
            runner.drop_hub(sid)
            db.delete_session(sid)


async def main():
    config.load()
    db.connect()
    claude_checks()
    codex_checks()
    opencode_checks()
    assert messages.number(10 ** 400) is None
    for malformed in (True, "1", [], {}, float("inf")):
        assert messages.number(malformed) is None
    context = {}
    for i in range(100):
        messages.once(context, messages.notice(str(i)))
    assert len(context["display_notices"]) == messages.NOTICE_MEMORY
    with ExitStack() as stack:
        for name in ("browser_agent", "terminal_agent", "vnc_agent", "spawn_agent", "session_agent"):
            stack.enter_context(patch.object(getattr(runner, name), "turn_mcp", return_value=None))
        stack.enter_context(patch.object(runner.session_links, "prepare_turn", AsyncMock()))
        stack.enter_context(patch.object(runner.system_prompts, "turn_prompt", return_value=""))
        stack.enter_context(patch.object(runner.notify, "session_finished"))
        await wire_contract(build_app)
        await wire_contract(backend_app)
    print("PASS: native readable messages, quota semantics, unknown/malformed fields, bounded repeats, "
          "and native frames through the runner and authenticated sockets on both runtimes")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT)
