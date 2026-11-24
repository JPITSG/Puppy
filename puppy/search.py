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

A search can be made expensive by whoever words it, and agents run many at
once, so none of them can hold anything up. The index has one writer, on a
thread of its own; searches read it through connections of their own, beside
the writer and each other, in threads nothing else uses. The full-text match
always drives the query and one pass serves a whole request, however many
sessions it names. Every search runs under a ``Job``: past its deadline, or
once nobody waits for its answer, it stops within a fraction of a second. And
a lane admits a few at a time - the console's and the agents' apart, one turn
never more than its share - while the rest wait on the event loop.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata

from aiohttp import web

from puppy import config, db, runner, workers, workspace_sync

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

# How long one search may read the index. Every caller waits longer than this
# for its answer: the console 20 s, a turn's bridge 45 s and the controller's
# relay to another backend 12 s, waits for a place in the lane included.
SEARCH_SECONDS = 8.0
PEOPLE_WAIT = 8.0
AGENTS_WAIT = 20.0
RELAYED_WAIT = 2.5
# SQLite asks between this many steps whether to go on. Measured on a 1.3 GB
# index the asking costs nothing and a stop comes at most 0.1 s late; at ten
# times the steps the widest queries overrun by most of a second.
PROGRESS_STEPS = 1000
# A connection that collected this many matches gives its memory back.
KEEP_READER_HITS = 50000
IDLE_READERS = 4
GONE_SECONDS = 0.25      # how often a search looks whether its browser left

_conn = None             # the index's one writer
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


class SearchError(RuntimeError):
    """Why a search has no answer, in words for whoever asked."""


class SearchUnavailableError(SearchError):
    pass


class SearchTimeoutError(SearchError):
    pass


class SearchBusyError(SearchError):
    pass


class SearchCancelled(SearchError):
    pass


class Job:
    """One search's leave to run: until its time is up or its caller is gone.

    ``over`` is SQLite's progress handler, so a statement under way stops too.
    SQLite reports such a stop as an ordinary OperationalError - the very error
    a query it cannot parse raises - which is why ``check`` is asked first."""

    def __init__(self, seconds=None):
        self.seconds = SEARCH_SECONDS if seconds is None else seconds
        self.deadline = None
        self.cancelled = False

    def begin(self) -> None:
        self.deadline = time.monotonic() + self.seconds

    def cancel(self) -> None:
        self.cancelled = True

    def over(self) -> bool:
        return self.cancelled or (
            self.deadline is not None and time.monotonic() >= self.deadline)

    def check(self) -> None:
        if self.cancelled:
            raise SearchCancelled("the search was stopped")
        if self.over():
            raise SearchTimeoutError(
                "the search took longer than {:g} seconds and was stopped".format(self.seconds))


_jobs = set()            # every search under way, for the shutdown to stop


def _new_lanes():
    """The console's searches and the agents': neither can take the other's
    threads, and one turn's parallel searches take one at a time."""
    return workers.Lane(2, waiting=16), workers.Lane(2, per_key=1, waiting=64)


_people, _agents = _new_lanes()


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
    """Return the index's writer, rebuilding an unusable file from scratch."""
    global _conn, _unavailable
    conn = _conn
    if conn is not None:
        return conn     # never behind the writer's own work
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


# ---- reading the index ----

# One search's matches, ranked, and the rows of them that are shown. Both live
# in the reading connection's own memory, never in the index file.
READER_TABLES = """
CREATE TEMP TABLE hits(
    session_id INTEGER, rank REAL, ts REAL, id INTEGER, seq INTEGER, kind TEXT,
    PRIMARY KEY(session_id, rank, ts DESC, id)
) WITHOUT ROWID;
CREATE TEMP TABLE page(id INTEGER PRIMARY KEY);
"""

_readers = []            # idle read connections
_readers_lock = threading.Lock()
_generation = 0          # moves on when the index file is let go of


