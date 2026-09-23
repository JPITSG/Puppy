"""OpenCode native compaction over its public HTTP API (probed on 1.18.30).

The runner owns one `opencode serve` process for this maintenance turn. Its
CLI readiness line gives the native-selected loopback endpoint; a per-turn
password never leaves this process's environment and HTTP client. There is
no database access, command dispatch, persistent server, or provider choice
in this adapter.

POST summarize's boolean is NOT its outcome: provider errors, cancellation
and empty summaries can all return true. Verify the new native messages,
after an acknowledged durable runner checkpoint and before reporting success.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import sys
from urllib.parse import quote, urlsplit

import aiohttp

from puppy.drivers.base import TurnTransport, end_probe

STARTUP_SECONDS = 20
REQUEST_SECONDS = 10
ABORT_SECONDS = 2
RESPONSE_BYTES = 8 * 1024 * 1024
PAGE_SIZE = 16
MAX_PAGES = 8
READY_PREFIX = "opencode server listening on "

# `serve` ignores stdin, unlike ACP. This stdlib-only child supervisor owns
# the same detached process group as the runner and watches its stdin pipe.
# EOF also arrives when Puppy is SIGKILLed, so neither the HTTP server nor a
# vendor launcher child can survive its owner. Running fixed code with -c
# works from the headless zipapp too, without importing Puppy in the project
# directory or putting a shell, temporary script or credentials in argv.
_STDIN_GUARD = """import os, select, signal, subprocess, sys
if os.getpgrp() != os.getpid():
    raise SystemExit('maintenance process requires its own process group')
