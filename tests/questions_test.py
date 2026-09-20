#!/usr/bin/env python3
"""Questions an engine asks the person (Claude Code's AskUserQuestion).

The reader that turns the tool's input into the rows a card draws - the exact
CLI shape, renamed keys, string options, a single question, unknown kinds,
bounds - and the answer map it hands back keyed by the question's own text,
the claude driver marking such a request and carrying the answers in its
allow, then a fixture engine speaking claude's stream-json through the real
runner on both runtimes: the request reaching the session socket and the
attach snapshot as a question, the answers, a skip and an unreadable reply
each landing in the engine's own control_response. No installed engine,
network or model quota is used.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch

FIXTURE_INPUT = {
    "title": "Release",
    "questions": [
        {"question": "The build is ready. Deploy now?", "header": "Deploy",
         "options": [
             {"label": "Deploy now (Recommended)", "description": "Restart both apps"},
             {"label": "Wait", "description": "Keep the running build"}],
         "multiSelect": False},
        {"question": "Which signals should I watch?", "header": "Signals",
         "options": [{"label": "Logs"}, {"label": "Metrics"}, {"label": "Traces, spans"}],
         "multiSelect": True},
    ],
}


def fixture():
    """An engine that asks one question through can_use_tool, echoes the
    host's reply into its tool result and ends the turn."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"], "response": {}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-session", "tools": []})
    send({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_ask", "name": "AskUserQuestion",
         "input": FIXTURE_INPUT}]}})
    send({"type": "control_request", "request_id": "perm_ask", "request": {
        "subtype": "can_use_tool", "tool_name": "AskUserQuestion",
        "input": FIXTURE_INPUT, "tool_use_id": "toolu_ask",
        "description": "Answer questions?", "permission_suggestions": []}})
    reply = read()
    assert reply["type"] == "control_response", reply
    answer = reply["response"]["response"]
    send({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_ask",
         "content": "host said " + json.dumps(answer, sort_keys=True)}]}})
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-session", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture()
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("questions-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import config, db, questions, runner  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402


def test_reader():
    rows, title = questions.questions_from("AskUserQuestion", FIXTURE_INPUT)
    assert title == "Release"
    assert [row["header"] for row in rows] == ["Deploy", "Signals"]
    assert [row["kind"] for row in rows] == ["choice", "choice"]
    assert [row["multi"] for row in rows] == [False, True]
    assert rows[0]["key"] == "The build is ready. Deploy now?"
    assert rows[0]["options"] == [
        {"label": "Deploy now (Recommended)", "description": "Restart both apps"},
        {"label": "Wait", "description": "Keep the running build"}]
    assert rows[1]["options"][2] == {"label": "Traces, spans"}
    assert [row["index"] for row in rows] == [0, 1]

    # renamed keys, string options, an unknown kind, a number
    loose = {"questions": [
        {"text": "  Colour?  ", "title": "Hue", "choices": ["Red", "Blue", "Red", "", 7],
         "multi_select": "true", "type": "Select"},
        {"prompt": "Name", "kind": "freeform", "placeholder": "e.g. Mira"},
        {"question": "How many?", "kind": "INTEGER", "minimum": "1", "max": 10,
         "defaultValue": 3, "unit": "cores"},
        {"question": "Nothing to pick", "options": []},
        "Just a string",
        {"options": [{"label": "no question text"}]},
        None,
    ]}
    rows, title = questions.questions_from("mcp__host__ask_user_question", loose)
    assert title == ""
    assert rows[0]["key"] == "  Colour?  " and rows[0]["question"] == "Colour?"
    assert rows[0]["header"] == "Hue" and rows[0]["multi"] is True
    assert rows[0]["options"] == [{"label": "Red"}, {"label": "Blue"}, {"label": "7"}]
    assert rows[1] == {"index": 1, "key": "Name", "question": "Name", "header": "",
                       "description": "", "kind": "text", "multi": False,
                       "options": [], "placeholder": "e.g. Mira"}
    assert rows[2]["kind"] == "number" and rows[2]["min"] == 1 and rows[2]["max"] == 10
    assert rows[2]["default"] == 3 and rows[2]["unit"] == "cores"
    assert rows[3]["kind"] == "text", "a choice without options is a text box"
    assert rows[4]["key"] == "Just a string" and rows[4]["kind"] == "text"
    assert len(rows) == 5, "an entry without a question, and a non-entry, are left out"
    assert [row["index"] for row in rows] == [0, 1, 2, 3, 4]

    # a single question, in a dict or as the input itself, under the tool's name
    single = questions.questions_from("AskUserQuestion", {"question": {"question": "Go?", "options": ["Yes", "No"]}})
    assert single[0][0]["key"] == "Go?" and single[0][0]["options"] == [{"label": "Yes"}, {"label": "No"}]
    flat = questions.questions_from("ask-user-question", {"question": "Go?", "options": ["Yes"]})
    assert flat[0][0]["key"] == "Go?" and flat[0][0]["kind"] == "choice"
    # a questions list is a question whatever the tool is called
    assert questions.questions_from("Survey", {"questions": [{"question": "Q", "options": ["A", "B"]}]})[0][0]["key"] == "Q"
    # but the looser spellings are read only under the tool's own name
    for name, value in (("TodoWrite", {"items": [{"title": "buy milk"}]}),
                        ("Bash", {"command": "ls"}), ("AskUserQuestion", {}),
                        ("AskUserQuestion", {"questions": []}),
                        ("AskUserQuestion", {"questions": [None, 3, {"header": "x"}]}),
                        ("AskUserQuestion", "questions"), ("AskUserQuestion", None)):
        assert questions.questions_from(name, value) is None, (name, value)
    assert questions.is_question_tool("mcp__ide__AskUserQuestion")
    assert questions.is_question_tool("user_question") and not questions.is_question_tool("Read")

    # bounds: the count, the options, the text
    many = {"questions": [{"question": "Q{}".format(i), "options": ["A", "B"]} for i in range(20)]}
    assert len(questions.questions_from("AskUserQuestion", many)[0]) == questions.QUESTION_LIMIT
    wide = {"questions": [{"question": "Q", "options": ["O{}".format(i) for i in range(40)]}]}
    assert len(questions.questions_from("AskUserQuestion", wide)[0][0]["options"]) == questions.OPTION_LIMIT
    long = {"questions": [{"question": "x" * 9000, "options": ["y" * 900]}]}
    row = questions.questions_from("AskUserQuestion", long)[0][0]
    assert len(row["question"]) == questions.TEXT_LIMIT and len(row["key"]) == 9000
    assert len(row["options"][0]["label"]) == questions.LABEL_LIMIT
    print("PASS: the reader on the CLI's shape, renamed keys, single questions, refusals and bounds")


def test_answers():
    rows, _ = questions.questions_from("AskUserQuestion", FIXTURE_INPUT)
    key0, key1 = rows[0]["key"], rows[1]["key"]
    assert questions.answer_map(rows, {"0": "Wait"}) == {key0: "Wait"}
    assert questions.answer_map(rows, {0: "Wait", 1: ["Logs", "Metrics"]}) == \
        {key0: "Wait", key1: "Logs, Metrics"}
    assert questions.answer_map(rows, {"1": ["Traces, spans", "Logs", "Logs"]}) == \
        {key1: '"Traces, spans", Logs'}, "a label holding the separator is quoted, a repeat dropped"
    assert questions.answer_map(rows, {"1": ["He said \"go\""]}) == {key1: '"He said \\"go\\""'}
    assert questions.answer_map(rows, {"0": "ship it, but slowly "}) == {key0: "ship it, but slowly"}
    assert questions.answer_map(rows, {"0": ["Deploy now (Recommended)", "Wait"]}) == \
        {key0: "Deploy now (Recommended)"}, "a list on a single choice takes the first"
    assert questions.answer_map(rows, {"0": 4, "1": [2.5, True]}) == {key0: "4", key1: "2.5"}
    assert questions.answer_map(rows, {"9": "Wait", "x": "Wait", "0": "", "1": [], "-1": "Wait"}) == {}
    assert questions.answer_map(rows, {"0": None, "1": [None, "", "   "]}) == {}
    assert questions.answer_map(rows, {"0": questions.SKIPPED}) == {}
    assert questions.answer_map(rows, {"0": True, "1": {"label": "Logs"}}) == {}
    assert questions.answer_map(rows, ["Wait"]) == {} and questions.answer_map(rows, None) == {}
    assert questions.answer_map(None, {"0": "Wait"}) == {} and questions.answer_map([{"index": "0", "key": "k"}], {"0": "Wait"}) == {}
    assert questions.answer_map(rows, {"0": "x" * 9000})[key0] == "x" * questions.ANSWER_LIMIT
    assert len(questions.answer_map(rows, {"1": ["L{}".format(i) for i in range(200)]})[key1].split(", ")) == questions.ANSWER_ITEMS
    assert questions.join_labels(["a", "b, c", 'd"e']) == 'a, "b, c", "d\\"e"'
    reply = questions.reply_input(FIXTURE_INPUT, rows, {"0": "Wait"})
    assert reply["questions"] is FIXTURE_INPUT["questions"] and reply["title"] == "Release"
    assert reply["answers"] == {key0: "Wait"} and "answers" not in FIXTURE_INPUT
    assert questions.reply_input(None, rows, {}) == {"answers": {}}
    assert questions.describe(rows, {"0": "Wait", "1": ["Logs"]}) == "Deploy: Wait; Signals: Logs"
    assert questions.describe(rows, {}) == "no answer"
    print("PASS: the answer map keyed by the question's own text, the CLI's join, refusals and bounds")


def test_driver():
    driver = get_driver("claude")
    ctx = {}
    line = json.dumps({"type": "control_request", "request_id": "perm_1", "request": {
        "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": FIXTURE_INPUT,
        "tool_use_id": "toolu_1", "description": "Answer questions?"}})
    (action,) = driver.parse_line(line, ctx)
    req = action["req"]
    assert action["a"] == "approval" and req["kind"] == "question"
    assert req["question_title"] == "Release" and req["tool_name"] == "AskUserQuestion"
    assert [row["header"] for row in req["questions"]] == ["Deploy", "Signals"]
    assert req["input"] is not None and req["request_id"] == "perm_1"
    plain = json.dumps({"type": "control_request", "request_id": "perm_2", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}}})
    (action,) = driver.parse_line(plain, ctx)
    assert "kind" not in action["req"] and "questions" not in action["req"]

    key0, key1 = FIXTURE_INPUT["questions"][0]["question"], FIXTURE_INPUT["questions"][1]["question"]
    payload = driver.approval_payload("perm_1", "allow", FIXTURE_INPUT, request=req,
                                      answers={"0": "Wait", "1": ["Logs", "Metrics"]})
    response = payload["response"]["response"]
    assert payload["type"] == "control_response" and payload["response"]["request_id"] == "perm_1"
    assert response["behavior"] == "allow"
    assert response["updatedInput"]["answers"] == {key0: "Wait", key1: "Logs, Metrics"}
    assert response["updatedInput"]["questions"] == FIXTURE_INPUT["questions"]
    assert "answers" not in FIXTURE_INPUT, "the engine's own input is never written to"
    # a skip is an explicit empty map; no answers at all is the plain allow
    assert driver.approval_payload("perm_1", "allow", FIXTURE_INPUT, request=req,
                                   answers={})["response"]["response"]["updatedInput"]["answers"] == {}
    assert driver.approval_payload("perm_1", "allow", FIXTURE_INPUT, request=req)[
        "response"]["response"]["updatedInput"] == FIXTURE_INPUT
    # answers mean nothing to a request that asked none, and nothing to a deny
    assert driver.approval_payload("perm_2", "allow", {"command": "ls"}, request={"request_id": "perm_2"},
                                   answers={"0": "Wait"})["response"]["response"]["updatedInput"] == {"command": "ls"}
    denied = driver.approval_payload("perm_1", "deny", FIXTURE_INPUT, request=req,
                                     answers={"0": "Wait"}, message="no")
    assert denied["response"]["response"] == {"behavior": "deny", "message": "no"}
    for engine in ("codex", "opencode"):
        other = get_driver(engine)
        assert other.approval_payload("r", "deny", {}, request={"request_id": "r", "_rpc_id": 1},
                                      answers={"0": "x"}) is not None, engine
    print("PASS: the claude driver marks the request and carries the answers; the others ignore them")


