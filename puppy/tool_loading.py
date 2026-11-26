"""Which of Puppy's own MCP tool sets a turn loads up front.

Claude Code defers MCP tools behind its tool search: the model sees their
names only, and looks a server's tools up before its first call (a ToolSearch
round trip). Every Puppy turn is a fresh CLI process resuming the
conversation, so a session that used the browser last turn looked its tools
up again: of the lookups in this node's history (the tool audit, 2026-11-26),
three in four were for a server the same session had already used in an
earlier turn. A server whose config says ``alwaysLoad`` has its tools sent
in full instead (probed on claude 2.1.332: "Equivalent to setting
defer_loading: false on the API").

Loading up front puts the server's schemas in the request's tools, which head
the prompt cache's prefix, so the set a session loads must change rarely:
every change rewrites that conversation's cache once. A server is loaded
when the session called its tools (or looked them up) within its last
RECENT_EVENTS events - a window that grows with use and lets a set go only
after a long stretch without it - or when the turn's own prompt names it
with one of the composer's mentions, an explicit request to use it now.
Nothing here is persisted: the events are the record. The window covered 87%
of the past repeat lookups; a few milliseconds per turn start on this node's
largest sessions, through the history pool.
"""
from __future__ import annotations

import json
import re
import sqlite3

from puppy import db, workers

# the events looked back over: the window the measurement chose
RECENT_EVENTS = 5000
# a Puppy server named in a tool name, whatever the engine's spelling:
# mcp__puppy_vnc__click (Claude Code), puppy_vnc.click (Codex), puppy_vnc_click
SERVER_RE = re.compile(r"puppy_[a-z]+")
# the composer's mentions, an explicit request to use that tool set now
MENTIONS = {
    "puppy_browser": re.compile(r"@(?:Browser [A-Z0-9]{4}\b|New browser\b)"),
    "puppy_terminal": re.compile(r"@(?:Terminal [A-Z0-9]{4}\b|New terminal\b)"),
    "puppy_vnc": re.compile(r"@(?:VNC|RDP)\b"),
    "puppy_spawn": re.compile(r"@Spawn\b"),
    "puppy_session": re.compile(r"@[Ss]ession\b"),
    "puppy_tasks": re.compile(r"@(?:Task-|All tasks\b|New task\b|Apply when done\b)"),
}

_json1 = None         # whether this SQLite has json_extract, learned on first use


def _names(value) -> set:
    return set(SERVER_RE.findall(value)) if isinstance(value, str) else set()


def recent_servers(session_id: int) -> set:
    """The Puppy servers this session's tool calls and tool lookups named
    within its last RECENT_EVENTS events."""
    global _json1
    top = db.query("SELECT max(seq) FROM events WHERE session_id=?", (session_id,))
    after = int((top[0][0] if top and top[0][0] is not None else 0) or 0) - RECENT_EVENTS
    found = set()
    if _json1 is not False:
        try:
            rows = db.query(
                "SELECT DISTINCT CASE WHEN json_valid(payload) THEN json_extract(payload, '$.tool') END, "
                "CASE WHEN json_valid(payload) AND json_extract(payload, '$.tool') = 'ToolSearch' "
                "THEN json_extract(payload, '$.input.query') END "
                "FROM events WHERE session_id=? AND seq>? AND kind='tool_use' "
                "AND instr(payload, 'puppy_') > 0", (session_id, after))
            _json1 = True
            for tool, query in rows:
                found |= _names(tool) | _names(query)
            return found
        except sqlite3.OperationalError as error:
            if "json" not in str(error).lower():
                raise
            _json1 = False
    for (payload,) in db.query(
            "SELECT payload FROM events WHERE session_id=? AND seq>? AND kind='tool_use' "
            "AND instr(payload, 'puppy_') > 0", (session_id, after)):
        try:
            data = json.loads(payload)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        found |= _names(data.get("tool"))
        if data.get("tool") == "ToolSearch" and isinstance(data.get("input"), dict):
            found |= _names(data["input"].get("query"))
    return found


def mentioned_servers(text) -> set:
    """The Puppy servers a prompt names with one of the composer's mentions."""
    if not isinstance(text, str):
        return set()
    return {name for name, pattern in MENTIONS.items() if pattern.search(text)}


async def mark(session_id: int, text, descriptors) -> list:
    """Mark the turn's bridge descriptors whose tools load up front
    (``always_load``): the servers the session used lately or the prompt
    names. Answers the names marked; a read that fails marks only what the
    prompt names, since deferring is merely the engine's default."""
    offered = [item for item in descriptors if item]
    if not offered:
        return []
    try:
        wanted = await workers.history.run(recent_servers, session_id)
    except Exception:
        wanted = set()
    wanted |= mentioned_servers(text)
    marked = []
    for item in offered:
        if item.get("name") in wanted:
            item["always_load"] = True
            marked.append(item["name"])
    return marked
