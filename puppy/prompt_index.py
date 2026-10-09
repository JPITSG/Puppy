"""The prompt index: every prompt of a session, one line each.

The console's prompt gutter numbers a session's prompts and lists them, so it
needs each prompt's place, time, whether it was steered into a running turn,
its first line and where its turn ended - nothing more. A session of hundreds of prompts can hold
megabytes of prompt text (pasted logs, long specifications), so this reads a
bounded slice of each prompt straight out of SQLite and answers with one
short excerpt per prompt instead of the messages themselves. A session's
events are append-only and go only with the session, so a console that holds
the index asks for what follows it (``after_seq``); the answer's ``total``
lets it notice when its copy no longer adds up and read the whole index again.

Each prompt also names how its turn ended and where: the turn of a prompt is
everything up to the next prompt that was not steered into it, and it ends at
its first result that is not a session tool's (compaction, undo). A turn with
a result has its final answer - the last assistant message before that result,
or the result itself when the turn wrote none, or failed - and its outcome,
"ok" or "bad"; a turn without one ended where an interruption ("stopped") or
an error ("bad") says so, and one with neither has not ended yet (or the
process holding it died) and says "". Only the newest prompt's turn can still
change, so a console reads its row again and trusts every earlier one.
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


def _ends(session_id: int, low: int, high) -> list:
    """(seq, kind, flag, tool) for the events after ``low`` (and before
    ``high``) that answer a turn or end it: assistant messages, errors,
    results (flag: ``ok``; tool: the session tool a result belongs to) and
    interruptions (info rows, flag: whether the row is one). Nothing but a
    result's or an info row's few keys is read."""
    global _json1
    bound, args = ("", (session_id, low)) if high is None else (" AND seq<?", (session_id, low, high))
    kinds = "kind IN ('assistant','error','result','info')"
    if _json1 is not False:
        try:
            rows = db.query(
                "SELECT seq, kind, "
                "CASE WHEN kind='result' THEN CASE WHEN json_valid(payload) "
                "THEN json_extract(payload, '$.ok') END "
                "WHEN kind='info' AND instr(payload, 'interrupted') > 0 THEN CASE WHEN json_valid(payload) "
                "THEN json_extract(payload, '$.subtype') = 'interrupted' END END, "
                "CASE WHEN kind='result' AND json_valid(payload) THEN json_extract(payload, '$.tool') END "
                "FROM events WHERE session_id=? AND seq>?" + bound + " AND " + kinds + " ORDER BY seq", args)
            _json1 = True
            return [(r[0], r[1], bool(r[2]), bool(r[3])) for r in rows]
        except sqlite3.OperationalError as error:
            if "json" not in str(error).lower():
                raise
            _json1 = False
    out = []
    for r in db.query("SELECT seq, kind, CASE WHEN kind IN ('result','info') THEN payload END "
                      "FROM events WHERE session_id=? AND seq>?" + bound + " AND " + kinds +
                      " ORDER BY seq", args):
        data = {}
        if r[2] is not None and (r[1] == "result" or "interrupted" in r[2]):
            try:
                data = json.loads(r[2])
            except Exception:
                data = {}
            data = data if isinstance(data, dict) else {}
        if r[1] == "result":
            out.append((r[0], r[1], bool(data.get("ok")), bool(data.get("tool"))))
        else:
            out.append((r[0], r[1], data.get("subtype") == "interrupted", False))
    return out


def _turns(session_id: int, rows: list, high) -> dict:
    """seq -> (answer seq, outcome) for every prompt in ``rows`` that was not
    steered, its turn running up to the next such prompt, or to ``high``."""
    starts = [seq for seq, _ts, steering, _text in rows if not steering]
    if not starts:
        return {}
    events = _ends(session_id, starts[0], high)
    out, at = {}, 0
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else high
        answer = error = stopped = 0
        outcome = ""
        while at < len(events) and (end is None or events[at][0] < end):
            seq, kind, flag, tool = events[at]
            at += 1
            if outcome:
                continue    # past its result: a session tool's turn, say
            if kind == "assistant":
                answer = seq
            elif kind == "error":
                error = seq
            elif kind == "info":
                stopped = seq if flag else stopped
            elif not tool:
                outcome = "ok" if flag else "bad"
                answer = (answer or seq) if flag else seq
        if not outcome and (stopped or error):
            # no result: the turn ended where the last of these says it did
            outcome, answer = ("stopped", stopped) if stopped > error else ("bad", error)
        out[start] = (answer, outcome)
    return out


def _turn_end(session_id: int, rows: list, more: bool):
    """Where the last prompt of a page's turn can end at the latest: the next
    prompt that was not steered after the page, or None for the session's end."""
    if not more:
        return None
    for seq, _ts, steering, _text in rows:
        if not steering:
            return seq
    after = rows[-1][0]
    while True:
        later = _rows(session_id, after, 64)
        if not later:
            return None
        for seq, _ts, steering, _text in later:
            if not steering:
                return seq
        after = later[-1][0]


def read(session_id: int, after_seq: int = 0, limit: int = PAGE_LIMIT) -> dict:
    """One page of the index: ``prompts`` as [seq, ts, steered (0/1),
    excerpt, answer seq, outcome] rows oldest first - a steered prompt has
    no turn of its own and says 0 and "" - ``more`` when the page did not
    reach the newest prompt, and ``total``, every prompt the session holds."""
    rows = _rows(session_id, after_seq, limit + 1)
    more = len(rows) > limit
    page = rows[:limit]
    turns = _turns(session_id, page, _turn_end(session_id, rows[limit:], more))
    total = db.query_one("SELECT count(*) FROM events WHERE session_id=? AND kind='user'",
                         (session_id,))[0]
    return {
        "prompts": [[seq, round(float(ts), 3), 1 if steering else 0, excerpt(text)] +
                    list(turns.get(seq, (0, "")))
                    for seq, ts, steering, text in page],
        "total": int(total),
        "more": more,
    }
