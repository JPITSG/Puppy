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
import json
import os
import shutil
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
    with open(os.path.join(data_dir, "config.json"), "w") as f:
        json.dump({"web": {"host": "127.0.0.1", "port": PORT}}, f)
    with open(os.path.join(work, "ledger.txt"), "w") as f:
        f.write("alpha\nbravo\ncharlie\n")

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
            async with http.get(URL + "/api/state") as r:
                state = await r.json()
            check("node advertises the capability",
                  "active-turn-side-question" in (state.get("capabilities") or []),
                  str(state.get("capabilities"))[:200])

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
                        "text": "Read ledger.txt, then write a file called "
                                "report.txt containing the word REPORT, then "
                                "reply with exactly: DONE"}) as r:
                    check("turn started", r.status == 200, str(r.status))

                ready = await sock.drain(
                    until=lambda d: d.get("type") == "side_question_state" and
                    d.get("side_question", {}).get("ready") is True, timeout=120)
                check("becomes ready during the turn", ready is not None)
                turn_id = (ready or {}).get("side_question", {}).get("turn_id", "")

                # ---- question one ------------------------------------------
                asked_at = time.time()
                async with http.post(f"{URL}/api/sessions/{sid}/ask", json={
                        "question": "In one short sentence: name the single file "
                                    "you read from this working directory.",
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
                async with http.post(f"{URL}/api/sessions/{sid}/ask", json={
                        "question": "How many lines did it have? Answer with the "
                                    "number only.",
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
                check("follow-up used the thread's subject",
                      "3" in str(follow.get("text", "")),
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
                    "text": "Reply with exactly YES if I have asked you how many "
                            "lines ledger.txt had at any point in this "
                            "conversation, otherwise exactly NO."}) as r:
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
