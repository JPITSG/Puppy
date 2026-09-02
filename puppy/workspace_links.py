"""Controller-side broker for remote-workspace sessions.

A workspace link binds one session on an execution node to one authoritative
project directory on a workspace node. The controller is the only party that
can reach both, so it owns the link records, performs every reconcile by
relaying streams between the two nodes with bounded memory, services the
execution node's sync barriers, and resolves conflicts the user decides.

Node 0 is this controller itself, reached over loopback exactly like a remote
backend, so local and remote pairings share one code path; when both sides of
a requested link resolve to the same machine (matching node UUIDs), no link is
created at all and an ordinary direct-directory session is made instead.

This module is imported by the full console only; headless backends know
nothing about links - they just serve the shared workspace-sync surface.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time

import aiohttp
from aiohttp import WSMsgType, web

from puppy import backends, config, db, protocol, runner, workspace_sync

log = logging.getLogger("puppy.wslinks")

KEEPALIVE_SECONDS = 45.0
SWEEP_SECONDS = 120.0
RETRY_SECONDS = 300.0
JSON_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=5, sock_connect=5)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=5, sock_connect=5,
                                       sock_read=600)

_locks = {}          # link id -> asyncio.Lock (one reconcile at a time)
_watch_tasks = {}    # exec backend id -> watcher Task
_sweep_task = None
_service_inflight = set()   # (exec bid, sid, phase)
_active_reconciles = set()  # link ids currently inside their reconcile lock
_retry_after = {}    # link id -> monotonic timestamp of the next allowed try
_snapshot_paused = False


class NodeError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---- link records ----

def _row_to_link(row) -> dict:
    link = dict(row)
    for key, default in (("conflicts", []), ("resolutions", {})):
        try:
            value = json.loads(link.get(key) or "")
        except Exception:
            value = default
        link[key] = value if isinstance(value, type(default)) else default
    return link


def list_links() -> list:
    return [_row_to_link(row) for row in
            db.query("SELECT * FROM workspace_links ORDER BY id")]


def get_link(link_id: int):
    row = db.query_one("SELECT * FROM workspace_links WHERE id=?", (link_id,))
    return _row_to_link(row) if row else None


def get_link_by_session(exec_bid: int, session_id: int):
    row = db.query_one(
        "SELECT * FROM workspace_links WHERE exec_backend=? AND session_id=?",
        (exec_bid, session_id))
    return _row_to_link(row) if row else None


def _node_names() -> dict:
    names = {0: str(config.get("instance_name") or "this backend")}
    for backend in backends.list_backends():
        names[int(backend["id"])] = str(backend.get("name") or backend["id"])
    return names


def public_links() -> list:
    names = _node_names()
    out = []
    for link in list_links():
        out.append({
            "id": link["id"], "uid": link["uid"],
            "exec_backend": link["exec_backend"],
            "session_id": link["session_id"],
            "ws_backend": link["ws_backend"],
            "exec_name": names.get(int(link["exec_backend"]),
                                   str(link["exec_backend"])),
            "ws_name": names.get(int(link["ws_backend"]),
                                 str(link["ws_backend"])),
            "root": link["root"], "state": link["state"],
            "generation": link["generation"],
            "conflicts": link["conflicts"],
            "last_error": link["last_error"],
            "last_sync_at": link["last_sync_at"],
            "created_at": link["created_at"],
        })
    return out


def _broadcast_links() -> None:
    runner.broadcast_update({"type": "workspace_links", "links": public_links()})


def _update_link(link_id: int, **fields) -> None:
    keys = ", ".join("{}=?".format(key) for key in fields)
    db.execute("UPDATE workspace_links SET {} WHERE id=?".format(keys),
               (*fields.values(), link_id))


def _lock_for(link_id: int) -> asyncio.Lock:
    lock = _locks.get(link_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[link_id] = lock
    return lock


def _base_path(uid: str) -> str:
    return os.path.join(config.DATA_DIR, "workspace", "base",
                        "{}.json.gz".format(uid))


def _load_base(uid: str) -> dict:
    import gzip
    try:
        with gzip.open(_base_path(uid), "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        # an absent base degrades gracefully: identical paths agree silently
        # and genuinely divergent ones surface as conflicts
        return {}


def _save_base(uid: str, entries: dict) -> None:
    import gzip
    path = _base_path(uid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(entries, fh, separators=(",", ":"))
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _drop_link_files(uid: str) -> None:
    import shutil
    try:
        os.unlink(_base_path(uid))
    except OSError:
        pass
    shutil.rmtree(os.path.join(config.DATA_DIR, "workspace", "keeps", uid),
                  ignore_errors=True)


# ---- node HTTP plumbing ----

def _headers(channel: dict) -> dict:
    return {"X-Puppy-Token": channel["token"]}


async def _request_json(channel: dict, method: str, path: str,
                        body=None) -> dict:
    """One JSON API call against a node, with URL failover before send."""
    last_error = "backend is unreachable"
    urls = channel.get("urls") or []
    for index, url in enumerate(urls):
        try:
            async with backends.client().request(
                    method, "{}/api/{}".format(url, path), json=body,
                    headers=_headers(channel), timeout=JSON_TIMEOUT,
                    allow_redirects=False, ssl=channel["ssl"]) as response:
                try:
                    data = await response.json()
                except Exception:
                    data = {}
                if response.status >= 400:
                    raise NodeError(
                        (data or {}).get("error") or
                        "{} returned {}".format(channel["name"], response.status),
                        response.status)
                return data if isinstance(data, dict) else {}
        except NodeError:
            raise
        except Exception as exc:
            last_error = backends._connection_error(exc)
            if index + 1 < len(urls) and backends._failed_before_request(exc):
                continue
            break
    raise NodeError("{}: {}".format(channel["name"], last_error), 502)


async def _open_stream(channel: dict, method: str, path: str, *,
                       json_body=None, data=None, content_type=None,
                       extra_headers=None):
    """A streaming request against the node's preferred URL (no failover)."""
    urls = channel.get("urls") or []
    if not urls:
        raise NodeError("{} has no configured URL".format(channel["name"]), 502)
    headers = _headers(channel)
    if isinstance(extra_headers, dict):
        headers.update({str(key): str(value)
                        for key, value in extra_headers.items()})
    if content_type:
        headers["Content-Type"] = content_type
    try:
        response = await backends.client().request(
            method, "{}/api/{}".format(urls[0], path), json=json_body,
            data=data, headers=headers, timeout=STREAM_TIMEOUT,
            allow_redirects=False, ssl=channel["ssl"])
    except Exception as exc:
        raise NodeError("{}: {}".format(
            channel["name"], backends._connection_error(exc)), 502)
    if response.status >= 400:
        try:
            payload = await response.json()
        except Exception:
            payload = {}
        message = (payload or {}).get("error") or \
            "{} returned {}".format(channel["name"], response.status)
        status = response.status
        response.release()
        raise NodeError(message, status)
    return response


