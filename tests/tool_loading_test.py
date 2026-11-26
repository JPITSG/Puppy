#!/usr/bin/env python3
"""Puppy's tool sets loaded up front on Claude Code: the servers a session's
recent tool calls and tool lookups named, in every engine's spelling, within
its last events and never another session's; the composer's mentions; the
descriptors marked and a failed read marking only what the prompt names; the
claude driver's alwaysLoad for marked servers alone; and real turns through
the runner on both runtimes - a session that used two tool sets and a prompt
naming a third, a fresh session, and an engine that does not defer. No
engine, network or quota."""
from __future__ import annotations

import json
import sys


def fixture():
    """A Claude Code that answers its initialize and ends the turn."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-load", "tools": []})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-load", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit

import asyncio  # noqa: E402
from contextlib import ExitStack  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402
import time  # noqa: E402
from unittest.mock import patch  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "backend"))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("tool-loading-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import (browser_agent, config, db, files_agent, runner, session_agent,  # noqa: E402
                   spawn_agent, task_agent, terminal_agent, tool_loading, vnc_agent)
from puppy.drivers import get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from puppy_backend.app import build_app as build_backend_app  # noqa: E402

BRIDGES = {"puppy_browser": browser_agent, "puppy_terminal": terminal_agent,
           "puppy_vnc": vnc_agent, "puppy_spawn": spawn_agent,
           "puppy_session": session_agent, "puppy_tasks": task_agent,
           "puppy_files": files_agent}


def use(sid, tool, **data):
    db.add_event(sid, "tool_use", dict(data, tool=tool, tool_use_id="t{}".format(time.monotonic_ns())))


def windows():
    sid = db.create_session("Used", "claude", str(ROOT), "", "", "", "default")
    other = db.create_session("Other", "claude", str(ROOT), "", "", "", "default")
    use(other, "mcp__puppy_files__share_file", input={})
    assert tool_loading.recent_servers(sid) == set(), "nothing used yet"
    use(sid, "mcp__puppy_vnc__click", input={"x": 1})
    use(sid, "puppy_browser.navigate", input={"url": "x"})          # Codex's spelling
    use(sid, "ToolSearch", input={"query": "select:mcp__puppy_session__read,Bash"})
    use(sid, "Bash", input={"command": "grep puppy_terminal notes.txt"})  # only words
    db.add_event(sid, "assistant", {"text": "the puppy_spawn bridge"})    # not a call
    db.add_event(sid, "tool_result", {"tool_use_id": "x", "content": "puppy_tasks"})
    expected = {"puppy_vnc", "puppy_browser", "puppy_session"}
    assert tool_loading.recent_servers(sid) == expected, tool_loading.recent_servers(sid)
    # the same answer where SQLite has no JSON functions
    tool_loading._json1 = False
    try:
        assert tool_loading.recent_servers(sid) == expected
    finally:
        tool_loading._json1 = None
    # past the window, a tool set goes; what is used again comes back
    with patch.object(tool_loading, "RECENT_EVENTS", 20):
        for n in range(25):
            db.add_event(sid, "assistant", {"text": "filler {}".format(n)})
        assert tool_loading.recent_servers(sid) == set()
        use(sid, "mcp__puppy_tasks__tasks", input={})
        assert tool_loading.recent_servers(sid) == {"puppy_tasks"}
    print("recent servers: every engine's spelling and lookups, only calls, one session, a window")


def mentions():
    m = tool_loading.mentioned_servers
    assert m("open @Browser A8AR and log in") == {"puppy_browser"}
    assert m("@New browser please") == {"puppy_browser"}
    assert m("type ls in @Terminal Q2W3 then @New terminal") == {"puppy_terminal"}
    assert m("@VNC 192.0.2.5:1 secret") == {"puppy_vnc"} and m("@RDP Z9Z9") == {"puppy_vnc"}
    assert m("@Spawn 3 agents using codex to review") == {"puppy_spawn"}
    assert m("compare with @Session-Plan-A7K2") == {"puppy_session"}
    assert m("@Task-Fix-login-12 and @All tasks, then @New task using claude") == {"puppy_tasks"}
    assert m("@Apply when done") == {"puppy_tasks"}
    assert m("email me@Browser.example about the VNCs") == set(), "a word that only contains a name"
    assert m("") == set() and m(None) == set()
    print("mentions: each of the composer's forms names its tool set")


async def marking():
    sid = db.create_session("Marked", "claude", str(ROOT), "", "", "", "default")
    use(sid, "mcp__puppy_vnc__screenshot", input={})
    items = [{"name": name, "command": "x"} for name in BRIDGES]
    marked = await tool_loading.mark(sid, "use @Browser A8AR", items + [None])
    assert sorted(marked) == ["puppy_browser", "puppy_vnc"], marked
    assert [item["name"] for item in items if item.get("always_load")] == sorted(marked, key=list(BRIDGES).index)
    # a read that fails marks what the prompt names, and nothing else
    items = [{"name": name, "command": "x"} for name in BRIDGES]
    with patch.object(tool_loading, "recent_servers", side_effect=RuntimeError("database")):
        assert await tool_loading.mark(sid, "@Spawn an agent using codex", items) == ["puppy_spawn"]
    assert await tool_loading.mark(sid, "@VNC x", [None, None]) == []
    # the claude driver says alwaysLoad for the marked servers alone
    items = [{"name": name, "command": "python3", "args": ["-m", name]} for name in BRIDGES]
    items[0]["always_load"] = items[2]["always_load"] = True
    argv = get_driver("claude").build_cmd(
        {"cwd": str(ROOT)}, True, "probe", "turn", browser_mcp=items[0], terminal_mcp=items[1],
        vnc_mcp=items[2], spawn_mcp=items[3], session_mcp=items[4], task_mcp=items[5],
        files_mcp=items[6])
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert {name for name, entry in servers.items() if entry.get("alwaysLoad") is True} == \
        {"puppy_browser", "puppy_vnc"}, servers
    assert all("alwaysLoad" not in entry for name, entry in servers.items()
               if name not in ("puppy_browser", "puppy_vnc"))
    assert get_driver("claude").defers_mcp_tools is True
    assert get_driver("codex").defers_mcp_tools is False and get_driver("opencode").defers_mcp_tools is False
    print("marking: used or named servers marked, a failed read leaves the prompt's, alwaysLoad for them alone")


async def turn(sid, text, engine="claude"):
    """One turn through the runner; answers the descriptors build_cmd got."""
    driver = get_driver(engine)
    hub = runner.hub(sid)
    seen = {}

    def command(*args, **kwargs):
        seen.update(kwargs)
        return [sys.executable, str(Path(__file__).resolve()), "--fixture"]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        for name, module in BRIDGES.items():
            stack.enter_context(patch.object(
                module, "turn_mcp", side_effect=lambda *a, _name=name, **k: {"name": _name, "command": "x"}))
        try:
            assert hub.send_message(text) == {"queued": False}
            deadline = time.monotonic() + 20
            while hub.status != "idle" or "browser_mcp" not in seen:
                assert time.monotonic() < deadline, "the fixture turn did not end"
                await asyncio.sleep(.02)
            await hub.turn_task
        finally:
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    return {item["name"] for item in seen.values()
            if isinstance(item, dict) and item.get("always_load")}


async def runtime(factory, label):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    async with TestClient(TestServer(app)):
        sid = db.create_session("Turns " + label, "claude", str(ROOT), "", "", "", "default")
        use(sid, "mcp__puppy_vnc__click", input={})
        use(sid, "ToolSearch", input={"query": "select:mcp__puppy_session__search"})
        loaded = await turn(sid, "and now @Browser Q1W2 too")
        assert loaded == {"puppy_vnc", "puppy_session", "puppy_browser"}, loaded
        fresh = db.create_session("Fresh " + label, "claude", str(ROOT), "", "", "", "default")
        assert await turn(fresh, "hello") == set(), "a fresh session defers everything"
        other = db.create_session("Codex " + label, "codex", str(ROOT), "", "", "", "default")
        use(other, "mcp__puppy_vnc__click", input={})
        assert await turn(other, "@Browser Q1W2", engine="codex") == set(), \
            "an engine that loads every tool is not marked"
    print("{}: a turn loads the sets its session used and its prompt names, on Claude Code alone".format(label))


async def main():
    config.load()
    db.connect()
    try:
        windows()
        mentions()
        await marking()
        await runtime(build_app, "full runtime")
        await runtime(build_backend_app, "headless backend")
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
    print("tool loading tests passed")


if __name__ == "__main__":
    asyncio.run(main())
