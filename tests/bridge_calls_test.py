"""The stdio server every Puppy MCP bridge runs (puppy/bridge_calls.py).

Look-only calls are answered side by side, actions alone and in the order
they came; pictures carry the detail the engine asked for; and a real bridge
child answers three look-only calls in the time of one. No engine, network
or quota.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from puppy import bridge_calls  # noqa: E402

TOOLS = [{"name": "look", "annotations": {"readOnlyHint": True}},
         {"name": "act", "annotations": {"readOnlyHint": False}}]


class Refused(RuntimeError):
    pass


def message(number, method, params=None):
    return {"jsonrpc": "2.0", "id": number, "method": method, "params": params or {}}


def tool(number, name, **arguments):
    return message(number, "tools/call", {"name": name, "arguments": arguments})


def run(messages, **options):
    events = []
    written = []

    def call(name, arguments, token):
        events.append(("start", arguments["n"], time.monotonic()))
        time.sleep(arguments.get("s", 0.3))
        events.append(("end", arguments["n"], time.monotonic()))
        if arguments.get("refuse"):
            raise Refused("refused {}".format(arguments["n"]))
        if arguments.get("crash"):
            raise ValueError("a bug")
        return {"text": "done {}".format(arguments["n"]) if not arguments.get("quiet") else ""}

    lines = [(json.dumps(item) + "\n").encode() for item in messages]
    started = time.monotonic()
    bridge_calls.serve(info={"name": "Test", "version": "1"}, instructions="Rules.",
                       tools=TOOLS, call=call, failure=Refused, max_request=4096,
                       write=written.append, fallback="Fallback.", stream=lines, **options)
    return events, written, time.monotonic() - started


def at(events, kind, number):
    return next(stamp for what, n, stamp in events if what == kind and n == number)


def side_by_side():
    events, written, took = run([
        message(1, "initialize", {"protocolVersion": "2025-06-18"}),
        tool(2, "look", n=1), tool(3, "look", n=2), tool(4, "look", n=3),
        message(5, "ping"),
        tool(6, "act", n=4), tool(7, "act", n=5), tool(8, "look", n=6, s=0.05),
    ])
    answers = {item["id"]: item for item in written}
    assert sorted(answers) == list(range(1, 9)) and len(written) == 8, written
    assert answers[1]["result"]["serverInfo"] == {"name": "Test", "version": "1"}
    assert answers[1]["result"]["protocolVersion"] == "2025-06-18"
    assert answers[1]["result"]["instructions"] == "Rules."
    # the three looks run together, and the ping is answered meanwhile
    first_end = min(at(events, "end", n) for n in (1, 2, 3))
    assert all(at(events, "start", n) < first_end for n in (1, 2, 3)), events
    assert [item["id"] for item in written].index(5) < \
        [item["id"] for item in written].index(2), "a ping waits for no look"
    # an action waits for every look before it, and actions keep their order
    assert at(events, "start", 4) >= max(at(events, "end", n) for n in (1, 2, 3))
    assert at(events, "start", 5) >= at(events, "end", 4)
    assert at(events, "start", 6) >= at(events, "end", 5)
    # 0.3 for the looks together, 0.3 for each action, 0.05 for the last look;
    # one after another they would take 1.55 s
    assert 0.9 < took < 1.3, "three looks took the time of one: {:.2f} s".format(took)
    assert answers[6]["result"] == {"content": [{"type": "text", "text": "done 4"}],
                                    "isError": False}
    print("PASS: look-only calls are answered side by side, a ping meanwhile, and every "
          "action alone after them, in the order it came")


def bounded():
    with patch.object(bridge_calls, "CONCURRENT_CALLS", 2):
        events, written, _took = run([tool(n, "look", n=n, s=0.2) for n in (1, 2, 3)])
    assert len(written) == 3
    assert at(events, "start", 3) >= min(at(events, "end", 1), at(events, "end", 2)), events
    print("PASS: at most CONCURRENT_CALLS looks at once; the next waits for a place")


def answers():
    events, written, _took = run([
        tool(1, "look", n=1, s=0, refuse=True), tool(2, "act", n=2, s=0, crash=True),
        tool(3, "act", n=3, s=0, quiet=True), tool(4, "nothing"),
        message(5, "tools/call", {"name": "look", "arguments": [1]}),
        message(6, "resources/list"), {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "look", "arguments": {"n": 7, "s": 0, "pad": "x" * 5000}}},
        message(8, "tools/list"),
    ])
    answers = {item["id"]: item for item in written}
    assert set(answers) == {1, 2, 3, 4, 5, 6, 8}, "a notification and an oversize line go unanswered"
    assert answers[1]["result"] == {"content": [{"type": "text", "text": "refused 1"}],
                                    "isError": True}
    assert answers[2]["error"]["code"] == -32603
    assert answers[3]["result"]["content"] == [{"type": "text", "text": "Fallback."}]
    assert answers[4]["error"]["code"] == -32603 and answers[5]["error"]["code"] == -32603
    assert answers[6]["error"]["code"] == -32601
    assert answers[8]["result"] == {"tools": TOOLS}
    _events, written, _took = run([tool(1, "nothing")], refusals_are_results=True)
    assert written[0]["result"]["isError"] is True and \
        "unknown tool: nothing" in written[0]["result"]["content"][0]["text"]
    print("PASS: refusals, failures, fallbacks, unknown tools and methods answer as before")


def pictures():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PUPPY_IMAGE_DETAIL", None)
        os.environ.pop("PUPPY_IMAGE_LIMITS", None)
        assert bridge_calls.image_block("QUJD", "image/png") == {
            "type": "image", "data": "QUJD", "mimeType": "image/png"}
        assert bridge_calls.picture_limits() is None
        os.environ["PUPPY_IMAGE_DETAIL"] = "original"
        os.environ["PUPPY_IMAGE_LIMITS"] = "6000 10240000 0"
        block = bridge_calls.content({"text": "", "image": {"data": "QUJD"}}, "x",
                                     "image/jpeg")
        assert block == [{"type": "image", "data": "QUJD", "mimeType": "image/jpeg",
                          "_meta": {"codex/imageDetail": "original"}}], block
        assert bridge_calls.picture_limits() == [6000, 10240000, 0]
        for wrong in ("6000 10240000", "a b c", ""):
            os.environ["PUPPY_IMAGE_LIMITS"] = wrong
            assert bridge_calls.picture_limits() is None, wrong
        os.environ["PUPPY_IMAGE_DETAIL"] = "sharpest"
        assert "_meta" not in bridge_calls.image_block("QUJD", "image/png")
    print("PASS: a picture asks for the detail the engine named, and the limits are read exactly")


async def real_child():
    """A real bridge child against a node socket that takes half a second
    to answer each call: three look-only calls in one message come back in
    about the time of one."""
    root = Path(tempfile.mkdtemp(prefix="puppy-bridge-"))
    path = str(root / "node.sock")
    seen = []

    async def node(reader, writer):
        request = json.loads(await reader.readline())
        seen.append(request["method"])
        await asyncio.sleep(0.5)
        writer.write((json.dumps({"ok": True, "result": {
            "text": "answered " + request["method"]}}) + "\n").encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(node, path=path)
    env = dict(os.environ, PYTHONPATH=str(BASE), PUPPY_SESSION_SOCKET=path,
               PUPPY_SESSION_SESSION_ID="1", PUPPY_SESSION_TURN_ID="t")
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "puppy.session_agent", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    try:
        started = time.monotonic()
        for number, name in ((1, "sessions"), (2, "search"), (3, "read")):
            child.stdin.write((json.dumps(tool(number, name, refs=["this"])) + "\n").encode())
        await child.stdin.drain()
        replies = [json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
                   for _ in range(3)]
        took = time.monotonic() - started
        assert sorted(reply["id"] for reply in replies) == [1, 2, 3], replies
        assert all(reply["result"]["isError"] is False for reply in replies), replies
        assert took < 1.2, "three looks took {:.2f} s, not the half second of one".format(took)
        # an action waits for nothing to answer before it, and only then runs
        child.stdin.write((json.dumps(tool(4, "send", refs=["x"], kind="question",
                                           text="hi")) + "\n").encode())
        await child.stdin.drain()
        reply = json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
        assert reply["id"] == 4 and "answered send" in reply["result"]["content"][0]["text"]
        child.stdin.close()
        assert await asyncio.wait_for(child.wait(), 10) == 0
    finally:
        if child.returncode is None:
            child.kill()
        server.close()
        await server.wait_closed()
        for item in root.iterdir():
            item.unlink()
        root.rmdir()
    assert sorted(seen) == ["read", "search", "send", "sessions"], seen
    print("PASS: a real bridge child answers three look-only calls in {:.2f} s, "
          "each taking 0.5 s at the node".format(took))


def main():
    side_by_side()
    bounded()
    answers()
    pictures()
    asyncio.run(real_child())
    print("bridge call tests passed")


if __name__ == "__main__":
    main()