async def _fetch_manifest(channel: dict, prefix: str, *,
                          accept_reset: bool = False) -> dict:
    response = await _open_stream(
        channel, "GET", prefix + "/manifest",
        extra_headers=({workspace_sync.MIRROR_RESET_HEADER: "1"}
                       if accept_reset else None))
    entries = {}
    skipped = {}
    reset_id = None
    try:
        header = await workspace_sync.read_frame(response.content)
        if not isinstance(header, dict):
            raise NodeError("invalid manifest stream", 502)
        if header.get("reset_id") is not None:
            reset_id = header.get("reset_id")
            if not accept_reset or not isinstance(reset_id, str) or \
                    not 16 <= len(reset_id) <= 128:
                raise NodeError("invalid mirror reset marker", 502)
        while True:
            frame = await workspace_sync.read_frame(response.content)
            if frame is None:
                raise NodeError("manifest stream ended early", 502)
            if frame.get("end"):
                value = frame.get("skipped")
                skipped = value if isinstance(value, dict) else {}
                break
            path = frame.pop("p", None)
            workspace_sync.validate_relpath(path)
            entries[path] = frame
            if len(entries) > workspace_sync.MAX_TREE_ENTRIES:
                raise NodeError("manifest exceeds the entry limit", 507)
    finally:
        response.release()
    return {"entries": entries, "skipped": skipped,
            "reset_id": reset_id}


async def _recover_lease(link: dict, ws_channel: dict) -> str:
    """Reacquire an expired/restored provider lease for the same root."""
    reply = await _request_json(
        ws_channel, "POST", "workspace/leases",
        {"root": link["root"], "reuse": True})
    lease = reply.get("lease") if isinstance(reply, dict) else None
    lease_id = lease.get("id") if isinstance(lease, dict) else None
    lease_root = str(lease.get("root") or "") if isinstance(lease, dict) else ""
    if not isinstance(lease_id, str) or not lease_id or \
            lease_root != str(link["root"]):
        raise NodeError("{} returned an invalid replacement lease".format(
            ws_channel["name"]), 502)
    _update_link(int(link["id"]), lease=lease_id)
    return lease_id


