"""``slackx fetch`` command."""

import sys
from typing import Annotated

import structlog
from cyclopts import Parameter

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._channels import _resolve_channel
from slack_cached.cli._internal._duration import _oldest_ts_from_last
from slack_cached.cli._internal._refs import _resolve_ref
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    ChannelArg,
    CommonArgs,
    DbArg,
    TsArg,
    UrlArg,
    VerboseArg,
    WorkspaceArg,
    _setup,
    app,
)

log = structlog.get_logger(__name__)


@app.command
async def fetch(
    url: UrlArg = None,
    *,
    channel: ChannelArg = None,
    ts: TsArg = None,
    full_threads: Annotated[
        bool,
        Parameter(help="When fetching a channel, also fetch all replies for every thread."),
    ] = False,
    last: Annotated[
        str,
        Parameter(
            help="When fetching a channel, limit history to the given lookback "
            "(e.g. 24h, 2d5h30m, 90m; default: 1d, use 'all' for full history).",
        ),
    ] = "1d",
    db: DbArg = None,
    workspace: WorkspaceArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Cache or refresh a Slack thread, or fetch all messages from a channel."""
    common = _setup(db, api_base_url, verbose, workspace)
    if channel:
        channel = await _resolve_channel(common, channel)
        if channel is None:
            return 1
    if channel and not ts and not url:
        return await _fetch_channel_messages(common, channel, full_threads, last)

    from slack_cached.cache import fetch_thread

    ref = _resolve_ref(url, channel, ts)
    async with _client._open_client(common) as client, _client._open_db(common, client) as conn:
        result = await fetch_thread(conn, client, ref)
    print(
        f"cached {result.total_messages} messages "
        f"({result.fetched_messages} new/updated, "
        f"{'incremental' if result.incremental else 'full'}) "
        f"for {result.channel}/{result.thread_ts}",
        file=sys.stderr,
    )
    return 0


async def _fetch_channel_messages(
    common: CommonArgs, channel: str, full_threads: bool, last: str
) -> int:
    """Fetch messages from a channel."""
    from slack_cached.cache import fetch_channel_messages

    oldest = _oldest_ts_from_last(last)
    async with _client._open_client(common) as client, _client._open_db(common, client) as conn:
        result = await fetch_channel_messages(
            conn, client, channel, full_threads=full_threads, oldest=oldest
        )
    detail = (
        f", {result.threads_with_replies_fetched} threads with replies fetched"
        if full_threads
        else ""
    )
    print(
        f"cached {result.total_messages} messages for {result.channel} "
        f"({result.fetched_messages} fetched{detail})",
        file=sys.stderr,
    )
    return 0
