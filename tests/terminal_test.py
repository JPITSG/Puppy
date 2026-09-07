#!/usr/bin/env python3
"""No-network, no-quota tests for shared identified Puppy terminals."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("terminal-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import config, db, protocol, runner, system_prompts, terminal  # noqa: E402
from puppy import terminal_agent  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import CodexDriver  # noqa: E402
from puppy.drivers.opencode import OpenCodeDriver  # noqa: E402
from puppy.web import build_app  # noqa: E402


class CaptureSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class TerminalViewer(CaptureSocket):
    def __init__(self):
        super().__init__()
        self.bytes = bytearray()
        self.closed = False

    async def send_bytes(self, payload):
        self.bytes.extend(payload)

    async def close(self, *args, **kwargs):
        self.closed = True


async def wait_for(predicate, timeout=4.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for terminal condition")


async def check_output_drain() -> None:
    instance = terminal.TerminalInstance("T1ST", "/bin/sh", str(TEST_ROOT), 80, 24)
    instance.running = True
    # No reader is registered: these tests invoke one readiness callback.
    read_fd, write_fd = os.pipe()
    instance.master = read_fd
    viewer = TerminalViewer()
    await instance.attach_viewer(viewer)
    try:
        parts = [b"\x1b[31m", b"split utf8: \xe2", b"\x82\xac\x1b[0m\r\n"]
        event = instance.output_event
        with patch.object(terminal.os, "read", side_effect=parts + [BlockingIOError()]):
            instance._on_readable()
        assert instance._raw_since()[0] == b"".join(parts)
        assert len(instance.raw_chunks) == 1
        assert event.is_set()
        # A permanently readable producer must yield after the byte budget.
        with patch.object(terminal.os, "read", side_effect=lambda fd, size: b"x" * min(size, 4095)):
            before = instance.output_sequence
            instance._on_readable()
        assert instance.output_sequence - before == terminal.OUTPUT_READ_BUDGET
        # EOF following data must retain/send that data before ending.
        with patch.object(terminal.os, "read", side_effect=[b"last output", OSError()]), \
                patch.object(instance, "_natural_end", new_callable=AsyncMock) as end:
            instance._on_readable()
            await instance.end_task
            end.assert_awaited_once()
        await wait_for(lambda: viewer.bytes.endswith(b"last output"))
        assert bytes(viewer.bytes) == instance._raw_since()[0]
    finally:
        instance.detach_viewer(viewer)
        instance.running = False
        instance._cancel_idle()
        instance._close_master()
        os.close(write_fd)


def check_transcript_tail() -> None:
    render = terminal._plain_terminal_text
    for raw, expected in [
            (b"abc\rZ", "Zbc"), (b"abc\b\bZ", "aZc"),
            (b"abc\r\tZ", "        Z"), (b"a\tZ", "a       Z"),
            (b"abc\r\r", "abc"), (b"abc   \r\n\r\n", "abc"),
            (b"\x1b[31mred\x1b[0m\r\nnext", "red\nnext"),
            (b"a\x1b]0;hidden\nOSC\x07b\nlast", "ab\nlast"),
            (b"one\n\bX\n\tY", "one\nX\n        Y"),
            ("café € 終\r\n".encode(), "café € 終"),
            (b"\xff\r\n", "\ufffd")]:
        assert render(raw, 1000, 100) == expected, raw
    assert render(b"old\rchanged\nabc\bZ\nlast", 1000, 2) == "abZ\nlast"
    assert render(b"old\nline\n\n", 1000, 2) == ""
    assert render(b"old\nline\nlast", 5, 2) == "\nlast"
    assert render(b"old\nline\nlast", 1000, 0) == "old\nline\nlast"
    # Discarded output can contain cursor controls and multiline OSC strings;
    # neither may change the retained lines or their limit.
    prefix = (b"old\rchanged\b!\t\n" * 100000) + b"\x1b]0;hidden\nOSC\x07"
    assert render(prefix + b"tail\r\nlast", 1000, 2) == "tail\nlast"
    assert render(b"x" * terminal.MAX_RAW_OUTPUT + b"tail\r", 4, 200) == "tail"


async def check_input_readiness() -> None:
    loop = asyncio.get_running_loop()
    instance = terminal.TerminalInstance("T1ST", "/bin/sh", str(TEST_ROOT), 80, 24)
    instance.running = True
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    instance.master = write_fd
    received = bytearray()

    def drain():
        try:
            while True:
                part = os.read(read_fd, 65536)
                if not part:
                    return
                received.extend(part)
        except BlockingIOError:
            pass

    tasks = []
    try:
        # Force partial writes and EAGAIN, then let the descriptor wake the
        # sender. Input cannot use a retry sleep, interleave writes or lose bytes.
        first, second = b"a" * 131072, b"b" * 131072
        with patch.object(terminal.asyncio, "sleep", side_effect=AssertionError("input polled")):
            tasks = [asyncio.create_task(instance.write(first)),
                     asyncio.create_task(instance.write(second))]
            loop.call_later(.005, loop.add_reader, read_fd, drain)
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        drain()
        assert received == first + second
        assert instance.write_ready is None and not instance.input_lock.locked()
        loop.remove_reader(read_fd)
        # Fill the pipe so timeout/cancel/close each meet a blocked writer.
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        try:
            await instance._wait_writable(time.monotonic() + .005)
        except terminal.TerminalError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("blocked input had no timeout")
        assert instance.write_ready is None
        for close in (False, True):
            task = asyncio.create_task(instance.write(b"blocked"))
            tasks.append(task)
            await wait_for(lambda: instance.write_ready is not None)
            with patch.object(loop, "remove_writer", wraps=loop.remove_writer) as removed:
                if close:
                    instance._close_master()
                else:
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    assert not close
                except terminal.TerminalError as exc:
                    assert close and "has ended" in str(exc)
                else:
                    raise AssertionError("blocked input incorrectly succeeded")
                # Closing must remove the watcher once, before fd reuse; the
                # waiting task must not remove a new owner's watcher afterwards.
                removed.assert_called_once_with(write_fd)
            assert instance.write_ready is None and not instance.input_lock.locked()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.remove_reader(read_fd)
        instance._close_master()
        os.close(read_fd)


async def check_resize_deduplication() -> None:
    instance = terminal.TerminalInstance("T1ST", "/bin/sh", str(TEST_ROOT), 80, 24)
    instance.running, instance.master, instance.pid = True, 123, 456
    with patch.object(terminal, "_set_winsize") as resize, \
            patch.object(terminal.os, "kill") as kill:
        await instance.resize(80, 24)
        resize.assert_not_called()
        await instance.resize(100, 30)
        resize.assert_called_once_with(123, 100, 30)
        await instance.resize("100", "30")
        resize.assert_called_once()
        await instance.resize(999, 999)
        await instance.resize(1000, 1000)
        assert resize.call_count == 2
        assert (instance.cols, instance.rows) == (500, 300)
        kill.assert_not_called()


async def check_slow_viewer() -> None:
    class SlowSocket(TerminalViewer):
        async def send_bytes(self, payload):
            await asyncio.Event().wait()

    instance = terminal.TerminalInstance("T1ST", "/bin/sh", str(TEST_ROOT), 80, 24)
    fast, slow, replay = TerminalViewer(), SlowSocket(), TerminalViewer()
    instance.viewers = {fast: terminal._Viewer(fast), slow: terminal._Viewer(slow)}
    sender = instance.viewers[slow]
    try:
        block = b"x" * terminal.OUTPUT_READ_BUDGET
        for _ in range(terminal.MAX_VIEWER_OUTPUT // len(block) + 2):
            instance._append_output(block)
            await asyncio.sleep(0)
        await wait_for(lambda: slow.closed and sender.task.done())
        assert sender.queued_bytes <= terminal.MAX_VIEWER_OUTPUT
        assert not fast.closed
        assert len(fast.bytes) == instance.output_sequence
        # A complete retained replay still fits the byte budget and precedes
        # live output on a newly attached viewer.
        instance.running = True
        raw, _ = instance._raw_since()
        await instance.attach_viewer(replay)
        instance._append_output(b"live after replay")
        await wait_for(lambda: replay.bytes.endswith(b"live after replay"))
        assert bytes(replay.bytes) == raw + b"live after replay"
        assert replay.messages[0]["replay_truncated"] is True
        assert [item["type"] for item in replay.messages[:2]] == ["status", "binding"]
        # Detach also cancels a sender which has already marked itself closed.
        sender.cancel()
    finally:
        instance.running = False
        instance.detach_viewer(fast)
        instance.detach_viewer(slow)
        instance.detach_viewer(replay)


def check_static_contract() -> None:
    caps = protocol.execution_capabilities(include_terminal=True)
    assert protocol.TERMINAL_CAPABILITY in caps
    assert protocol.TERMINAL_INSTANCES_CAPABILITY in caps
    assert protocol.TERMINAL_HANDOFF_CAPABILITY in caps
    disabled = protocol.execution_capabilities(include_terminal=False)
    assert protocol.TERMINAL_CAPABILITY not in disabled
    assert protocol.TERMINAL_INSTANCES_CAPABILITY not in disabled
    assert protocol.TERMINAL_HANDOFF_CAPABILITY not in disabled

    assert terminal._request_spec({"command": "/bin/bash"})["command"] == "/bin/bash"
    try:
        terminal._request_spec({"cmd": "/bin/bash"})
    except terminal.TerminalError as exc:
        assert "use command" in str(exc)
    else:
        raise AssertionError("retired anonymous-terminal command alias was accepted")

    app = build_app()
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    expected = {
        ("GET", "/api/terminal/instances"),
        ("POST", "/api/terminal/instances"),
        ("DELETE", "/api/terminal/instances/{terminal_id}"),
        ("GET", "/api/terminal/instances/{terminal_id}/binding"),
        ("POST", "/api/terminal/instances/{terminal_id}/binding"),
        ("DELETE", "/api/terminal/instances/{terminal_id}/binding"),
        ("GET", "/api/ws/terminal/{terminal_id}"),
    }
    assert expected <= routes, sorted(routes)

    ui = (BASE / "puppy" / "static" / "app.js").read_text(encoding="utf-8")
    css = (BASE / "puppy" / "static" / "app.css").read_text(encoding="utf-8")
    assert "`Terminal ${id} @ ${backendName(bid)}`" in ui
    assert 'class="br-meta term-meta edge-scroll-viewport hidden"' in ui
    assert 'aria-label="Terminal identity"' in ui
    assert 'aria-label="Copy Terminal ID"' in ui
    assert '"Terminal guidance"' in ui
    assert 'case "terminal_activity":' in ui
    assert 'handleTerminalActivity(0, d.session_id, d.turn_id, d.terminal_id)' in ui
    assert 'backend.capabilities.includes("terminal-instances")' in ui
    assert '`terminal/instances/${encodeURIComponent(terminalId)}/binding`' in ui
    # The terminal has the browser strip's six-pixel gaps on both sides: a
    # direct six above, then four below plus the terminal wrapper's two.
    assert '.br-meta.term-meta{padding-top:6px;' in css
    assert '.term-wrap{flex:1;min-height:0;padding:2px 8px 8px;display:flex}' in css
    assert '.term-host::after{' in css
    assert 'border-radius:inherit;box-shadow:inset 0 0 0 1px var(--browser-frame);' \
        in css
    assert '.chip.browser,.chip.terminal{' in css

    # The grid is fitted from the box the terminal is opened on, so that box
    # must carry no inset of its own. FitAddon reads
    # getComputedStyle(parent).height, which border-box sizing reports as the
    # padding box: measuring the padded .term-host counted its 6px inset as
    # room for another row, and .term-host{overflow:hidden} then cut that row
    # in half along the bottom edge.
    assert '<div class="term-mount"></div>' in ui
    assert 'this.mount = this.root.querySelector(".term-mount");' in ui
    assert "this.term.open(this.mount);" in ui
    assert "this.resizeObs.observe(this.mount);" in ui
    assert "this.term.open(this.host)" not in ui
    assert ".term-mount{height:100%}" in css
    assert ".term-mount .xterm{height:100%}" in css
    mount_start = css.index(".term-mount{")
    mount_rule = css[mount_start:css.index("}", mount_start)]
    assert "padding" not in mount_rule and "border" not in mount_rule, mount_rule
    # the visible inset stays on the frame, which is not what gets measured
    assert ".term-host{" in css and "padding:6px" in css[
        css.index(".term-host{"):css.index("}", css.index(".term-host{"))]

    start = ui.index("function terminalInstancesFor(")
    end = ui.index("\n}\n", start) + 2
    script = """
