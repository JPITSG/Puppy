#!/usr/bin/env python3
"""Puppy integration test. Boots a real server on a test port with an isolated
data dir, then exercises: auth setup/login, engine status, session CRUD, a live
claude turn (haiku) + native resume, a live codex turn + resume-after-switch
handoff to claude, the terminal websocket, and events pagination.

NOTE: spends a small amount of real subscription quota (3 tiny haiku turns +
1 tiny codex turn). Run deliberately: python3 tests/integration_test.py
"""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import aiohttp

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("PUPPY_TEST_PORT", "10999"))
URL = f"http://127.0.0.1:{PORT}"

PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name} {extra}")


async def wait_events(ws, want_kinds=(), until="turn_done", timeout=240):
    """Collect broadcast frames until a frame of type `until` arrives."""
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = await ws.receive(timeout=min(30, max(1, deadline - time.time())))
        except asyncio.TimeoutError:
            continue
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
            continue
        d = json.loads(msg.data)
        events.append(d)
        if d.get("type") == until:
            return events
        if d.get("type") == "approval_request":
            # auto-allow anything the test trips over
            await ws.send_json({"type": "approval_response",
                                "request_id": d["req"]["request_id"], "behavior": "allow"})
    return events


def texts_of(frames, kind):
    out = []
    for f in frames:
        if f.get("type") == "event" and f["event"]["kind"] == kind:
            out.append(f["event"]["data"].get("text", ""))
    return out


