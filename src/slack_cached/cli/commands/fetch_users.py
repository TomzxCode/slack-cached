"""``slack-cached fetch-users`` command."""

import sys

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._shared import ApiBaseUrlArg, DbArg, VerboseArg, _setup, app


@app.command(name="fetch-users")
def fetch_users(
    *,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Fetch and cache every workspace user."""
    from slack_cached.cache import fetch_users

    common = _setup(db, api_base_url, verbose)
    client = _client._build_client(common)
    with _client._open_db(common) as conn:
        result = fetch_users(conn, client)
    print(
        f"processed {result.processed} users ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0