const state={backends:[
  {id:1,protocol:0,capabilities:[]},
  {id:2,protocol:2,capabilities:[\"terminal\"]},
  {id:3,protocol:2,capabilities:[\"terminal-instances\"]}
]};
%s
console.log(JSON.stringify([terminalInstancesFor(0),terminalInstancesFor(1),
  terminalInstancesFor(2),terminalInstancesFor(3)]));
""" % ui[start:end]
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [True, False, False, True]


def check_mcp_protocol() -> None:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "terminal-test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "puppy.terminal_agent"], cwd=str(BASE),
        input="".join(json.dumps(item) + "\n" for item in requests),
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [item["id"] for item in responses] == [1, 2], responses
    initialized = responses[0]["result"]
    assert initialized["serverInfo"]["name"] == "Puppy shared terminal"
    instructions = initialized["instructions"]
    assert "only when the user specifically asks" in instructions
    assert "ordinary shell and file tools" in instructions
    tools = {item["name"]: item for item in responses[1]["result"]["tools"]}
    assert set(tools) == {
        "terminals", "snapshot", "type", "press", "wait_for",
        "new_terminal", "close_terminal",
    }
    assert "terminal_id" in tools["snapshot"]["inputSchema"]["properties"]
    assert "terminal_id" not in tools["new_terminal"]["inputSchema"]["properties"]
    assert tools["type"]["annotations"]["destructiveHint"] is True
    assert tools["snapshot"]["annotations"]["readOnlyHint"] is True


def check_driver_wiring(session_id: int) -> None:
    old_server = terminal_agent._server
    terminal_agent._server = object()
    try:
        descriptor = terminal_agent.turn_mcp(session_id, "terminal-turn")
    finally:
        terminal_agent._server = old_server
    assert descriptor and descriptor["name"] == "puppy_terminal"
    assert descriptor["env"]["PUPPY_TERMINAL_SESSION_ID"] == str(session_id)
    assert descriptor["env"]["PUPPY_TERMINAL_TURN_ID"] == "terminal-turn"
    assert "explicitly asks" in descriptor["engine_guidance"]
    browser = {
        "name": "puppy_browser", "command": "/tmp/browser-agent", "args": [],
        "env": {"BROWSER_TEST": "1"}, "engine_guidance": "Browser policy.",
    }
    session = db.get_session(session_id)

    claude = ClaudeDriver().build_cmd(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=descriptor, system_prompt="Node policy.")
    mcp_config = json.loads(claude[claude.index("--mcp-config") + 1])
    assert set(mcp_config["mcpServers"]) == {"puppy_browser", "puppy_terminal"}
    guidance = claude[claude.index("--append-system-prompt") + 1]
    assert guidance == "Node policy.\n\nBrowser policy.\n\n" + \
        descriptor["engine_guidance"]

    codex = CodexDriver().build_cmd(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=descriptor, system_prompt="Node policy.")
    assert any("mcp_servers.puppy_browser.command" in item for item in codex)
    assert any("mcp_servers.puppy_terminal.command" in item for item in codex)
    codex_context = CodexDriver().turn_context(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=descriptor, system_prompt="Node policy.")
    assert "<puppy_terminal_policy>" in codex_context["prompt"]
    assert codex_context["prompt"].endswith("\n\nhello")

    opencode = OpenCodeDriver()
    context = opencode.turn_context(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=descriptor, system_prompt="Node policy.")
    assert [item["name"] for item in context["mcp_servers"]] == \
        ["puppy_browser", "puppy_terminal"]
    inline = json.loads(opencode.build_env(
        session, True, "hello", "pin", browser_mcp=browser,
        terminal_mcp=descriptor,
        system_prompt="Node policy.")["OPENCODE_CONFIG_CONTENT"])
    prompt = inline["agent"]["puppy_console"]["prompt"]
    assert prompt == "Node policy.\n\nBrowser policy.\n\n" + \
        descriptor["engine_guidance"]


async def check_terminal_lifecycle(session_id: int, other_session_id: int) -> None:
    registry = terminal.TerminalRegistry()
    old_manager = terminal._manager
    terminal._manager = registry
    viewer = TerminalViewer()
    session_capture = CaptureSocket()
    updates_capture = CaptureSocket()
    hub = runner.hub(session_id)
    hub.status = "running"
    hub._active_turn_id = "terminal-turn"
    hub.attach(session_capture)
    runner.updates_attach(updates_capture)
    try:
        old_idle = config.get("terminal.idle_timeout")
        config.set_timeouts({"terminal_idle_seconds": 1})
        try:
            unviewed = await registry.create(
                command="/bin/bash --noprofile --norc", cwd=str(TEST_ROOT))
            await wait_for(lambda: not unviewed.running, timeout=2.0)
            assert "No viewers" in unviewed.ended_reason
            await registry.close(unviewed.terminal_id, "idle test complete")
        finally:
            config.set_timeouts({"terminal_idle_seconds": old_idle})

        instance = await registry.create(
            command="/bin/bash --noprofile --norc", cwd=str(TEST_ROOT),
            owner_session=session_id)
        assert len(instance.terminal_id) == 4
        assert instance.terminal_id.isalnum() and instance.terminal_id.isupper()
        assert any(char.isalpha() for char in instance.terminal_id)
        assert any(char.isdigit() for char in instance.terminal_id)
        await instance.attach_viewer(viewer)
        await wait_for(lambda: len(viewer.messages) >= 2)
        assert viewer.messages[0]["type"] == "status"
        assert viewer.messages[1]["type"] == "binding"
        assert viewer.messages[1]["session_id"] == session_id

        second = await registry.create(
            command="/bin/bash --noprofile --norc", cwd=str(TEST_ROOT))
        assert second.terminal_id != instance.terminal_id
        await registry.bind(second.terminal_id, session_id)
        assert instance.owner_session is None and second.owner_session == session_id
        await registry.bind(second.terminal_id, other_session_id)
        assert second.owner_session == other_session_id
        await registry.bind(instance.terminal_id, session_id)
        await registry.close(second.terminal_id, "binding test complete")

        listed = await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "terminals", "params": {},
        })
        assert "Terminal {}".format(instance.terminal_id) in listed["text"]
        snapshot = await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "snapshot", "params": {"terminal_id": instance.terminal_id},
        })
        assert "UNTRUSTED TERMINAL OUTPUT" in snapshot["text"]
        await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "type", "params": {
                "terminal_id": instance.terminal_id,
                "text": "printf '\\033[31mPUPPY_MCP_OK\\033[0m\\n'"},
        })
        await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "press", "params": {
                "terminal_id": instance.terminal_id, "key": "Enter"},
        })
        waited = await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "wait_for", "params": {
                "terminal_id": instance.terminal_id, "text": "PUPPY_MCP_OK",
                "timeout_ms": 3000},
        })
        assert "Requested text was observed." in waited["text"], waited
        assert "\x1b" not in waited["text"]
        await wait_for(lambda: any(item.get("type") == "agent_input"
                                   for item in viewer.messages))
        await wait_for(lambda: any(item.get("type") == "terminal_activity"
                                   for item in session_capture.messages))
        await wait_for(lambda: any(item.get("type") == "terminal_activity"
                                   for item in updates_capture.messages))
        session_events = [item for item in session_capture.messages
                          if item.get("type") == "terminal_activity"]
        update_events = [item for item in updates_capture.messages
                         if item.get("type") == "terminal_activity"]
        assert len(session_events) == 1 and len(update_events) == 1
        assert session_events[0]["terminal_id"] == instance.terminal_id

        closed = await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "close_terminal", "params": {},
        })
        assert "Closed Terminal {}".format(instance.terminal_id) in closed["text"]
        await wait_for(lambda: not instance.running)
        try:
            await terminal_agent._dispatch({
                "session_id": session_id, "turn_id": "terminal-turn",
                "method": "close_terminal", "params": {},
            })
        except terminal_agent.TerminalAgentError as exc:
            assert "No shared Terminal is linked" in str(exc)
        else:
            raise AssertionError("close_terminal allocated a terminal to close")

        fresh = await terminal_agent._dispatch({
            "session_id": session_id, "turn_id": "terminal-turn",
            "method": "new_terminal", "params": {},
        })
        fresh_id = fresh["text"].split(" ", 2)[1]
        assert fresh_id != instance.terminal_id
        replacement = await registry.linked_terminal(session_id)
        assert replacement.terminal_id == fresh_id and replacement.running

        hub.status = "idle"
        try:
            await terminal_agent._dispatch({
                "session_id": session_id, "turn_id": "terminal-turn",
                "method": "snapshot", "params": {},
            })
        except terminal_agent.TerminalAgentError as exc:
            assert "no longer running" in str(exc)
        else:
            raise AssertionError("expired terminal turn retained bridge authority")
    finally:
        hub.detach(session_capture)
        runner.updates_detach(updates_capture)
        instance = locals().get("instance")
        if instance is not None:
            instance.detach_viewer(viewer)
        await registry.stop("terminal test cleanup")
        terminal._manager = old_manager
        runner._hubs.pop(session_id, None)


async def main() -> None:
    try:
        config.load()
        db.connect()
        config.set_system_prompts(
            "", config.DEFAULT_REMOTE_WORKSPACE_SYSTEM_PROMPT,
            config.DEFAULT_BROWSER_SYSTEM_PROMPT,
            config.DEFAULT_TERMINAL_SYSTEM_PROMPT,
            config.DEFAULT_SPAWN_SYSTEM_PROMPT)
        prompts = system_prompts.payload()
        assert prompts["terminal"] == config.DEFAULT_TERMINAL_SYSTEM_PROMPT
        assert prompts["terminal_default"] == config.DEFAULT_TERMINAL_SYSTEM_PROMPT
        exported = config.export_data()
        assert config.normalize_import(exported) == exported
        outdated = config.export_data()
        outdated["system_prompt"].pop("terminal")
        try:
            config.normalize_import(outdated)
        except ValueError:
            pass
        else:
            raise AssertionError("outdated prompt settings shape was accepted")
        session_id = db.create_session(
            "terminal agent", "codex", str(TEST_ROOT), "", "", "#7aa2f7",
            "danger-full-access")
        other_session_id = db.create_session(
            "other", "codex", str(TEST_ROOT), "", "", "#4dd0c4",
            "danger-full-access")
        check_static_contract()
        check_mcp_protocol()
        check_driver_wiring(session_id)
        check_transcript_tail()
        await check_output_drain()
        await check_input_readiness()
        await check_resize_deduplication()
        await check_slow_viewer()
        await check_terminal_lifecycle(session_id, other_session_id)
        print("terminal tests passed")
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
