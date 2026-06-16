"""SQLite storage for slack-watcher queries, runs, and settings.

Schema:

  queries
    id             TEXT    primary key    uuid
    name           TEXT    not null
    source_kind    TEXT    not null       one of: channels, dms, mentions
    source_config  TEXT    not null       JSON, kind-specific
    prompt         TEXT    not null       prompt template
    interval       TEXT    not null       duration string e.g. "5m"
    lookback       TEXT    not null       duration string e.g. "1h"
    dedup          TEXT    not null       new_messages|every_cycle|once_per_thread
    full_threads   INTEGER not null       0/1, fetch replies for thread parents
    model          TEXT    not null       OpenAI-compatible model id
    enabled        INTEGER not null       0/1
    created_at     REAL    not null       epoch seconds
    updated_at     REAL    not null       epoch seconds

  runs
    id             TEXT    primary key    uuid
    query_id       TEXT    not null       FK -> queries(id)
    channel        TEXT    not null
    thread_ts      TEXT    not null
    prompt         TEXT    not null       rendered prompt snapshot
    response       TEXT    nullable       LLM response (null on error)
    error          TEXT    nullable       error message if failed
    model          TEXT    not null
    elapsed_ms     INTEGER not null
    prompt_tokens  INTEGER nullable
    completion_tokens INTEGER nullable
    ran_at         REAL    not null       epoch seconds

  query_state
    query_id       TEXT    not null       FK -> queries(id)
    channel        TEXT    not null
    thread_ts      TEXT    not null
    last_seen_ts   TEXT    nullable       highest message ts processed for this thread
    last_run_at    REAL    not null       epoch seconds of last run for this thread
    processed      INTEGER not null       0/1, used by once_per_thread
    PRIMARY KEY (query_id, channel, thread_ts)

  settings
    key    TEXT primary key
    value  TEXT not null

Known settings keys:
  - llm_base_url      (default: https://api.openai.com/v1)
  - llm_api_key       (default: empty)
  - slack_api_base_url (default: https://slack.com/api, falls back to env)
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    source_config TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    lookback      TEXT NOT NULL,
    dedup         TEXT NOT NULL,
    full_threads  INTEGER NOT NULL,
    model         TEXT NOT NULL,
    enabled       INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    query_id         TEXT NOT NULL,
    channel          TEXT NOT NULL,
    thread_ts        TEXT NOT NULL,
    prompt           TEXT NOT NULL,
    response         TEXT,
    error            TEXT,
    model            TEXT NOT NULL,
    elapsed_ms       INTEGER NOT NULL,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    ran_at           REAL NOT NULL,
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_query ON runs (query_id, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_ran_at ON runs (ran_at DESC);

CREATE TABLE IF NOT EXISTS query_state (
    query_id     TEXT NOT NULL,
    channel      TEXT NOT NULL,
    thread_ts    TEXT NOT NULL,
    last_seen_ts TEXT,
    last_run_at  REAL NOT NULL,
    processed    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (query_id, channel, thread_ts),
    FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class QueryRow:
    id: str
    name: str
    source_kind: str
    source_config: dict[str, Any]
    prompt: str
    interval: str
    lookback: str
    dedup: str
    full_threads: bool
    model: str
    enabled: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class RunRow:
    id: str
    query_id: str
    channel: str
    thread_ts: str
    prompt: str
    response: str | None
    error: str | None
    model: str
    elapsed_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    ran_at: float


@dataclass(frozen=True)
class QueryStateRow:
    query_id: str
    channel: str
    thread_ts: str
    last_seen_ts: str | None
    last_run_at: float
    processed: bool


VALID_SOURCE_KINDS = {"channels", "dms", "mentions"}
VALID_DEDUP_STRATEGIES = {"new_messages", "every_cycle", "once_per_thread"}


def validate_source(kind: str, config: dict[str, Any]) -> None:
    """Raise ValueError if a source config is malformed."""
    if kind == "channels":
        ids = config.get("channel_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) and x for x in ids):
            raise ValueError("channels source requires 'channel_ids': list[str]")
    elif kind in {"dms", "mentions"}:
        # dms/mentions accept an optional include_mpim flag; no required fields.
        return
    else:
        raise ValueError(f"unknown source_kind: {kind!r}")


def validate_dedup(strategy: str) -> None:
    if strategy not in VALID_DEDUP_STRATEGIES:
        raise ValueError(f"dedup must be one of {sorted(VALID_DEDUP_STRATEGIES)}, got {strategy!r}")


def default_db_path() -> Path:
    """Return the default watcher database path."""
    import os

    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "slack-cached" / "watcher.db"
    return Path.home() / ".local" / "share" / "slack-cached" / "watcher.db"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open or create the watcher database, returning a regular Connection."""
    log.debug("watcher_db_connect", db_path=str(db_path))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --- queries ---------------------------------------------------------------


def insert_query(conn: sqlite3.Connection, q: QueryRow) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO queries
               (id, name, source_kind, source_config, prompt, interval, lookback,
                dedup, full_threads, model, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                q.id,
                q.name,
                q.source_kind,
                json.dumps(q.source_config, ensure_ascii=False),
                q.prompt,
                q.interval,
                q.lookback,
                q.dedup,
                1 if q.full_threads else 0,
                q.model,
                1 if q.enabled else 0,
                q.created_at,
                q.updated_at,
            ),
        )


