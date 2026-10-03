"""The prompt index: every prompt of a session, one line each.

The console's prompt gutter numbers a session's prompts and lists them, so it
needs each prompt's place, time, whether it was steered into a running turn,
and its first line - nothing more. A session of hundreds of prompts can hold
megabytes of prompt text (pasted logs, long specifications), so this reads a
bounded slice of each prompt straight out of SQLite and answers with one
short excerpt per prompt instead of the messages themselves. A session's
events are append-only and go only with the session, so a console that holds
the index asks for what follows it (``after_seq``); the answer's ``total``
lets it notice when its copy no longer adds up and read the whole index again.
"""
import json
import os
import sqlite3

from puppy import db, uploads

EXCERPT_LIMIT = 120   # characters of the line a prompt is listed by
SCAN_LIMIT = 4000     # characters of a prompt read to find that line
PAGE_LIMIT = 2000     # prompts per answer; a longer index is read in pages

_json1 = None         # whether this SQLite has json_extract, learned on first use


def _marker_name(line: str):
    """The file an attachment marker line names ("" when it names none), or
    None when the line is not a marker - the console's parseAttachmentMarker."""
    if line.startswith(uploads.ATTACH_IMAGE_PREFIX) and line.endswith(uploads.ATTACH_IMAGE_SUFFIX):
        return os.path.basename(line[len(uploads.ATTACH_IMAGE_PREFIX):-len(uploads.ATTACH_IMAGE_SUFFIX)])
    if not (line.startswith(uploads.ATTACH_FILE_PREFIX) and line.endswith(uploads.ATTACH_FILE_SUFFIX)):
        return None
    # "<path> (<name>, <size>)", where <name> is the path's own basename
    inner = line[len(uploads.ATTACH_FILE_PREFIX):-len(uploads.ATTACH_FILE_SUFFIX)]
    at = inner.find(" (")
    while at >= 0:
        path, tail = inner[:at], inner[at + 2:]
        name = os.path.basename(path)
        if path and tail.startswith(name + ", ") and tail.endswith(")"):
            return name
        at = inner.find(" (", at + 1)
    return None


def _bounded(text: str) -> str:
    return text if len(text) <= EXCERPT_LIMIT else text[:EXCERPT_LIMIT - 1].rstrip() + "…"


def excerpt(text) -> str:
    """The line a prompt is listed by: its first line that is not an
    attachment marker, whitespace collapsed and bounded - or, for a prompt of
    attachments alone, the names of its files. The console's promptExcerpt
    says the same for prompts it has on the page."""
    names = []
    for line in str(text or "")[:SCAN_LIMIT].split("\n"):
        name = _marker_name(line)
        if name is not None:
            if name:
                names.append(name)
            continue
        flat = " ".join(line.split())
        if flat:
            return _bounded(flat)
    return _bounded(", ".join(names))


def _rows(session_id: int, after_seq: int, limit: int) -> list:
    """(seq, created_at, steering, text) for the prompts after ``after_seq``,
    each text cut to SCAN_LIMIT inside SQLite where it can be."""
    global _json1
    if _json1 is not False:
        try:
            rows = db.query(
                "SELECT seq, created_at, "
                "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.steering') END, "
                "CASE WHEN json_valid(payload) AND json_type(payload, '$.text') = 'text' "
                "THEN substr(json_extract(payload, '$.text'), 1, ?) END "
                "FROM events WHERE session_id=? AND kind='user' AND seq>? ORDER BY seq LIMIT ?",
                (SCAN_LIMIT, session_id, after_seq, limit))
            _json1 = True
            return [(r[0], r[1], bool(r[2]), r[3] if isinstance(r[3], str) else "") for r in rows]
        except sqlite3.OperationalError as error:
            if "json" not in str(error).lower():
                raise
            _json1 = False
    out = []
    for r in db.query("SELECT seq, created_at, payload FROM events "
                      "WHERE session_id=? AND kind='user' AND seq>? ORDER BY seq LIMIT ?",
                      (session_id, after_seq, limit)):
        try:
            data = json.loads(r[2])
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        text = data.get("text")
        out.append((r[0], r[1], bool(data.get("steering")), text[:SCAN_LIMIT] if isinstance(text, str) else ""))
    return out


def read(session_id: int, after_seq: int = 0, limit: int = PAGE_LIMIT) -> dict:
    """One page of the index: ``prompts`` as [seq, ts, steered (0/1), excerpt]
    rows oldest first, ``more`` when the page did not reach the newest prompt,
    and ``total``, every prompt the session holds."""
    rows = _rows(session_id, after_seq, limit + 1)
    more = len(rows) > limit
    total = db.query_one("SELECT count(*) FROM events WHERE session_id=? AND kind='user'",
                         (session_id,))[0]
    return {
        "prompts": [[seq, round(float(ts), 3), 1 if steering else 0, excerpt(text)]
                    for seq, ts, steering, text in rows[:limit]],
        "total": int(total),
        "more": more,
    }
