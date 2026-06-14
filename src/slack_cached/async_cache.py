"""Async high-level cache operations for slack-cached.

Mirrors cache.fetch_channel_messages but uses AsyncSlackClient for
concurrent HTTP requests. SQLite writes remain synchronous since they
are fast and happen within a single event loop.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import structlog

from .async_slack_api import AsyncSlackClient
from .cache import ChannelFetchResult, _latest_ts, _ts_to_iso
from .storage import (
    count_channel_messages,
    record_thread_refresh,
    transaction,
    upsert_messages,
)

log = structlog.get_logger(__name__)


async def fetch_channel_messages_async(
    conn: sqlite3.Connection,
    client: AsyncSlackClient,
    channel: str,
    full_threads: bool = False,
    oldest: str | None = None,
) -> ChannelFetchResult:
    """Async version of fetch_channel_messages.

    The HTTP history fetch is the main win: it yields control back to the
    event loop so other channels can fetch concurrently. Thread replies are
    also fetched concurrently when full_threads is True.
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

        import asyncio

        async def fetch_thread(thread_ts: str) -> list[dict[str, Any]]:
            return [
                msg
                async for msg in client.iter_thread_replies(channel=channel, thread_ts=thread_ts)
            ]

        results = await asyncio.gather(*(fetch_thread(ts) for ts in parent_tss))
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
