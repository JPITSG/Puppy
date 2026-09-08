#!/usr/bin/env python3
"""Host CPU history, the trimmed Puppy process tree, and backend latency.

Everything is exercised against a fabricated /proc and a stub backend on
loopback: no engine is invoked, no real network is reached, and no
subscription quota is spent.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
TEST_ROOT = private_root("host-metrics-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from puppy import auth, backends, config, db, host_metrics, protocol
from puppy import web as webui
from backend.puppy_backend.app import build_app as backend_app

PAGE = host_metrics._page_size()
TICKS = host_metrics._clock_ticks()


def write_process(root: Path, pid, ppid, comm, argv, jiffies=0, rss_pages=64,
                  threads=1, starttime=1000):
    """One process in a fabricated /proc, in the real stat field order."""
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(jiffies), "0"] + ["0"] * 4 + \
        [str(threads), "0", str(starttime), "0", str(rss_pages)]
    assert len(fields) == 22
    entry = root / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "stat").write_text("{} ({}) {}\n".format(pid, comm, " ".join(fields)))
    (entry / "cmdline").write_bytes(b"\0".join(part.encode() for part in argv) + b"\0")


def fake_proc(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "uptime").write_text("200000.00 100000.00\n")
    (root / "meminfo").write_text(
        "MemTotal:       16000000 kB\nMemFree:         1000000 kB\n"
        "MemAvailable:    8000000 kB\n")
    (root / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n")
    return root


def parsing():
    assert host_metrics._parse_cpu_stat("cpu  10 0 10 80 0 0 0 0\n") == (100, 80)
    for broken in ("", "cpu\n", "cpu  1 2\n", "cpu  -1 0 0 0\n",
                   "cpu  0 0 0 0\n", "cpu  x y z w\n", "cpu0 1 2 3 4\n"):
        assert host_metrics._parse_cpu_stat(broken) is None, broken
    assert host_metrics._cpu_percent((100, 80), (200, 130)) == 50.0
    for previous, current in (((100, 80), (100, 80)), ((100, 80), (90, 70)),
                              ((100, 80), (110, 79)), ((100, 80), (110, 200))):
        assert host_metrics._cpu_percent(previous, current) is None

    # a command containing ") " must not shift every field after it
    line = "42 (my ) prog) S 7 " + " ".join(["0"] * 9) + " 30 12 0 0 0 0 5 0 900 0 77"
    record = host_metrics._parse_process_stat(line)
    assert record["pid"] == 42 and record["comm"] == "my ) prog"
    assert record["ppid"] == 7 and record["jiffies"] == 42
    assert record["threads"] == 5 and record["starttime"] == 900
    assert record["rss"] == 77 * PAGE
    for broken in ("", "no parens", "1 (x) S", "x (y) S 1 " + " ".join(["0"] * 21)):
        assert host_metrics._parse_process_stat(broken) is None, broken

    # labels: what a person needs to tell one row from the next
    assert host_metrics._label(["/usr/local/bin/python3", "-m", "puppy"], "python3") == \
        "python3 -m puppy"
    assert host_metrics._label(["node", "/opt/x/cli.js", "--print"], "node") == "node cli.js"
    assert host_metrics._label(["/usr/lib/chromium/chromium", "--type=renderer"],
                               "chromium") == "chromium renderer"
    assert host_metrics._label(["/bin/sh", "-c", "long script here"], "sh") == "sh"
    assert host_metrics._label([], "kworker/2:1") == "kworker/2:1"
    assert host_metrics._label(["python3", "-m", "puppy.browser_agent"],
                               "python3") == "browser bridge"
    # families come from the engine registry and the browser's own candidates
    from puppy.drivers import engine_keys
    for key in engine_keys():
        assert host_metrics._kind(key, ["/usr/local/bin/" + key, "-p"], key) == key
    assert host_metrics._kind("chromium", ["/usr/bin/chromium"], "chromium") == "browser"
    assert host_metrics._kind("bash", ["/bin/bash"], "bash") == "shell"
    assert host_metrics._kind("python3 -m puppy.spawn_agent",
                              ["python3", "-m", "puppy.spawn_agent"], "python3") == "bridge"
    assert host_metrics._kind("ls", ["/bin/ls"], "ls") == "proc"
    # a huge argument (an engine's MCP config) is shortened, never dropped
    command = host_metrics._display_command(["claude", "--mcp-config", "x" * 400], "claude")
    assert command.startswith("claude --mcp-config x") and len(command) <= host_metrics.CMD_CHARS
    assert "…" in command


def tree_shape():
    root = fake_proc(TEST_ROOT / "proc")
    write_process(root, 100, 1, "python3", ["/usr/local/bin/python3", "-m", "puppy"],
                  jiffies=0, rss_pages=1000, threads=9)
    write_process(root, 200, 100, "claude", ["claude", "-p"], jiffies=0, rss_pages=5000)
    for index, name in enumerate(("browser", "terminal", "vnc", "session", "spawn")):
        write_process(root, 210 + index, 200, "python3",
                      ["python3", "-m", "puppy.{}_agent".format(name)], rss_pages=100)
    write_process(root, 300, 100, "chromium", ["/usr/bin/chromium", "--headless"])
    for index in range(4):
        write_process(root, 310 + index, 300,
                      "chromium", ["/usr/bin/chromium", "--type=renderer"], rss_pages=200)
    write_process(root, 900, 1, "sshd", ["/usr/sbin/sshd"])   # not ours, never shown

    host_metrics.reset_for_tests()
    first = host_metrics._scan(str(root), 1000.0)
    assert set(first) == {100, 200, 300, 900} | set(range(210, 215)) | set(range(310, 314))
    assert all(record["cpu"] is None for record in first.values())   # one scan proves nothing
    # a second scan turns the jiffy delta into a percentage of one core
    write_process(root, 200, 100, "claude", ["claude", "-p"],
                  jiffies=2 * TICKS, rss_pages=5000)
    records = host_metrics._scan(str(root), 1004.0)
    assert records[200]["cpu"] == 50.0
    assert records[100]["cpu"] == 0.0
    tree = host_metrics.build_tree(records, 100, str(root))
    assert tree["label"] == "python3 -m puppy" and tree["kind"] == "puppy"
    assert tree["rss"] == 1000 * PAGE and tree["threads"] == 9
    assert tree["uptime"] == round(200000.0 - 1000 / TICKS, 1)
    labels = [child["label"] for child in tree["children"]]
    assert labels == ["claude", "chromium"], labels          # busiest node first
    engine = tree["children"][0]
    assert [child["label"] for child in engine["children"]] == ["agent bridges"]
    bridges = engine["children"][0]
    assert bridges["count"] == 5 and bridges["group"] is True
    assert bridges["rss"] == 5 * 100 * PAGE and bridges["cpu"] == 0.0
    assert sorted(bridges["pids"]) == list(range(210, 215))
    renderers = tree["children"][1]["children"][0]
    assert renderers["label"] == "chromium renderer" and renderers["count"] == 4
    assert renderers["kind"] == "browser"
    assert host_metrics._count(tree) == 12
    # the tree is Puppy's own: another user's shell on the same host is absent
    assert 900 not in [node["pid"] for node in _flatten(tree) if "pid" in node]
    # a process that ends is forgotten rather than kept as a stale sample
    shutil.rmtree(root / "900")
    host_metrics._scan(str(root), 1008.0)
    assert 900 not in host_metrics._process_samples
    # a recycled pid does not inherit the previous process's counters
    write_process(root, 200, 100, "codex", ["codex", "exec"],
                  jiffies=0, rss_pages=10, starttime=99999)
    assert host_metrics._scan(str(root), 1012.0)[200]["cpu"] is None
    return root


async def concurrent_scans(root: Path):
    """Two consoles reading one node share a scan instead of blanking it.

    The scans run in the executor; interleaving them would let each read back
    the other's fresh timestamps as "no time has passed" and report no CPU at
    all for half the tree.
    """
    host_metrics.reset_for_tests()
    host_metrics.processes(str(root), 100)          # one scan proves nothing
    await asyncio.sleep(.1)
    host_metrics._process_cache.update({"at": 0.0, "payload": None, "key": None})
    loop = asyncio.get_event_loop()
    readers = [loop.run_in_executor(None, host_metrics.processes, str(root), 100)
               for _ in range(4)]
    payloads = await asyncio.gather(*readers)
    assert all(payload is payloads[0] for payload in payloads)
    for node in _flatten(payloads[0]["root"]):
        assert node["cpu"] is not None, node["label"]


def _flatten(node):
    yield node
    for child in node.get("children", ()):
        for item in _flatten(child):
            yield item


def trimming():
    root = fake_proc(TEST_ROOT / "wide")
    write_process(root, 1, 0, "python3", ["python3", "-m", "puppy"])
    # a fan of distinct children, wider than the per-parent cap
    for index in range(60):
        write_process(root, 100 + index, 1, "worker", ["/usr/bin/worker-{}".format(index)])
    # a chain deeper than the depth cap
    parent = 1
    for depth in range(host_metrics.MAX_DEPTH + 3):
        pid = 500 + depth
        write_process(root, pid, parent, "deep", ["/usr/bin/deep-{}".format(depth)])
        parent = pid
    host_metrics.reset_for_tests()
    host_metrics._scan(str(root), 2000.0)
    tree = host_metrics.build_tree(host_metrics._scan(str(root), 2004.0), 1, str(root))
    assert len(tree["children"]) == host_metrics.MAX_CHILDREN
    assert tree["more"] > 0 and tree["hidden"] >= tree["more"]
    assert host_metrics._count(tree) <= host_metrics.MAX_NODES
    depths = []
    node, depth = tree, 0
    while node is not None:
        deeper = [child for child in node["children"] if child["label"].startswith("deep")]
        depths.append(depth)
        node = deeper[0] if deeper else None
        depth += 1
    assert max(depths) <= host_metrics.MAX_DEPTH + 1
    # nothing about a trimmed tree is silent: the branch that was cut says so
    assert any(node.get("more") for node in _flatten(tree))


def history_window():
    class App(dict):
        pass
    app = App()
    host_metrics.register(_RouterlessApp(app))
    now = time.time()
    for offset in range(0, 40):
        host_metrics._record_sample(app, now - 1200 + offset * 30, offset % 100)
    assert len(host_metrics.history(app, 900, now)) == 30
    assert host_metrics.history(app, 900, now)[0][0] >= now - 900.01
    assert host_metrics.history(app, 0, now) == []
    host_metrics._record_sample(app, now, None)            # a failed sample is not a zero
    assert host_metrics.history(app, 60, now)[-1][1] != None
    app["puppy_host_metrics"]["history"].clear()
    assert host_metrics.history(app, 900, now) == []


class _RouterlessApp:
    """register() only needs somewhere to put its state and its route."""
    def __init__(self, store):
        self.store = store
        self.router = self
        self.cleanup_ctx = []

    def get(self, key, default=None):
        return self.store.get(key, default)

    def __setitem__(self, key, value):
        self.store[key] = value

    def add_get(self, *_args):
        pass


async def routes(factory, proc_root):
    app = factory()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    assert protocol.HOST_METRICS_CAPABILITY in app["puppy_capabilities"]
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/host/metrics")).status == 401
        headers = {"X-Puppy-Token": config.get("auth.api_token")}
        response = await client.get("/api/host/metrics", headers=headers)
        assert response.status == 200
        payload = await response.json()
        assert payload["ok"] is True
        assert payload["cpu"]["cores"] >= 1
        assert payload["cpu"]["interval"] == host_metrics.SAMPLE_INTERVAL_SECONDS
        assert payload["cpu"]["window"] == host_metrics.DEFAULT_WINDOW_SECONDS
        assert payload["processes"]["root"]["pid"] == os.getpid()
        assert payload["processes"]["total"] >= 1
        # a hostile or oversized window falls back to the default rather than
        # letting a caller ask for an unbounded history
        for window in ("abc", "-5", "0", str(host_metrics.MAX_WINDOW_SECONDS + 10)):
            response = await client.get(
                "/api/host/metrics?window=" + window, headers=headers)
            if window == "abc":
                assert response.status == 400
                continue
            assert (await response.json())["cpu"]["window"] == \
                host_metrics.DEFAULT_WINDOW_SECONDS
        response = await client.get("/api/host/metrics?window=120", headers=headers)
        assert (await response.json())["cpu"]["window"] == 120


async def latency():
    """The controller times each backend itself, and never probes an offline one."""
    served = {"pings": 0}

    async def ping(_request):
        served["pings"] += 1
        return web.json_response({"ok": True, "protocol": protocol.API_PROTOCOL,
                                  "capabilities": [], "version": "0.0.0"})

    stub = web.Application()
    stub.router.add_get("/api/ping", ping)
    server = TestServer(stub)
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    for name, target in (("reachable", url), ("dead", "http://127.0.0.1:1")):
        db.execute("INSERT INTO backends(name,url,urls,token,protocol,capabilities,"
                   "created_at) VALUES(?,?,?,?,?,?,?)",
                   (name, target, json.dumps([target]), "token", protocol.API_PROTOCOL,
                    json.dumps([protocol.HOST_METRICS_CAPABILITY]), time.time()))
    rows = {item["name"]: item["id"] for item in backends.list_backends()}
    backends._mark_backend_online(rows["reachable"])
    backends._mark_backend_online(rows["dead"])
    payload = await backends.measure_latency()
    measured = {row["name"]: row for row in payload["backends"]}
    assert measured["reachable"]["ok"] is True
    assert measured["reachable"]["ms"] >= 0 and measured["reachable"]["url"] == url
    assert served["pings"] == 1
    assert measured["dead"]["ok"] is False and measured["dead"].get("offline") is not True
    assert measured["dead"]["error"]
    # a failed measurement is a measurement, not a health verdict
    assert backends.backend_is_online(rows["dead"])
    # one shared measurement per moment, however many consoles are watching
    await asyncio.gather(backends.measure_latency(), backends.measure_latency())
    assert served["pings"] == 1
    backends._latency.update({"at": 0.0, "payload": None})
    # an offline node is reported from the controller's verdict, never probed
    backends._mark_backend_offline(rows["reachable"], "connection refused")
    payload = await backends.measure_latency()
    measured = {row["name"]: row for row in payload["backends"]}
    assert measured["reachable"]["offline"] is True
    assert measured["reachable"]["error"] == "connection refused"
    assert served["pings"] == 1
    await server.close()
    await backends.close_client()
    db.execute("DELETE FROM backends")


async def main():
    try:
        db.connect()
        config.load()
        auth.create_user("host-metrics-test", "test-password")
        parsing()
        proc_root = tree_shape()
        await concurrent_scans(proc_root)
        trimming()
        history_window()
        host_metrics.reset_for_tests()
        await routes(webui.build_app, proc_root)
        await routes(backend_app, proc_root)
        await latency()
        print("host metrics tests passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
