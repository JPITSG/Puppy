"""SQLite storage (stdlib sqlite3, WAL). Single shared connection guarded by a lock."""
from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time

from puppy import config

log = logging.getLogger("puppy.db")

_conn = None
_lock = threading.RLock()

# selectable session dot colors - gray is reserved for the Settings tab
SESSION_COLORS = ["#e0784f", "#4dd0c4", "#9d7bff", "#4dc6ff", "#22e5a4",
                  "#ff6b81", "#f0a84a", "#e8d24b", "#7ea2ff", "#ff9ad5"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    pwhash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS backends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    token TEXT NOT NULL,
    protocol INTEGER NOT NULL DEFAULT 0,
    capabilities TEXT NOT NULL DEFAULT '[]',
    remote_version TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    tls_fingerprint TEXT NOT NULL DEFAULT '',
    auto_upgrade INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
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
    status TEXT NOT NULL DEFAULT 'idle',
    archived INTEGER NOT NULL DEFAULT 0,
    workspace_kind TEXT NOT NULL DEFAULT 'directory',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_websessions_exp ON web_sessions(expires_at);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _conn = conn
        return _conn


def _migrate(conn) -> None:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
    if "effort" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN effort TEXT NOT NULL DEFAULT ''")
    if "sort_order" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE sessions SET sort_order=id")
    if "color" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN color TEXT NOT NULL DEFAULT ''")
        for (sid,) in conn.execute("SELECT id FROM sessions").fetchall():
            conn.execute("UPDATE sessions SET color=? WHERE id=?",
                         (random.choice(SESSION_COLORS), sid))
    if "workspace_kind" not in cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN workspace_kind TEXT NOT NULL DEFAULT 'directory'")
    backend_cols = {r["name"] for r in conn.execute("PRAGMA table_info(backends)")}
    if "protocol" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN protocol INTEGER NOT NULL DEFAULT 0")
    if "capabilities" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]'")
    if "remote_version" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN remote_version TEXT NOT NULL DEFAULT ''")
    if "role" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN role TEXT NOT NULL DEFAULT ''")
    if "tls_fingerprint" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN tls_fingerprint TEXT NOT NULL DEFAULT ''")
    if "auto_upgrade" not in backend_cols:
        conn.execute("ALTER TABLE backends ADD COLUMN auto_upgrade INTEGER NOT NULL DEFAULT 0")


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
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
