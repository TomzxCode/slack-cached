"""High-level cache operations for slack-cached.

`fetch_thread` decides whether to do a full or incremental fetch based on
existing cache state, calls the Slack API, and writes the results back to
SQLite.

`load_thread` reads a cached thread back out for display.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import structlog

from .slack_api import SlackClient
from .storage import (
    CachedMessage,
    count_channels,
    count_messages,
    count_users,
    get_thread_state,
    load_thread_messages,
    record_thread_refresh,
    transaction,
    upsert_channels,
    upsert_messages,
    upsert_users,
)
from .urls import ThreadRef

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FetchResult:
    """Summary of what `fetch_thread` did."""

    channel: str
    thread_ts: str
    fetched_messages: int
    total_messages: int
    incremental: bool


@dataclass(frozen=True)
class ListFetchResult:
    """Summary of a bulk fetch of users or channels.

    `processed` is how many records were received from Slack and written.
    `added` is how many of those were new rows in the database (the rest were
    updates to existing rows). `total` is the row count after the fetch.
    """

    processed: int
    added: int
    total: int


def fetch_thread(
    conn: sqlite3.Connection,
    client: SlackClient,
    ref: ThreadRef,
) -> FetchResult:
    """Fetch a thread from Slack, doing an incremental refresh when possible.

    Strategy:
    - If the thread is not cached, fetch all messages from Slack.
    - If the thread is cached, ask Slack for replies with oldest=latest_reply.
      That call returns any new replies plus possibly an edit of an older one;
      we upsert by ts so edits overwrite stale rows.
    """
    state = get_thread_state(conn, ref.channel, ref.thread_ts)
    incremental = state is not None and state.latest_reply is not None
    oldest = state.latest_reply if incremental else None

    log.info(
        "fetch_thread_start",
        channel=ref.channel,
        thread_ts=ref.thread_ts,
        incremental=incremental,
        oldest=oldest,
    )

    new_messages: list[dict[str, Any]] = list(
        client.iter_thread_replies(
            channel=ref.channel,
            thread_ts=ref.thread_ts,
            oldest=oldest,
        )
    )

    latest_reply = _latest_ts(new_messages)
    if latest_reply is None and state is not None:
        latest_reply = state.latest_reply

    with transaction(conn):
        # Record the thread row first so the messages FK constraint is satisfied.
        record_thread_refresh(conn, ref.channel, ref.thread_ts, latest_reply)
        written = upsert_messages(conn, ref.channel, ref.thread_ts, new_messages)

    total = count_messages(conn, ref.channel, ref.thread_ts)
    log.info(
        "fetch_thread_done",
        channel=ref.channel,
        thread_ts=ref.thread_ts,
        written=written,
        total=total,
        incremental=incremental,
    )
    return FetchResult(
        channel=ref.channel,
        thread_ts=ref.thread_ts,
        fetched_messages=written,
        total_messages=total,
        incremental=incremental,
    )


def load_thread(conn: sqlite3.Connection, ref: ThreadRef) -> list[CachedMessage]:
    """Return the cached messages for a thread, ordered by ts."""
    return load_thread_messages(conn, ref.channel, ref.thread_ts)


def fetch_users(conn: sqlite3.Connection, client: SlackClient) -> ListFetchResult:
    """Fetch every workspace user from Slack and cache them."""
    log.info("fetch_users_start")
    before = count_users(conn)
    users: list[dict[str, Any]] = list(client.iter_users())

    with transaction(conn):
        processed = upsert_users(conn, users)

    total = count_users(conn)
    added = total - before
    log.info("fetch_users_done", processed=processed, added=added, total=total)
    return ListFetchResult(processed=processed, added=added, total=total)


def fetch_channels(conn: sqlite3.Connection, client: SlackClient) -> ListFetchResult:
    """Fetch every visible conversation from Slack and cache them."""
    log.info("fetch_channels_start")
    before = count_channels(conn)
    channels: list[dict[str, Any]] = list(client.iter_channels())

    with transaction(conn):
        processed = upsert_channels(conn, channels)

    total = count_channels(conn)
    added = total - before
    log.info("fetch_channels_done", processed=processed, added=added, total=total)
    return ListFetchResult(processed=processed, added=added, total=total)


def _latest_ts(messages: list[dict[str, Any]]) -> str | None:
    """Return the highest 'ts' in messages, treated as a float, or None."""
    best: tuple[float, str] | None = None
    for msg in messages:
        ts = msg.get("ts")
        if not ts:
            continue
        try:
            value = float(ts)
        except (TypeError, ValueError):
            continue
        if best is None or value > best[0]:
            best = (value, ts)
    return best[1] if best else None
