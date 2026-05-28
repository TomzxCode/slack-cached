"""SQLite storage layer for cached Slack threads.

Schema:

  threads
    channel        TEXT    not null
    thread_ts      TEXT    not null
    last_fetched   REAL    not null    unix epoch seconds
    latest_reply   TEXT    nullable    highest ts seen so far for this thread
    PRIMARY KEY (channel, thread_ts)

  messages
    channel        TEXT    not null
    thread_ts      TEXT    not null    root ts of the thread
    ts             TEXT    not null    this message's ts (unique within thread)
    user           TEXT    nullable
    text           TEXT    nullable
    payload        TEXT    not null    full JSON-encoded Slack message
    PRIMARY KEY (channel, thread_ts, ts)
    FOREIGN KEY (channel, thread_ts) REFERENCES threads(channel, thread_ts)

  users
    id             TEXT    not null    Slack user id (e.g. U123)
    name           TEXT    nullable    the user's handle (the 'name' field)
    real_name      TEXT    nullable    the user's display/real name
    fetched_at     REAL    not null    unix epoch seconds of last fetch
    payload        TEXT    not null    full JSON-encoded Slack user
    PRIMARY KEY (id)

  channels
    id             TEXT    not null    Slack channel id (e.g. C123)
    name           TEXT    nullable    the channel name (the 'name' field)
    is_private     INTEGER nullable    1 if private, 0 if public, null if unknown
    fetched_at     REAL    not null    unix epoch seconds of last fetch
    payload        TEXT    not null    full JSON-encoded Slack channel
    PRIMARY KEY (id)
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    channel       TEXT NOT NULL,
    thread_ts     TEXT NOT NULL,
    last_fetched  REAL NOT NULL,
    latest_reply  TEXT,
    PRIMARY KEY (channel, thread_ts)
);

CREATE TABLE IF NOT EXISTS messages (
    channel    TEXT NOT NULL,
    thread_ts  TEXT NOT NULL,
    ts         TEXT NOT NULL,
    user       TEXT,
    text       TEXT,
    payload    TEXT NOT NULL,
    PRIMARY KEY (channel, thread_ts, ts),
    FOREIGN KEY (channel, thread_ts) REFERENCES threads(channel, thread_ts)
);

CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages (channel, thread_ts, ts);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT NOT NULL,
    name        TEXT,
    real_name   TEXT,
    fetched_at  REAL NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS channels (
    id          TEXT NOT NULL,
    name        TEXT,
    is_private  INTEGER,
    fetched_at  REAL NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (id)
);
"""


@dataclass(frozen=True)
class ThreadState:
    """Cached state for a thread, used to decide what to refetch."""

    channel: str
    thread_ts: str
    last_fetched: float
    latest_reply: str | None


@dataclass(frozen=True)
class CachedMessage:
    """A single cached Slack message as returned by show()."""

    ts: str
    user: str | None
    text: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class CachedUser:
    """A single cached Slack user."""

    id: str
    name: str | None
    real_name: str | None
    fetched_at: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class CachedChannel:
    """A single cached Slack channel/conversation."""

    id: str
    name: str | None
    is_private: bool | None
    fetched_at: float
    payload: dict[str, Any]


def _normalize_sql(statement: str) -> str:
    """Collapse whitespace in a SQL statement for compact logging."""
    return " ".join(statement.split())


def _log_sql(statement: str, duration_s: float) -> None:
    """Log a completed SQL statement and how long it took.

    Emits at debug level, so the statements only surface when verbose
    (debug) logging is enabled. The duration is reported in milliseconds.
    """
    log.debug(
        "sql",
        statement=_normalize_sql(statement),
        duration_ms=round(duration_s * 1000, 3),
    )


class _LoggingCursor(sqlite3.Cursor):
    """Cursor that logs each executed statement with its duration."""

    def execute(self, sql: str, parameters: Any = (), /) -> _LoggingCursor:
        start = time.perf_counter()
        try:
            return super().execute(sql, parameters)
        finally:
            _log_sql(sql, time.perf_counter() - start)

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> _LoggingCursor:
        start = time.perf_counter()
        try:
            return super().executemany(sql, seq_of_parameters)
        finally:
            _log_sql(sql, time.perf_counter() - start)


