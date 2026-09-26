#!/usr/bin/env python3
"""Another model answering in place of the requested one.

The claude driver reports the model answering the main conversation only -
never a subagent's, never the CLI's <synthetic> stand-in - and carries the
CLI's own words for a switch (system/model_fallback, model_consent_fallback)
on the report of the model it switched to. The runner, against each turn's
own request: a card where a stand-in starts, another where the requested
model comes back within the turn, the live model_substitute state on session
payloads (hidden once the request or engine it belongs to has changed), a
quiet return at a later turn's start, the neutral card for an alias that now
names a newer release, and the summary ahead of the result. Then a fixture
engine speaking claude's stream-json through the real runner on both
runtimes: the state reaching the session socket and a late attach's
snapshot, the cards in transcript order with the summary just above the
result line, subagent messages on another model moving nothing, and the
next turn clearing it. No installed engine, network or model quota is used.
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

# An invented catalog: the engine's default and "main" resolve to the same
# model, "lite" and "tiny" to others. Nothing here names a real model.
CATALOG = [
    {"value": "default", "displayName": "Default", "resolvedModel": "vendor-main-1"},
    {"value": "main", "displayName": "Main 1", "resolvedModel": "vendor-main-1"},
    {"value": "lite", "displayName": "Lite 2", "resolvedModel": "vendor-lite-2"},
    {"value": "tiny", "displayName": "Tiny 3", "resolvedModel": "vendor-tiny-3"},
]
SWITCH_NOTE = "Switched to Lite 2 due to high demand for Main 1"


def fixture(scenario):
    """An engine that answers one prompt the way ``scenario`` says: the
    requested model throughout, a fallback that lasts (with a subagent on
    yet another model beside it), or a fallback that comes back."""
    def read():
        line = sys.stdin.readline()
        assert line, "stdin closed before the expected request"
        return json.loads(line)

    def send(value):
        print(json.dumps(value), flush=True)

    def say(model, text, parent=None):
        send({"type": "assistant", "parent_tool_use_id": parent, "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}]}})

    init = read()
    assert init["request"]["subtype"] == "initialize"
    send({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"],
        "response": {"models": CATALOG}}})
    read()  # the prompt
    send({"type": "system", "subtype": "init", "session_id": "native-session",
          "model": "vendor-main-1", "tools": []})
    if scenario == "steady":
        say("vendor-main-1", "All done on the requested model.")
    else:
        say("vendor-main-1", "Starting on the requested model.")
        # a subagent on its own model, inside its tool call
        say("vendor-tiny-3", "Subagent notes.", parent="toolu_agent")
        send({"type": "system", "subtype": "model_fallback", "trigger": "overloaded",
              "original_model": "vendor-main-1", "fallback_model": "vendor-lite-2",
              "content": SWITCH_NOTE, "uuid": "u-fallback", "session_id": "native-session"})
        say("vendor-lite-2", "Carrying on after the switch.")
        say("vendor-lite-2", "Still going.")
        if scenario == "resume":
            say("vendor-main-1", "Back on the requested model.")
    send({"type": "result", "subtype": "success", "is_error": False,
          "session_id": "native-session", "num_turns": 1,
          "usage": {"input_tokens": 2, "output_tokens": 1}, "result": "done"})
    while sys.stdin.readline():
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    fixture(sys.argv[2])
    raise SystemExit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("model-substitute-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from puppy import config, db, runner  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402


def line(value):
    return json.dumps(value)


def assistant(model, text="", parent=None):
    event = {"type": "assistant", "message": {
        "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}] if text else []}}
    if parent is not None:
        event["parent_tool_use_id"] = parent
    return line(event)


def reports(actions):
    return [{k: v for k, v in action.items() if k != "a"}
            for action in actions if action.get("a") == "model"]


def driver_context(driver):
    ctx = driver.turn_context({"id": 0, "model": "main"}, True, "hello", "pin")
    assert driver.parse_line(line({
        "type": "control_response", "response": {
            "subtype": "success", "request_id": "init_1",
            "response": {"models": CATALOG}}}), ctx) == []
    return ctx


def test_driver():
    driver = ClaudeDriver()
    ctx = driver_context(driver)
    init = driver.parse_line(line({"type": "system", "subtype": "init",
                                   "session_id": "native", "model": "vendor-main-1",
                                   "tools": []}), ctx)
    assert reports(init) == [{"model": "vendor-main-1"}], init
    assert any(a.get("a") == "transient" and a["msg"]["type"] == "turn_init" for a in init)
    # the same model again, in any spelling the CLI uses for it, is no report
    assert reports(driver.parse_line(assistant("vendor-main-1", "hi"), ctx)) == []
    assert reports(driver.parse_line(assistant("vendor-main-1[1m]"), ctx)) == []

    # a subagent answers on its own model inside its tool call: its text is
    # still the transcript's, but the conversation's model does not move
    sub = driver.parse_line(assistant("vendor-tiny-3", "subagent notes", "toolu_agent"), ctx)
    assert reports(sub) == [], sub
    assert [a["data"]["text"] for a in sub if a.get("a") == "event"] == ["subagent notes"]
    # an explicit null parent is the main conversation, as the CLI sends it
    assert reports(driver.parse_line(assistant("vendor-main-1", parent=None), ctx)) == []

    # the CLI answering in the model's place is no model at all
    assert reports(driver.parse_line(line({"type": "assistant", "message": {
        "model": "<synthetic>", "content": [{"type": "text", "text": "x"}]}}), ctx)) == []

    # a fallback: nothing until that model answers, then its report carries
    # the CLI's own words, once
    assert driver.parse_line(line({
        "type": "system", "subtype": "model_fallback", "trigger": "overloaded",
        "original_model": "vendor-main-1", "fallback_model": "vendor-lite-2",
        "content": SWITCH_NOTE + "\x1b[31m\x07", "uuid": "u1"}), ctx) == []
    assert reports(driver.parse_line(assistant("vendor-lite-2", "after"), ctx)) == [
        {"model": "vendor-lite-2", "note": SWITCH_NOTE}]
    assert reports(driver.parse_line(assistant("vendor-lite-2", "more"), ctx)) == []
    assert reports(driver.parse_line(assistant("vendor-main-1", "back"), ctx)) == [
        {"model": "vendor-main-1"}]

    # An explicit switch still counts when catalog equivalence would hide it
    # (for example, the engine dropping a requested context variant).
    variant_note = "Switched to Main 1 because the larger context is unavailable"
    driver.parse_line(line({"type": "system", "subtype": "model_fallback",
                            "fallback_model": "vendor-main-1", "content": variant_note}), ctx)
    assert reports(driver.parse_line(assistant("vendor-main-1"), ctx)) == [
        {"model": "vendor-main-1", "note": variant_note}]
    assert reports(driver.parse_line(assistant("vendor-main-1"), ctx)) == []

    # a switch named by an alias the catalog resolves still finds its model;
    # a report of some other model first leaves the words waiting for it
    assert driver.parse_line(line({
        "type": "system", "subtype": "model_consent_fallback", "choice": "cancelled",
        "original_model": "vendor-main-1", "fallback_model": "tiny",
        "content": "Switched to Tiny 3 for this session", "uuid": "u2"}), ctx) == []
    assert reports(driver.parse_line(assistant("vendor-lite-2"), ctx)) == [
        {"model": "vendor-lite-2"}]
    assert reports(driver.parse_line(assistant("vendor-tiny-3"), ctx)) == [
        {"model": "vendor-tiny-3", "note": "Switched to Tiny 3 for this session"}]

    # no words, no model, or no text: nothing is kept
    for bad in ({"fallback_model": "vendor-lite-2"}, {"content": "Switched"},
                {"fallback_model": 7, "content": "Switched"}):
        ctx2 = driver_context(driver)
        driver.parse_line(line({"type": "system", "subtype": "model_fallback", **bad}), ctx2)
        assert "model_switch" not in ctx2, bad
    print("PASS: the driver reports the main conversation's model alone, once per move, "
          "with the CLI's own words for a switch on the model it switched to")


class Turn:
    """Model reports straight into a hub, one prompt at a time."""

    def __init__(self, sid, model="main"):
        self.sid = sid
        self.hub = runner.hub(sid)
        self.driver = get_driver("claude")
        self.ctx = {"model_options": self.driver.model_options()}
        db.touch_session(sid, model=model)

    def start(self):
        session = db.get_session(self.sid)
        self.hub._turn_models = runner._TurnModels((session["model"] or "").strip())
        self.session = session
        self.mark = len(db.get_events(self.sid))

    def report(self, model, note=""):
        self.hub._note_effective_model(self.session, self.driver, self.ctx, model, note)

    def finish(self):
        self.hub._note_turn_models(self.session)
        return [e["data"] for e in db.get_events(self.sid)[self.mark:]]

    def live(self):
        return runner.session_payload(db.get_session(self.sid))["model_substitute"]


def test_runner():
    from puppy.drivers import claude
    driver = get_driver("claude")
    driver._model_catalog_state().ingest(claude.parse_model_catalog(CATALOG), source="turn")
    sid = db.create_session("stand-ins", "claude", str(ROOT), "main", "high", "blue", "auto")
    try:
        turn = Turn(sid)
        # the requested model answers: nothing to say, nothing standing in
        turn.start()
        turn.report("vendor-main-1")
        assert turn.live() is None and db.get_session(sid)["last_model"] == "vendor-main-1"

        # the engine switches in its own words, then again without them
        turn.report("vendor-lite-2", SWITCH_NOTE)
        assert turn.live() == {"requested": "main", "served": "vendor-lite-2",
                               "baseline": "", "note": SWITCH_NOTE}
        turn.report("vendor-lite-2")
        turn.report("vendor-tiny-3")
        assert turn.live() == {"requested": "main", "served": "vendor-tiny-3",
                               "baseline": "", "note": ""}
        rows = turn.finish()
        assert [r.get("state") for r in rows[:-1]] == ["substituted", "substituted"], rows
        assert rows[0] == {"subtype": "model_switch", "state": "substituted", "engine": "claude",
                           "requested": "main", "baseline": "", "served": "vendor-lite-2",
                           "note": SWITCH_NOTE, "text": SWITCH_NOTE}, rows[0]
        assert rows[1]["text"] == "vendor-tiny-3 is answering instead of the requested main"
        assert rows[-1] == {
            "subtype": "model_substituted", "engine": "claude", "requested": "main",
            "baseline": "", "models": ["vendor-lite-2", "vendor-tiny-3"],
            "throughout": False, "note": "",
            "text": "vendor-lite-2 and vendor-tiny-3 answered part of this prompt instead of "
                    "the requested main"}, rows[-1]
        # once only, whatever asks again
        turn.hub._note_turn_models(turn.session)
        assert len(db.get_events(sid)) == turn.mark + len(rows)

        # it stays said between turns, for as long as that model is the one
        # last reported - and a request changed while idle is not claimed
        assert turn.live()["served"] == "vendor-tiny-3"
        db.touch_session(sid, model="lite")
        assert turn.live() is None
        db.touch_session(sid, model="main")
        assert turn.live()["served"] == "vendor-tiny-3"

        # the next prompt starts back on the requested model: expected, so quiet
        turn.start()
        turn.report("vendor-main-1")
        assert turn.finish() == [] and turn.live() is None

        # a stand-in that gives way within the turn: both cards, and a
        # summary that says it answered only part of the prompt
        turn.start()
        turn.report("vendor-main-1")
        turn.report("vendor-lite-2", SWITCH_NOTE)
        turn.report("vendor-main-1")
        rows = turn.finish()
        assert [r.get("state") for r in rows[:-1]] == ["substituted", "resumed"], rows
        assert rows[1]["text"] == "vendor-main-1 is answering again"
        assert rows[-1]["throughout"] is False and rows[-1]["note"] == ""
        assert rows[-1]["text"] == ("vendor-lite-2 answered part of this prompt instead of "
                                    "the requested main"), rows[-1]
        assert turn.live() is None

        # an alias that now names a newer release still serves the request:
        # the neutral card, once, and never a stand-in
        db.touch_session(sid, last_model="vendor-main-0")
        turn.session = db.get_session(sid)
        turn.start()
        turn.report("vendor-main-1")
        rows = turn.finish()
        assert rows == [{"subtype": "model_switch", "state": "changed", "engine": "claude",
                         "from_model": "vendor-main-0", "to_model": "vendor-main-1",
                         "text": "engine model changed: vendor-main-0 → vendor-main-1"}], rows

        # the engine's own word that it switched makes a stand-in even of a
        # model that would otherwise pass for the request
        turn.start()
        turn.report("vendor-main-1[1m]", "Switched to Main 1 because Main 1 [1m] is not available")
        rows = turn.finish()
        assert rows[0]["state"] == "substituted" and rows[-1]["subtype"] == "model_substituted"
        assert turn.live() is not None
        assert rows[-1]["throughout"] is True, rows[-1]

        # a request changed for the next turn: the divider speaks, the old
        # stand-in is gone before that turn's engine has said anything
        turn.hub._note_turn_config(db.get_session(sid))   # the turns so far asked for main
        assert turn.hub._model_substitute is not None
        db.touch_session(sid, model="lite")
        turn.hub._note_turn_config(db.get_session(sid))
        assert db.get_events(sid)[-1]["data"]["subtype"] == "config_change"
        assert db.get_session(sid)["last_model"] == "" and turn.hub._model_substitute is None
        db.touch_session(sid, model="main")
        turn.hub._note_turn_config(db.get_session(sid))

        # the engine's default: the model this turn reported first is what it
        # asked for, and another one standing in is said against it
        db.touch_session(sid, model="", last_model="")
        turn.start()
        turn.report("vendor-main-1")
        turn.report("vendor-lite-2")
        rows = turn.finish()
        assert rows[0]["text"] == ("vendor-lite-2 is answering instead of the engine's "
                                   "default, vendor-main-1"), rows[0]
        assert rows[0]["baseline"] == "vendor-main-1" and rows[0]["requested"] == ""
        assert rows[-1]["text"] == ("vendor-lite-2 answered part of this prompt instead of the "
                                    "engine's default, vendor-main-1"), rows[-1]
        assert turn.live() == {"requested": "", "served": "vendor-lite-2",
                               "baseline": "vendor-main-1", "note": ""}

        # an engine switch leaves nothing standing in
        target = get_driver("codex")
        assert turn.hub._apply_engine_switch({
            "engine": "codex", "model": target.default_model(), "effort": "",
            "permission_mode": target.default_permission(), "fast_mode": "off"}) is True
        assert turn.hub._model_substitute is None and turn.live() is None
    finally:
        runner.drop_hub(sid)
        db.delete_session(sid)
    print("PASS: the runner names each stand-in against its own turn's request, the "
          "requested model's return, a quiet next turn, an alias moving, the engine "
          "default, and the summary; the live state follows the request and engine")


async def frame(ws, kind, timeout=15):
    while True:
        value = await ws.receive_json(timeout=timeout)
        if value["type"] == kind:
            return value


async def fixture_turn(client, headers, sid, scenario):
    """One prompt through the real runner with the fixture engine; returns
    the session_meta frames it published and that turn's transcript rows."""
    driver = get_driver("claude")
    hub = runner.hub(sid)

    def command(*args, **kwargs):
        return [sys.executable, str(Path(__file__).resolve()), "--fixture", scenario]

    metas = []
    mark = len(db.get_events(sid))
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        stack.enter_context(patch.object(runner, "STEERING_ACK_GRACE", .2))
        stack.enter_context(patch.object(runner, "SIDE_QUESTION_GRACE", .2))
        ws = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
        try:
            await frame(ws, "snapshot")
            assert hub.send_message("go") == {"queued": False}
            done = False
            deadline = time.monotonic() + 20
            while not done:
                assert time.monotonic() < deadline, "the fixture turn did not end"
                value = await ws.receive_json(timeout=15)
                if value["type"] == "session_meta":
                    metas.append(value["session"].get("model_substitute"))
                done = value["type"] == "turn_done"
            if hub.turn_task:
                await hub.turn_task
        finally:
            await ws.close()
    return metas, [e for e in db.get_events(sid)[mark:]]