def _open_reader():
    """A connection that only reads the index: beside the writer, never in it."""
    uri = Path(os.path.abspath(INDEX_PATH)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(READER_TABLES)
    except BaseException:
        conn.close()
        raise
    return conn


class _Reading:
    """One search's connection; every statement answers to the search's job."""

    def __init__(self, conn, job):
        self.conn = conn
        self.job = job
        self.collected = 0

    def rows(self, sql, args=(), run=None):
        self.job.check()
        try:
            return (run or self.conn.execute)(sql, args).fetchall()
        except sqlite3.OperationalError:
            self.job.check()
            raise

    def many(self, sql, rows) -> None:
        self.rows(sql, rows, self.conn.executemany)


@contextmanager
def _reading(job):
    """A read connection for one search, whoever else reads or writes: one
    moment of the index throughout, and the reader's own tables left empty."""
    job = job or Job()
    job.begin()
    conn = None
    keep = False
    try:
        connect()        # the index is there and current
        with _readers_lock:
            generation = _generation
            conn = _readers.pop() if _readers else None
        if conn is None:
            conn = _open_reader()
        conn.execute("BEGIN")
        conn.set_progress_handler(job.over, PROGRESS_STEPS)
        reading = _Reading(conn, job)
        try:
            yield reading
        except (sqlite3.OperationalError, ValueError):
            keep = True  # an unusable query says nothing about the connection
            raise
        keep = reading.collected <= KEEP_READER_HITS
    finally:
        if conn is not None:
            conn.set_progress_handler(None, 0)
            if keep:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    keep = False
            with _readers_lock:
                keep = keep and generation == _generation and len(_readers) < IDLE_READERS
                if keep:
                    _readers.append(conn)
            if not keep:
                conn.close()


def _close_readers() -> None:
    global _generation
    with _readers_lock:
        _generation += 1     # one in use now closes instead of coming back
        idle, _readers[:] = list(_readers), []
    for conn in idle:
        conn.close()


def close_for_tests() -> None:
    global _conn, _ready, _full_requested
    _close_readers()
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
        if data.get("subtype") == "session_task_archive":
            # a removed task's whole condensed conversation, folded into its
            # Main: searchable there because the task's own rows are gone
            from puppy import session_tasks
            text = session_tasks.archive_text(data)
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


def _index_new_events(conn, session_id: int, last_seq: int, stop=None) -> None:
    while stop is None or not stop.is_set():
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


def reconcile(session_ids=None, stop=None) -> None:
    """Bring the index in line with the main database.

    ``session_ids`` limits the pass to those sessions (deleted ones are
    purged); None reconciles everything, including sessions the index knows
    but the database no longer does. A session whose history shrank - a
    restored backup - is rebuilt from scratch rather than trusted. ``stop`` is
    the Event that ends it between batches: every batch is complete in itself.
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
        if stop is not None and stop.is_set():
            return
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
            _index_new_events(conn, session_id, last_seq, stop)


# ---- query translation ----

_TOKEN_RE = re.compile(r'(-?)"([^"]*)"|(\S+)')
_MATCHABLE_RE = re.compile(r"\w", re.UNICODE)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# A bare word this short matches whole words only: as a prefix a single letter
# is a large part of every word there is, which is slow and finds nothing.
WHOLE_WORD_CHARS = 2


def _matches_prefixes(term: str) -> bool:
    """Whether a bare word also finds the words that begin with it.

    Judged by its last word, the one FTS5 would extend. Scripts written without
    spaces index whole runs of text, so there even one character must."""
    words = _WORD_RE.findall(term)
    if not words:
        return True
    plain = "".join(char for char in unicodedata.normalize("NFKD", words[-1])
                    if not unicodedata.combining(char))
    return len(plain) > WHOLE_WORD_CHARS or not plain.isascii()


def _quoted(term: str, prefix: bool) -> str:
    quoted = '"{}"'.format(term.replace('"', '""'))
    return quoted + " *" if prefix else quoted


def match_expression(query: str) -> str:
    """Translate a human query into FTS5 MATCH syntax.

    Bare words prefix-match - one- and two-letter words match whole words -
    and AND together, "quoted phrases" match exactly, a leading - excludes,
    and OR joins the terms beside it. Raises ValueError when nothing positive
    remains to match.
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
        rendered = _quoted(term, prefix and _matches_prefixes(term))
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

def _condition(kinds, after, before, session_ids):
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
    if session_ids is not None:
        clauses.append("d.session_id IN ({})".format(",".join("?" * len(session_ids))))
        args.extend(int(session_id) for session_id in session_ids)
    return " AND ".join(clauses), args


def _hits_sql(where: str) -> str:
    """Every match and its rank, into the reader's own table.

    CROSS JOIN is SQLite's promise to keep this order: the full-text match
    drives and each match is looked up once. Left to choose, it reads a session
    filter the other way round - it has no statistics and takes a session for
    ten rows - and matches the whole index again for every one of them."""
    return ("INSERT INTO temp.hits(session_id, rank, ts, id, seq, kind) "
            "SELECT d.session_id, bm25(docs_fts), d.ts, d.id, d.seq, d.kind "
            "FROM docs_fts CROSS JOIN docs d ON d.id = docs_fts.rowid "
            "WHERE " + where)


_SNIPPETS_SQL = (
    "SELECT p.id AS id, snippet(docs_fts, 0, ?, ?, '…', ?) AS snip "
    "FROM docs_fts CROSS JOIN temp.page p ON p.id = docs_fts.rowid "
    "WHERE docs_fts MATCH ?")


def _collect(reading, query, kinds, after, before, session_ids):
    """Rank every match once, whatever the request wants of them: the
    expression that was used and each session's number of matches."""
    where, filter_args = _condition(kinds, after, before, session_ids)
    sql = _hits_sql(where)
    expression = match_expression(query)
    try:
        reading.rows(sql, [expression] + filter_args)
    except sqlite3.OperationalError:
        # Operator soup the translator let through: retry fully literal.
        expression = _literal_expression(query)
        reading.rows(sql, [expression] + filter_args)
    counts = {int(row["session_id"]): int(row["n"]) for row in reading.rows(
        "SELECT session_id, COUNT(*) AS n FROM temp.hits GROUP BY session_id")}
    reading.collected = sum(counts.values())
    return expression, counts


def _ranked(reading, order, limit, offset=0, session_id=None):
    sql = "SELECT id, session_id, seq, kind, ts, rank FROM temp.hits "
    args = []
    if session_id is not None:
        sql += "WHERE session_id=? "
        args.append(int(session_id))
    sql += "ORDER BY " + ("ts DESC, rank, id" if order == "recent" else "rank, ts DESC, id")
    return reading.rows(sql + " LIMIT ? OFFSET ?", args + [int(limit), int(offset)])


def _snippets(reading, expression, rows) -> dict:
    """Doc id -> its excerpt, for the rows that will be shown and no others."""
    if not rows:
        return {}
    reading.many("INSERT INTO temp.page(id) VALUES(?)", [(row["id"],) for row in rows])
    return {row["id"]: _clean_snippet(row["snip"]) for row in reading.rows(
        _SNIPPETS_SQL, [HL_START, HL_END, SNIPPET_TOKENS, expression])}


def _clean_snippet(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _match(row, snippets, *keys) -> dict:
    value = {"session_id": int(row["session_id"]), "seq": int(row["seq"]),
             "kind": str(row["kind"]), "ts": float(row["ts"]), "rank": float(row["rank"]),
             "snippet": snippets.get(row["id"], "")}
    return {key: value[key] for key in keys} if keys else value


def _elapsed(started) -> int:
    return int((time.monotonic() - started) * 1000)


def query_index(query, kinds=None, after=None, before=None, session_id=None,
                order="relevance", limit=DEFAULT_PAGE, offset=0,
                max_sessions=DEFAULT_GROUP_SESSIONS,
                per_session=DEFAULT_PER_SESSION, job=None):
    """Execute one search and shape the response payload (sans HTTP)."""
    started = time.monotonic()
    if session_id is not None:
        page = query_sessions(query, [session_id], kinds=kinds, after=after, before=before,
                              order=order, limit=limit, offset=offset, job=job)
        return page.get(int(session_id)) or {
            "ok": True, "partial": not _ready, "sid": int(session_id), "total": 0,
            "offset": int(offset), "matches": [], "elapsed_ms": _elapsed(started)}
    sessions = {}        # id -> the session and the rows of it that are shown
    overflow = False
    with _reading(job) as reading:
        expression, counts = _collect(reading, query, kinds, after, before, None)
        rows = _ranked(reading, order, CANDIDATE_CAP)
        for row in rows:
            session_id = int(row["session_id"])
            shown = sessions.get(session_id)
            if shown is None:
                if len(sessions) >= max_sessions:
                    overflow = True
                    continue
                session = db.get_session(session_id)
                if session is None:
                    continue   # deleted while this query ran
                shown = sessions[session_id] = (session, [])
            if len(shown[1]) < per_session:
                shown[1].append(row)
        snippets = _snippets(reading, expression,
                             [row for _session, shown in sessions.values() for row in shown])
    return {
        "ok": True, "partial": not _ready, "total": sum(counts.values()),
        "session_total": len(counts),
        "sessions": [{
            "session": runner.session_payload(session),
            "total": counts.get(session_id, 0),
            "matches": [_match(row, snippets, "seq", "kind", "ts", "rank", "snippet")
                        for row in shown],
        } for session_id, (session, shown) in sessions.items()],
        "truncated": overflow or len(rows) >= CANDIDATE_CAP,
        "elapsed_ms": _elapsed(started),
    }


def query_sessions(query, session_ids, kinds=None, after=None, before=None,
                   order="relevance", limit=DEFAULT_PAGE, offset=0, job=None) -> dict:
    """The same search in exactly these sessions: each one's total and its own
    page of matches, by session id, leaving out those with none. One pass over
    the index, however many sessions are asked for."""
    started = time.monotonic()
    session_ids = list(dict.fromkeys(int(session_id) for session_id in session_ids))
    if not session_ids:
        match_expression(query)     # an unusable query is refused all the same
        return {}
    with _reading(job) as reading:
        expression, counts = _collect(reading, query, kinds, after, before, session_ids)
        rows = {session_id: _ranked(reading, order, limit, offset, session_id)
                for session_id in session_ids if counts.get(session_id)}
        snippets = _snippets(reading, expression,
                             [row for shown in rows.values() for row in shown])
    return {session_id: {
        "ok": True, "partial": not _ready, "sid": session_id,
        "total": counts[session_id], "offset": int(offset),
        "matches": [_match(row, snippets) for row in shown],
        "elapsed_ms": _elapsed(started),
    } for session_id, shown in rows.items()}


async def _run(lane, function, *args, key=None, wait=0.0, **kwargs):
    """``function(*args, job=…, **kwargs)`` as one search: let in by its lane,
    run in a search thread, stopped at its deadline or when its caller goes."""
    job = Job()
    _jobs.add(job)
    try:
        return await lane.run(workers.search,
                              functools.partial(function, *args, job=job, **kwargs),
                              key=key, wait=wait, abandoned=job.cancel)
    except workers.Busy:
        raise SearchBusyError("too many searches are waiting here")
    finally:
        _jobs.discard(job)


async def for_agent(source, query, session_ids, wait, **kwargs) -> dict:
    """A turn's search of the sessions it names (``query_sessions``), one at a
    time for each ``source``: its parallel searches, and its sub-agents', wait
    for each other rather than take every place there is. One relayed from
    another backend (no source) waits only for a place."""
    try:
        return await _run(_agents, query_sessions, query, session_ids, key=source,
                          wait=wait, **kwargs)
    except SearchTimeoutError as exc:
        raise SearchTimeoutError(
            "{}: use more distinctive words or an exact \"phrase\", fewer kinds, or fewer "
            "sessions at a time".format(exc))
    except SearchBusyError as exc:
        raise SearchBusyError("{}; try again in a moment, one search at a time".format(exc))


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


async def _worker(stop) -> None:
    global _ready, _full_requested
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
        pending = workers.index.run(reconcile, None if full else session_ids, stop)
        try:
            await asyncio.shield(pending)
            if full:
                _ready = True
        except asyncio.CancelledError:
            # it has been told to stop, which it does between two batches
            done, _pending_still = await asyncio.wait({pending}, timeout=5)
            for future in done:
                future.exception()
            raise
        except SearchUnavailableError:
            return
        except Exception:
            log.exception("search index reconcile failed")
            await asyncio.sleep(5)


async def _stop_searches(_app) -> None:
    for job in list(_jobs):
        job.cancel()


async def _lifecycle(app):
    global _loop, _wake, _people, _agents
    _loop = asyncio.get_running_loop()
    _wake = asyncio.Event()
    _wake.set()   # first pass builds or catches up the index immediately
    _people, _agents = _new_lanes()
    stop = threading.Event()
    task = asyncio.create_task(_worker(stop), name="puppy-search-index")
    app["puppy_search_task"] = task
    try:
        yield
    finally:
        # Python waits for its threads before it exits: none of them may be
        # left in the middle of something long.
        stop.set()
        await _stop_searches(app)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---- HTTP ----

def _bad(message: str):
    return web.json_response({"error": message}, status=400)


async def _while_connected(request, awaitable):
    """Its result, unless the browser stops waiting for it first."""
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=GONE_SECONDS)
            if not task.done() and (request.transport is None or
                                    request.transport.is_closing()):
                raise SearchCancelled("the search was stopped")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _refused(message: str):
    return web.json_response({"error": message}, status=503, headers={"Retry-After": "2"})


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
    try:
        payload = await _while_connected(request, _run(
            _people, query_index, query, kinds=kinds, after=numbers["after"],
            before=numbers["before"], session_id=numbers["sid"], order=order,
            limit=numbers["limit"], offset=numbers["offset"],
            max_sessions=numbers["sessions"], per_session=numbers["per"],
            wait=PEOPLE_WAIT))
    except ValueError as exc:
        return _bad(str(exc))
    except SearchTimeoutError as exc:
        return _refused("{} · use more distinctive words".format(exc))
    except SearchBusyError as exc:
        return _refused("{} · try again in a moment".format(exc))
    except SearchError as exc:
        return _refused(str(exc))
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
    # first when the node stops, so no open request waits for a search
    app.on_shutdown.append(_stop_searches)
    app.cleanup_ctx.append(_lifecycle)