async def frame(ws, kind, timeout=15):
    while True:
        value = await ws.receive_json(timeout=timeout)
        if value["type"] == kind:
            return value


async def question_turn(client, headers, sid, reply):
    """One prompt through the real runner: the fixture asks, the socket
    answers with ``reply``, and the fixture's tool result says what the
    engine was handed."""
    driver = get_driver("claude")
    hub = runner.hub(sid)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), "--fixture"]

    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
        try:
            await frame(ws, "snapshot")
            assert hub.send_message("ask me") == {"queued": False}
            request = await frame(ws, "approval_request")
            req = request["req"]
            assert req["kind"] == "question" and req["tool_name"] == "AskUserQuestion"
            assert req["question_title"] == "Release"
            assert [row["header"] for row in req["questions"]] == ["Deploy", "Signals"]
            assert req["questions"][1]["multi"] is True
            # a console attaching now sees the same question
            late = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
            snapshot = await frame(late, "snapshot")
            assert snapshot["pending_approval"]["kind"] == "question"
            assert snapshot["pending_approval"]["questions"][0]["key"] == \
                FIXTURE_INPUT["questions"][0]["question"]
            await late.close()
            await ws.send_json({"type": "approval_response", "request_id": req["request_id"],
                                "behavior": "allow", **reply})
            resolved = await frame(ws, "approval_resolved")
            assert resolved["request_id"] == req["request_id"] and resolved["behavior"] == "allow"
            deadline = time.monotonic() + 20
            while hub.status != "idle":
                assert time.monotonic() < deadline, "the fixture turn did not end"
                await asyncio.sleep(.02)
            await hub.turn_task
        finally:
            await ws.close()
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await asyncio.gather(hub.turn_task, return_exceptions=True)
    events = db.get_events(sid)
    assert any(e["kind"] == "result" and e["data"].get("ok") for e in events), events
    asked = [e for e in events if e["kind"] == "tool_use" and e["data"]["tool"] == "AskUserQuestion"]
    assert asked and asked[-1]["data"]["input"] == FIXTURE_INPUT
    told = [e for e in events if e["kind"] == "tool_result" and e["data"]["tool_use_id"] == "toolu_ask"]
    content = told[-1]["data"]["content"]
    assert content.startswith("host said "), content
    return json.loads(content[len("host said "):])


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    key0, key1 = FIXTURE_INPUT["questions"][0]["question"], FIXTURE_INPUT["questions"][1]["question"]
    async with TestClient(TestServer(app)) as client:
        sid = db.create_session("Question fixture", "claude", str(ROOT), "", "", "", "default")
        try:
            handed = await question_turn(client, headers, sid, {
                "answers": {"0": "Deploy now (Recommended)", "1": ["Metrics", "Traces, spans", "ship carefully"]}})
            assert handed["behavior"] == "allow"
            assert handed["updatedInput"]["answers"] == {
                key0: "Deploy now (Recommended)", key1: 'Metrics, "Traces, spans", ship carefully'}
            assert handed["updatedInput"]["questions"] == FIXTURE_INPUT["questions"]
            assert handed["updatedInput"]["title"] == "Release"
            # Skip: an explicit empty map, the CLI's "did not answer"
            handed = await question_turn(client, headers, sid, {"answers": {}})
            assert handed["updatedInput"]["answers"] == {}
            # an unreadable reply is the plain allow the engine always took
            handed = await question_turn(client, headers, sid, {"answers": ["Wait"]})
            assert handed["updatedInput"] == FIXTURE_INPUT
            assert "answers" not in handed["updatedInput"]
        finally:
            runner.drop_hub(sid)
    print("PASS: " + factory.__module__ + " asks on the session socket and hands the answers, a skip and an unreadable reply to the engine")


async def main():
    config.load()
    config.set_value("auth.api_token", "question-test-token-0123456789abcdef")
    db.connect()
    test_reader()
    test_answers()
    test_driver()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