class _LoggingConnection(sqlite3.Connection):
    """Connection whose execute helpers go through _LoggingCursor."""

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:
        return super().cursor(factory or _LoggingCursor)

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        cur = self.cursor()
        return cur.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        cur = self.cursor()
        return cur.executemany(sql, parameters)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        start = time.perf_counter()
        try:
            cur = sqlite3.Connection.cursor(self)
            return cur.executescript(sql_script)
        finally:
            _log_sql(sql_script, time.perf_counter() - start)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (and initialize) the SQLite database at db_path."""
    log.debug("db_connect_start", db_path=str(db_path))

    start = time.perf_counter()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.debug("db_mkdir_done", duration_ms=round((time.perf_counter() - start) * 1000, 3))

    start = time.perf_counter()
    conn = sqlite3.connect(db_path, factory=_LoggingConnection)
    conn.row_factory = sqlite3.Row
    log.debug("db_open_done", duration_ms=round((time.perf_counter() - start) * 1000, 3))

    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)

    log.debug("db_connect_done", db_path=str(db_path))
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside a transaction, committing on success."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_thread_state(conn: sqlite3.Connection, channel: str, thread_ts: str) -> ThreadState | None:
    """Return the cached state for the given thread, or None if missing."""
    row = conn.execute(
        "SELECT channel, thread_ts, last_fetched, latest_reply "
        "FROM threads WHERE channel = ? AND thread_ts = ?",
        (channel, thread_ts),
    ).fetchone()
    if row is None:
        return None
    return ThreadState(
        channel=row["channel"],
        thread_ts=row["thread_ts"],
        last_fetched=row["last_fetched"],
        latest_reply=row["latest_reply"],
    )


def upsert_messages(
    conn: sqlite3.Connection,
    channel: str,
    thread_ts: str,
    messages: Iterable[dict[str, Any]],
) -> int:
    """Insert or replace messages for a thread; returns the count written."""
    rows = [
        (
            channel,
            thread_ts,
            msg["ts"],
            msg.get("user"),
            msg.get("text"),
            json.dumps(msg, ensure_ascii=False, sort_keys=True),
        )
        for msg in messages
    ]
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO messages "
        "(channel, thread_ts, ts, user, text, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def record_thread_refresh(
    conn: sqlite3.Connection,
    channel: str,
    thread_ts: str,
    latest_reply: str | None,
    now: float | None = None,
) -> None:
    """Insert or update the thread row to record this refresh."""
    ts_now = time.time() if now is None else now
    conn.execute(
        "INSERT INTO threads (channel, thread_ts, last_fetched, latest_reply) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(channel, thread_ts) DO UPDATE SET "
        "  last_fetched = excluded.last_fetched, "
        "  latest_reply = excluded.latest_reply",
        (channel, thread_ts, ts_now, latest_reply),
    )


def load_thread_messages(
    conn: sqlite3.Connection, channel: str, thread_ts: str
) -> list[CachedMessage]:
    """Return all cached messages for a thread, ordered chronologically by ts."""
    rows = conn.execute(
        "SELECT ts, user, text, payload FROM messages "
        "WHERE channel = ? AND thread_ts = ? "
        "ORDER BY CAST(ts AS REAL) ASC",
        (channel, thread_ts),
    ).fetchall()
    return [
        CachedMessage(
            ts=row["ts"],
            user=row["user"],
            text=row["text"],
            payload=json.loads(row["payload"]),
        )
        for row in rows
    ]


def count_messages(conn: sqlite3.Connection, channel: str, thread_ts: str) -> int:
    """Return the number of cached messages for a thread."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE channel = ? AND thread_ts = ?",
        (channel, thread_ts),
    ).fetchone()
    return int(row["n"]) if row else 0


