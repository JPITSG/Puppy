#!/usr/bin/env python3
"""Opt-in installed OpenCode probe with an isolated, loopback-only model.

Run: python3 tests/opencode_compact_live_test.py --binary /path/to/opencode
No account, quota, existing native history or user configuration is used.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--package", help="test Puppy modules from a built backend zipapp")
ARGS = parser.parse_args()
if ARGS.package:
    sys.path.insert(0, str(Path(ARGS.package).resolve()))

ROOT = private_root("opencode-compact-live-")
os.environ["PUPPY_DATA"] = str(ROOT / "puppy")
for name in ("config", "data", "cache", "state", "tmp", "project", "home"):
    (ROOT / name).mkdir()
for key in ("CONFIG", "DATA", "CACHE", "STATE"):
    os.environ["XDG_" + key + "_HOME"] = str(ROOT / key.lower())
(ROOT / "models.json").write_text("{}")
os.environ.update({
    "TMPDIR": str(ROOT / "tmp"), "OPENCODE_TEST_HOME": str(ROOT / "home"),
    "OPENCODE_CONFIG": str(ROOT / "fixture.json"),
    "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_MODELS_PATH": str(ROOT / "models.json"),
    "OPENCODE_PURE": "1", "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "1",
})

from aiohttp import web
from aiohttp.test_utils import TestServer
from puppy import config, db, runner
from puppy.drivers import get_driver

SUMMARY = "Checkpoint: the invented lighthouse project uses the codeword lantern. Await the next prompt."


class Model:
    def __init__(self):
        self.mode = "normal"
        self.calls = []
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def handle(self, request):
        body = await request.json()
        self.calls.append(body)
        summary = body.get("model") == "sample-summary"
        if summary:
            self.started.set()
            assert not body.get("tools"), "native compaction must not execute coding tools"
        if summary and self.mode == "fail":
            return web.json_response({"error": {"message": "Fixture compaction refused",
                "type": "invalid_request_error", "code": "fixture_error"}}, status=400)
        if summary and self.mode == "wait":
            await self.release.wait()
        text = SUMMARY if summary else "Fixture reply: acknowledged the invented lighthouse plan."
        if summary and self.mode == "empty":
            text = ""
        incoming, outgoing = max(1, len(json.dumps(body.get("messages", []))) // 4), max(1, len(text) // 4)
        usage = {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": incoming + outgoing}
        common = {"id": "fixture-response", "created": int(time.time()), "model": body.get("model")}
        if not body.get("stream"):
            return web.json_response(dict(common, object="chat.completion", choices=[{"index": 0,
                "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], usage=usage))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        frames = [dict(common, object="chat.completion.chunk", choices=[{"index": 0,
            "delta": {"role": "assistant", "content": text}, "finish_reason": None}]),
            dict(common, object="chat.completion.chunk", choices=[{"index": 0,
            "delta": {}, "finish_reason": "stop"}], usage=usage)]
        for frame in frames:
            await response.write(("data: " + json.dumps(frame) + "\n\n").encode())
        await response.write(b"data: [DONE]\n\n")
        return response


def settings(url):
    return {
        "model": "fixture/sample-chat", "small_model": "fixture/sample-chat",
        "enabled_providers": ["fixture"], "autoupdate": False, "share": "disabled",
        "snapshot": False, "permission": {"*": "deny"},
        "compaction": {"auto": False, "prune": False, "preserve_recent_tokens": 1000},
        "agent": {"compaction": {"model": "fixture/sample-summary"}},
        "command": {"compact": {"template": "COLLISION_FIXTURE: this ordinary command must never run"}},
        "provider": {"fixture": {"npm": "@ai-sdk/openai-compatible", "name": "Fixture provider",
            "options": {"baseURL": url + "/v1", "apiKey": "fixture-only"},
            "models": {key: {"name": key, "limit": {"context": 64000, "output": 4096}}
                       for key in ("sample-chat", "sample-summary")}}},
    }


async def finished(hub):
    async def wait():
        while hub.status != "idle":
            await asyncio.sleep(.05)
    await asyncio.wait_for(wait(), 45)
    if hub.turn_task:
        await hub.turn_task


def last_result(sid):
    return [e["data"] for e in db.get_events(sid) if e["kind"] == "result"][-1]


async def main(binary):
    config.load()
    db.connect()
    model = Model()
    app = web.Application()
    app.router.add_post("/{path:.*}", model.handle)
    async with TestServer(app) as server:
        (ROOT / "fixture.json").write_text(json.dumps(settings(str(server.make_url("/")).rstrip("/"))))
        driver = get_driver("opencode")
        with ExitStack() as stack:
            stack.enter_context(patch.object(driver, "resolved_binary", return_value=binary))
            for name in ("browser_agent", "terminal_agent", "vnc_agent", "spawn_agent", "session_agent"):
                stack.enter_context(patch.object(getattr(runner, name), "turn_mcp", return_value=None))
            stack.enter_context(patch.object(runner.session_links, "prepare_turn", AsyncMock()))
            stack.enter_context(patch.object(runner.system_prompts, "turn_prompt", return_value=""))
            try:
                for mode in ("normal", "fail", "empty", "wait"):
                    sid = db.create_session("Native fixture", "opencode", str(ROOT / "project"),
                                            "", "", "", "auto")
                    hub = runner.hub(sid)
                    model.mode = "normal"
                    history = "HISTORY_{}: invented lighthouse plan. ".format(mode) + "A fictional detail. " * 300
                    hub.send_message(history)
                    await finished(hub)
                    assert last_result(sid)["ok"], db.get_events(sid)
                    old = db.get_session(sid)["native_session_id"]
                    assert old
                    model.mode = mode
                    model.started.clear()
                    start = len(model.calls)
                    assert hub.request_tool("compact")["ok"]
                    if mode != "normal":
                        hub.send_message("Queued prompt must stay held")
                    if mode == "wait":
                        await asyncio.wait_for(model.started.wait(), 30)
                        await hub.interrupt()
                    await finished(hub)
                    summary_calls = model.calls[start:]
                    assert summary_calls and all(c["model"] == "sample-summary" for c in summary_calls)
                    assert "COLLISION_FIXTURE" not in json.dumps(summary_calls)
                    result = last_result(sid)
                    current = db.get_session(sid)["native_session_id"]
                    if mode == "normal":
                        assert result["ok"] and result["compacted"] and current == old, result
                        assert "context_used" not in result and result.get("usage"), result
                    else:
                        assert not current and hub.held == ["Queued prompt must stay held"], db.get_events(sid)
                        if mode != "wait":
                            assert not result["ok"], result
                    model.mode = "normal"
                    start = len(model.calls)
                    hub.send_message("Continue the invented project")
                    await finished(hub)
                    assert last_result(sid)["ok"], db.get_events(sid)
                    continued = json.dumps(model.calls[start:])
                    if mode == "normal":
                        assert SUMMARY in continued
                        assert db.get_session(sid)["native_session_id"] == old
                    else:
                        assert "HISTORY_" + mode in continued and "PREVIOUS CONVERSATION" in continued
                        assert db.get_session(sid)["native_session_id"] != old
                    print("PASS: installed OpenCode {} compaction and subsequent ACP continuation".format(mode), flush=True)
            finally:
                model.release.set()
                for hub in list(runner._hubs.values()):
                    hub.clear_queue()
                    if hub.status == "running":
                        await hub.kill()


if __name__ == "__main__":
    try:
        if ARGS.package:
            assert str(Path(ARGS.package).resolve()) in runner.__file__
        asyncio.run(main(str(Path(ARGS.binary).resolve())))
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
