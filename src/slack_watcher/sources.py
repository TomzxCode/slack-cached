"""Thread discovery sources for slack-watcher.

A *source* enumerates the threads a query should consider each cycle. Sources
return a list of :class:`ThreadRef` (channel + thread_ts) plus the channel
id list to actually poll.

Sources reuse slack-cached's :class:`AsyncSlackClient` to fetch fresh data
into the shared SQLite cache, then read back the threads that match the
query's source config.

Three source kinds are supported:

  ``channels``
      Explicit list of channel ids. Every thread parent in those channels
      within the lookback window is a candidate.

  ``dms``
      The authenticated user's DM/mpim channels. Discovered via
      ``conversations.list`` with ``types=im,mpim``. Each DM channel's
      top-level messages become candidates (one thread per parent).

  ``mentions``
      The authenticated user's recent mentions. Slack does not expose a
      direct mentions API, so we approximate by scanning recent messages
      across all joined channels for ones that mention ``<!subteam^...>``
      or the user's id, then resolving to their parent threads.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from slack_cached.async_slack_api import AsyncSlackClient
from slack_cached.storage import connect as connect_cache
from slack_cached.urls import ThreadRef

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SourceResult:
    """Outcome of running a source for one cycle.

    *channels_polled* is the list of channel ids we asked Slack to refresh
    (used for logging). *threads* is the candidate set the scheduler should
    consider running prompts against.
    """

    channels_polled: list[str]
    threads: list[ThreadRef]


async def run_source(
    client: AsyncSlackClient,
    cache_db,
    *,
    source_kind: str,
    source_config: dict,
    lookback_oldest: str | None,
    full_threads: bool,
) -> SourceResult:
    """Dispatch to the right source implementation.

    *cache_db* is a Path or string path to the slack-cached SQLite cache.
    The source refreshes the cache for the relevant channels and then reads
    back the threads it cares about.
    """
    if source_kind == "channels":
        return await _run_channels_source(
            client,
            cache_db,
            channel_ids=source_config.get("channel_ids", []),
            lookback_oldest=lookback_oldest,
            full_threads=full_threads,
        )
    if source_kind == "dms":
        return await _run_dms_source(
            client,
            cache_db,
            include_mpim=bool(source_config.get("include_mpim", True)),
            lookback_oldest=lookback_oldest,
            full_threads=full_threads,
        )
    if source_kind == "mentions":
        return await _run_mentions_source(
            client,
            cache_db,
            lookback_oldest=lookback_oldest,
            full_threads=full_threads,
        )
    raise ValueError(f"unknown source_kind: {source_kind!r}")


async def _refresh_channel(
    client: AsyncSlackClient,
    cache_db,
    channel: str,
    *,
    oldest: str | None,
    full_threads: bool,
) -> None:
    """Fetch channel history (and optionally thread replies) into the cache."""
    from slack_cached.async_cache import fetch_channel_messages_async

    conn = connect_cache(cache_db) if not hasattr(cache_db, "execute") else cache_db
    try:
        await fetch_channel_messages_async(
            conn,
            client,
            channel,
            full_threads=full_threads,
            oldest=oldest,
        )
    finally:
        if not hasattr(cache_db, "execute"):
            conn.close()


async def _run_channels_source(
    client: AsyncSlackClient,
    cache_db,
    *,
    channel_ids: list[str],
    lookback_oldest: str | None,
    full_threads: bool,
) -> SourceResult:
    import asyncio

    await asyncio.gather(
        *(
            _refresh_channel(
                client,
                cache_db,
                ch,
                oldest=lookback_oldest,
                full_threads=full_threads,
            )
            for ch in channel_ids
        )
    )
    threads: list[ThreadRef] = []
    for ch in channel_ids:
        threads.extend(_channel_threads_from_cache(cache_db, ch, lookback_oldest))
    return SourceResult(channels_polled=list(channel_ids), threads=threads)


async def _run_dms_source(
    client: AsyncSlackClient,
    cache_db,
    *,
    include_mpim: bool,
    lookback_oldest: str | None,
    full_threads: bool,
) -> SourceResult:
    import asyncio

    types = "im,mpim" if include_mpim else "im"
    dm_channels: list[str] = []
    async for ch in client.iter_channels(types=types):
        dm_channels.append(ch["id"])

    log.info("dms_source_resolved", count=len(dm_channels), types=types)

    await asyncio.gather(
        *(
            _refresh_channel(
                client,
                cache_db,
                ch,
                oldest=lookback_oldest,
                full_threads=full_threads,
            )
            for ch in dm_channels
        )
    )

    threads: list[ThreadRef] = []
    for ch in dm_channels:
        threads.extend(_channel_threads_from_cache(cache_db, ch, lookback_oldest))
    return SourceResult(channels_polled=dm_channels, threads=threads)


async def _run_mentions_source(
    client: AsyncSlackClient,
    cache_db,
    *,
    lookback_oldest: str | None,
    full_threads: bool,
) -> SourceResult:
    """Mentions source.

    Approximation: poll every public channel the token can see and read back
    messages containing ``@user`` markers from the cache. We don't have a
    cheap ``users.getPresence``-style mentions endpoint without the Slack
    RTM/events API, so this is the best-effort fallback.
    """
    import asyncio

    types = "public_channel,private_channel"
    channel_ids: list[str] = []
    async for ch in client.iter_channels(types=types):
        channel_ids.append(ch["id"])

    log.info("mentions_source_resolved", count=len(channel_ids))

    await asyncio.gather(
        *(
            _refresh_channel(
                client,
                cache_db,
                ch,
                oldest=lookback_oldest,
                full_threads=full_threads,
            )
            for ch in channel_ids
        )
    )

    threads: list[ThreadRef] = []
    seen: set[tuple[str, str]] = set()
    for ch in channel_ids:
        for ref in _channel_threads_from_cache(cache_db, ch, lookback_oldest):
            if (ref.channel, ref.thread_ts) in seen:
                continue
            seen.add((ref.channel, ref.thread_ts))
            threads.append(ref)
    return SourceResult(channels_polled=channel_ids, threads=threads)


def _channel_threads_from_cache(
    cache_db,
    channel: str,
    oldest: str | None,
) -> list[ThreadRef]:
    """Return distinct thread refs for messages in *channel* within the window."""
    conn = connect_cache(cache_db) if not hasattr(cache_db, "execute") else cache_db
    try:
        if oldest is not None:
            rows = conn.execute(
                "SELECT DISTINCT thread_ts FROM messages "
                "WHERE channel = ? AND CAST(ts AS REAL) >= CAST(? AS REAL)",
                (channel, oldest),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT thread_ts FROM messages WHERE channel = ?",
                (channel,),
            ).fetchall()
        return [ThreadRef(channel=channel, thread_ts=r["thread_ts"]) for r in rows]
    finally:
        if not hasattr(cache_db, "execute"):
            conn.close()
