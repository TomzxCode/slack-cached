"""``slack-cached search`` command."""

import sys
from typing import Annotated, Literal

import structlog
from cyclopts import Parameter

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._channels import _channel_id_names
from slack_cached.cli._internal._refs import _output_format
from slack_cached.cli._internal._render import _render_search_human, _render_search_json
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    DbArg,
    JsonArg,
    JsonlArg,
    VerboseArg,
    _setup,
    _timed,
    app,
)
from slack_cached.storage import load_user_display_names

log = structlog.get_logger(__name__)


@app.command
async def search(
    query: Annotated[
        str,
        Parameter(help="Slack search query (same syntax as the Slack search box)."),
    ],
    *,
    count: Annotated[int, Parameter(help="Maximum results per page (default: 20).")] = 20,
    sort: Annotated[
        Literal["score", "timestamp"],
        Parameter(help="Sort matches by score or timestamp."),
    ] = "timestamp",
    sort_dir: Annotated[Literal["asc", "desc"], Parameter(help="Sort direction.")] = "desc",
    full_threads: Annotated[
        bool,
        Parameter(help="Also fetch all replies for every thread a match belongs to."),
    ] = False,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    verbose: VerboseArg = False,
) -> int:
    """Search Slack via search.messages and cache the matched messages/threads.

    Search is inherently a live operation: every run hits the API. Every
    matched message is cached under its ``(channel, thread_ts)`` so it can be
    revisited later with `show`. Output is human-readable by default, JSON
    with --json.
    """
    from slack_cached.cache import fetch_search

    common = _setup(db, api_base_url, verbose)
    fmt = _output_format(json_output, jsonl_output)

    log.debug("cmd_search_start", query=query)
    async with _client._open_client(common) as client:
        with _client._open_db(common) as conn:
            with _timed("fetch_search", query=query):
                result = await fetch_search(
                    conn,
                    client,
                    query=query,
                    count=count,
                    sort=sort,
                    sort_dir=sort_dir,
                    full_threads=full_threads,
                )
            matches = result.matches
            log.debug("search_matches", count=len(matches))

            user_ids = {m.get("user") for m in matches if m.get("user")}
            channel_ids = {m.get("channel") for m in matches if m.get("channel")}
            with _timed("build_user_names"):
                user_names = load_user_display_names(conn, user_ids)
            with _timed("build_channel_names"):
                channel_names = _channel_id_names(conn, channel_ids)

    with _timed("render", format=fmt, matches=len(matches)):
        if fmt in ("json", "jsonl"):
            output = _render_search_json(
                query,
                matches,
                user_names,
                channel_names,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_search_human(query, matches, user_names, channel_names)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    threads_existing = result.threads_seen - result.threads_new
    messages_existing = result.messages_seen - result.messages_new
    print(
        f"searched {query!r}: {len(matches)} match(es), "
        f"{result.threads_seen} thread(s) "
        f"({threads_existing} existing, {result.threads_new} new), "
        f"{result.messages_seen} message(s) "
        f"({messages_existing} existing, {result.messages_new} new)",
        file=sys.stderr,
    )
    return 0
