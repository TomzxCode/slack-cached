"""``slackx show`` command."""

import sys
from typing import Annotated

import structlog
from cyclopts import Parameter

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._channels import _resolve_channel
from slack_cached.cli._internal._duration import _oldest_ts_from_last
from slack_cached.cli._internal._format import _build_user_names, _format_ts
from slack_cached.cli._internal._refs import _output_format, _resolve_ref
from slack_cached.cli._internal._render import (
    _render_channel_human,
    _render_channel_json,
    _render_human,
    _render_json,
)
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    ChannelArg,
    CommonArgs,
    DbArg,
    JsonArg,
    JsonlArg,
    NoFetchArg,
    TsArg,
    UrlArg,
    VerboseArg,
    WorkspaceArg,
    _setup,
    _timed,
    app,
)
from slack_cached.storage import (
    get_thread_state,
    load_channel_display_names,
    load_channel_messages,
    load_thread_messages,
)

log = structlog.get_logger(__name__)


@app.command
async def show(
    url: UrlArg = None,
    *,
    channel: ChannelArg = None,
    ts: TsArg = None,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    last: Annotated[
        str,
        Parameter(
            help="When showing a channel, limit history to the given lookback "
            "(e.g. 24h, 2d5h30m, 90m; default: 1d, use 'all' for full history).",
        ),
    ] = "1d",
    db: DbArg = None,
    workspace: WorkspaceArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print a cached thread or channel to stdout (human-readable by default).

    Fetches first if not already cached (unless --no-fetch is given).

    When --channel is given without --ts, shows all messages for that channel
    (fetching first if needed, unless --no-fetch).
    """
    common = _setup(db, api_base_url, verbose, workspace)
    fmt = _output_format(json_output, jsonl_output)

    if channel:
        channel = await _resolve_channel(common, channel)
        if channel is None:
            return 1

    if channel and not ts and not url:
        return await _show_channel(common, channel, no_fetch, last, fmt)

    log.debug("cmd_show_start")
    with _timed("resolve_ref"):
        ref = _resolve_ref(url, channel, ts)

    # Read first from the offline-resolved workspace database; only hit the
    # network (and its auth.test-resolved workspace) on a cache miss.
    need_fetch = False
    async with _client._open_db(common) as conn:
        if get_thread_state(conn, ref.channel, ref.thread_ts) is not None or no_fetch:
            messages, user_names, channel_name = _load_thread_view(conn, ref)
        else:
            need_fetch = True

    if need_fetch:
        # Imported lazily so the cached-read path does not pull in the
        # httpx-based Slack client (see _build_client).
        from slack_cached.cache import fetch_thread

        async with (
            _client._open_client(common) as client,
            _client._open_db(common, client) as conn,
        ):
            if get_thread_state(conn, ref.channel, ref.thread_ts) is None:
                log.info(
                    "thread_not_cached_fetching",
                    channel=ref.channel,
                    thread_ts=ref.thread_ts,
                    thread_ts_iso=_format_ts(ref.thread_ts),
                )
                if verbose:
                    print(
                        f"fetching thread {ref.channel}/{ref.thread_ts} from Slack...",
                        file=sys.stderr,
                    )
                await fetch_thread(conn, client, ref)
            messages, user_names, channel_name = _load_thread_view(conn, ref)

    log.debug("loaded_messages", count=len(messages))
    with _timed("render", format=fmt, messages=len(messages)):
        if fmt in ("json", "jsonl"):
            output = _render_json(
                ref,
                messages,
                user_names,
                channel_name,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_human(ref, messages, user_names)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    return 0


def _load_thread_view(conn, ref) -> tuple[list, dict[str, str], str | None]:
    """Load a thread's messages plus resolved user/channel names from ``conn``."""
    messages = load_thread_messages(conn, ref.channel, ref.thread_ts)
    user_names = _build_user_names(conn, messages)
    channel_name = load_channel_display_names(conn, [ref.channel]).get(ref.channel)
    return messages, user_names, channel_name


async def _show_channel(
    common: CommonArgs, channel: str, no_fetch: bool, last: str, fmt: str
) -> int:
    """Show all messages for a channel, fetching first if needed."""
    oldest = _oldest_ts_from_last(last)

    # Read first from the offline-resolved workspace database; only hit the
    # network (and its auth.test-resolved workspace) on a cache miss.
    async with _client._open_db(common) as conn:
        messages = load_channel_messages(conn, channel)
        user_names = _build_user_names(conn, messages)
        channel_name = load_channel_display_names(conn, [channel]).get(channel)

    if not messages and not no_fetch:
        from slack_cached.cache import fetch_channel_messages

        async with (
            _client._open_client(common) as client,
            _client._open_db(common, client) as conn,
        ):
            if not load_channel_messages(conn, channel):
                log.info("channel_not_cached_fetching", channel=channel)
                if common.verbose:
                    print(
                        f"fetching messages for {channel} from Slack...",
                        file=sys.stderr,
                    )
                await fetch_channel_messages(conn, client, channel, oldest=oldest)
            messages = load_channel_messages(conn, channel)
            user_names = _build_user_names(conn, messages)
            channel_name = load_channel_display_names(conn, [channel]).get(channel)

    with _timed("render", format=fmt, messages=len(messages)):
        if fmt in ("json", "jsonl"):
            output = _render_channel_json(
                channel,
                messages,
                user_names,
                channel_name,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_channel_human(channel, messages, user_names, channel_name)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    return 0
