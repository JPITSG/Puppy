#!/usr/bin/env python3
"""The Codex driver's turn-start grace over the CLI's own retries.

The notification bodies below are the ones codex-cli 0.154.0 really sends
while its response stream falls back from WebSockets to HTTPS. No engine runs,
no network, no quota.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("codex-turn-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
os.environ["CODEX_HOME"] = str(ROOT / "codex")

from puppy.drivers.codex import START_GRACE_SECONDS, CodexDriver  # noqa: E402

THREAD = "01a089cc-bbe5-7e30-a708-c8e0f7f6c8bf"
TURN = "01a089cf-2942-7780-88ff-f4c219038af2"

# One real "the stream dropped, I am trying again myself" notification.
RETRY = {"method": "error", "params": {
    "error": {
        "message": "Reconnecting... 2/5",
        "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 503}},
        "additionalDetails": "unexpected status 503 Service Unavailable: "
                             "upstream connect error or disconnect/reset before "
                             "headers. reset reason: connection termination, url: "
                             "wss://chatgpt.com/backend-api/codex/responses",
        "misalignment": None,
    },
    "willRetry": True, "threadId": THREAD, "turnId": TURN,
}, "emittedAtMs": 1789018451437}

# The same channel when nothing more will be tried.
FATAL = {"method": "error", "params": {
    "error": {"message": "You've hit your usage limit."},
    "threadId": THREAD, "turnId": TURN,
}}


def turn_context(driver, age=0.0):
    """One turn's live context, optionally started `age` seconds ago."""
    session = {"id": 1, "engine": "codex", "cwd": str(ROOT), "model": "",
               "effort": "", "permission_mode": "read-only",
               "native_session_id": THREAD}
    ctx = driver.turn_context(session, False, "hello", "pinned-1")
    ctx.update(thread_id=THREAD, turn_id=TURN, phase="running")
    if age:
        ctx["started_at"] = ctx["started_at"] - age
    return ctx


def status_of(actions):
    assert len(actions) == 1, actions
    action = actions[0]
    assert action["a"] == "transient", action
    assert action["msg"]["type"] == "status", action
    return action["msg"]["text"]


def checks():
    driver = CodexDriver()

    # The runner builds the context immediately before spawning the process,
    # so the stamp dates the turn itself.
    fresh = turn_context(driver)
    assert isinstance(fresh["started_at"], float)
    assert abs(time.monotonic() - fresh["started_at"]) < 5

    # Inside the window the countdown never reaches the person who pressed
    # Send: they keep reading the word the console already showed.
    text = status_of(driver.parse_line(json.dumps(RETRY), fresh))
    assert text == "Starting…", text
    assert "Reconnecting" not in text, text
    # Masking is a status choice only - nothing is dropped from the record and
    # the turn is not treated as failing.
    assert not fresh.get("last_error")

    # A retry still going on after the window is worth reading, exactly as the
    # engine worded it.
    for age in (START_GRACE_SECONDS, START_GRACE_SECONDS + 30):
        late = turn_context(driver, age=age)
        assert status_of(driver.parse_line(json.dumps(RETRY), late)) == \
            "Retrying: Reconnecting... 2/5"

    # A context with no stamp (a caller that builds its own) is not in the
    # window: the engine's wording is the safe answer.
    unstamped = turn_context(driver)
    unstamped.pop("started_at")
    assert status_of(driver.parse_line(json.dumps(RETRY), unstamped)) == \
        "Retrying: Reconnecting... 2/5"

    # A failure the CLI will not retry is still an error row, inside the
    # window as much as outside it.
    for ctx in (turn_context(driver), turn_context(driver, age=START_GRACE_SECONDS + 1)):
        actions = driver.parse_line(json.dumps(FATAL), ctx)
        assert actions == [{"a": "event", "kind": "error", "data": {
            "text": "You've hit your usage limit."}}], actions
        assert ctx["last_error"] == "You've hit your usage limit."

    print("PASS: codex turn-start grace masks the CLI's own retries, keeps "
          "later ones and every real failure")


try:
    checks()
finally:
    shutil.rmtree(ROOT, ignore_errors=True)
