"""Close missing tool results when their owning engine can no longer reply.

This is lifecycle cleanup, not a transcript migration: existing events stay
intact and the normal tool_result vocabulary records the unknown outcome.
Never infer completion from elapsed time or run a missing call again.
"""
from __future__ import annotations

import json
import logging
import time

from puppy import db

log = logging.getLogger("puppy.tool_calls")
TURN_ENDED = "The turn ended before this tool reported a result."
RESTARTED = "Puppy restarted before this tool reported a result."
RESTORED = "Puppy state was restored without a result for this tool."


class PendingTools:
    """Only native call identities and names; never retain commands/output."""

    def __init__(self) -> None:
        self.calls = {}

    def observe(self, kind: str, data: dict) -> None:
        if not isinstance(data, dict):
            return
        ident = data.get("tool_use_id")
        if not isinstance(ident, str) or not ident:
            return
        if kind == "tool_use":
            self.calls[ident] = str(data.get("tool") or "tool")
        elif kind == "tool_result" or (kind == "info" and
                data.get("subtype") == "task" and
                data.get("status") in ("completed", "failed", "stopped")):
            self.calls.pop(ident, None)

    def results(self, reason: str):
        for ident, tool in self.calls.items():
            yield {"tool_use_id": ident, "tool": tool, "is_error": True,
                   "interrupted": True,
                   "content": reason + " Its outcome is unknown."}


def recover(connection, reason: str) -> int:
    """Append missing results with no engines running, in the caller's transaction.

    Stream one session at a time so neither transcript size nor tool output
    accumulates in memory. Read all its results before closing anything: a
    native result or background ending may arrive after another turn boundary.
    Repeating this after a crash is harmless, including a crash during cleanup.
    Keep session ordering/timestamps, prompts, queues and actual outcomes intact.
    """
    count = 0
    for row in connection.execute("SELECT id FROM sessions").fetchall():
        sid = row[0]
        pending = PendingTools()
        for kind, payload in connection.execute(
                "SELECT kind,payload FROM events WHERE session_id=? "
                "AND kind IN ('tool_use','tool_result','info') ORDER BY seq", (sid,)):
            pending.observe(kind, json.loads(payload))
        if not pending.calls:
            continue
        seq = connection.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?", (sid,)).fetchone()[0]
        for data in pending.results(reason):
            seq += 1
            connection.execute(
                "INSERT INTO events(session_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (sid, seq, "tool_result", json.dumps(data), time.time()))
            count += 1
    return count


def recover_runtime() -> None:
    """Shared full/headless startup; run before accepting any engine work."""
    with db._lock:
        connection = db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            count = recover(connection, RESTARTED)
            connection.execute("UPDATE sessions SET status='idle' WHERE status!='idle'")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    if count:
        log.info("closed %s tool call(s) left without results", count)
