"""``slackx show-users`` command."""

import json
import sys
from dataclasses import asdict

import structlog

from slack_cached.cli._internal import _client
from slack_cached.cli._internal._refs import _output_format
from slack_cached.cli._internal._render import _render_users_human
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
from slack_cached.storage import load_users

log = structlog.get_logger(__name__)


@app.command(name="show-users")
async def show_users(
    *,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    db: DbArg = None,
    workspace: WorkspaceArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print cached users to stdout (human-readable by default)."""
    common = _setup(db, api_base_url, verbose, workspace)
    fmt = _output_format(json_output, jsonl_output)
    async with _client._open_db(common) as conn:
        users = load_users(conn)

    if not users and not no_fetch:
        from slack_cached.cache import fetch_users

        log.info("users_not_cached_fetching")
        async with (
            _client._open_client(common) as client,
            _client._open_db(common, client) as conn,
        ):
            if not load_users(conn):
                await fetch_users(conn, client)
            users = load_users(conn)

    if fmt in ("json", "jsonl"):
        payload = {"user_count": len(users), "users": [asdict(u) for u in users]}
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False, indent=2 if fmt == "json" else None) + "\n"
        )
    else:
        sys.stdout.write(_render_users_human(users))
    return 0