async def _transfer(src: dict, src_prefix: str, dst: dict, dst_prefix: str,
                    ops: list) -> list:
    """Relay one bounded batch: a fetch stream from the source node piped
    frame-by-frame into an apply stream on the destination node. Bytes pass
    through this process in CHUNK-sized pieces; nothing is buffered whole."""
    if not ops:
        return []
    write_ops = [op for op in ops if op["op"] == "write"]
    local_results = []
    fetch_response = None
    if write_ops:
        fetch_response = await _open_stream(
            src, "POST", src_prefix + "/fetch",
            json_body={"paths": [op["p"] for op in write_ops]})

    async def payload():
        stream = fetch_response.content if fetch_response is not None else None
        for op in ops:
            if op["op"] != "write":
                yield workspace_sync.encode_frame(op)
                continue
            item = await workspace_sync.read_frame(stream)
            if not isinstance(item, dict) or item.get("p") != op["p"]:
                raise workspace_sync.SyncError("fetch stream out of order")
            if item.get("missing"):
                local_results.append({"p": op["p"], "ok": False,
                                      "conflict": True,
                                      "reason": "gone from the source"})
                continue
            size = int(item.get("s") or 0)
            # ship the source's freshest bytes under the plan's CAS base
            yield workspace_sync.encode_frame({
                "op": "write", "p": op["p"], "s": size,
                "h": item.get("h"), "m": item.get("m", op.get("m")),
                "mt": item.get("mt"), "base": op.get("base"),
                "nocas": bool(op.get("nocas"))})
            if size:
                async for block in workspace_sync.read_exact(stream, size):
                    yield block
            trailer = await workspace_sync.read_frame(stream)
            yield workspace_sync.encode_frame({
                "p": op["p"],
                "ok": isinstance(trailer, dict) and trailer.get("ok") is True})
        yield workspace_sync.encode_frame({"end": True})

    try:
        response = await _open_stream(
            dst, "POST", dst_prefix + "/apply", data=payload(),
            content_type=workspace_sync.CONTENT_TYPE)
        try:
            result = await response.json()
        finally:
            response.release()
    finally:
        if fetch_response is not None:
            fetch_response.close()
    results = result.get("results") if isinstance(result, dict) else None
    if not isinstance(results, list):
        raise NodeError("invalid apply reply from {}".format(dst["name"]), 502)
    combined = results + local_results
    expected = sorted(str(op.get("p") or "") for op in ops)
    actual = []
    for item in combined:
        if not isinstance(item, dict) or not isinstance(item.get("p"), str) or \
                type(item.get("ok")) is not bool:
            raise NodeError("invalid apply result from {}".format(
                dst["name"]), 502)
        actual.append(item["p"])
    if sorted(actual) != expected:
        raise NodeError("incomplete apply result from {}".format(
            dst["name"]), 502)
    return combined


