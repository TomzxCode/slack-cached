"""High-level async cache operations for slack-cached.

``fetch_thread`` decides whether to do a full or incremental fetch based on
existing cache state, calls the Slack API concurrently, and writes the results
back to SQLite. SQLite writes remain synchronous since they are fast and
happen within a single event loop.

``load_thread`` reads a cached thread back out for display (pure DB read).
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from .slack_api import SlackClient
from .storage import (
    CachedMessage,
    count_channel_messages,
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


def _ts_to_iso(ts: str | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _normalize_channel_id(channel: Any) -> str | None:
    """Return a bare channel id from a search match's ``channel`` field.

    ``conversations.history``/``replies`` return ``channel`` as a bare id
    string, but ``search.messages`` returns it as an object
    (``{"id", "name", ...}``). Accept either and return the id, or None when it
    cannot be determined.
    """
    if isinstance(channel, dict):
        cid = channel.get("id")
        return cid if isinstance(cid, str) and cid else None
    if isinstance(channel, str):
        return channel or None
    return None


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


@dataclass(frozen=True)
class FetchResult:
    """Summary of what ``fetch_thread`` did."""

    channel: str
    thread_ts: str
    fetched_messages: int
    total_messages: int
    incremental: bool


@dataclass(frozen=True)
class ListFetchResult:
    """Summary of a bulk fetch of users or channels.

    ``processed`` is how many records were received from Slack and written.
    ``added`` is how many of those were new rows in the database (the rest were
    updates to existing rows). ``total`` is the row count after the fetch.
    """

    processed: int
    added: int
    total: int


@dataclass(frozen=True)
class ChannelFetchResult:
    """Summary of what ``fetch_channel_messages`` did."""

    channel: str
    fetched_messages: int
    total_messages: int
    threads_with_replies_fetched: int


@dataclass(frozen=True)
class SearchFetchResult:
    """Summary of what ``fetch_search`` did.

    ``matches`` is the raw list of search matches (each carrying its own
    ``channel``, ``ts`` and ``permalink``) so the caller can render them
    without re-reading the cache. ``threads_touched`` is the number of distinct
    threads that received at least one cached message.
    """

    query: str
    matches: list[dict[str, Any]]
    threads_touched: int


async def fetch_thread(
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
        thread_ts_iso=_ts_to_iso(ref.thread_ts),
        incremental=incremental,
        oldest=oldest,
        oldest_iso=_ts_to_iso(oldest),
    )

    new_messages: list[dict[str, Any]] = [
        msg
        async for msg in client.iter_thread_replies(
            channel=ref.channel,
            thread_ts=ref.thread_ts,
            oldest=oldest,
        )
    ]

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
        thread_ts_iso=_ts_to_iso(ref.thread_ts),
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


async def fetch_channel_messages(
    conn: sqlite3.Connection,
    client: SlackClient,
    channel: str,
    full_threads: bool = False,
    oldest: str | None = None,
) -> ChannelFetchResult:
    """Fetch messages from a channel.

    By default only top-level messages are fetched via conversations.history
    (standalone messages and thread parents, but not thread replies).  When
    *full_threads* is True, every thread that has replies is also fetched in
    full via conversations.replies, concurrently.

    *oldest* limits the history scan to messages with ts >= oldest (epoch
    seconds as a string).  When None, the entire channel history is fetched.
    """
    log.info(
        "fetch_channel_messages_start",
        channel=channel,
        full_threads=full_threads,
        oldest=oldest,
        oldest_iso=_ts_to_iso(oldest),
    )

    history: list[dict[str, Any]] = [
        msg async for msg in client.iter_channel_history(channel=channel, oldest=oldest)
    ]
    log.info("fetch_channel_history_done", channel=channel, count=len(history))

    written = 0
    threads_with_replies_fetched = 0

    with transaction(conn):
        for msg in history:
            thread_ts = msg.get("thread_ts") or msg["ts"]
            record_thread_refresh(conn, channel, thread_ts, None)
            written += upsert_messages(conn, channel, thread_ts, [msg])

    if full_threads:
        parent_tss = sorted(
            {
                msg.get("thread_ts") or msg["ts"]
                for msg in history
                if msg.get("reply_count", 0) > 0 or msg.get("latest_reply")
            }
        )
        log.info("fetch_channel_threads_start", channel=channel, thread_count=len(parent_tss))

        async def fetch_thread_replies(thread_ts: str) -> list[dict[str, Any]]:
            return [
                msg
                async for msg in client.iter_thread_replies(channel=channel, thread_ts=thread_ts)
            ]

        results = await asyncio.gather(*(fetch_thread_replies(ts) for ts in parent_tss))
        for thread_ts, replies in zip(parent_tss, results, strict=True):
            if not replies:
                continue
            latest = _latest_ts(replies)
            with transaction(conn):
                record_thread_refresh(conn, channel, thread_ts, latest)
                written += upsert_messages(conn, channel, thread_ts, replies)
            threads_with_replies_fetched += 1

        log.info(
            "fetch_channel_threads_done",
            channel=channel,
            threads_fetched=threads_with_replies_fetched,
        )

    total = count_channel_messages(conn, channel)
    log.info(
        "fetch_channel_messages_done",
        channel=channel,
        written=written,
        total=total,
        threads_with_replies_fetched=threads_with_replies_fetched,
    )
    return ChannelFetchResult(
        channel=channel,
        fetched_messages=written,
        total_messages=total,
        threads_with_replies_fetched=threads_with_replies_fetched,
    )


async def fetch_search(
    conn: sqlite3.Connection,
    client: SlackClient,
    query: str,
    count: int = 20,
    sort: str = "timestamp",
    sort_dir: str = "desc",
    full_threads: bool = False,
) -> SearchFetchResult:
    """Search Slack via search.messages and cache every matched message.

    Each match carries its own ``channel`` and ``ts``; it is upserted into the
    messages table under its ``(channel, thread_ts)`` thread (defaulting the
    thread ts to the message ts when Slack omits one, matching the channel
    fetch behaviour).  When *full_threads* is True, every distinct matched
    thread is then fetched in full via conversations.replies.

    Returns the raw matches so the caller can render them (with permalinks)
    without re-reading the cache.
    """
    log.info(
        "fetch_search_start",
        query=query,
        count=count,
        sort=sort,
        sort_dir=sort_dir,
        full_threads=full_threads,
    )

    matches: list[dict[str, Any]] = [
        match
        async for match in client.iter_search_messages(
            query=query, count=count, sort=sort, sort_dir=sort_dir
        )
    ]
    log.info("fetch_search_matches", query=query, matches=len(matches))

    # search.messages returns ``channel`` as an object (``{"id", "name", ...}``)
    # rather than the bare id used everywhere else, so normalise each match to a
    # string channel id in place. Callers (caching below and rendering) can then
    # treat ``channel`` uniformly.
    for msg in matches:
        msg["channel"] = _normalize_channel_id(msg.get("channel"))

    written = 0
    threads_touched: set[tuple[str, str]] = set()

    with transaction(conn):
        for msg in matches:
            channel = msg.get("channel")
            if not channel or not msg.get("ts"):
                continue
            thread_ts = msg.get("thread_ts") or msg["ts"]
            record_thread_refresh(conn, channel, thread_ts, None)
            written += upsert_messages(conn, channel, thread_ts, [msg])
            threads_touched.add((channel, thread_ts))

    if full_threads:
        log.info(
            "fetch_search_threads_start",
            query=query,
            thread_count=len(threads_touched),
        )

        async def fetch_one(channel: str, thread_ts: str) -> list[dict[str, Any]]:
            return [
                msg
                async for msg in client.iter_thread_replies(channel=channel, thread_ts=thread_ts)
            ]

        ordered = sorted(threads_touched)
        results = await asyncio.gather(*(fetch_one(c, t) for c, t in ordered))
        for (channel, thread_ts), replies in zip(ordered, results, strict=True):
            if not replies:
                continue
            latest = _latest_ts(replies)
            with transaction(conn):
                record_thread_refresh(conn, channel, thread_ts, latest)
                written += upsert_messages(conn, channel, thread_ts, replies)

    log.info(
        "fetch_search_done",
        query=query,
        matches=len(matches),
        written=written,
        threads_touched=len(threads_touched),
    )
    return SearchFetchResult(
        query=query,
        matches=matches,
        threads_touched=len(threads_touched),
    )


async def fetch_users(conn: sqlite3.Connection, client: SlackClient) -> ListFetchResult:
    """Fetch every workspace user from Slack and cache them."""
    log.info("fetch_users_start")
    before = count_users(conn)
    users: list[dict[str, Any]] = [u async for u in client.iter_users()]

    with transaction(conn):
        processed = upsert_users(conn, users)

    total = count_users(conn)
    added = total - before
    log.info("fetch_users_done", processed=processed, added=added, total=total)
    return ListFetchResult(processed=processed, added=added, total=total)


async def fetch_channels(conn: sqlite3.Connection, client: SlackClient) -> ListFetchResult:
    """Fetch every visible conversation from Slack and cache them."""
    log.info("fetch_channels_start")
    before = count_channels(conn)
    channels: list[dict[str, Any]] = [c async for c in client.iter_channels()]

    with transaction(conn):
        processed = upsert_channels(conn, channels)

    total = count_channels(conn)
    added = total - before
    log.info("fetch_channels_done", processed=processed, added=added, total=total)
    return ListFetchResult(processed=processed, added=added, total=total)
