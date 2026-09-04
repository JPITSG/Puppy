"""SQLite storage (stdlib sqlite3, WAL). Single shared connection guarded by a lock."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid

from puppy import config

log = logging.getLogger("puppy.db")

_conn = None
_lock = threading.RLock()
_change_listeners = []


def add_change_listener(listener) -> None:
    """Register a callable receiving a session id after transcript-affecting
    writes: event appends, session create, rename, and delete. Listeners run
    outside the storage lock; a listener failure is logged, never raised."""
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def _notify_change(session_id) -> None:
    for listener in _change_listeners:
        try:
            listener(int(session_id))
        except Exception:
            log.warning("session change listener failed", exc_info=True)

# On Send, the same composer value travels once as the message and once as the
# conditional draft-consume guard. A quarter-million Unicode code points keeps
# even maximally JSON-escaped text below the 4 MiB session-frame ceiling while
# remaining far beyond an ordinary interactive prompt.
MAX_DRAFT_CHARS = 256 * 1024

# Selectable session dot colors - gray is reserved for the Settings tab. The
# second ten fill the hue gaps the first ten left (yellow-green through green,
# and magenta) and lean on lightness where hue alone would not separate them:
# a 12px dot has to stay tellable apart on both themes, not merely be a
# different number. Existing rows keep whatever they were given, so this list
# only ever grows - removing an entry would orphan the sessions holding it.
SESSION_COLORS = ["#e0784f", "#4dd0c4", "#9d7bff", "#4dc6ff", "#22e5a4",
                  "#ff6b81", "#f0a84a", "#e8d24b", "#7ea2ff", "#ff9ad5",
                  "#b4d94a", "#63cf4e", "#7fdbb6", "#2a9d8f", "#3fd6e0",
                  "#4a4fd8", "#b56cf0", "#e56ce0", "#d94f6e", "#b07d2e"]

SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO meta(key,value) VALUES(
    'web.transport',
    '{"format":1,"scheme":"http","https_source":"auto"}'
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    pwhash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE web_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE backends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    urls TEXT NOT NULL DEFAULT '[]',
    token TEXT NOT NULL,
    protocol INTEGER NOT NULL DEFAULT 0,
    capabilities TEXT NOT NULL DEFAULT '[]',
    remote_version TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    tls_fingerprint TEXT NOT NULL DEFAULT '',
    auto_upgrade INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL,
    cwd TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    permission_mode TEXT NOT NULL DEFAULT '',
    native_session_id TEXT NOT NULL DEFAULT '',
    last_model TEXT NOT NULL DEFAULT '',
    -- requested model/effort of the last turn, JSON; last_model separately
    -- holds the engine-confirmed effective model; '' until a turn was sent
    used_config TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    archived INTEGER NOT NULL DEFAULT 0,
    workspace_kind TEXT NOT NULL DEFAULT 'directory',
    -- linked remote-workspace descriptor JSON ({uid, root, node, label});
    -- '' for ordinary sessions. ws_dirty means the private mirror may hold
    -- changes the authoritative project has not durably accepted yet.
    workspace TEXT NOT NULL DEFAULT '',
    ws_dirty INTEGER NOT NULL DEFAULT 0,
    -- whether the chat head (engine/model, node, cwd) is shown; on by default
    show_meta INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE session_drafts (
    session_id INTEGER PRIMARY KEY,
    text TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE workspace_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    exec_backend INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    ws_backend INTEGER NOT NULL,
    root TEXT NOT NULL,
    lease TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'init',
    generation INTEGER NOT NULL DEFAULT 0,
    conflicts TEXT NOT NULL DEFAULT '[]',
    resolutions TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    last_sync_at REAL,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX idx_wslinks_session ON workspace_links(exec_backend, session_id);
CREATE INDEX idx_events_session ON events(session_id, seq);
CREATE INDEX idx_websessions_exp ON web_sessions(expires_at);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            existing = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if existing is None:
                conn.executescript(SCHEMA)
            else:
                require_current_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
        except Exception:
            conn.close()
            raise
        _conn = conn
        return _conn


class SchemaMismatchError(RuntimeError):
    pass


def _quoted_identifier(value: str) -> str:
    return '"{}"'.format(value.replace('"', '""'))


def _schema_layout(conn) -> dict:
    objects = tuple(sorted(
        (str(row[0]), str(row[1])) for row in conn.execute(
            "SELECT type,name FROM sqlite_master "
            "WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%'")))
    tables = [name for kind, name in objects if kind == "table"]
    layout = {"objects": objects, "tables": {}}
    for table in tables:
        quoted_table = _quoted_identifier(table)
        columns = tuple(sorted(
            (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]),
             int(row[6]))
            for row in conn.execute("PRAGMA table_xinfo({})".format(quoted_table))))
        foreign_keys = tuple(sorted(
            (str(row[2]), str(row[3]), str(row[4]), str(row[5]),
             str(row[6]), str(row[7]))
            for row in conn.execute("PRAGMA foreign_key_list({})".format(quoted_table))))
        indexes = []
        for row in conn.execute("PRAGMA index_list({})".format(quoted_table)).fetchall():
            index_name = str(row[1])
            quoted_index = _quoted_identifier(index_name)
            index_columns = tuple(
                (int(item[1]), None if item[2] is None else str(item[2]),
                 int(item[3]), str(item[4]), int(item[5]))
                for item in
                conn.execute("PRAGMA index_xinfo({})".format(quoted_index)).fetchall())
            indexes.append((index_name, int(row[2]), str(row[3]), int(row[4]),
                            index_columns))
        layout["tables"][table] = {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": tuple(sorted(indexes)),
        }
    return layout


def require_current_schema(conn) -> None:
    """Reject a populated database unless its schema is exactly current."""
    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        expected.executescript(SCHEMA)
        wanted = _schema_layout(expected)
    finally:
        expected.close()
    if _schema_layout(conn) != wanted:
        raise SchemaMismatchError(
            "database schema is not current; update it manually before starting Puppy")
    _require_current_session_fast_state(conn)


_SESSION_FAST_PREFIX = "session_fast_mode."


def _require_current_session_fast_state(conn) -> None:
    """Validate the exact optional ledger shape: presence means boolean on."""
    session_ids = {int(row[0]) for row in conn.execute("SELECT id FROM sessions")}
    for key, value in conn.execute(
            "SELECT key,value FROM meta WHERE key GLOB 'session_fast_mode.*'"):
        suffix = str(key)[len(_SESSION_FAST_PREFIX):]
        if not suffix.isdigit() or int(suffix) not in session_ids or value != "true":
            raise SchemaMismatchError(
                "session Fast-mode state is not current; update it manually "
                "before starting Puppy")


def query(sql: str, args=()) -> list:
    with _lock:
        cur = connect().execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows


def query_one(sql: str, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, args)
        conn.commit()
        rowid = cur.lastrowid
        cur.close()
        return rowid


def backup_to(path: str) -> None:
    """Write a transactionally consistent, standalone SQLite snapshot."""
    with _lock:
        destination = sqlite3.connect(path)
        try:
            connect().backup(destination)
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            destination.commit()
        finally:
            destination.close()
    os.chmod(path, 0o600)


def replace_from(path: str) -> None:
    """Atomically install a validated database file and reconnect this process."""
    global _conn
    with _lock:
        current = _conn
        _conn = None
        if current is not None:
            try:
                current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            current.close()
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(config.DB_PATH + suffix)
            except FileNotFoundError:
                pass
        try:
            os.replace(path, config.DB_PATH)
            os.chmod(config.DB_PATH, 0o600)
            connect()
        except Exception:
            # If replacement itself failed, the old path is still usable. If
            # opening the new file failed, the caller owns rollback.
            if _conn is None and os.path.exists(config.DB_PATH):
                try:
                    connect()
                except Exception:
                    pass
            raise


# ---- meta helpers ----

def node_uuid() -> str:
    """Stable identity for this node across pairings, names, and URLs.

    Lets a controller notice that two backend records - or a backend and the
    controller itself - are one machine, so a "remote" workspace on the same
    node degrades to a plain direct-directory session instead of a mirror.
    """
    value = meta_get("node.uuid")
    if not isinstance(value, str) or len(value) != 32:
        value = uuid.uuid4().hex
        meta_set("node.uuid", value)
    return value


def meta_get(key: str, default=None):
    row = query_one("SELECT value FROM meta WHERE key=?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def meta_set(key: str, value) -> None:
    execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))


def meta_apply(upserts=None, delete_keys=(), delete_globs=()) -> None:
    """Atomically apply a small related set of durable meta changes."""
    with _lock:
        conn = connect()
        try:
            for key, value in (upserts or {}).items():
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(key), json.dumps(value)))
            for key in delete_keys:
                conn.execute("DELETE FROM meta WHERE key=?", (str(key),))
            for pattern in delete_globs:
                conn.execute("DELETE FROM meta WHERE key GLOB ?",
                             (str(pattern),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def meta_del(key: str) -> None:
    execute("DELETE FROM meta WHERE key=?", (key,))


# ---- session helpers ----

def create_session(name: str, engine: str, cwd: str, model: str, effort: str,
                   color: str, permission_mode: str,
                   workspace_kind: str = "directory",
                   workspace: str = "") -> int:
    """Create a session and assign its sticky order in one transaction."""
    with _lock:
        conn = connect()
        now = time.time()
        cursor = None
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(CASE WHEN sort_order>0 THEN sort_order END),0)+1 "
                "AS n FROM sessions").fetchone()
            cursor = conn.execute(
                "INSERT INTO sessions(name,engine,cwd,model,effort,color,permission_mode,"
                "workspace_kind,workspace,sort_order,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, engine, cwd, model, effort, color, permission_mode,
                 workspace_kind, workspace, row["n"], now, now))
            session_id = int(cursor.lastrowid)
            pinned, unpinned = _session_order_lists(conn)
            _write_session_order(conn, pinned, unpinned)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
    _notify_change(session_id)
    return session_id


def session_row_to_dict(row) -> dict:
    d = dict(row)
    d["archived"] = bool(d.get("archived"))
    raw_fast = d.pop("_fast_mode", None)
    if raw_fast not in (None, "true"):
        raise SchemaMismatchError("session Fast-mode state is not current")
    d["fast_mode"] = raw_fast == "true"
    # The sign is deliberately part of the existing order value instead of a
    # new persisted column: old positive rows remain ordinary sessions and
    # the exact database shape stays unchanged.
    d["pinned"] = int(d.get("sort_order") or 0) < 0
    return d


def get_session(session_id: int):
    row = query_one(
        "SELECT sessions.*,fast.value AS _fast_mode FROM sessions "
        "LEFT JOIN meta AS fast ON fast.key=?||sessions.id WHERE sessions.id=?",
        (_SESSION_FAST_PREFIX, session_id))
    return session_row_to_dict(row) if row else None


def list_sessions(include_archived: bool = False) -> list:
    # Negative values are the manually ordered pinned block; non-negative
    # values are the activity/manual order below it.
    sql = ("SELECT sessions.*,fast.value AS _fast_mode FROM sessions "
           "LEFT JOIN meta AS fast ON fast.key=?||sessions.id" +
           ("" if include_archived else " WHERE sessions.archived=0") +
           " ORDER BY sessions.sort_order,sessions.id")
    return [session_row_to_dict(r) for r in query(sql, (_SESSION_FAST_PREFIX,))]


class SessionOrderConflict(RuntimeError):
    """A reorder was based on a session list that is no longer current."""


def _session_order_lists(conn) -> tuple:
    """Return every session id, including archived rows, split by pin state."""
    rows = conn.execute(
        "SELECT id,sort_order FROM sessions ORDER BY sort_order,id").fetchall()
    return (
        [int(row["id"]) for row in rows if int(row["sort_order"]) < 0],
        [int(row["id"]) for row in rows if int(row["sort_order"]) >= 0],
    )


def _write_session_order(conn, pinned: list, unpinned: list) -> None:
    """Write the canonical dense representation without touching timestamps."""
    pinned_count = len(pinned)
    ordered = [(sid, index - pinned_count)
               for index, sid in enumerate(pinned)]
    ordered.extend((sid, index + 1) for index, sid in enumerate(unpinned))
    for sid, value in ordered:
        conn.execute(
            "UPDATE sessions SET sort_order=? WHERE id=? AND sort_order<>?",
            (value, int(sid), value))


def _validated_session_ids(value, label: str) -> list:
    if not isinstance(value, list) or any(type(sid) is not int or sid <= 0
                                          for sid in value):
        raise ValueError("{} must be a list of session ids".format(label))
    if len(set(value)) != len(value):
        raise ValueError("{} contains duplicate session ids".format(label))
    return list(value)


def reorder_sessions(ids: list, expected_order=None,
                     expected_pinned=None) -> list:
    """Apply one full order without ever allowing a row across the pin edge.

    New clients provide the order and pin cohort they began dragging from, so
    another console's activation, pin, reorder, creation, or deletion wins
    cleanly instead of being overwritten. Older clients may omit those compare
    values; the authoritative stable partition still protects the boundary.
    """
    requested = _validated_session_ids(ids, "order")
    before = (None if expected_order is None else
              _validated_session_ids(expected_order, "expected_order"))
    before_pinned = (None if expected_pinned is None else
                     _validated_session_ids(expected_pinned, "expected_pinned"))
    with _lock:
        conn = connect()
        try:
            pinned, unpinned = _session_order_lists(conn)
            current = pinned + unpinned
            if len(requested) != len(current) or set(requested) != set(current):
                raise SessionOrderConflict("session list changed on this node")
            if before is not None and before != current:
                raise SessionOrderConflict("session order changed on this node")
            if before_pinned is not None and before_pinned != pinned:
                raise SessionOrderConflict("session pins changed on this node")
            pinned_set = set(pinned)
            next_pinned = [sid for sid in requested if sid in pinned_set]
            next_unpinned = [sid for sid in requested if sid not in pinned_set]
            _write_session_order(conn, next_pinned, next_unpinned)
            conn.commit()
            return next_pinned + next_unpinned
        except Exception:
            conn.rollback()
            raise


def set_session_pinned(session_id: int, pinned: bool,
                       expected=None) -> bool:
    """Set one pin and return whether it changed.

    A new pin joins the foot of the pinned block, preserving priorities the
    user already arranged. An unpinned row enters at the head of the ordinary
    block, the same place its next activity promotion would put it.
    """
    if type(session_id) is not int or session_id <= 0 or type(pinned) is not bool:
        raise ValueError("invalid session pin")
    if expected is not None and type(expected) is not bool:
        raise ValueError("expected_pinned must be true or false")
    with _lock:
        conn = connect()
        try:
            pinned_ids, unpinned_ids = _session_order_lists(conn)
            if session_id in pinned_ids:
                current = True
            elif session_id in unpinned_ids:
                current = False
            else:
                raise SessionOrderConflict("session list changed on this node")
            if expected is not None and expected is not current:
                raise SessionOrderConflict("session pin changed on this node")
            if pinned is current:
                return False
            if pinned:
                unpinned_ids.remove(session_id)
                pinned_ids.append(session_id)
            else:
                pinned_ids.remove(session_id)
                unpinned_ids.insert(0, session_id)
            _write_session_order(conn, pinned_ids, unpinned_ids)
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def bump_session_to_top(session_id: int) -> None:
    """Move one ordinary session to the front below every pinned session.

    The runner calls this on every idle -> running transition, so the
    order is a move-to-front history of activity that the user's manual
    drag-and-drop then edits. Pinned sessions and their manual order never
    move merely because work starts."""
    with _lock:
        conn = connect()
        try:
            pinned, unpinned = _session_order_lists(conn)
            sid = int(session_id)
            if sid in pinned or sid not in unpinned or unpinned[0] == sid:
                return
            unpinned.remove(sid)
            unpinned.insert(0, sid)
            _write_session_order(conn, pinned, unpinned)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_session_draft(session_id: int) -> dict:
    """Return the authoritative composer draft and its monotonic version."""
    row = query_one(
        "SELECT text,revision,updated_at FROM session_drafts WHERE session_id=?",
        (int(session_id),))
    if row is None:
        return {"text": "", "revision": 0, "updated_at": None}
    return {
        "text": str(row["text"] or ""),
        "revision": max(0, int(row["revision"])),
        "updated_at": float(row["updated_at"]),
    }


def set_session_draft(session_id: int, text: str) -> tuple:
    """Replace one draft and return ``(previous, current)`` atomically.

    Revisions advance even when two clients submit the same text. This gives
    every accepted socket edit a unique total order and its sender an
    unambiguous acknowledgement.
    """
    if not isinstance(text, str):
        raise TypeError("draft text must be text")
    if len(text) > MAX_DRAFT_CHARS:
        raise ValueError("draft cannot exceed {} characters".format(MAX_DRAFT_CHARS))
    session_id = int(session_id)
    with _lock:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT text,revision,updated_at FROM session_drafts WHERE session_id=?",
                (session_id,)).fetchone()
            previous = {
                "text": str(row["text"] or "") if row else "",
                "revision": max(0, int(row["revision"])) if row else 0,
                "updated_at": float(row["updated_at"]) if row else None,
            }
            revision = previous["revision"] + 1
            updated_at = time.time()
            conn.execute(
                "INSERT INTO session_drafts(session_id,text,revision,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at",
                (session_id, text, revision, updated_at))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return previous, {
        "text": text, "revision": revision, "updated_at": updated_at,
    }


def consume_session_draft(session_id: int, expected_text: str) -> tuple:
    """Clear a submitted draft only if no other client has replaced it.

    Returns ``(consumed, previous, current)``. A mismatch is intentionally a
    no-op: another device's newer contents must survive this device sending an
    older shared value.
    """
    if not isinstance(expected_text, str):
        raise TypeError("draft text must be text")
    if len(expected_text) > MAX_DRAFT_CHARS:
        raise ValueError("draft cannot exceed {} characters".format(MAX_DRAFT_CHARS))
    session_id = int(session_id)
    with _lock:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT text,revision,updated_at FROM session_drafts WHERE session_id=?",
                (session_id,)).fetchone()
            previous = {
                "text": str(row["text"] or "") if row else "",
                "revision": max(0, int(row["revision"])) if row else 0,
                "updated_at": float(row["updated_at"]) if row else None,
            }
            if row is None or previous["text"] != expected_text:
                return False, previous, dict(previous)
            revision = previous["revision"] + 1
            updated_at = time.time()
            conn.execute(
                "UPDATE session_drafts SET text='',revision=?,updated_at=? WHERE session_id=?",
                (revision, updated_at, session_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True, previous, {
        "text": "", "revision": revision, "updated_at": updated_at,
    }


def touch_session(session_id: int, **fields) -> None:
    missing = object()
    fast_mode = fields.pop("fast_mode", missing)
    fast_supplied = fast_mode is not missing
    if fast_supplied and (type(fast_mode) not in (bool, int) or fast_mode not in (0, 1)):
        raise ValueError("fast_mode must be boolean")
    fields["updated_at"] = time.time()
    keys = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        conn = connect()
        try:
            changed = conn.execute(f"UPDATE sessions SET {keys} WHERE id=?",
                                   (*fields.values(), session_id)).rowcount
            if fast_supplied and bool(fast_mode) and changed:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_SESSION_FAST_PREFIX + str(int(session_id)), "true"))
            elif fast_supplied:
                conn.execute("DELETE FROM meta WHERE key=?",
                             (_SESSION_FAST_PREFIX + str(int(session_id)),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if "name" in fields:
        _notify_change(session_id)


def add_event(session_id: int, kind: str, data: dict) -> dict:
    with _lock:
        conn = connect()
        row = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM events WHERE session_id=?",
                           (session_id,)).fetchone()
        seq = row["s"]
        ts = time.time()
        conn.execute("INSERT INTO events(session_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                     (session_id, seq, kind, json.dumps(data), ts))
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, session_id))
        conn.commit()
    _notify_change(session_id)
    return {"seq": seq, "kind": kind, "ts": ts, "data": data}


def get_events(session_id: int, before_seq=None, limit: int = 200,
               after_seq=None) -> list:
    """Return up to ``limit`` events in ascending seq order.

    Without a cursor these are the newest events; ``before_seq`` pages back
    from there, and ``after_seq`` pages forward, which lets a console load a
    window of history around one event rather than paging back to it.
    """
    if after_seq is not None:
        rows = query("SELECT seq,kind,payload,created_at FROM events WHERE session_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
                     (session_id, after_seq, limit))
        ordered = rows
    elif before_seq is not None:
        rows = query("SELECT seq,kind,payload,created_at FROM events WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                     (session_id, before_seq, limit))
        ordered = reversed(rows)
    else:
        rows = query("SELECT seq,kind,payload,created_at FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?",
                     (session_id, limit))
        ordered = reversed(rows)
    out = []
    for r in ordered:
        try:
            data = json.loads(r["payload"])
        except Exception:
            data = {}
        out.append({"seq": r["seq"], "kind": r["kind"], "ts": r["created_at"], "data": data})
    return out


def delete_session(session_id: int) -> None:
    with _lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM session_drafts WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            # the durable queue/held record rides under this session's meta key
            conn.execute("DELETE FROM meta WHERE key=?",
                         ("session_queue.{}".format(session_id),))
            conn.execute("DELETE FROM meta WHERE key=?",
                         ("session_completion.{}".format(session_id),))
            # a pending engine-context rollback belongs to the session that queued
            # it (drivers.base._UNDO_STATE_KEY), and outlives it otherwise
            conn.execute("DELETE FROM meta WHERE key=?",
                         ("session_undo.{}".format(session_id),))
            conn.execute("DELETE FROM meta WHERE key=?",
                         (_SESSION_FAST_PREFIX + str(int(session_id)),))
            pinned, unpinned = _session_order_lists(conn)
            _write_session_order(conn, pinned, unpinned)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    _notify_change(session_id)
