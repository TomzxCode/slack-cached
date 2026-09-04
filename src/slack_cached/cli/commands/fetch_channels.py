"""``slackx fetch-channels`` command."""

import sys

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    DbArg,
    VerboseArg,
    WorkspaceArg,
    _setup,
    app,
)


@app.command(name="fetch-channels")
async def fetch_channels(
    *,
    db: DbArg = None,
    workspace: WorkspaceArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Fetch and cache every visible conversation."""
    from slack_cached.cache import fetch_channels

    common = _setup(db, api_base_url, verbose, workspace)
    async with _client._open_client(common) as client, _client._open_db(common, client) as conn:
        result = await fetch_channels(conn, client)
    print(
        f"processed {result.processed} channels ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0