def upsert_users(
    conn: sqlite3.Connection,
    users: Iterable[dict[str, Any]],
    now: float | None = None,
) -> int:
    """Insert or replace users; returns the count written."""
    fetched_at = time.time() if now is None else now
    rows = [
        (
            user["id"],
            user.get("name"),
            user.get("real_name") or (user.get("profile") or {}).get("real_name"),
            fetched_at,
            json.dumps(user, ensure_ascii=False, sort_keys=True),
        )
        for user in users
    ]
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO users "
        "(id, name, real_name, fetched_at, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def upsert_channels(
    conn: sqlite3.Connection,
    channels: Iterable[dict[str, Any]],
    now: float | None = None,
) -> int:
    """Insert or replace channels; returns the count written."""
    fetched_at = time.time() if now is None else now
    rows = [
        (
            channel["id"],
            channel.get("name"),
            _bool_to_int(channel.get("is_private")),
            fetched_at,
            json.dumps(channel, ensure_ascii=False, sort_keys=True),
        )
        for channel in channels
    ]
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO channels "
        "(id, name, is_private, fetched_at, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def get_user(conn: sqlite3.Connection, user_id: str) -> CachedUser | None:
    """Return the cached user with the given id, or None if missing."""
    row = conn.execute(
        "SELECT id, name, real_name, fetched_at, payload FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def get_channel(conn: sqlite3.Connection, channel_id: str) -> CachedChannel | None:
    """Return the cached channel with the given id, or None if missing."""
    row = conn.execute(
        "SELECT id, name, is_private, fetched_at, payload FROM channels WHERE id = ?",
        (channel_id,),
    ).fetchone()
    return _row_to_channel(row) if row is not None else None


def load_users(conn: sqlite3.Connection) -> list[CachedUser]:
    """Return all cached users, ordered by id."""
    rows = conn.execute(
        "SELECT id, name, real_name, fetched_at, payload FROM users ORDER BY id ASC"
    ).fetchall()
    return [_row_to_user(row) for row in rows]


def load_user_display_names(conn: sqlite3.Connection, user_ids: Iterable[str]) -> dict[str, str]:
    """Return a {user_id: display_name} map for just the requested users.

    This avoids loading and JSON-decoding every user's full payload: only the
    denormalized name columns of the rows matching user_ids are read, so cost
    scales with the thread's participants rather than the whole workspace.

    The display name is formatted as "Real name (handle)" when both are known.
    When only one is present that value is used alone, and when neither is known
    the value falls back to the id.
    """
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return {}
    names: dict[str, str] = {}
    # Chunk to stay well under SQLite's bound-variable limit (default 999).
    for start in range(0, len(ids), 900):
        chunk = ids[start : start + 900]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, name, real_name FROM users WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            names[row["id"]] = _format_display_name(row["real_name"], row["name"], row["id"])
    return names


def _format_display_name(real_name: str | None, name: str | None, user_id: str) -> str:
    """Combine a user's real name and handle into "Real name (handle)".

    Falls back to whichever single value is available, and finally to the id.
    """
    if real_name and name:
        return f"{real_name} ({name})"
    return real_name or name or user_id


def load_channels(conn: sqlite3.Connection) -> list[CachedChannel]:
    """Return all cached channels, ordered by id."""
    rows = conn.execute(
        "SELECT id, name, is_private, fetched_at, payload FROM channels ORDER BY id ASC"
    ).fetchall()
    return [_row_to_channel(row) for row in rows]


def count_users(conn: sqlite3.Connection) -> int:
    """Return the number of cached users."""
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def count_channels(conn: sqlite3.Connection) -> int:
    """Return the number of cached channels."""
    row = conn.execute("SELECT COUNT(*) AS n FROM channels").fetchone()
    return int(row["n"]) if row else 0


def _bool_to_int(value: Any) -> int | None:
    """Map a Slack boolean-ish value to 0/1, preserving None."""
    if value is None:
        return None
    return 1 if value else 0


def _row_to_user(row: sqlite3.Row) -> CachedUser:
    return CachedUser(
        id=row["id"],
        name=row["name"],
        real_name=row["real_name"],
        fetched_at=row["fetched_at"],
        payload=json.loads(row["payload"]),
    )


def _row_to_channel(row: sqlite3.Row) -> CachedChannel:
    is_private = row["is_private"]
    return CachedChannel(
        id=row["id"],
        name=row["name"],
        is_private=None if is_private is None else bool(is_private),
        fetched_at=row["fetched_at"],
        payload=json.loads(row["payload"]),
    )
