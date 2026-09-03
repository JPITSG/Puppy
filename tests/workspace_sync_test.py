#!/usr/bin/env python3
"""No-quota tests for remote-workspace sync: planner, store safety, the node
HTTP surface, and the controller link broker relaying between two trees.

Everything runs in one process against one loopback Puppy: the broker's node
channels are pointed back at the same server, which therefore plays the
controller, the execution node, and the workspace node at once. No engine is
invoked and no subscription quota is spent.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("wsync-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import (backends, config, db, protocol, runner,  # noqa: E402
                   workspace_links, workspace_sync)
from puppy.web import build_app  # noqa: E402


def expect_sync_error(call, contains: str) -> None:
    try:
        call()
    except workspace_sync.SyncError as exc:
        assert contains.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError("expected a SyncError containing " + contains)


# ---- pure units ----

def test_validate_relpath() -> None:
    workspace_sync.validate_relpath("a/b/c.txt")
    workspace_sync.validate_relpath("weird name/π.txt")
    for bad in ("", ".", "..", "/abs", "a//b", "a/../b", "a/./b",
                "a/" + "x" * 300, "a\x00b", ".puppy-sync-tmp-x/y",
                "a/.puppy-sync-tmp-x", "x/" * 130 + "y"):
        expect_sync_error(lambda b=bad: workspace_sync.validate_relpath(b),
                          "sync")


def entry_f(h, m=0o644, s=1):
    return {"t": "f", "s": s, "m": m, "h": h, "mt": 1}


def test_planner() -> None:
    plan = workspace_sync.plan_reconcile
    # untouched everywhere
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h1")}, {"a": entry_f("h1")})
    assert not result["pull"] and not result["push"] and not result["conflicts"]
    assert result["base"]["a"] == entry_f("h1")
    # only workspace moved -> pull
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h2")}, {"a": entry_f("h1")})
    assert [op["op"] for op in result["pull"]] == ["write"]
    assert not result["push"] and result["base"]["a"]["h"] == "h2"
    # only mirror moved -> push
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h1")}, {"a": entry_f("h2")})
    assert [op["op"] for op in result["push"]] == ["write"]
    assert result["base"]["a"]["h"] == "h2"
    # both moved identically -> silent agreement
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h2")}, {"a": entry_f("h2")})
    assert not result["pull"] and not result["push"] and not result["conflicts"]
    # both moved apart -> conflict, base keeps the OLD entry
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h2")}, {"a": entry_f("h3")})
    assert result["conflicts"][0]["path"] == "a"
    assert result["base"]["a"]["h"] == "h1"
    # created only on workspace -> pull; deleted only on mirror -> push delete
    result = plan({}, {"new": entry_f("h9")}, {})
    assert result["pull"][0]["op"] == "write" and result["pull"][0]["base"] is None
    result = plan({"gone": entry_f("h1")}, {"gone": entry_f("h1")}, {})
    assert result["push"][0]["op"] == "delete"
    assert "gone" not in result["base"]
    # modify/delete is a conflict
    result = plan({"a": entry_f("h1")}, {"a": entry_f("h2")}, {})
    assert result["conflicts"] and result["conflicts"][0]["mirror"] is None
    # permission-only divergence settles toward the workspace
    result = plan({"a": entry_f("h1", 0o644)}, {"a": entry_f("h1", 0o755)},
                  {"a": entry_f("h1", 0o600)})
    assert [op["op"] for op in result["pull"]] == ["chmod"]
    assert result["base"]["a"]["m"] == 0o755
    # kind flip file -> dir carries a pre-delete before the mkdir
    ops = workspace_sync.ops_for("a", entry_f("h1"), {"t": "d", "m": 0o755})
    assert [op["op"] for op in ops] == ["delete", "mkdir"]
    assert ops[0].get("pre") and ops[1].get("nocas")
    # ordering: mkdir first, flip pair adjacent, plain deletes last/deepest-first
    mixed = [{"op": "delete", "p": "z/deep/x", "base": None},
             {"op": "delete", "p": "z", "base": None},
             {"op": "write", "p": "b/f", "s": 1, "h": "h", "base": None},
             {"op": "mkdir", "p": "b", "m": 0o755, "base": None}]
    workspace_sync.order_ops(mixed)
    assert [op["p"] for op in mixed] == ["b", "b/f", "z/deep/x", "z"]
    # batches keep deletes behind writes
    batches = workspace_sync.split_batches(mixed)
    flattened = [op["op"] for batch in batches for op in batch]
    assert flattened.index("delete") > flattened.index("write")


# ---- tree helpers ----

def write_tree(root: Path, spec: dict) -> None:
    for rel, value in spec.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, tuple) and value[0] == "link":
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(value[1])
        else:
            target.write_bytes(value if isinstance(value, bytes)
                               else value.encode())


def snapshot_tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            out[rel] = ("link", os.readlink(str(path)))
        elif stat.S_ISDIR(info.st_mode):
            out[rel] = ("dir", stat.S_IMODE(info.st_mode) & 0o777)
        elif stat.S_ISREG(info.st_mode):
            out[rel] = ("file", path.read_bytes(),
                        stat.S_IMODE(info.st_mode) & 0o777)
    return out


def assert_trees_equal(a: Path, b: Path) -> None:
    left, right = snapshot_tree(a), snapshot_tree(b)
    assert left == right, "trees differ: only-left={} only-right={} diff={}".format(
        sorted(set(left) - set(right)), sorted(set(right) - set(left)),
        [k for k in set(left) & set(right) if left[k] != right[k]][:5])


def test_store_scan() -> None:
    tree = TEST_ROOT / "scantree"
    meta = TEST_ROOT / "scanmeta"
    write_tree(tree, {"a.txt": "alpha", "sub/b.bin": b"\x00\x01",
                      "ln": ("link", "a.txt"), "abs": ("link", "/etc/passwd")})
    os.chmod(str(tree / "a.txt"), 0o755)
    os.mkfifo(str(tree / "pipe"))
    store = workspace_sync.Store(str(tree), str(meta))
    first = store.scan()
    entries = first["entries"]
    assert entries["a.txt"]["t"] == "f" and entries["a.txt"]["m"] == 0o755
    assert entries["sub"]["t"] == "d"
    assert entries["ln"] == {"t": "l", "lt": "a.txt"}
    assert entries["abs"] == {"t": "l", "lt": "/etc/passwd"}
    assert "pipe" not in entries and first["skipped"]["special"] == 1
    second = store.scan()
    assert second["entries"] == entries   # cache path: same result, no rehash
    assert (meta / "hashcache.json.gz").exists()


def test_journal_recovery() -> None:
    tree = TEST_ROOT / "jtree"
    meta = TEST_ROOT / "jmeta"
    tree.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    temp_name = ".puppy-sync-tmp-recovery"
    (tree / temp_name).write_bytes(b"staged bytes")
    (meta / "journal.json").write_text(json.dumps({
        "state": "committing",
        "renames": [{"p": "recovered.txt", "tmp": temp_name}]}))
    workspace_sync.Store(str(tree), str(meta))
    assert (tree / "recovered.txt").read_bytes() == b"staged bytes"
    assert not (tree / temp_name).exists()
    assert not (meta / "journal.json").exists()


# ---- HTTP + broker ----

class Harness:
    def __init__(self, url: str, token: str, http: aiohttp.ClientSession):
        self.url = url
        self.token = token
        self.http = http

    def headers(self, extra=None) -> dict:
        merged = {"X-Puppy-Token": self.token}
        merged.update(extra or {})
        return merged

    async def api(self, method: str, path: str, **kwargs):
        kwargs.setdefault("headers", self.headers(kwargs.pop("extra_headers", None)))
        async with self.http.request(method, self.url + "/api/" + path,
                                     **kwargs) as response:
            body = await response.read()
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            return response.status, data


async def wait_for(predicate, timeout=15.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting for " + message)


async def exercise(harness: Harness) -> None:
    project = TEST_ROOT / "project"
    write_tree(project, {
        "README.md": "hello project\n",
        "src/main.py": "print('one')\n",
        "src/util/helpers.py": "def x():\n    return 1\n",
        "assets/logo.bin": os.urandom(70000),
        "link-rel": ("link", "src/main.py"),
    })
    os.chmod(str(project / "src/main.py"), 0o755)

    # -- linked session creation on the "execution node" --
    status, created = await harness.api("POST", "sessions", json={
        "engine": "claude", "name": "linked",
        "workspace": {"uid": "", "root": str(project), "node": "nodeW",
                      "label": "nodeW:" + str(project)}})
    assert status == 200, created
    session = created["session"]
    sid = int(session["id"])
    descriptor = session["workspace"]
    assert descriptor and descriptor["root"] == str(project)
    uid = descriptor["uid"]
    # The private execution mirror never appears on the user-facing session
    # payload; only internal storage retains it.
    assert session["cwd"] == str(project)
    mirror = Path(db.get_session(sid)["cwd"])
    assert mirror.is_dir()
    assert mirror.parent == Path(config.DATA_DIR) / "mirrors" / uid
    assert mirror.name == project.name

    # -- lease on the "workspace node" --
    status, lease_reply = await harness.api(
        "POST", "workspace/leases", json={"root": str(project)})
    assert status == 200, lease_reply
    lease = lease_reply["lease"]
    status, overlap = await harness.api(
        "POST", "workspace/leases", json={"root": str(project / "src")})
    assert status == 409, overlap

    link_id = db.execute(
        "INSERT INTO workspace_links(uid,exec_backend,session_id,ws_backend,"
        "root,lease,state,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, 1, sid, 2, str(project), lease["id"], "init", time.time()))

    # -- initial clone: everything pulls into the mirror --
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"], summary
    assert summary["conflicts"] == 0 and summary["pushed"] == 0
    assert_trees_equal(project, mirror)
    assert (mirror / "src/main.py").stat().st_mode & 0o777 == 0o755
    link = workspace_links.get_link(link_id)
    assert link["state"] == "ok" and link["generation"] == 1

    # steady state: nothing moves
    summary = await workspace_links.run_reconcile(link_id)
    assert summary == {"ok": True, "clean": True, "conflicts": 0,
                       "pulled": 0, "pushed": 0, "retry": 0}

    # A snapshot rebuilds no mirror bytes. Its durable marker makes an older
    # controller fail closed, while the capable broker ignores the stale
    # three-way base, pulls from authority, and acknowledges only once clean.
    shutil.rmtree(str(mirror))
    mirror.mkdir(mode=0o700)
    (mirror / "unexpected-local.txt").write_text(
        "must never push during a restore reset", encoding="utf-8")
    reset_id = "r" * 32
    workspace_sync.mark_mirror_reset(str(mirror.parent), reset_id)
    status, reset_blocked = await harness.api(
        "GET", "sessions/{}/workspace/manifest".format(sid))
    assert status == 409 and "rebuilt" in reset_blocked["error"], reset_blocked
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"] and summary["pushed"] == 0, summary
    assert_trees_equal(project, mirror)
    assert workspace_sync.mirror_reset(
        workspace_sync.mirror_store_for(db.get_session(sid))) is None

    # Provider leases are rebuildable too. Losing one must reacquire the same
    # authoritative root and update the durable link instead of marooning it.
    old_lease = lease["id"]
    status, released = await harness.api(
        "DELETE", "workspace/leases/{}".format(old_lease))
    assert status == 200 and released["removed"] is True
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"], summary
    replacement = workspace_links.get_link(link_id)["lease"]
    assert replacement != old_lease
    lease["id"] = replacement
    status, reused = await harness.api(
        "POST", "workspace/leases",
        json={"root": str(project), "reuse": True})
    assert status == 200 and reused["lease"]["id"] == replacement, reused

    # -- engine-side changes push; workspace-side changes pull; deletes flow --
    write_tree(mirror, {"src/new_module.py": "print('new')\n",
                        "src/main.py": "print('changed by engine')\n"})
    (mirror / "assets/logo.bin").unlink()
    write_tree(project, {"docs/notes.md": "external edit\n"})
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"], summary
    assert_trees_equal(project, mirror)
    assert (project / "src/new_module.py").exists()
    assert not (project / "assets/logo.bin").exists()
    assert (mirror / "docs/notes.md").exists()

    # -- divergent same-path edits conflict; both sides untouched --
    write_tree(project, {"src/main.py": "print('user version')\n"})
    write_tree(mirror, {"src/main.py": "print('engine version')\n"})
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and not summary["clean"]
    assert summary["conflicts"] == 1
    assert (project / "src/main.py").read_text() == "print('user version')\n"
    assert (mirror / "src/main.py").read_text() == "print('engine version')\n"
    link = workspace_links.get_link(link_id)
    assert link["state"] == "conflict"
    assert link["conflicts"][0]["path"] == "src/main.py"

    # unrelated changes still flow around a standing conflict
    write_tree(project, {"docs/second.md": "still flowing\n"})
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["conflicts"] == 1
    assert (mirror / "docs/second.md").exists()

    # -- resolution is fail-closed until the losing bytes are preserved --
    # Store the user's choice through the real route, but suppress its normal
    # background launch so the fetch race below is deterministic.
    real_schedule = workspace_links._schedule_service
    workspace_links._schedule_service = lambda *_args: None
    try:
        status, resolved = await harness.api(
            "POST", "workspaces/{}/resolve".format(link_id),
            json={"choices": {"src/main.py": "workspace"}})
    finally:
        workspace_links._schedule_service = real_schedule
    assert status == 200, resolved
    assert workspace_links.get_link(link_id)["resolutions"] == {
        "src/main.py": "workspace"}

    # If the loser changes after the manifests were scanned, its fetch no
    # longer matches the version Puppy promised to retain. Nothing is applied,
    # the choice remains retryable, and both live versions survive.
    real_open_stream = workspace_links._open_stream
    raced = False

    async def race_loser_fetch(channel, method, path, **kwargs):
        nonlocal raced
        if not raced and method == "POST" and \
                path == "sessions/{}/workspace/fetch".format(sid) and \
                kwargs.get("json_body") == {"paths": ["src/main.py"]}:
            write_tree(mirror, {
                "src/main.py": "print('engine raced preservation')\n"})
            raced = True
        return await real_open_stream(channel, method, path, **kwargs)

    workspace_links._open_stream = race_loser_fetch
    try:
        summary = await workspace_links.run_reconcile(link_id)
    finally:
        workspace_links._open_stream = real_open_stream
    assert not summary["ok"] and "could not preserve" in summary["error"]
    assert (project / "src/main.py").read_text() == "print('user version')\n"
    assert (mirror / "src/main.py").read_text() == \
        "print('engine raced preservation')\n"
    link = workspace_links.get_link(link_id)
    assert link["state"] == "error" and "not resolved" in link["last_error"]
    assert link["resolutions"] == {"src/main.py": "workspace"}

    # A stable retry first atomically preserves and verifies the current loser,
    # then applies the winner.
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"], summary
    assert (mirror / "src/main.py").read_text() == "print('user version')\n"
    assert_trees_equal(project, mirror)
    keeps = list((Path(config.DATA_DIR) / "workspace" / "keeps" / uid)
                 .rglob("src/main.py"))
    assert keeps and keeps[0].read_text() == \
        "print('engine raced preservation')\n"
    assert not list((Path(config.DATA_DIR) / "workspace" / "keeps" / uid)
                    .rglob(".puppy-keep-tmp-*"))

    # -- preserved losers are bounded by size, never aged out silently --
    keeps_root = Path(config.DATA_DIR) / "workspace" / "keeps" / uid
    usage = workspace_links.keeps_usage(uid)
    assert usage["generations"] >= 1 and usage["bytes"] > 0, usage
    assert usage["max_bytes"] == workspace_links.KEEPS_MAX_BYTES

    # three synthetic generations well past the minimum age, over budget
    old = time.time() - workspace_links.KEEPS_MIN_AGE_SECONDS - 3600
    for generation in (9001, 9002, 9003):
        directory = keeps_root / str(generation)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "loser.bin").write_bytes(b"x" * 4096)
        os.utime(str(directory), (old, old))
    over = workspace_links.KEEPS_MAX_BYTES
    workspace_links.KEEPS_MAX_BYTES = 6000       # fits one of them
    try:
        removed = workspace_links.enforce_keeps_budget(uid)
        assert removed >= 2, removed
        left = sorted(int(p.name) for p in keeps_root.iterdir() if p.name.isdigit())
        # oldest first, and the newest generation always survives
        assert 9003 in left and 9001 not in left, left

        # nothing inside the minimum age is a candidate, even when over budget
        young = keeps_root / "9100"
        young.mkdir(parents=True, exist_ok=True)
        (young / "loser.bin").write_bytes(b"y" * 20000)
        workspace_links.KEEPS_MAX_BYTES = 1
        before = sorted(p.name for p in keeps_root.iterdir())
        workspace_links.enforce_keeps_budget(uid)
        after = sorted(p.name for p in keeps_root.iterdir())
        assert "9100" in after, after
        assert len(after) <= len(before)
    finally:
        workspace_links.KEEPS_MAX_BYTES = over

    # the explicit clear is the only thing that takes a recent version
    assert workspace_links.clear_keeps(uid) >= 1
    assert workspace_links.keeps_usage(uid)["generations"] == 0

    # -- an individual apply failure is a sync failure, never "Synced" --
    write_tree(project, {"docs/failing.txt": "must reach the mirror\n"})
    before_failure = workspace_links.get_link(link_id)
    previous_sync = before_failure["last_sync_at"]
    previous_generation = before_failure["generation"]
    real_transfer = workspace_links._transfer

    async def fail_one_transfer(src, src_prefix, dst, dst_prefix, ops):
        if any(op.get("p") == "docs/failing.txt" for op in ops):
            return [{"p": op["p"], "ok": False,
                     "reason": "injected apply failure"} for op in ops]
        return await real_transfer(src, src_prefix, dst, dst_prefix, ops)

    workspace_links._transfer = fail_one_transfer
    try:
        summary = await workspace_links.run_reconcile(link_id)
        assert not summary["ok"] and not summary["clean"], summary
        assert summary["retry"] == 1
        link = workspace_links.get_link(link_id)
        assert link["state"] == "error"
        assert "pull docs/failing.txt: injected apply failure" in \
            link["last_error"]
        assert link["last_sync_at"] == previous_sync
        assert link["generation"] == previous_generation
        assert not (mirror / "docs/failing.txt").exists()

        # Manual sync reports an HTTP failure instead of a success toast.
        status, failed_sync = await harness.api(
            "POST", "workspaces/{}/sync".format(link_id))
        assert status == 502 and "injected apply failure" in \
            failed_sync["error"], failed_sync

        # The same per-file failure denies a pre-turn grant, so the engine
        # cannot run against the stale mirror.
        hub = runner.hub(sid)
        barrier = asyncio.ensure_future(hub._workspace_barrier("pre"))
        await wait_for(lambda: hub.workspace_barrier_active(), message="barrier")
        await workspace_links.service_session(1, sid, "pre")
        outcome = await asyncio.wait_for(barrier, timeout=5)
        assert not outcome["ok"]
        assert "injected apply failure" in outcome["error"]
    finally:
        workspace_links._transfer = real_transfer

    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"] and summary["clean"], summary
    assert (mirror / "docs/failing.txt").read_text() == \
        "must reach the mirror\n"
    assert workspace_links.get_link(link_id)["state"] == "ok"

    # -- barrier mechanics over the real grant route --
    hub = runner.hub(sid)
    barrier = asyncio.ensure_future(hub._workspace_barrier("pre"))
    await wait_for(lambda: hub.workspace_barrier_active(), message="barrier")
    status, progress = await harness.api(
        "POST", "sessions/{}/workspace/grant".format(sid),
        json={"progress": True})
    assert status == 200 and progress["phase"] == "pre"
    status, wrong = await harness.api(
        "POST", "sessions/{}/workspace/grant".format(sid),
        json={"phase": "post", "ok": True})
    assert status == 409, wrong
    status, granted = await harness.api(
        "POST", "sessions/{}/workspace/grant".format(sid),
        json={"phase": "pre", "ok": True, "clean": True, "conflicts": 0})
    assert status == 200, granted
    outcome = await asyncio.wait_for(barrier, timeout=5)
    assert outcome["ok"] is True and not hub.workspace_barrier_active()

    # an interrupted pre barrier stops waiting
    barrier = asyncio.ensure_future(hub._workspace_barrier("pre"))
    await wait_for(lambda: hub.workspace_barrier_active(), message="barrier")
    hub.interrupted = True
    outcome = await asyncio.wait_for(barrier, timeout=5)
    assert outcome.get("interrupted") is True
    hub.interrupted = False

    # -- the idle grant path clears a parked dirty flag via the broker --
    db.touch_session(sid, ws_dirty=1)
    await workspace_links.service_session(1, sid, "idle")
    assert db.get_session(sid)["ws_dirty"] == 0

    # -- mirror refuses applies mid-turn outside a barrier --
    hub.status = "running"
    frames = workspace_sync.encode_frame({"end": True})
    status, refused = await harness.api(
        "POST", "sessions/{}/workspace/apply".format(sid), data=frames,
        extra_headers={"Content-Type": workspace_sync.CONTENT_TYPE})
    assert status == 409, refused
    hub.status = "idle"

    # -- hostile applies against the lease surface --
    async def apply_ops(ops_bytes):
        return await harness.api(
            "POST", "workspace/leases/{}/apply".format(lease["id"]),
            data=ops_bytes,
            extra_headers={"Content-Type": workspace_sync.CONTENT_TYPE})

    evil = b"".join([
        workspace_sync.encode_frame({"op": "mkdir", "p": "../escape",
                                     "m": 0o755, "base": None}),
        workspace_sync.encode_frame({"op": "mkdir", "p": "/abs",
                                     "m": 0o755, "base": None}),
        workspace_sync.encode_frame({"op": "symlink", "p": "ok-link",
                                     "lt": "/tmp", "base": None}),
        workspace_sync.encode_frame({"end": True}),
    ])
    status, reply = await apply_ops(evil)
    assert status == 200, reply
    by_path = {item["p"]: item for item in reply["results"]}
    assert not by_path["../escape"]["ok"] and not by_path["/abs"]["ok"]
    assert by_path["ok-link"]["ok"]   # absolute TARGETS are data, not paths
    assert not (project.parent / "escape").exists()

    # writes routed through a symlinked parent must not follow it
    outside = TEST_ROOT / "outside"
    outside.mkdir(exist_ok=True)
    write_tree(project, {"sneak": ("link", str(outside))})
    payload = b"ESCAPED"
    import hashlib
    escape_write = b"".join([
        workspace_sync.encode_frame({
            "op": "write", "p": "sneak/owned.txt", "s": len(payload),
            "h": hashlib.sha256(payload).hexdigest(), "m": 0o644,
            "base": None, "nocas": True}),
        payload,
        workspace_sync.encode_frame({"p": "sneak/owned.txt", "ok": True}),
        workspace_sync.encode_frame({"end": True}),
    ])
    status, reply = await apply_ops(escape_write)
    assert status == 200, reply
    assert not reply["results"][0]["ok"]
    assert not (outside / "owned.txt").exists()
    (project / "sneak").unlink()

    # stale CAS base is a per-item conflict, not an overwrite
    current = (project / "README.md").read_bytes()
    stale = b"".join([
        workspace_sync.encode_frame({
            "op": "write", "p": "README.md", "s": 3,
            "h": hashlib.sha256(b"foo").hexdigest(), "m": 0o644,
            "base": {"t": "f", "h": "0" * 64, "m": 0o644}}),
        b"foo",
        workspace_sync.encode_frame({"p": "README.md", "ok": True}),
        workspace_sync.encode_frame({"end": True}),
    ])
    status, reply = await apply_ops(stale)
    assert status == 200 and reply["results"][0]["conflict"], reply
    assert (project / "README.md").read_bytes() == current

    # bad content hash never lands
    lying = b"".join([
        workspace_sync.encode_frame({
            "op": "write", "p": "lied.txt", "s": 3,
            "h": hashlib.sha256(b"xyz").hexdigest(), "m": 0o644,
            "base": None}),
        b"abc",
        workspace_sync.encode_frame({"p": "lied.txt", "ok": True}),
        workspace_sync.encode_frame({"end": True}),
    ])
    status, reply = await apply_ops(lying)
    assert status == 200 and not reply["results"][0]["ok"]
    assert not (project / "lied.txt").exists()

    # reconcile again afterwards: hostile leftovers healed, trees equal
    summary = await workspace_links.run_reconcile(link_id)
    assert summary["ok"], summary
    assert_trees_equal(project, mirror)

    # -- same-node pairing degrades to a plain direct session --
    direct_root = TEST_ROOT / "directproj"
    direct_root.mkdir(exist_ok=True)
    result = await workspace_links.create_linked_session({
        "backend": 1, "workspace_backend": 2, "root": str(direct_root),
        "engine": "claude"})
    assert result["same_node"] is True
    assert result["session"]["cwd"] == str(direct_root)
    assert result["session"]["workspace"] is None
    status, _gone = await harness.api(
        "DELETE", "sessions/{}".format(result["session"]["id"]))
    assert status == 200

    # -- link deletion cascades: session, mirror, lease, records --
    status, done = await harness.api(
        "DELETE", "workspaces/{}?with_session=1".format(link_id))
    assert status == 200, done
    assert workspace_links.get_link(link_id) is None
    assert db.get_session(sid) is None
    assert not mirror.parent.exists()
    status, missing = await harness.api(
        "GET", "workspace/leases/{}/manifest".format(lease["id"]))
    assert status == 404, missing
    assert not workspace_links._base_path(uid) or \
        not os.path.exists(workspace_links._base_path(uid))


async def main() -> None:
    test_validate_relpath()
    test_planner()
    test_store_scan()
    test_journal_recovery()

    app = build_app()
    app_runner = web.AppRunner(app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    token = config.get("auth.api_token")

    # every "node" the broker talks to is this same loopback server
    def fake_channel(bid: int):
        return {"bid": bid, "name": "node{}".format(bid), "urls": [url],
                "token": token, "ssl": True,
                "capabilities": list(protocol.execution_capabilities())}
    backends.node_channel = fake_channel

    try:
        async with aiohttp.ClientSession() as http:
            await exercise(Harness(url, token, http))
    finally:
        await app_runner.cleanup()
        shutil.rmtree(str(TEST_ROOT), ignore_errors=True)
    print("workspace sync tests passed")


if __name__ == "__main__":
    asyncio.run(main())
