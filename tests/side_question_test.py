#!/usr/bin/env python3
"""Puppy side-question (claude /btw) end-to-end test.

Boots a real server on a test port with an isolated data dir, starts a live
claude turn, and asks questions beside it over the real HTTP route and session
socket. Verifies the properties the feature is built on, all probed against
claude 2.1.258 first:

  * a question is answered while the turn keeps running, and the turn's own
    answer is unaffected by it
  * the exchange never enters the engine's conversation
  * a follow-up is threaded (the model resolves "it" from the first answer)
  * the control is refused when there is no active turn

NOTE: spends a small amount of real subscription quota (one haiku turn plus a
few side questions). Run deliberately: python3 tests/side_question_test.py
"""
import asyncio
import copy
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import aiohttp

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("PUPPY_TEST_PORT", "10997"))
URL = f"http://127.0.0.1:{PORT}"

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" {extra}"))
    sys.stdout.flush()


class Socket:
    """Session websocket that keeps every frame it has seen."""

    def __init__(self, ws):
        self.ws = ws
        self.frames = []

    async def drain(self, until=None, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await self.ws.receive(
                    timeout=min(10, max(1, deadline - time.time())))
            except asyncio.TimeoutError:
                if until is None:
                    return None
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    return None
                continue
            d = json.loads(msg.data)
            self.frames.append(d)
            if until and until(d):
                return d
        return None

    def events(self, kind):
        return [f["event"]["data"] for f in self.frames
                if f.get("type") == "event" and f["event"]["kind"] == kind]

    def latest(self, frame_type):
        found = [f for f in self.frames if f.get("type") == frame_type]
        return found[-1] if found else None


async def main():
    data_dir = tempfile.mkdtemp(prefix="puppy_sq_data_")
    work = tempfile.mkdtemp(prefix="puppy_sq_ws_")
    env = dict(os.environ)
    env["PUPPY_DATA"] = data_dir
    for k in list(env):
        if k.startswith("CLAUDE_") or k == "CLAUDECODE":
            env.pop(k)
    # the config file must match the current shape exactly - no partial file
    sys.path.insert(0, BASE)
    os.environ["PUPPY_DATA"] = data_dir
    from puppy import config as puppy_config
    cfg = copy.deepcopy(puppy_config.DEFAULTS)
    cfg["web"] = dict(cfg["web"], host="127.0.0.1", port=PORT)
    cfg["auth"] = dict(cfg["auth"], api_token=secrets.token_urlsafe(32))
    with open(os.path.join(data_dir, "config.json"), "w") as f:
        json.dump(cfg, f)
    with open(os.path.join(work, "ledger.txt"), "w") as f:
        f.write("alpha\nbravo\ncharlie\n")

    # a leftover server on the test port would answer every request and look
    # like a bewildering series of failures; refuse to start instead
    probe = socket.socket()
    try:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"port {PORT} is already in use - stop the stale test server first")
            sys.exit(2)
    finally:
        probe.close()

    server = subprocess.Popen([sys.executable, "-m", "puppy"], cwd=BASE, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)
    print(f"server pid={server.pid} data={data_dir}")
    try:
        async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            up = False
            for _ in range(60):
                if server.poll() is not None:
                    break
                try:
                    async with http.get(URL + "/api/auth/status") as r:
                        up = r.status == 200
                        if up:
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            check("server boots", up)
            if not up:
                print((server.stdout.read() if server.poll() is not None
                       else "(unreachable)")[-3000:])
                return
            async with http.post(URL + "/api/auth/setup",
                                 json={"username": "tester",
                                       "password": "secret123"}) as r:
                check("admin setup", r.status == 200, str(r.status))
            async with http.get(URL + "/api/ping") as r:
                ping = await r.json()
            check("node advertises the capability",
                  "active-turn-side-question" in (ping.get("capabilities") or []),
                  str(ping.get("capabilities"))[:200])

            async with http.post(URL + "/api/sessions", json={
                    "engine": "claude", "cwd": work, "model": "haiku",
                    "permission_mode": "acceptEdits"}) as r:
                d = await r.json()
                check("session created", r.status == 200, str(d))
                sid = d["session"]["id"]

            # ---- refused with no active turn -------------------------------
            async with http.post(f"{URL}/api/sessions/{sid}/ask", json={
                    "question": "anything", "expected_turn_id": "nope"}) as r:
                body = await r.json()
                check("refused while idle", r.status == 409 and "error" in body,
                      f"{r.status} {body}")

            async with http.ws_connect(f"{URL}/api/ws/session/{sid}") as raw:
                sock = Socket(raw)
                await sock.drain(until=lambda d: d.get("type") == "snapshot", timeout=30)
                snap = sock.latest("snapshot")
                check("snapshot carries readiness",
                      isinstance((snap or {}).get("side_question"), dict) and
                      (snap or {})["side_question"]["supported"] is True,
                      str((snap or {}).get("side_question")))

                # ---- a real turn, deliberately slow enough to ask during ----
                async with http.post(f"{URL}/api/sessions/{sid}/message", json={
                        "text": "Work through this one step at a time. First "
                                "read ledger.txt. Then, one at a time, create "
                                "note1.txt through note6.txt, each containing "
                                "only its own number, and read each file back "
                                "with a separate Read immediately after writing "
                                "it. Finally write report.txt containing the "
                                "word REPORT and reply with exactly: DONE"}) as r:
                    check("turn started", r.status == 200, str(r.status))

                ready = await sock.drain(
                    until=lambda d: d.get("type") == "side_question_state" and
                    d.get("side_question", {}).get("ready") is True, timeout=120)
                check("becomes ready during the turn", ready is not None)
                turn_id = (ready or {}).get("side_question", {}).get("turn_id", "")

                # ---- question one, once the turn has really read the file --
                read = await sock.drain(
                    until=lambda d: d.get("type") == "event" and
                    d["event"]["kind"] == "tool_result" and
                    "alpha" in str(d["event"]["data"].get("content") or ""),
                    timeout=180)
                check("the turn read the file before we ask", read is not None)
                asked_at = time.time()
                async with http.post(f"{URL}/api/sessions/{sid}/ask", json={
                        "question": "Name the single file you have read from "
                                    "this working directory, then append the "
                                    "word ZEBRA. Reply with nothing else.",
                        "request_id": "ask-one",
                        "expected_turn_id": turn_id}) as r:
                    body = await r.json()
                    check("question accepted", r.status == 200 and body.get("ok"),
                          f"{r.status} {body}")

                first = await sock.drain(
                    until=lambda d: d.get("type") == "event" and
                    d["event"]["kind"] == "side_question_result", timeout=180)
                answer = (first or {}).get("event", {}).get("data", {})
                check("answered", bool(answer.get("ok")) and bool(answer.get("text")),
                      str(answer)[:300])
                check("answered while the turn was still running",
                      not any(f.get("type") == "turn_done" for f in sock.frames),
                      "the turn finished first - inconclusive, not a failure")
                print(f"    answer 1 ({time.time() - asked_at:.1f}s): "
                      f"{str(answer.get('text'))[:160]}")
                check("names the file it read",
                      "ledger" in str(answer.get("text", "")).lower(),
                      str(answer.get("text"))[:200])

                # ---- follow-up: resolved only from the thread ---------------
                again = await sock.drain(
                    until=lambda d: d.get("type") == "side_question_state" and
                    d.get("side_question", {}).get("ready") is True, timeout=120)
                check("ready again once the answer landed", again is not None)
                async with http.post(f"{URL}/api/sessions/{sid}/ask", json={
                        "question": "What was the last word of your previous "
                                    "answer? Reply with that word only.",
                        "request_id": "ask-two",
                        "expected_turn_id": turn_id}) as r:
                    body = await r.json()
                    check("follow-up accepted", r.status == 200 and body.get("ok"),
                          f"{r.status} {body}")
                second = await sock.drain(
                    until=lambda d: d.get("type") == "event" and
                    d["event"]["kind"] == "side_question_result" and
                    d["event"]["data"].get("request_id") == "ask-two", timeout=180)
                follow = (second or {}).get("event", {}).get("data", {})
                check("follow-up answered", bool(follow.get("ok")), str(follow)[:300])
                print(f"    answer 2: {str(follow.get('text'))[:160]}")
                check("follow-up answered from the thread, not the turn",
                      "ZEBRA" in str(follow.get("text", "")).upper(),
                      str(follow.get("text"))[:200])

                # ---- the turn itself is untouched ---------------------------
                done = await sock.drain(
                    until=lambda d: d.get("type") == "turn_done", timeout=600)
                check("turn completed", done is not None)
                assistant = " ".join(sock.events("assistant") and
                                     [str(e.get("text") or "")
                                      for e in sock.events("assistant")])
                check("the turn did its own work, not the side questions",
                      "DONE" in assistant, assistant[-200:])
                check("the turn wrote its file",
                      os.path.isfile(os.path.join(work, "report.txt")))
                results = [e for e in sock.events("result")]
                check("exactly one turn result", len(results) == 1, str(len(results)))

            # ---- the engine never saw the exchange -------------------------
            async with http.post(f"{URL}/api/sessions/{sid}/message", json={
                    "text": "Reply with exactly YES if the word ZEBRA has appeared "
                            "anywhere in this conversation before now, otherwise "
                            "exactly NO."}) as r:
                check("second turn started", r.status == 200, str(r.status))
            async with http.ws_connect(f"{URL}/api/ws/session/{sid}") as raw:
                sock2 = Socket(raw)
                await sock2.drain(until=lambda d: d.get("type") == "turn_done",
                                  timeout=600)
                said = " ".join(str(e.get("text") or "")
                                for e in sock2.events("assistant"))
                print(f"    engine recall: {said[:160]}")
                check("the engine's conversation never saw the side questions",
                      "NO" in said.upper() and "YES" not in said.upper(),
                      said[:200])

            # ---- both exchanges persisted for a reload ---------------------
            async with http.get(f"{URL}/api/sessions/{sid}") as r:
                events = (await r.json())["events"]
            kinds = [e["kind"] for e in events]
            check("questions persisted", kinds.count("side_question") == 2,
                  str(kinds.count("side_question")))
            check("answers persisted", kinds.count("side_question_result") == 2,
                  str(kinds.count("side_question_result")))
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAILED:", name)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
