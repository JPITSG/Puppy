"""Full-history transcript search over a node-local FTS5 index.

Each node answers GET /api/search for its own sessions only; the console fans
a query out to itself and to online remote nodes advertising the additive
``session-search`` capability and merges what comes back, so offline nodes
simply contribute nothing and no transcript is ever mirrored for search.

The index at data/search/index.db is a derived, rebuildable cache of the main
database's sessions and events tables - never authoritative state. It is not
migrated: a missing, corrupt, or shape-mismatched index file is deleted and
rebuilt from the source tables, and it stays outside snapshot coverage like
the other rebuildable caches (workspace mirrors and leases). SQLite's FTS5 is
compiled into every supported host interpreter; no external service or binary
is involved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time

from aiohttp import web

from puppy import config, db, runner, workspace_sync

log = logging.getLogger("puppy.search")

INDEX_DIR = os.path.join(config.DATA_DIR, "search")
INDEX_PATH = os.path.join(INDEX_DIR, "index.db")

# Bumping this abandons every existing index file at startup (fresh rebuild),
# which is the entire upgrade story for this cache.
SCHEMA_VERSION = 1

# One searchable document per transcript event (seq >= 1), plus one synthetic
# "title" document per session (seq 0) carrying its name and location so the
# advanced search can find sessions by what the sidebar shows. The FTS table
# is external-content over ``docs``; the triggers are the only writers allowed
# to touch ``docs_fts`` besides the 'delete' commands they issue themselves.
SCHEMA = """
CREATE TABLE docs (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ts REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX idx_docs_session ON docs(session_id, seq);
CREATE TABLE indexed_sessions (
    session_id INTEGER PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0,
    title_fp TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE docs_fts USING fts5(
    body,
    content='docs',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, body) VALUES('delete', old.id, old.body);
END;
"""

# Objects the schema creates directly; FTS5's own shadow tables (docs_fts_*)
# are implementation detail and excluded from the shape comparison.
_SCHEMA_OBJECTS = frozenset(
    ["docs", "idx_docs_session", "indexed_sessions", "docs_fts",
     "docs_ai", "docs_ad"])

KINDS = ("user", "assistant", "thinking", "tool", "info", "title")

MAX_QUERY_CHARS = 400
MAX_DOC_CHARS = 16 * 1024
SNIPPET_TOKENS = 10
# Highlight markers survive JSON intact and cannot appear in indexed text
# (control characters are scrubbed at indexing time), so the client can
# escape the snippet as text and then translate markers into <mark> safely.
HL_START = "\x01"
HL_END = "\x02"
CANDIDATE_CAP = 400
MAX_GROUP_SESSIONS = 50
DEFAULT_GROUP_SESSIONS = 24
MAX_PER_SESSION = 10
DEFAULT_PER_SESSION = 4
MAX_PAGE = 200
DEFAULT_PAGE = 50
INDEX_BATCH = 500
SWEEP_SECONDS = 900
DEBOUNCE_SECONDS = 0.4

_conn = None
_lock = threading.RLock()
_unavailable = ""        # non-empty: FTS5 truly unusable on this host
_ready = False           # one full reconcile has completed since startup
_loop = None
_wake = None
_pending = set()
_pending_lock = threading.Lock()
_full_requested = False

# ANSI escape sequences and stray control characters would pollute snippets
# (and could forge highlight markers), so they never enter the index.
_SCRUB_RE = re.compile(
    "\x1b\\[[0-9;?]*[ -/]*[@-~]"
    "|\x1b\\][^\x07\x1b]*(?:\x07|\x1b\\\\)?"
    "|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SearchUnavailableError(RuntimeError):
    pass


# ---- index storage ----

def _discard_index_files() -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(INDEX_PATH + suffix)
        except FileNotFoundError:
            pass


def _objects(conn) -> frozenset:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "AND name NOT LIKE 'docs_fts_%'").fetchall()
    return frozenset(str(row[0]) for row in rows)


def _try_open():
    """Open and validate one candidate index file; None means rebuild it."""
    conn = sqlite3.connect(INDEX_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        present = _objects(conn)
        if not present and version == 0:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA user_version={}".format(SCHEMA_VERSION))
            conn.commit()
        elif version != SCHEMA_VERSION or present != _SCHEMA_OBJECTS:
            conn.close()
            return None
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()
        return conn
    except sqlite3.DatabaseError:
        conn.close()
        return None


def connect():
    """Return the index connection, rebuilding an unusable file from scratch."""
    global _conn, _unavailable
    with _lock:
        if _unavailable:
            raise SearchUnavailableError(_unavailable)
        if _conn is not None:
            return _conn
        os.makedirs(INDEX_DIR, mode=0o700, exist_ok=True)
        try:
            os.chmod(INDEX_DIR, 0o700)
        except OSError:
            pass
        try:
            conn = _try_open()
            if conn is None:
                log.info("search index is not current; rebuilding it")
                _discard_index_files()
                conn = _try_open()
            if conn is None:
                raise sqlite3.DatabaseError("search index cannot be initialized")
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower():
                _unavailable = "this host's SQLite lacks FTS5"
                log.error("search disabled: %s", _unavailable)
                raise SearchUnavailableError(_unavailable)
            raise
        try:
            os.chmod(INDEX_PATH, 0o600)
        except OSError:
            pass
        _conn = conn
        return _conn


def close_for_tests() -> None:
    global _conn, _ready, _full_requested
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _ready = False
        _full_requested = False
    with _pending_lock:
        _pending.clear()


# ---- document extraction ----

def _scrub(text) -> str:
    return _SCRUB_RE.sub("", str(text or ""))


def _text_of(value, depth=0) -> str:
    """Best-effort text of a driver payload value (tool inputs and results)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if depth >= 4:
        return ""
    if isinstance(value, list):
        return "\n".join(
            part for part in (_text_of(item, depth + 1) for item in value) if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                              default=str)
        except (TypeError, ValueError):
            return ""
    return str(value)


def _event_doc(kind, data):
    """Map one persisted transcript event to (indexed kind, body) or None."""
    data = data if isinstance(data, dict) else {}
    if kind in ("user", "assistant", "thinking"):
        text = data.get("text")
    elif kind == "tool_use":
        text = " ".join(part for part in (
            str(data.get("tool") or ""), _text_of(data.get("input"))) if part)
        kind = "tool"
    elif kind == "tool_result":
        text = _text_of(data.get("content"))
        kind = "tool"
    elif kind in ("info", "error"):
        text = data.get("text")
        kind = "info"
    elif kind == "side_question":
        # asked beside a turn rather than inside it, but the person did write
        # it and will look for it again; folded into the existing kinds so the
        # query language keeps exactly the vocabulary it already publishes
        text = data.get("question")
        kind = "user"
    elif kind == "side_question_result":
        text = data.get("text")
        kind = "assistant"
    else:
        return None   # result/engine_switch lines carry no searchable prose
    text = _scrub(text).strip()
    if not text:
        return None
    return kind, text[:MAX_DOC_CHARS]


def _title_body(session) -> str:
    parts = [str(session["name"] or ""), workspace_sync.public_cwd(session)]
    workspace = str(session["workspace"] or "")
    if workspace:
        try:
            descriptor = json.loads(workspace)
            for key in ("label", "node", "root"):
                value = descriptor.get(key) if isinstance(descriptor, dict) else ""
                if isinstance(value, str) and value:
                    parts.append(value)
        except ValueError:
            pass
    return _scrub("\n".join(part for part in parts if part))[:MAX_DOC_CHARS]


def _title_fp(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16]


# ---- reconcile (runs in a worker thread) ----

def _purge_session(conn, session_id: int) -> None:
    conn.execute("DELETE FROM docs WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM indexed_sessions WHERE session_id=?", (session_id,))


def _index_new_events(conn, session_id: int, last_seq: int) -> None:
    while True:
        rows = db.query(
            "SELECT seq,kind,payload,created_at FROM events "
            "WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (session_id, last_seq, INDEX_BATCH))
        if not rows:
            return
        with _lock:
            for row in rows:
                last_seq = int(row["seq"])
                try:
                    data = json.loads(row["payload"])
                except ValueError:
                    data = {}
                doc = _event_doc(str(row["kind"]), data)
                if doc is not None:
                    conn.execute(
                        "INSERT INTO docs(session_id,seq,kind,ts,body) "
                        "VALUES(?,?,?,?,?)",
                        (session_id, last_seq, doc[0],
                         float(row["created_at"]), doc[1]))
            conn.execute(
                "UPDATE indexed_sessions SET last_seq=? WHERE session_id=?",
                (last_seq, session_id))
            conn.commit()
        if len(rows) < INDEX_BATCH:
            return


def reconcile(session_ids=None) -> None:
    """Bring the index in line with the main database.

    ``session_ids`` limits the pass to those sessions (deleted ones are
    purged); None reconciles everything, including sessions the index knows
    but the database no longer does. A session whose history shrank - a
    restored backup - is rebuilt from scratch rather than trusted.
    """
    conn = connect()
    scope = None if session_ids is None else sorted(
        {int(session_id) for session_id in session_ids})
    if scope is not None and not scope:
        return
    marks = ",".join("?" * len(scope)) if scope else ""
    live = {}
    for row in db.query(
            "SELECT id,name,cwd,workspace,created_at FROM sessions" +
            (" WHERE id IN ({})".format(marks) if scope else ""),
            tuple(scope) if scope else ()):
        live[int(row["id"])] = row
    maxes = {}
    for row in db.query(
            "SELECT session_id, MAX(seq) AS max_seq FROM events" +
            (" WHERE session_id IN ({})".format(marks) if scope else "") +
            " GROUP BY session_id",
            tuple(scope) if scope else ()):
        maxes[int(row["session_id"])] = int(row["max_seq"])
    with _lock:
        state = {
            int(row["session_id"]): (int(row["last_seq"]), str(row["title_fp"]))
            for row in conn.execute(
                "SELECT session_id,last_seq,title_fp FROM indexed_sessions" +
                (" WHERE session_id IN ({})".format(marks) if scope else ""),
                tuple(scope) if scope else ())}
        for session_id in sorted(set(state) - set(live)):
            _purge_session(conn, session_id)
            state.pop(session_id, None)
        conn.commit()
    for session_id in sorted(live):
        session = live[session_id]
        body = _title_body(session)
        fingerprint = _title_fp(body)
        max_seq = maxes.get(session_id, 0)
        with _lock:
            known = state.get(session_id)
            if known is not None and max_seq < known[0]:
                _purge_session(conn, session_id)   # shrunk history: rebuild
                known = None
            if known is None:
                conn.execute(
                    "INSERT OR REPLACE INTO indexed_sessions"
                    "(session_id,last_seq,title_fp) VALUES(?,0,'')",
                    (session_id,))
                known = (0, "")
            if known[1] != fingerprint:
                conn.execute(
                    "DELETE FROM docs WHERE session_id=? AND seq=0", (session_id,))
                if body:
                    conn.execute(
                        "INSERT INTO docs(session_id,seq,kind,ts,body) "
                        "VALUES(?,0,'title',?,?)",
                        (session_id, float(session["created_at"]), body))
                conn.execute(
                    "UPDATE indexed_sessions SET title_fp=? WHERE session_id=?",
                    (fingerprint, session_id))
            conn.commit()
            last_seq = known[0]
        if max_seq > last_seq:
            _index_new_events(conn, session_id, last_seq)


# ---- query translation ----

_TOKEN_RE = re.compile(r'(-?)"([^"]*)"|(\S+)')
_MATCHABLE_RE = re.compile(r"\w", re.UNICODE)


def _quoted(term: str, prefix: bool) -> str:
    quoted = '"{}"'.format(term.replace('"', '""'))
    return quoted + " *" if prefix else quoted


def match_expression(query: str) -> str:
    """Translate a human query into FTS5 MATCH syntax.

    Bare words prefix-match and AND together, "quoted phrases" match exactly,
    a leading - excludes, and OR joins the terms beside it. Raises ValueError
    when nothing positive remains to match.
    """
    groups = []       # AND-chain of OR-groups
    negatives = []
    pending_or = False
    for match in _TOKEN_RE.finditer(query):
        minus, phrase, word = match.group(1), match.group(2), match.group(3)
        if word == "OR":
            pending_or = bool(groups)
            continue
        negated = minus == "-"
        prefix = False
        if phrase is not None:
            term = phrase
        else:
            term = word
            if term.startswith("-") and len(term) > 1:
                negated = True
                term = term[1:]
            prefix = True
        term = _scrub(term).strip()
        # unicode61 only indexes word characters; a purely punctuational term
        # cannot match anything and would only risk a syntax error
        if not _MATCHABLE_RE.search(term):
            continue
        if negated:
            negatives.append(_quoted(term, False))
            pending_or = False
            continue
        rendered = _quoted(term, prefix)
        if pending_or and groups:
            groups[-1].append(rendered)
        else:
            groups.append([rendered])
        pending_or = False
    if not groups:
        raise ValueError("search needs at least one term to match")
    positive = " AND ".join(
        group[0] if len(group) == 1 else "({})".format(" OR ".join(group))
        for group in groups)
    if negatives:
        positive = "({})".format(positive)
        for negative in negatives:
            positive += " NOT " + negative
    return positive


def _literal_expression(query: str) -> str:
    terms = [
        _quoted(term, False) for term in
        (_scrub(part).strip() for part in re.split(r'[\s"]+', query))
        if term and _MATCHABLE_RE.search(term)]
    if not terms:
        raise ValueError("search needs at least one term to match")
    return " AND ".join(terms)


# ---- query execution ----

def _condition(kinds, after, before, session_id):
    clauses = ["docs_fts MATCH ?"]
    args = []
    if kinds is not None:
        clauses.append("d.kind IN ({})".format(",".join("?" * len(kinds))))
        args.extend(kinds)
    if after is not None:
        clauses.append("d.ts >= ?")
        args.append(float(after))
    if before is not None:
        clauses.append("d.ts <= ?")
        args.append(float(before))
    if session_id is not None:
        clauses.append("d.session_id = ?")
        args.append(int(session_id))
    return " AND ".join(clauses), args


def _run_match(conn, expression, kinds, after, before, session_id, order,
               limit, offset=0):
    where, filter_args = _condition(kinds, after, before, session_id)
    ordering = ("d.ts DESC, bm25(docs_fts)" if order == "recent"
                else "bm25(docs_fts), d.ts DESC")
    sql = ("SELECT d.session_id, d.seq, d.kind, d.ts, "
           "snippet(docs_fts, 0, ?, ?, '…', ?) AS snip, "
           "bm25(docs_fts) AS rank "
           "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
           "WHERE " + where + " ORDER BY " + ordering + " LIMIT ? OFFSET ?")
    args = [HL_START, HL_END, SNIPPET_TOKENS, expression] + filter_args + \
        [int(limit), int(offset)]
    return conn.execute(sql, args).fetchall()


def _count_by_session(conn, expression, kinds, after, before, session_id):
    where, filter_args = _condition(kinds, after, before, session_id)
    sql = ("SELECT d.session_id, COUNT(*) AS n "
           "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
           "WHERE " + where + " GROUP BY d.session_id")
    rows = conn.execute(sql, [expression] + filter_args).fetchall()
    return {int(row["session_id"]): int(row["n"]) for row in rows}


def _clean_snippet(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def query_index(query, kinds=None, after=None, before=None, session_id=None,
                order="relevance", limit=DEFAULT_PAGE, offset=0,
                max_sessions=DEFAULT_GROUP_SESSIONS,
                per_session=DEFAULT_PER_SESSION):
    """Execute one search and shape the response payload (sans HTTP)."""
    started = time.monotonic()
    conn = connect()
    expression = match_expression(query)
    with _lock:
        try:
            counts = _count_by_session(conn, expression, kinds, after, before,
                                       session_id)
        except sqlite3.OperationalError:
            # Operator soup the translator let through: retry fully literal.
            expression = _literal_expression(query)
            counts = _count_by_session(conn, expression, kinds, after, before,
                                       session_id)
        total = sum(counts.values())
        if session_id is not None:
            rows = _run_match(conn, expression, kinds, after, before,
                              session_id, order, limit, offset)
        else:
            rows = _run_match(conn, expression, kinds, after, before, None,
                              order, CANDIDATE_CAP)
    matches = [{
        "session_id": int(row["session_id"]),
        "seq": int(row["seq"]),
        "kind": str(row["kind"]),
        "ts": float(row["ts"]),
        "rank": float(row["rank"]),
        "snippet": _clean_snippet(row["snip"]),
    } for row in rows]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if session_id is not None:
        return {
            "ok": True, "partial": not _ready, "sid": int(session_id),
            "total": total, "offset": int(offset), "matches": matches,
            "elapsed_ms": elapsed_ms,
        }
    groups = []
    by_session = {}
    overflow = False
    for match in matches:
        group = by_session.get(match["session_id"])
        if group is None:
            if len(groups) >= max_sessions:
                overflow = True
                continue
            session = db.get_session(match["session_id"])
            if session is None:
                continue   # deleted while this query ran
            group = {
                "session": runner.session_payload(session),
                "total": counts.get(match["session_id"], 0),
                "matches": [],
            }
            by_session[match["session_id"]] = group
            groups.append(group)
        if len(group["matches"]) < per_session:
            group["matches"].append(
                {key: match[key] for key in ("seq", "kind", "ts", "rank",
                                             "snippet")})
    return {
        "ok": True, "partial": not _ready, "total": total,
        "session_total": len(counts), "sessions": groups,
        "truncated": overflow or len(matches) >= CANDIDATE_CAP,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


# ---- change feed and worker ----

def _on_db_change(session_id) -> None:
    with _pending_lock:
        _pending.add(int(session_id))
    loop = _loop
    if loop is not None:
        try:
            loop.call_soon_threadsafe(_signal_wake)
        except RuntimeError:
            pass


def _signal_wake() -> None:
    if _wake is not None:
        _wake.set()


def request_full_reconcile() -> None:
    """The main database was replaced wholesale (backup restore): re-derive."""
    global _full_requested
    _full_requested = True
    _signal_wake()


async def _worker() -> None:
    global _ready, _full_requested
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), timeout=SWEEP_SECONDS)
            await asyncio.sleep(DEBOUNCE_SECONDS)   # coalesce event bursts
        except asyncio.TimeoutError:
            _full_requested = True                  # periodic safety sweep
        _wake.clear()
        full = _full_requested or not _ready
        _full_requested = False
        with _pending_lock:
            session_ids = None if full else sorted(_pending)
            _pending.clear()
        try:
            await loop.run_in_executor(
                None, reconcile, None if full else session_ids)
            if full:
                _ready = True
        except SearchUnavailableError:
            return
        except Exception:
            log.exception("search index reconcile failed")
            await asyncio.sleep(5)


async def _lifecycle(app):
    global _loop, _wake
    _loop = asyncio.get_running_loop()
    _wake = asyncio.Event()
    _wake.set()   # first pass builds or catches up the index immediately
    task = asyncio.create_task(_worker(), name="puppy-search-index")
    app["puppy_search_task"] = task
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---- HTTP ----

def _bad(message: str):
    return web.json_response({"error": message}, status=400)


async def h_search(request: web.Request):
    query = str(request.query.get("q") or "").strip()
    if not query:
        return _bad("a search query is required")
    if len(query) > MAX_QUERY_CHARS:
        return _bad("search query cannot exceed {} characters".format(
            MAX_QUERY_CHARS))
    kinds = None
    raw_kinds = request.query.get("kinds")
    if raw_kinds is not None:
        kinds = sorted({part.strip() for part in raw_kinds.split(",")
                        if part.strip()})
        if not kinds or any(kind not in KINDS for kind in kinds):
            return _bad("unknown search kind")
        if set(kinds) == set(KINDS):
            kinds = None
    order = str(request.query.get("order") or "relevance")
    if order not in ("relevance", "recent"):
        return _bad("search order must be relevance or recent")
    numbers = {}
    for name, default, low, high in (
            ("after", None, 0, None), ("before", None, 0, None),
            ("sid", None, 1, None), ("limit", DEFAULT_PAGE, 1, MAX_PAGE),
            ("offset", 0, 0, 1000000),
            ("sessions", DEFAULT_GROUP_SESSIONS, 1, MAX_GROUP_SESSIONS),
            ("per", DEFAULT_PER_SESSION, 1, MAX_PER_SESSION)):
        raw = request.query.get(name)
        if raw is None:
            numbers[name] = default
            continue
        try:
            value = float(raw) if name in ("after", "before") else int(raw)
        except (TypeError, ValueError):
            return _bad("invalid search parameter: {}".format(name))
        if value < low or (high is not None and value > high):
            return _bad("invalid search parameter: {}".format(name))
        numbers[name] = value
    loop = asyncio.get_running_loop()
    try:
        payload = await loop.run_in_executor(None, lambda: query_index(
            query, kinds=kinds, after=numbers["after"], before=numbers["before"],
            session_id=numbers["sid"], order=order, limit=numbers["limit"],
            offset=numbers["offset"], max_sessions=numbers["sessions"],
            per_session=numbers["per"]))
    except ValueError as exc:
        return _bad(str(exc))
    except SearchUnavailableError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    except sqlite3.OperationalError:
        return _bad("unusable search query")
    return web.json_response(payload)


def register(app: web.Application) -> None:
    """Attach the search route and its index worker to either runtime."""
    if app.get("puppy_search_registered"):
        return
    app["puppy_search_registered"] = True
    db.add_change_listener(_on_db_change)
    app.router.add_get("/api/search", h_search)
    app.cleanup_ctx.append(_lifecycle)
