"""SQLite storage (stdlib sqlite3, WAL). Single shared connection guarded by a lock."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

from puppy import config

log = logging.getLogger("puppy.db")

_conn = None
_lock = threading.RLock()

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
    -- model/effort the last turn actually ran with, JSON; '' until one has
    used_config TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    archived INTEGER NOT NULL DEFAULT 0,
    workspace_kind TEXT NOT NULL DEFAULT 'directory',
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


# ---- session helpers ----

def create_session(name: str, engine: str, cwd: str, model: str, effort: str,
                   color: str, permission_mode: str,
                   workspace_kind: str = "directory") -> int:
    """Create a session and assign its sticky order in one transaction."""
    with _lock:
        conn = connect()
        now = time.time()
        cursor = None
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM sessions").fetchone()
            cursor = conn.execute(
                "INSERT INTO sessions(name,engine,cwd,model,effort,color,permission_mode,"
                "workspace_kind,sort_order,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name, engine, cwd, model, effort, color, permission_mode,
                 workspace_kind, row["n"], now, now))
            session_id = int(cursor.lastrowid)
            conn.commit()
            return session_id
        except Exception:
            conn.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()


def session_row_to_dict(row) -> dict:
    d = dict(row)
    d["archived"] = bool(d.get("archived"))
    return d


def get_session(session_id: int):
    row = query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    return session_row_to_dict(row) if row else None


def list_sessions(include_archived: bool = False) -> list:
    # sticky user-defined order (drag & drop), not recency - sessions must not jump around
    sql = "SELECT * FROM sessions" + ("" if include_archived else " WHERE archived=0") + " ORDER BY sort_order, id"
    return [session_row_to_dict(r) for r in query(sql)]


def reorder_sessions(ids: list) -> None:
    with _lock:
        conn = connect()
        for i, sid in enumerate(ids):
            conn.execute("UPDATE sessions SET sort_order=? WHERE id=?", (i + 1, int(sid)))
        conn.commit()


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
    fields["updated_at"] = time.time()
    keys = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE sessions SET {keys} WHERE id=?", (*fields.values(), session_id))


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
    return {"seq": seq, "kind": kind, "ts": ts, "data": data}


def get_events(session_id: int, before_seq=None, limit: int = 200) -> list:
    if before_seq is not None:
        rows = query("SELECT seq,kind,payload,created_at FROM events WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                     (session_id, before_seq, limit))
    else:
        rows = query("SELECT seq,kind,payload,created_at FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?",
                     (session_id, limit))
    out = []
    for r in reversed(rows):
        try:
            data = json.loads(r["payload"])
        except Exception:
            data = {}
        out.append({"seq": r["seq"], "kind": r["kind"], "ts": r["created_at"], "data": data})
    return out


def delete_session(session_id: int) -> None:
    with _lock:
        conn = connect()
        conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM session_drafts WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        # the durable queue/held record rides under this session's meta key
        conn.execute("DELETE FROM meta WHERE key=?",
                     ("session_queue.{}".format(session_id),))
        conn.commit()
