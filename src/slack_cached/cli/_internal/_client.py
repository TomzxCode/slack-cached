"""Database connection and Slack client construction."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from slack_cached.cli._internal._shared import CommonArgs
from slack_cached.storage import connect
from slack_cached.workspace import (
    cached_workspace_name,
    claim_workspace_db,
    offline_db_path,
    remember_workspace_name,
    workspace_name_from_auth,
)

if TYPE_CHECKING:
    from slack_cached.slack_api import SlackClient

log = structlog.get_logger(__name__)


async def _resolve_db_path(common: CommonArgs, client: "SlackClient | None" = None) -> Path:
    """Resolve the cache database path for this invocation.

    An explicit ``--db`` always wins. Otherwise ``--workspace`` selects the
    per-workspace database by name. With a Slack client, the workspace is
    identified from the on-disk credentials mapping when available, otherwise
    one ``auth.test`` call (so the cache always matches the credentials in
    use); without a client, the last-used workspace is resolved offline.
    """
    if common.db is not None:
        return common.db
    if common.workspace:
        return claim_workspace_db(common.workspace)
    if client is not None:
        name = cached_workspace_name(client.token, client.base_url)
        if name is None:
            auth = await client.auth_test()
            name = workspace_name_from_auth(auth)
        remember_workspace_name(name, client.token, client.base_url)
        return claim_workspace_db(name)
    return offline_db_path()


def _resolve_db_path_sync(common: CommonArgs) -> Path:
    """Resolve the cache database path synchronously (used by ``serve``).

    Without ``--db`` or ``--workspace``, the configured token/cookie determine
    the workspace: the on-disk credentials mapping when it has the answer,
    otherwise one auth.test call. Falls back to offline resolution when no
    credentials are configured or Slack cannot be reached.
    """
    from slack_cached.config import load_api_base_url, load_credentials
    from slack_cached.slack_api import DEFAULT_API_BASE

    if common.db is not None:
        return common.db
    if common.workspace:
        return claim_workspace_db(common.workspace)

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    credentials = load_credentials(require=False)
    if not credentials.token:
        return offline_db_path()

    name = cached_workspace_name(credentials.token, base_url)
    if name is not None:
        return claim_workspace_db(name)

    async def _resolve_with_client() -> Path:
        client = _build_client(common)
        try:
            return await _resolve_db_path(common, client)
        finally:
            await client.aclose()

    import httpx

    from slack_cached.slack_api import SlackAPIError

    try:
        return asyncio.run(_resolve_with_client())
    except (SlackAPIError, httpx.HTTPError) as exc:
        log.warning("serve_workspace_resolution_failed", reason=str(exc))
        return offline_db_path()


@asynccontextmanager
async def _open_db(
    common: CommonArgs, client: "SlackClient | None" = None
) -> AsyncIterator[sqlite3.Connection]:
    """Open the workspace-resolved cache database."""
    db_path = await _resolve_db_path(common, client)
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _open_db_at(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a known cache database path (resolution already done)."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _build_client(common: CommonArgs) -> "SlackClient":
    """Construct a ``SlackClient`` (httpx-backed, async).

    The returned client owns no ``httpx.AsyncClient`` until the first request;
    callers should pair this with ``_open_client`` so ``aclose()`` runs.
    """
    # Imported lazily so commands that never hit the network (e.g.
    # `show --no-fetch`) avoid loading httpx and friends.
    from slack_cached.config import load_api_base_url, load_credentials
    from slack_cached.slack_api import DEFAULT_API_BASE, SlackClient

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise
    return SlackClient(credentials, base_url=base_url)


@asynccontextmanager
async def _open_client(common: CommonArgs) -> AsyncIterator["SlackClient"]:
    """Build a ``SlackClient`` and ensure its httpx client is closed on exit."""
    client = _build_client(common)
    try:
        yield client
    finally:
        await client.aclose()
