"""
db.py — shared SQLite database layer.

One DB, one messages table, namespaced by backend.
All timestamps stored as integer milliseconds since Unix epoch.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DAEMON_DIR = os.path.expanduser("~/.messaging_daemon")
DB_PATH = os.path.join(DAEMON_DIR, "messages.db")


def init_db() -> sqlite3.Connection:
    os.makedirs(DAEMON_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id           INTEGER PRIMARY KEY,
            backend      TEXT    NOT NULL,
            account      TEXT    NOT NULL,
            uid          TEXT    NOT NULL,
            sender       TEXT,
            sender_name  TEXT,
            recipient    TEXT,
            subject      TEXT,
            body         TEXT,
            thread_id    TEXT,
            timestamp_ms INTEGER,
            received_at  INTEGER NOT NULL,
            metadata     TEXT,
            UNIQUE(backend, account, uid)
        )
    """)
    db.commit()
    return db


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def store_message(db: sqlite3.Connection, msg: dict) -> bool:
    """
    Insert a normalised message dict. Returns True if a new row was inserted.

    Required keys: backend, account, uid
    Optional keys: sender, sender_name, recipient, subject, body,
                   thread_id, timestamp_ms, metadata (any dict/str)
    """
    metadata = msg.get("metadata")
    if isinstance(metadata, dict):
        metadata = json.dumps(metadata)

    db.execute(
        """INSERT OR IGNORE INTO messages
           (backend, account, uid, sender, sender_name, recipient,
            subject, body, thread_id, timestamp_ms, received_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            msg["backend"],
            msg["account"],
            msg["uid"],
            msg.get("sender"),
            msg.get("sender_name"),
            msg.get("recipient"),
            msg.get("subject"),
            msg.get("body"),
            msg.get("thread_id"),
            msg.get("timestamp_ms"),
            now_ms(),
            metadata,
        ),
    )
    inserted = db.execute("SELECT changes()").fetchone()[0] == 1
    db.commit()
    return inserted


def query_messages(
    backend: str | None = None,
    account: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
    thread_id: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    limit: int = 100,
) -> list[dict]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    clauses: list[str] = ["body IS NOT NULL"]
    params: list = []

    if backend:
        clauses.append("backend = ?")
        params.append(backend)
    if account:
        clauses.append("account = ?")
        params.append(account)
    if sender:
        clauses.append("(sender LIKE ? OR sender_name LIKE ?)")
        params.extend([f"%{sender}%", f"%{sender}%"])
    if subject:
        clauses.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if thread_id:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    if since_ms is not None:
        clauses.append("timestamp_ms >= ?")
        params.append(since_ms)
    if until_ms is not None:
        clauses.append("timestamp_ms <= ?")
        params.append(until_ms)

    sql = (
        f"SELECT * FROM messages WHERE {' AND '.join(clauses)}"
        f" ORDER BY timestamp_ms DESC LIMIT ?"
    )
    params.append(limit)

    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_config(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
    )
    db.commit()