async def runtime_contract(factory):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    async with TestClient(TestServer(app)) as client:
        sid = db.create_session("Stand-in fixture", "claude", str(ROOT), "main", "high", "", "default")
        try:
            metas, rows = await fixture_turn(client, headers, sid, "fallback")
            live = {"requested": "main", "served": "vendor-lite-2", "baseline": "",
                    "note": SWITCH_NOTE}
            assert live in metas and metas[-1] == live, metas
            infos = [e["data"] for e in rows if e["kind"] == "info"]
            assert [(d["subtype"], d.get("state")) for d in infos] == [
                ("model_switch", "substituted"), ("model_substituted", None)], infos
            assert infos[0]["note"] == SWITCH_NOTE and infos[0]["served"] == "vendor-lite-2"
            kinds = [e["kind"] for e in rows]
            assert kinds[-2:] == ["info", "result"], kinds
            assert rows[-2]["data"]["subtype"] == "model_substituted"
            assert rows[-2]["data"]["text"] == ("vendor-lite-2 answered part of this prompt instead "
                                                "of the requested main")
            # the subagent's words are in the transcript; its model moved nothing
            texts = [e["data"]["text"] for e in rows if e["kind"] == "assistant"]
            assert "Subagent notes." in texts, texts
            assert db.get_session(sid)["last_model"] == "vendor-lite-2"

            # a console attaching now is told the same
            late = await client.ws_connect("/api/ws/session/{}".format(sid), headers=headers)
            snapshot = await frame(late, "snapshot")
            assert snapshot["session"]["model_substitute"] == live
            await late.close()

            # the next prompt on the requested model clears it, quietly
            metas, rows = await fixture_turn(client, headers, sid, "steady")
            assert metas and metas[-1] is None, metas
            assert not [e for e in rows if e["kind"] == "info"], rows
            assert db.get_session(sid)["last_model"] == "vendor-main-1"

            # a switch that gives way before the end
            metas, rows = await fixture_turn(client, headers, sid, "resume")
            assert live in metas and metas[-1] is None, metas
            infos = [e["data"] for e in rows if e["kind"] == "info"]
            assert [d.get("state") for d in infos] == ["substituted", "resumed", None], infos
            assert infos[-1]["throughout"] is False
            assert [e["kind"] for e in rows][-2:] == ["info", "result"]
        finally:
            runner.drop_hub(sid)
            db.delete_session(sid)
    print("PASS: " + factory.__module__ + " publishes the stand-in live and to a late attach, "
          "cards it where it happened and sums it up just above the result")


async def main():
    config.load()
    config.set_value("auth.api_token", "model-substitute-test-token-0123456789")
    db.connect()
    test_driver()
    test_runner()
    await runtime_contract(build_app)
    await runtime_contract(backend_app)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