def update_query(conn: sqlite3.Connection, q: QueryRow) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE queries SET
                 name = ?, source_kind = ?, source_config = ?, prompt = ?,
                 interval = ?, lookback = ?, dedup = ?, full_threads = ?,
                 model = ?, enabled = ?, updated_at = ?
               WHERE id = ?""",
            (
                q.name,
                q.source_kind,
                json.dumps(q.source_config, ensure_ascii=False),
                q.prompt,
                q.interval,
                q.lookback,
                q.dedup,
                1 if q.full_threads else 0,
                q.model,
                1 if q.enabled else 0,
                q.updated_at,
                q.id,
            ),
        )


def delete_query(conn: sqlite3.Connection, query_id: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM queries WHERE id = ?", (query_id,))


def get_query(conn: sqlite3.Connection, query_id: str) -> QueryRow | None:
    row = conn.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()
    return _row_to_query(row) if row else None


def list_queries(conn: sqlite3.Connection, only_enabled: bool = False) -> list[QueryRow]:
    sql = "SELECT * FROM queries"
    if only_enabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql).fetchall()
    return [_row_to_query(r) for r in rows]


def _row_to_query(row: sqlite3.Row) -> QueryRow:
    return QueryRow(
        id=row["id"],
        name=row["name"],
        source_kind=row["source_kind"],
        source_config=json.loads(row["source_config"]),
        prompt=row["prompt"],
        interval=row["interval"],
        lookback=row["lookback"],
        dedup=row["dedup"],
        full_threads=bool(row["full_threads"]),
        model=row["model"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# --- runs ------------------------------------------------------------------


def insert_run(conn: sqlite3.Connection, run: RunRow) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO runs
               (id, query_id, channel, thread_ts, prompt, response, error,
                model, elapsed_ms, prompt_tokens, completion_tokens, ran_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.id,
                run.query_id,
                run.channel,
                run.thread_ts,
                run.prompt,
                run.response,
                run.error,
                run.model,
                run.elapsed_ms,
                run.prompt_tokens,
                run.completion_tokens,
                run.ran_at,
            ),
        )


def list_runs(
    conn: sqlite3.Connection,
    query_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RunRow]:
    sql = "SELECT * FROM runs"
    params: list[Any] = []
    if query_id is not None:
        sql += " WHERE query_id = ?"
        params.append(query_id)
    sql += " ORDER BY ran_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_run(r) for r in rows]


def _row_to_run(row: sqlite3.Row) -> RunRow:
    return RunRow(
        id=row["id"],
        query_id=row["query_id"],
        channel=row["channel"],
        thread_ts=row["thread_ts"],
        prompt=row["prompt"],
        response=row["response"],
        error=row["error"],
        model=row["model"],
        elapsed_ms=row["elapsed_ms"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        ran_at=row["ran_at"],
    )


# --- query_state -----------------------------------------------------------


def get_query_state(
    conn: sqlite3.Connection, query_id: str, channel: str, thread_ts: str
) -> QueryStateRow | None:
    row = conn.execute(
        "SELECT * FROM query_state WHERE query_id = ? AND channel = ? AND thread_ts = ?",
        (query_id, channel, thread_ts),
    ).fetchone()
    return _row_to_state(row) if row else None


def upsert_query_state(
    conn: sqlite3.Connection,
    query_id: str,
    channel: str,
    thread_ts: str,
    last_seen_ts: str | None,
    last_run_at: float,
    processed: bool,
) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO query_state
                 (query_id, channel, thread_ts, last_seen_ts, last_run_at, processed)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(query_id, channel, thread_ts) DO UPDATE SET
                 last_seen_ts = excluded.last_seen_ts,
                 last_run_at  = excluded.last_run_at,
                 processed    = excluded.processed""",
            (
                query_id,
                channel,
                thread_ts,
                last_seen_ts,
                last_run_at,
                1 if processed else 0,
            ),
        )


def list_query_state(conn: sqlite3.Connection, query_id: str) -> list[QueryStateRow]:
    rows = conn.execute(
        "SELECT * FROM query_state WHERE query_id = ?",
        (query_id,),
    ).fetchall()
    return [_row_to_state(r) for r in rows]


def _row_to_state(row: sqlite3.Row) -> QueryStateRow:
    return QueryStateRow(
        query_id=row["query_id"],
        channel=row["channel"],
        thread_ts=row["thread_ts"],
        last_seen_ts=row["last_seen_ts"],
        last_run_at=row["last_run_at"],
        processed=bool(row["processed"]),
    )


# --- settings --------------------------------------------------------------


DEFAULT_SETTINGS: dict[str, str] = {
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "default_model": "gpt-4o-mini",
}


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def get_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


# --- helpers ---------------------------------------------------------------


def ts_to_iso(ts: float | str | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def now_epoch() -> float:
    return time.time()


def each_query_state_for(
    conn: sqlite3.Connection, query_id: str, threads: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], QueryStateRow]:
    """Bulk-load query_state for a set of (channel, thread_ts) pairs."""
    out: dict[tuple[str, str], QueryStateRow] = {}
    threads_list = list(threads)
    if not threads_list:
        return out
    for chunk_start in range(0, len(threads_list), 500):
        chunk = threads_list[chunk_start : chunk_start + 500]
        placeholders = ", ".join("(?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for ch, ts in chunk:
            params.extend([query_id, ch, ts])
        rows = conn.execute(
            f"SELECT * FROM query_state WHERE (query_id, channel, thread_ts) IN ({placeholders})",
            params,
        ).fetchall()
        for row in rows:
            state = _row_to_state(row)
            out[(state.channel, state.thread_ts)] = state
    return out