async def main():
    data_dir = tempfile.mkdtemp(prefix="puppy_test_data_")
    work1 = tempfile.mkdtemp(prefix="puppy_test_ws1_")
    work2 = tempfile.mkdtemp(prefix="puppy_test_ws2_")
    env = dict(os.environ)
    env["PUPPY_DATA"] = data_dir
    for k in list(env):
        if k.startswith("CLAUDE_") or k == "CLAUDECODE":
            env.pop(k)

    # test-port config
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "config.json"), "w") as f:
        json.dump({"web": {"host": "127.0.0.1", "port": PORT}}, f)

    server = subprocess.Popen([sys.executable, "-m", "puppy"], cwd=BASE, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(f"server pid={server.pid} data={data_dir}")
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            # ---- wait for boot ----
            up = False
            for _ in range(60):
                if server.poll() is not None:
                    break
                try:
                    async with http.get(URL + "/api/auth/status") as r:
                        if r.status == 200:
                            up = True
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            check("server boots", up)
            if not up:
                out = server.stdout.read() if server.poll() is not None else "(still running, unreachable)"
                print(out[-3000:])
                return

            # ---- auth ----
            async with http.get(URL + "/api/auth/status") as r:
                st = await r.json()
            check("first run needs setup", st.get("setup_required") is True)
            async with http.post(URL + "/api/auth/setup",
                                 json={"username": "tester", "password": "secret123"}) as r:
                check("admin setup", r.status == 200, str(r.status))
            async with http.get(URL + "/api/state") as r:
                check("authed after setup", r.status == 200)
                state = await r.json()
            engines = {e["key"]: e for e in state["engines"]}
            check("claude engine ready", engines.get("claude", {}).get("auth") == "ok",
                  str(engines.get("claude")))
            check("codex engine ready", engines.get("codex", {}).get("auth") == "ok",
                  str(engines.get("codex")))

            # unauthenticated access denied
            async with aiohttp.ClientSession() as anon:
                async with anon.get(URL + "/api/state") as r:
                    check("anon request denied", r.status == 401, str(r.status))

            # ---- claude session: live turn ----
            async with http.post(URL + "/api/sessions", json={
                    "engine": "claude", "cwd": work1, "model": "haiku",
                    "permission_mode": "acceptEdits"}) as r:
                d = await r.json()
                check("claude session created", r.status == 200, str(d))
                sid1 = d["session"]["id"]

            ws1 = await http.ws_connect(URL + f"/api/ws/session/{sid1}")
            snap = json.loads((await ws1.receive()).data)
            check("snapshot arrives", snap.get("type") == "snapshot")

            await ws1.send_json({"type": "message",
                                 "text": "Create a file named bark.txt containing exactly: woof\nThen reply with just the word: done"})
            frames = await wait_events(ws1)
            bark = os.path.join(work1, "bark.txt")
            check("claude turn completes", any(f.get("type") == "turn_done" for f in frames))
            check("claude created file", os.path.exists(bark) and "woof" in open(bark).read(),
                  "file missing")
            results = [f["event"]["data"] for f in frames
                       if f.get("type") == "event" and f["event"]["kind"] == "result"]
            check("claude result ok", results and results[-1].get("ok") is True, str(results))

            # turn 2 -> exercises claude native --resume in a fresh process
            await ws1.send_json({"type": "message",
                                 "text": "What filename did you create earlier in this session? Reply with only the filename."})
            frames2 = await wait_events(ws1)
            a2 = " ".join(texts_of(frames2, "assistant"))
            check("claude resume remembers context", "bark.txt" in a2, a2[:200])
            await ws1.close()

            # ---- codex session: live turn ----
            async with http.post(URL + "/api/sessions", json={
                    "engine": "codex", "cwd": work2,
                    "permission_mode": "read-only"}) as r:
                d = await r.json()
                check("codex session created", r.status == 200, str(d))
                sid2 = d["session"]["id"]

            ws2 = await http.ws_connect(URL + f"/api/ws/session/{sid2}")
            await ws2.receive()  # snapshot
            await ws2.send_json({"type": "message",
                                 "text": "Reply with exactly the word: pineapple2"})
            framesc = await wait_events(ws2)
            ac = " ".join(texts_of(framesc, "assistant"))
            check("codex turn completes", any(f.get("type") == "turn_done" for f in framesc))
            check("codex replied", "pineapple2" in ac, ac[:200])

            # ---- engine switch codex -> claude with handoff ----
            async with http.post(URL + f"/api/sessions/{sid2}/switch",
                                 json={"engine": "claude"}) as r:
                d = await r.json()
                check("switch to claude", r.status == 200 and d["session"]["engine"] == "claude", str(d))
            async with http.patch(URL + f"/api/sessions/{sid2}",
                                  json={"model": "haiku"}) as r:
                check("model patch", r.status == 200)

            await ws2.send_json({"type": "message",
                                 "text": "What exact word did you reply with earlier in this conversation? Reply with only that word."})
            framesh = await wait_events(ws2)
            ah = " ".join(texts_of(framesh, "assistant"))
            check("handoff carries context across engines", "pineapple2" in ah, ah[:300])
            await ws2.close()

            # ---- events pagination ----
            async with http.get(URL + f"/api/sessions/{sid1}/events?limit=2") as r:
                d = await r.json()
                check("events pagination", r.status == 200 and len(d["events"]) == 2, str(len(d.get("events", []))))

            # ---- terminal ws ----
            wst = await http.ws_connect(URL + "/api/ws/term?cols=80&rows=24&cmd=/bin/bash")
            await wst.send_bytes(b"echo PUPPY_$((40+2))\n")
            got = b""
            deadline = time.time() + 15
            while time.time() < deadline and b"PUPPY_42" not in got:
                try:
                    msg = await wst.receive(timeout=3)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.BINARY:
                    got += msg.data
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
            check("terminal echo", b"PUPPY_42" in got, got[-120:].decode(errors="replace"))
            await wst.send_bytes(b"exit\n")
            await wst.close()

            # ---- fs + settings + delete ----
            async with http.get(URL + "/api/fs?path=/etc") as r:
                d = await r.json()
                check("fs list", r.status == 200 and "scripts" in d["dirs"])
            async with http.delete(URL + f"/api/sessions/{sid1}") as r:
                check("session delete", r.status == 200)
            async with http.get(URL + "/api/sessions") as r:
                d = await r.json()
                check("delete reflected", all(s["id"] != sid1 for s in d["sessions"]))
    finally:
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        for p in (data_dir, work1, work2):
            shutil.rmtree(p, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("ALL GREEN 🐾")


if __name__ == "__main__":
    asyncio.run(main())