stopping = False
def stop(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
child = None
try:
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    while child.poll() is None and not stopping:
        if select.select([0], [], [], .1)[0] and not os.read(0, 4096):
            break
finally:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.killpg(os.getpgrp(), signal.SIGTERM)
    if child is not None:
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""


def _guarded_command(argv: list) -> list:
    # Isolated mode keeps a project's subprocess.py/select.py (or PYTHONPATH)
    # from replacing the supervisor's stdlib imports.
    return [sys.executable, "-I", "-c", _STDIN_GUARD] + argv


def serve_command(binary: str) -> list:
    return _guarded_command([binary, "serve", "--hostname", "127.0.0.1",
                             "--port", "0", "--mdns", "false"])


class CompactionError(RuntimeError):
    pass


class CompactionCancelled(CompactionError):
    pass


def endpoint(line: str) -> str:
    """Accept only serve's readiness report, never an arbitrary logged URL."""
    if not line.startswith(READY_PREFIX):
        return ""
    value = line[len(READY_PREFIX):].strip()
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and \
            parsed.port is not None and 0 < parsed.port < 65536 and \
            parsed.username is None and parsed.password is None and \
            parsed.path in ("", "/") and not parsed.query and not parsed.fragment
    except ValueError:
        valid = False
    if not valid:
        raise CompactionError("OpenCode reported an invalid local API endpoint")
    return value.rstrip("/")


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        math.isfinite(value) and value >= 0


def message_rows(value, sid: str) -> list:
    if not isinstance(value, list) or len(value) > PAGE_SIZE:
        raise CompactionError("OpenCode returned an invalid message page")
    seen = set()
    for row in value:
        info = row.get("info") if isinstance(row, dict) else None
        parts = row.get("parts") if isinstance(row, dict) else None
        if not isinstance(info, dict) or not isinstance(parts, list) or \
                info.get("sessionID") != sid or info.get("role") not in ("user", "assistant") or \
                not isinstance(info.get("id"), str) or not info["id"] or \
                info["id"] in seen or any(not isinstance(p, dict) for p in parts):
            raise CompactionError("OpenCode returned an invalid native message")
        seen.add(info["id"])
    return value


def verify_summary(rows: list, sid: str, model: dict) -> dict:
    """Require exactly this operation's manual request and completed summary."""
    requests = [row for row in rows if row["info"]["role"] == "user" and
                any(p.get("type") == "compaction" and p.get("auto") is False
                    for p in row["parts"])]
    if len(requests) != 1:
        raise CompactionError("OpenCode did not identify the requested compaction")
    request = requests[0]["info"]
    requested_model = request.get("model")
    if not isinstance(requested_model, dict) or any(
            requested_model.get(key) != value for key, value in model.items()):
        raise CompactionError("OpenCode compacted with an unexpected session model")
    summaries = [row for row in rows if row["info"].get("summary") is True and
                 row["info"].get("parentID") == request["id"] and
                 row["info"]["role"] == "assistant"]
    if len(rows) != 2 or len(summaries) != 1:
        raise CompactionError("OpenCode did not return an unambiguous compaction outcome")
    summary = summaries[0]
    info = summary["info"]
    error = info.get("error")
    if error is not None:
        if isinstance(error, dict) and error.get("name") == "MessageAbortedError":
            raise CompactionCancelled("OpenCode compaction was cancelled")
        detail = (error.get("data") or {}).get("message") if isinstance(error, dict) and \
            isinstance(error.get("data"), dict) else None
        raise CompactionError(str(detail or "OpenCode could not compact the context")[:2000])
    completed = (info.get("time") or {}).get("completed") if isinstance(info.get("time"), dict) else None
    if info.get("finish") != "stop" or not number(completed) or completed <= 0:
        raise CompactionError("OpenCode did not finish the context summary")
    if any(part.get("type") == "tool" for row in rows for part in row["parts"]):
        raise CompactionError("OpenCode unexpectedly ran a tool during compaction")
    if not any(part.get("type") == "text" and isinstance(part.get("text"), str) and
               part["text"].strip() for part in summary["parts"]):
        raise CompactionError("OpenCode produced an empty context summary")
    result = {
        "ok": True, "stop_reason": "end_turn", "compacted": True,
        "context_verified": True, "native_session_id": sid,
        "native_compaction_id": request["id"], "native_summary_id": info["id"],
    }
    # Native summary usage counts tokens, not the next turn's context
    # size. Unknown counters stay unknown; never label the summary input as
    # the reduced context or use a private database schema to recover it.
    tokens = info.get("tokens")
    cache = tokens.get("cache") if isinstance(tokens, dict) else None
    if isinstance(cache, dict) and all(number(tokens.get(key)) for key in
            ("input", "output", "reasoning")) and all(number(cache.get(key)) for key in ("read", "write")):
        result.update(usage_scope="turn", usage={
            "input_tokens": int(tokens["input"]),
            "output_tokens": int(tokens["output"] + tokens["reasoning"]),
            "reasoning_output_tokens": int(tokens["reasoning"]),
            "cache_read_input_tokens": int(cache["read"]),
            "cache_creation_input_tokens": int(cache["write"]),
        })
    return result


class NativeCompaction(TurnTransport):
    def __init__(self, session: dict):
        self.sid = str(session.get("native_session_id") or "")
        self.cwd = session["cwd"]
        self.model = str(session.get("model") or "")
        self.password = secrets.token_urlsafe(32)
        self.url = ""
        self.http = None
        self.process = None
        self.task = None
        self.queue = None
        self.stop_event = None
        self.stopped = False
        self.mutating = False

    def environment(self):
        return {"OPENCODE_SERVER_PASSWORD": self.password,
                "OPENCODE_SERVER_USERNAME": "puppy"}

    def interrupt(self):
        self.stopped = True
        if self.stop_event is not None:
            self.stop_event.set()
        return True

    async def read_actions(self, process):
        if self.task is None:
            self.process = process
            self.queue = asyncio.Queue()
            self.stop_event = asyncio.Event()
            if self.stopped:
                self.stop_event.set()
            self.task = asyncio.create_task(self._run())
        return await self.queue.get()

    async def close(self):
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _cancellable(self, awaitable):
        work = asyncio.ensure_future(awaitable)
        stop = asyncio.create_task(self.stop_event.wait())
        dead = asyncio.create_task(self.process.wait())
        try:
            await asyncio.wait((work, stop, dead), return_when=asyncio.FIRST_COMPLETED)
            if self.stopped:
                raise CompactionCancelled("OpenCode compaction was cancelled")
            if work.done():
                return await work
            raise CompactionError("OpenCode exited before compaction completed")
        finally:
            for task in (work, stop, dead):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, stop, dead, return_exceptions=True)

    async def _endpoint(self):
        while True:
            line = await self.process.stdout.readline()
            if not line:
                raise CompactionError("OpenCode did not start its local maintenance API")
            if len(line) > 65536:
                raise CompactionError("OpenCode returned an oversized readiness line")
            found = endpoint(line.decode(errors="replace").strip())
            if found:
                return found

    async def _drain_stdout(self):
        # Only the startup report is a protocol input. Keep subsequent native
        # logging drained without buffering it or interpreting it as actions.
        while await self.process.stdout.read(65536):
            pass

    def _path(self, suffix=""):
        return "/session/" + quote(self.sid, safe="") + suffix

    async def _request(self, method, path, body=None, params=None, timeout=REQUEST_SECONDS):
        query = {"directory": self.cwd}
        query.update(params or {})
        async with self.http.request(
                method, self.url + path, json=body, params=query,
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            data = bytearray()
            async for block in response.content.iter_chunked(65536):
                data.extend(block)
                if len(data) > RESPONSE_BYTES:
                    raise CompactionError("OpenCode's response is too large to verify safely")
            if response.status != 200:
                raise CompactionError("OpenCode maintenance API returned HTTP {}".format(response.status))
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                raise CompactionError("OpenCode returned an invalid maintenance response")
            return value, response.headers.get("X-Next-Cursor", "")

    async def _page(self, before=""):
        params = {"limit": PAGE_SIZE}
        if before:
            params["before"] = before
        value, cursor = await self._request("GET", self._path("/message"), params=params)
        return message_rows(value, self.sid), cursor

    async def _new_messages(self, baseline):
        found = []
        seen = set()
        cursors = set()
        cursor = ""
        for _ in range(MAX_PAGES):
            rows, next_cursor = await self._page(cursor)
            for row in reversed(rows):
                mid = row["info"]["id"]
                if mid == baseline:
                    return list(reversed(found))
                if mid in seen:
                    raise CompactionError("OpenCode returned overlapping message pages")
                seen.add(mid)
                found.append(row)
            if not next_cursor or next_cursor in cursors:
                break
            cursors.add(next_cursor)
            cursor = next_cursor
        raise CompactionError("OpenCode's compaction could not be matched to the original history")

    async def _compact(self):
        native, _ = await self._cancellable(self._request("GET", self._path()))
        if not isinstance(native, dict) or native.get("id") != self.sid or \
                not isinstance(native.get("directory"), str) or \
                os.path.realpath(native["directory"]) != os.path.realpath(self.cwd):
            raise CompactionError("OpenCode's session does not match this working directory")
        if native.get("revert") is not None:
            raise CompactionError("Finish the pending native undo in OpenCode before compacting")
        rows, _ = await self._cancellable(self._page())
        if not rows:
            raise CompactionError("There is no native conversation to compact")
        if self.model:
            provider, separator, model_id = self.model.partition("/")
            selected = {"providerID": provider, "modelID": model_id} if separator else {}
        else:
            # OpenCode owns Default. Ask the session itself rather than choose
            # a provider/model or apply today's global default to old history.
            chosen = native.get("model")
            selected = {"providerID": chosen.get("providerID"), "modelID": chosen.get("id")} \
                if isinstance(chosen, dict) else {}
        if not selected or any(not isinstance(value, str) or not value for value in selected.values()):
            raise CompactionError("OpenCode did not report the session's selected model")
        baseline = rows[-1]["info"]["id"]
        ack = asyncio.get_running_loop().create_future()
        await self.queue.put([{"a": "context_checkpoint", "id": self.sid, "ack": ack}])
        await self._cancellable(ack)
        self.mutating = True
        post_error = None
        try:
            await self._cancellable(self._request(
                "POST", self._path("/summarize"), body=dict(selected, auto=False), timeout=None))
        except CompactionCancelled:
            raise
        except (CompactionError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # A lost reply may follow a committed summary. Read its actual
            # outcome; never retry a POST or accept the HTTP boolean as proof.
            post_error = exc
        new = await self._cancellable(self._new_messages(baseline))
        if not new and post_error:
            raise post_error
        return verify_summary(new, self.sid, selected)

    async def _run(self):
        drain = None
        result = None
        try:
            self.url = await self._cancellable(asyncio.wait_for(self._endpoint(), STARTUP_SECONDS))
            drain = asyncio.create_task(self._drain_stdout())
            async with aiohttp.ClientSession(
                    auth=aiohttp.BasicAuth("puppy", self.password), trust_env=False) as client:
                self.http = client
                try:
                    result = await self._compact()
                except CompactionCancelled:
                    if self.mutating:
                        try:
                            await self._request("POST", self._path("/abort"), timeout=ABORT_SECONDS)
                        except (CompactionError, aiohttp.ClientError, asyncio.TimeoutError):
                            pass  # the runner-owned process group is the backstop
                    raise
        except CompactionCancelled as exc:
            result = {"ok": False, "stop_reason": "cancelled", "error": str(exc)}
        except (CompactionError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            detail = str(exc).strip() if isinstance(exc, CompactionError) else \
                "OpenCode's maintenance API did not complete the request"
            result = {"ok": False, "stop_reason": "error", "error": detail[:2000]}
        except Exception:
            result = {"ok": False, "stop_reason": "error",
                      "error": "OpenCode compaction could not be verified"}
        finally:
            # This is the runner's detached process group, not a second
            # lifecycle. Reuse the bounded group reaper, including children
            # that outlive their leader, before publishing a final result.
            await end_probe(self.process)
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
            if result is not None:
                actions = [{"a": "event", "kind": "info", "data": {
                    "subtype": "compact", "text": "Context compacted"}}] if result["ok"] else []
                if not result["ok"] and result["stop_reason"] != "cancelled":
                    actions.append({"a": "event", "kind": "error", "data": {"text": result["error"]}})
                actions.append({"a": "result", "data": result})
                await self.queue.put(actions)
            await self.queue.put(None)
