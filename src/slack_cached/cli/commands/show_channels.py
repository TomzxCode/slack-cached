"""``slackx show-channels`` command."""

import json
import sys
from dataclasses import asdict

import structlog

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._refs import _output_format
from slack_cached.cli._internal._render import _render_channels_human
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    DbArg,
    JsonArg,
    JsonlArg,
    NoFetchArg,
    VerboseArg,
    WorkspaceArg,
    _setup,
    app,
)
from slack_cached.storage import load_channel_display_names, load_channels

log = structlog.get_logger(__name__)


@app.command(name="show-channels")
async def show_channels(
    *,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    db: DbArg = None,
    workspace: WorkspaceArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print cached channels to stdout (human-readable by default)."""
    common = _setup(db, api_base_url, verbose, workspace)
    fmt = _output_format(json_output, jsonl_output)
    async with _client._open_db(common) as conn:
        channels = load_channels(conn)
        display_names = load_channel_display_names(conn, [c.id for c in channels])

    if not channels and not no_fetch:
        from slack_cached.cache import fetch_channels

        log.info("channels_not_cached_fetching")
        async with (
            _client._open_client(common) as client,
            _client._open_db(common, client) as conn,
        ):
            if not load_channels(conn):
                await fetch_channels(conn, client)
            channels = load_channels(conn)
            display_names = load_channel_display_names(conn, [c.id for c in channels])

    if fmt in ("json", "jsonl"):
        payload = {"channel_count": len(channels), "channels": [asdict(c) for c in channels]}
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False, indent=2 if fmt == "json" else None) + "\n"
        )
    else:
        sys.stdout.write(_render_channels_human(channels, display_names))
    return 0