def _open_keep_parent(link: dict, path: str) -> tuple:
    """Open the private keep directory for ``path`` without following links.

    Every newly created directory is fsync'd through its parent before the
    next component is opened. The caller owns the returned descriptor.
    """
    workspace_sync.validate_relpath(path)
    uid = str(link.get("uid") or "")
    # Reuse the mirror namespace validator: link ids and mirror ids deliberately
    # share the same private, lowercase-alphanumeric shape.
    workspace_sync.mirror_base(uid)
    generation = str(int(link["generation"]) + 1)
    parts = ["workspace", "keeps", uid, generation] + path.split("/")[:-1]
    fd = os.open(os.path.realpath(config.DATA_DIR),
                 os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY |
                            os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, path.rsplit("/", 1)[-1]
    except Exception:
        os.close(fd)
        raise


def _write_fd(fd: int, block: bytes) -> None:
    view = memoryview(block)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while preserving conflict data")
        view = view[written:]


async def _keep_loser(link: dict, entry, path: str, channel: dict,
                      prefix: str) -> None:
    """Durably preserve a losing regular file before resolution can proceed.

    Missing entries and non-files contain no losing file bytes to retain. A
    regular file is streamed into a private adjacent temp, checked against the
    manifest entry and fetch trailer, fsync'd, and atomically promoted. Any
    failure aborts the whole reconcile before its winning operation is sent.
    """
    if not isinstance(entry, dict) or entry.get("t") != "f":
        return
    workspace_sync.validate_relpath(path)
    expected_size = entry.get("s")
    expected_hash = entry.get("h")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or \
            expected_size < 0 or not isinstance(expected_hash, str):
        raise NodeError(
            "could not preserve losing file {}: invalid manifest entry; "
            "the conflict was not resolved".format(path), 502)
    try:
        response = await _open_stream(channel, "POST", prefix + "/fetch",
                                      json_body={"paths": [path]})
    except NodeError as exc:
        raise NodeError(
            "could not preserve losing file {}: {}; the conflict was not "
            "resolved".format(path, exc), exc.status) from exc
    parent_fd = None
    temp_fd = None
    temp_name = ".puppy-keep-tmp-{}".format(secrets.token_hex(8))
    try:
        item = await workspace_sync.read_frame(response.content)
        if not isinstance(item, dict) or item.get("p") != path or \
                item.get("missing") or item.get("s") != expected_size or \
                item.get("h") != expected_hash:
            raise workspace_sync.SyncError(
                "the losing file changed after it was scanned")
        parent_fd, leaf = _open_keep_parent(link, path)
        temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        digest = hashlib.sha256()
        async for block in workspace_sync.read_exact(
                response.content, expected_size):
            digest.update(block)
            _write_fd(temp_fd, block)
        trailer = await workspace_sync.read_frame(response.content)
        end = await workspace_sync.read_frame(response.content)
        if not isinstance(trailer, dict) or trailer.get("p") != path or \
                trailer.get("ok") is not True or not isinstance(end, dict) or \
                end.get("end") is not True or digest.hexdigest() != expected_hash:
            raise workspace_sync.SyncError(
                "the losing file could not be verified")
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        os.replace(temp_name, leaf, src_dir_fd=parent_fd,
                   dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except Exception as exc:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if parent_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except OSError:
                pass
        status = exc.status if isinstance(exc, NodeError) else \
            507 if isinstance(exc, OSError) else 409
        raise NodeError(
            "could not preserve losing file {}: {}; the conflict was not "
            "resolved".format(path, exc), status) from exc
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        response.release()


# ---- reconcile ----

def _public_conflict(conflict: dict) -> dict:
    def describe(entry):
        if entry is None:
            return "missing"
        return {"f": "file", "d": "directory", "l": "symlink"}.get(
            entry.get("t"), "special")
    return {"path": conflict["path"],
            "workspace": describe(conflict.get("workspace")),
            "session": describe(conflict.get("mirror"))}


async def _keepalive(exec_channel: dict, session_id: int) -> None:
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        try:
            await _request_json(
                exec_channel, "POST",
                "sessions/{}/workspace/grant".format(session_id),
                {"progress": True})
        except Exception:
            pass   # the barrier's own quiet-limit is the real timeout


async def run_reconcile(link_id: int) -> dict:
    """One full three-way reconcile of a link; serialized per link."""
    async with _lock_for(link_id):
        link = get_link(link_id)
        if link is None:
            return {"ok": False, "error": "workspace link is gone"}
        exec_channel = backends.node_channel(int(link["exec_backend"]))
        ws_channel = backends.node_channel(int(link["ws_backend"]))
        if exec_channel is None or ws_channel is None:
            _update_link(link_id, state="error",
                         last_error="a linked backend is no longer paired")
            _broadcast_links()
            return {"ok": False, "error": "a linked backend is no longer paired"}
        _update_link(link_id, state="syncing")
        _broadcast_links()
        mirror_prefix = "sessions/{}/workspace".format(link["session_id"])
        ws_prefix = "workspace/leases/{}".format(link["lease"])
        keepalive = asyncio.ensure_future(
            _keepalive(exec_channel, int(link["session_id"])))
        _active_reconciles.add(int(link_id))
        try:
            try:
                workspace_manifest = await _fetch_manifest(ws_channel, ws_prefix)
            except NodeError as exc:
                if exc.status != 404:
                    raise
                lease_id = await _recover_lease(link, ws_channel)
                ws_prefix = "workspace/leases/{}".format(lease_id)
                workspace_manifest = await _fetch_manifest(ws_channel, ws_prefix)
            mirror_manifest = await _fetch_manifest(
                exec_channel, mirror_prefix, accept_reset=True)
            reset_id = mirror_manifest.get("reset_id")
            base = {} if reset_id else _load_base(link["uid"])
            plan = (workspace_sync.plan_authoritative_pull(
                workspace_manifest["entries"], mirror_manifest["entries"])
                if reset_id else workspace_sync.plan_reconcile(
                    base, workspace_manifest["entries"],
                    mirror_manifest["entries"]))

            resolutions = link["resolutions"]
            unresolved = []
            used = set()
            attempted = {}
            for conflict in plan["conflicts"]:
                path = conflict["path"]
                choice = resolutions.get(path)
                if choice == "workspace":
                    await _keep_loser(link, conflict.get("mirror"), path,
                                      exec_channel, mirror_prefix)
                    attempted[path] = conflict
                    plan["pull"].extend(workspace_sync.ops_for(
                        path, conflict.get("mirror"), conflict.get("workspace")))
                    if conflict.get("workspace") is not None:
                        plan["base"][path] = conflict["workspace"]
                    else:
                        plan["base"].pop(path, None)
                    used.add(path)
                elif choice == "session":
                    await _keep_loser(link, conflict.get("workspace"), path,
                                      ws_channel, ws_prefix)
                    attempted[path] = conflict
                    plan["push"].extend(workspace_sync.ops_for(
                        path, conflict.get("workspace"), conflict.get("mirror")))
                    if conflict.get("mirror") is not None:
                        plan["base"][path] = conflict["mirror"]
                    else:
                        plan["base"].pop(path, None)
                    used.add(path)
                else:
                    unresolved.append(conflict)
            workspace_sync.order_ops(plan["pull"])
            workspace_sync.order_ops(plan["push"])

            pull_results = []
            for batch in workspace_sync.split_batches(plan["pull"]):
                pull_results += await _transfer(
                    ws_channel, ws_prefix, exec_channel, mirror_prefix, batch)
            push_results = []
            for batch in workspace_sync.split_batches(plan["push"]):
                push_results += await _transfer(
                    exec_channel, mirror_prefix, ws_channel, ws_prefix, batch)

            # anything that failed keeps its OLD base entry, so the next
            # reconcile sees it changed and tries (or reports) again
            next_base = plan["base"]
            failed = {item["p"] for item in pull_results + push_results
                      if not item.get("ok")}
            for path in failed:
                if path in base:
                    next_base[path] = base[path]
                else:
                    next_base.pop(path, None)
            _save_base(link["uid"], next_base)

            failed_conflicts = [attempted[path] for path in sorted(used & failed)]
            visible_conflicts = unresolved + failed_conflicts
            clean = not visible_conflicts and not failed
            if reset_id and clean:
                await _request_json(
                    exec_channel, "POST", mirror_prefix + "/reset-ack",
                    {"reset_id": reset_id})
            completed_resolutions = used - failed
            remaining = {path: value for path, value in resolutions.items()
                         if path not in completed_resolutions}
            failure_parts = []
            for direction, results in (("pull", pull_results),
                                       ("push", push_results)):
                for item in results:
                    if item.get("ok"):
                        continue
                    reason = item.get("reason")
                    detail = str(reason).strip() if reason is not None else ""
                    failure_parts.append("{} {}: {}".format(
                        direction, item["p"], detail or "not applied"))
            failure_error = "workspace transfer incomplete: {}".format(
                "; ".join(failure_parts))[:500] if failure_parts else ""
            fields = {
                "state": "error" if failed else
                         "conflict" if visible_conflicts else "ok",
                # Generation and last_sync_at describe the same completed
                # pass in the UI; a partial/failed pass advances neither.
                "generation": int(link["generation"]) + (0 if failed else 1),
                "conflicts": json.dumps(
                    [_public_conflict(c) for c in visible_conflicts],
                                     separators=(",", ":")),
                "resolutions": json.dumps(remaining, separators=(",", ":")),
                "last_error": failure_error,
            }
            if not failed:
                fields["last_sync_at"] = time.time()
            _update_link(link_id, **fields)
            if failed:
                _retry_after[link_id] = time.monotonic() + RETRY_SECONDS
            else:
                _retry_after.pop(link_id, None)
            pulled = sum(1 for item in pull_results if item.get("ok"))
            pushed = sum(1 for item in push_results if item.get("ok"))
            if pulled or pushed or visible_conflicts or failed:
                log.info("link %s reconciled: %d pulled, %d pushed, "
                         "%d conflict(s), %d retry", link_id, pulled, pushed,
                         len(visible_conflicts), len(failed))
            return {"ok": not failed, "clean": clean,
                    "conflicts": len(visible_conflicts),
                    "pulled": pulled, "pushed": pushed,
                    "retry": len(failed), **(
                        {"error": failure_error} if failed else {})}
        except (NodeError, workspace_sync.SyncError) as exc:
            message = str(exc)
            _update_link(link_id, state="error", last_error=message[:500])
            _retry_after[link_id] = time.monotonic() + RETRY_SECONDS
            return {"ok": False, "error": message}
        except Exception as exc:
            log.exception("reconcile failed for link %s", link_id)
            _update_link(link_id, state="error", last_error=str(exc)[:500])
            _retry_after[link_id] = time.monotonic() + RETRY_SECONDS
            return {"ok": False, "error": str(exc)}
        finally:
            keepalive.cancel()
            _active_reconciles.discard(int(link_id))
            _broadcast_links()


# ---- barrier servicing ----

async def _send_grant(exec_bid: int, session_id: int, payload: dict) -> None:
    channel = backends.node_channel(exec_bid)
    if channel is None:
        return
    try:
        await _request_json(
            channel, "POST",
            "sessions/{}/workspace/grant".format(session_id), payload)
    except NodeError as exc:
        # a 409 simply means the barrier moved on (timeout, interrupt)
        if exc.status != 409:
            log.warning("grant delivery to session %s failed: %s",
                        session_id, exc)
    except Exception as exc:
        log.warning("grant delivery to session %s failed: %s", session_id, exc)


async def service_session(exec_bid: int, session_id: int, phase: str) -> dict:
    """Reconcile the link behind one session and answer its barrier."""
    link = get_link_by_session(exec_bid, session_id)
    if link is None:
        summary = {"ok": False,
                   "error": "no workspace link exists on this controller"}
        if phase in ("pre", "post"):
            await _send_grant(exec_bid, session_id, {
                "phase": phase, "ok": False,
                "error": summary["error"]})
        return summary
    summary = await run_reconcile(link["id"])
    await _send_grant(exec_bid, session_id, {
        "phase": phase,
        "ok": bool(summary.get("ok")),
        "clean": bool(summary.get("clean")),
        "conflicts": int(summary.get("conflicts") or 0),
        "error": str(summary.get("error") or "")})
    return summary


def _schedule_service(exec_bid: int, session_id: int, phase: str) -> None:
    if _snapshot_paused:
        return
    key = (exec_bid, session_id, phase)
    if key in _service_inflight:
        return
    _service_inflight.add(key)

    async def run():
        try:
            await service_session(exec_bid, session_id, phase)
        except Exception:
            log.exception("workspace service failed for session %s/%s",
                          exec_bid, session_id)
        finally:
            _service_inflight.discard(key)

    asyncio.ensure_future(run())


def _hub_hook(session_id: int, phase: str) -> None:
    """Local linked sessions announce barriers in-process."""
    if phase in ("pre", "post") and \
            get_link_by_session(0, session_id) is not None:
        _schedule_service(0, session_id, phase)


# ---- watchers and sweep ----

def ensure_watchers() -> None:
    needed = {int(link["exec_backend"]) for link in list_links()
              if int(link["exec_backend"]) != 0}
    for bid in list(_watch_tasks):
        if bid not in needed:
            _watch_tasks.pop(bid).cancel()
    for bid in needed:
        task = _watch_tasks.get(bid)
        if task is None or task.done():
            _watch_tasks[bid] = asyncio.ensure_future(_watch_backend(bid))


def _scan_sessions(bid: int, sessions: list) -> None:
    for session in sessions:
        if not isinstance(session, dict):
            continue
        phase = str(session.get("workspace_phase") or "")
        if session.get("workspace") and phase in ("pre", "post"):
            try:
                _schedule_service(bid, int(session["id"]), phase)
            except (TypeError, ValueError):
                continue


async def _watch_backend(bid: int) -> None:
    """Follow one execution node's updates socket for barrier phases."""
    backoff = 2.0
    while True:
        channel = backends.node_channel(bid)
        if channel is None or not channel.get("urls"):
            await asyncio.sleep(30)
            continue
        target = "{}/api/ws/updates".format(channel["urls"][0])
        ws_url = "ws" + target[4:] if target.startswith("http") else target
        socket = None
        try:
            socket = await backends.client().ws_connect(
                ws_url, headers=_headers(channel), heartbeat=30,
                max_msg_size=1 << 22, ssl=channel["ssl"])
            backoff = 2.0
            await _poll_backend_sessions(bid)
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    if message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
                    continue
                try:
                    data = json.loads(message.data)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("type") == "sessions":
                    _scan_sessions(bid, data.get("sessions") or [])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("workspace watcher for backend %s: %s", bid, exc)
        finally:
            if socket is not None and not socket.closed:
                try:
                    await socket.close()
                except Exception:
                    pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


async def _poll_backend_sessions(bid: int) -> None:
    channel = backends.node_channel(bid)
    if channel is None:
        return
    try:
        data = await _request_json(channel, "GET", "sessions")
    except NodeError:
        return
    sessions = data.get("sessions")
    if isinstance(sessions, list):
        _scan_sessions(bid, sessions)


async def _sweep_once() -> None:
    """Retry errored links, finish parked dirty mirrors, reap orphans."""
    if _snapshot_paused:
        return
    links = list_links()
    if not links:
        return
    now = time.monotonic()
    by_node = {}
    for link in links:
        by_node.setdefault(int(link["exec_backend"]), []).append(link)
    for exec_bid, node_links in by_node.items():
        channel = backends.node_channel(exec_bid)
        if channel is None:
            continue
        try:
            data = await _request_json(channel, "GET", "sessions")
        except NodeError:
            continue   # unreachable node: nothing to decide from here
        sessions = {}
        for session in (data.get("sessions") or []):
            if isinstance(session, dict) and "id" in session:
                sessions[int(session["id"])] = session
        for link in node_links:
            link_id = int(link["id"])
            session = sessions.get(int(link["session_id"]))
            if session is None:
                log.info("link %s session is gone; releasing its lease",
                         link_id)
                try:
                    await delete_link(link_id, with_session=False)
                except NodeError as exc:
                    log.warning("orphan link %s cleanup failed: %s",
                                link_id, exc)
                continue
            phase = str(session.get("workspace_phase") or "")
            if phase in ("pre", "post"):
                _schedule_service(exec_bid, int(link["session_id"]), phase)
                continue
            if _retry_after.get(link_id, 0) > now:
                continue
            if str(session.get("status") or "") != "running" and \
                    (session.get("ws_dirty") or
                     link["state"] in ("init", "error")):
                _schedule_service(exec_bid, int(link["session_id"]), "idle")


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("workspace link sweep failed")


# ---- lifecycle orchestration ----

def snapshot_blockers() -> list:
    return (["workspace reconciliation in progress"]
            if _active_reconciles or _service_inflight else [])


def pause_for_snapshot() -> None:
    global _snapshot_paused
    _snapshot_paused = True


def resume_after_snapshot(restored: bool = False) -> None:
    """Resume background servicing, discarding cache state after a restore."""
    global _snapshot_paused
    if restored:
        _locks.clear()
        _retry_after.clear()
    _snapshot_paused = False
    ensure_watchers()
    if restored:
        asyncio.ensure_future(_startup_sweep())

async def create_linked_session(body: dict) -> dict:
    exec_bid = int(body.get("backend") or 0)
    ws_bid = int(body.get("workspace_backend") or 0)
    root = str(body.get("root") or "").strip()
    if not root:
        raise NodeError("workspace path is required", 400)
    exec_channel = backends.node_channel(exec_bid)
    ws_channel = backends.node_channel(ws_bid)
    if exec_channel is None or ws_channel is None:
        raise NodeError("unknown backend", 404)
    session_fields = {}
    for key in ("engine", "name", "model", "effort", "permission_mode", "color"):
        if body.get(key) is not None:
            session_fields[key] = body.get(key)

    if body.get("mkdir"):
        await _request_json(ws_channel, "POST", "fs/mkdir", {"path": root})

    exec_ping = await _request_json(exec_channel, "GET", "ping")
    ws_ping = await _request_json(ws_channel, "GET", "ping")
    same_node = exec_bid == ws_bid or (
        exec_ping.get("node_uuid") and
        exec_ping.get("node_uuid") == ws_ping.get("node_uuid"))
    if same_node:
        # one machine: a mirror would only copy a directory onto itself
        created = await _request_json(exec_channel, "POST", "sessions", {
            **session_fields, "workspace_kind": "directory", "cwd": root,
            "mkdir": bool(body.get("mkdir"))})
        return {"ok": True, "bid": exec_bid, "session": created.get("session"),
                "same_node": True}

    if protocol.WORKSPACE_MIRROR_CAPABILITY not in \
            (exec_ping.get("capabilities") or []):
        raise NodeError("{} cannot host linked sessions yet - upgrade it".format(
            exec_channel["name"]), 409)
    if protocol.WORKSPACE_PROVIDER_CAPABILITY not in \
            (ws_ping.get("capabilities") or []):
        raise NodeError("{} cannot share workspaces yet - upgrade it".format(
            ws_channel["name"]), 409)

    lease_reply = await _request_json(ws_channel, "POST", "workspace/leases",
                                      {"root": root})
    lease = lease_reply.get("lease") if isinstance(lease_reply, dict) else None
    if not isinstance(lease, dict) or not lease.get("id"):
        raise NodeError("{} returned an invalid lease".format(
            ws_channel["name"]), 502)
    resolved_root = str(lease.get("root") or root)
    uid = workspace_sync.new_mirror_uid()
    label = "{}:{}".format(ws_channel["name"], resolved_root)
    try:
        created = await _request_json(exec_channel, "POST", "sessions", {
            **session_fields, "workspace_kind": "directory",
            "workspace": {"uid": uid, "root": resolved_root,
                          "node": ws_channel["name"], "label": label}})
        session = created.get("session") or {}
        session_id = int(session.get("id"))
    except Exception:
        try:
            await _request_json(ws_channel, "DELETE",
                                "workspace/leases/{}".format(lease["id"]))
        except Exception as exc:
            log.warning("lease rollback failed on %s: %s",
                        ws_channel["name"], exc)
        raise
    link_id = db.execute(
        "INSERT INTO workspace_links(uid,exec_backend,session_id,ws_backend,"
        "root,lease,state,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, exec_bid, session_id, ws_bid, resolved_root,
         str(lease["id"]), "init", time.time()))
    ensure_watchers()
    _broadcast_links()
    _schedule_service(exec_bid, session_id, "idle")   # warm the first clone
    log.info("workspace link %s created: session %s on %s <-> %s", link_id,
             session_id, exec_channel["name"], label)
    return {"ok": True, "bid": exec_bid, "session": session,
            "link": next((item for item in public_links()
                          if item["id"] == link_id), None),
            "same_node": False}


async def delete_link(link_id: int, with_session: bool) -> dict:
    link = get_link(link_id)
    if link is None:
        raise NodeError("unknown workspace link", 404)
    exec_channel = backends.node_channel(int(link["exec_backend"]))
    ws_channel = backends.node_channel(int(link["ws_backend"]))
    if with_session and exec_channel is not None:
        try:
            await _request_json(exec_channel, "DELETE",
                                "sessions/{}".format(link["session_id"]))
        except NodeError as exc:
            if exc.status != 404:
                raise   # e.g. a running turn: the user must stop it first
    if ws_channel is not None:
        try:
            await _request_json(ws_channel, "DELETE",
                                "workspace/leases/{}".format(link["lease"]))
        except Exception as exc:
            log.warning("lease release failed for link %s: %s", link_id, exc)
    db.execute("DELETE FROM workspace_links WHERE id=?", (link_id,))
    _drop_link_files(link["uid"])
    _locks.pop(link_id, None)
    _retry_after.pop(link_id, None)
    ensure_watchers()
    _broadcast_links()
    return {"ok": True}


# ---- API ----

def _error_response(exc: Exception) -> web.Response:
    status = getattr(exc, "status", 500)
    return web.json_response({"error": str(exc)}, status=status)


async def h_list(request: web.Request):
    return web.json_response({"links": public_links()})


async def h_create_session(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid request"}, status=400)
    try:
        return web.json_response(await create_linked_session(body))
    except (NodeError, workspace_sync.SyncError) as exc:
        return _error_response(exc)


async def h_sync(request: web.Request):
    link = get_link(int(request.match_info["lid"]))
    if link is None:
        return web.json_response({"error": "unknown workspace link"}, status=404)
    summary = await service_session(int(link["exec_backend"]),
                                    int(link["session_id"]), "idle")
    fresh = get_link(int(link["id"]))
    if not summary.get("ok"):
        return web.json_response(
            {"error": summary.get("error") or
                      (fresh and fresh["last_error"]) or "sync failed",
             "link": next((item for item in public_links()
                           if item["id"] == link["id"]), None)}, status=502)
    return web.json_response({
        "ok": True, "clean": bool(summary.get("clean")),
        "conflicts": int(summary.get("conflicts") or 0),
        "link": next((item for item in public_links()
                      if item["id"] == link["id"]), None)})


async def h_resolve(request: web.Request):
    link = get_link(int(request.match_info["lid"]))
    if link is None:
        return web.json_response({"error": "unknown workspace link"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid request"}, status=400)
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, dict) or not choices:
        return web.json_response({"error": "conflict choices are required"},
                                 status=400)
    conflict_paths = {item.get("path") for item in link["conflicts"]}
    resolutions = dict(link["resolutions"])
    for path, choice in choices.items():
        if choice not in ("workspace", "session"):
            return web.json_response(
                {"error": "choices must be 'workspace' or 'session'"},
                status=400)
        if path not in conflict_paths:
            return web.json_response(
                {"error": "{} is not currently in conflict".format(path)},
                status=409)
        resolutions[path] = choice
    _update_link(int(link["id"]),
                 resolutions=json.dumps(resolutions, separators=(",", ":")))
    _schedule_service(int(link["exec_backend"]), int(link["session_id"]),
                      "idle")
    return web.json_response({"ok": True})


async def h_delete(request: web.Request):
    with_session = request.query.get("with_session") == "1"
    try:
        return web.json_response(
            await delete_link(int(request.match_info["lid"]), with_session))
    except NodeError as exc:
        return _error_response(exc)


# ---- lifecycle ----

async def start_worker(app: web.Application) -> None:
    global _sweep_task
    runner.register_workspace_hook(_hub_hook)
    ensure_watchers()
    if _sweep_task is None or _sweep_task.done():
        _sweep_task = asyncio.ensure_future(_sweep_loop())
    asyncio.ensure_future(_startup_sweep())


async def _startup_sweep() -> None:
    try:
        await _sweep_once()
    except Exception:
        log.exception("startup workspace sweep failed")


async def stop_worker(_app: web.Application = None) -> None:
    global _sweep_task
    tasks = list(_watch_tasks.values())
    _watch_tasks.clear()
    if _sweep_task is not None:
        tasks.append(_sweep_task)
        _sweep_task = None
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def register(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/workspaces", h_list)
    r.add_post("/api/workspaces/sessions", h_create_session)
    r.add_post("/api/workspaces/{lid:\\d+}/sync", h_sync)
    r.add_post("/api/workspaces/{lid:\\d+}/resolve", h_resolve)
    r.add_delete("/api/workspaces/{lid:\\d+}", h_delete)
