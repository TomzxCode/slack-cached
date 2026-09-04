"""FastAPI application serving the slack-cached web UI and its JSON API.

The server is a read-mostly view over the local SQLite cache. A handful of
POST endpoints trigger live Slack fetches (reusing the same client and
credential resolution as the CLI) so the cache can be refreshed from the
browser.

SQLite connections are opened per request on the event loop thread: locally
the queries are sub-millisecond and the existing cache layer is synchronous
by design (see cache.py).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from slack_cached import storage
from slack_cached.config import Credentials, default_db_path, load_api_base_url, load_credentials
from slack_cached.urls import ThreadRef

log = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

NO_CREDENTIALS_DETAIL = (
    "No Slack credentials configured, so this refresh cannot run. "
    "Set SLACK_TOKEN (and SLACK_COOKIE for xoxc- tokens), or add them to the "
    "slack-cached config file, then restart the server."
)


async def _db_conn(request: Request) -> AsyncIterator[sqlite3.Connection]:
    """Open a short-lived database connection for one request.

    Defined at module level (not inside create_app) so FastAPI can resolve
    the ``Conn`` annotation: annotations are strings under
    ``from __future__ import annotations`` and are evaluated against module
    globals.
    """
    conn = storage.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(_db_conn)]


def _client_for_refresh(api_base_url: str | None, credentials: Credentials) -> Any:  # noqa: ANN401 - SlackClient import kept lazy
    """Build a SlackClient for live fetches, mirroring the CLI behaviour."""
    from slack_cached.slack_api import DEFAULT_API_BASE, SlackClient

    base_url = api_base_url or load_api_base_url() or DEFAULT_API_BASE
    return SlackClient(credentials, base_url=base_url)


def _display_name(user: storage.CachedUser) -> str:
    """Format a user's display name the same way CLI rendering does."""
    if user.real_name and user.name:
        return f"{user.real_name} ({user.name})"
    return user.real_name or user.name or user.id


def _user_payload(user: storage.CachedUser) -> dict[str, Any]:
    """Reduce a cached user to the fields the web UI needs."""
    profile = user.payload.get("profile") or {}
    return {
        "id": user.id,
        "name": user.name,
        "real_name": user.real_name,
        "display_name": _display_name(user),
        "is_bot": bool(user.payload.get("is_bot")),
        "tz": user.payload.get("tz"),
        "title": profile.get("title"),
    }


def _with_names(
    messages: list[Any],
    user_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Serialize message dataclasses, attaching resolved author display names."""
    out: list[dict[str, Any]] = []
    for m in messages:
        item = asdict(m)
        item["user_name"] = user_names.get(m.user or "", m.user)
        out.append(item)
    return out


def create_app(
    db_path: Path | None = None,
    api_base_url: str | None = None,
) -> FastAPI:
    """Build the FastAPI app bound to a cache database path."""
    db = db_path or default_db_path()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        conn = storage.connect(db)
        try:
            storage.ensure_search_index(conn)
        finally:
            conn.close()
        yield

    fastapp = FastAPI(
        title="slack-cached",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    fastapp.state.db_path = db
    fastapp.state.api_base_url = api_base_url

    def _require_channel(conn: sqlite3.Connection, channel_id: str) -> storage.CachedChannel:
        channel = storage.get_channel(conn, channel_id)
        if channel is None:
            raise HTTPException(status_code=404, detail=f"Unknown channel {channel_id!r}")
        return channel

    def _resolve_names(conn: sqlite3.Connection, user_ids: list[str | None]) -> dict[str, str]:
        return storage.load_user_display_names(conn, [u for u in user_ids if u])

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @fastapp.get("/api/summary")
    async def summary(conn: Conn) -> dict[str, Any]:
        """Workspace-level counters for the home view."""
        return {
            "db_path": str(fastapp.state.db_path),
            "users": storage.count_users(conn),
            "channels": storage.count_channels(conn),
            "messages": storage.count_all_messages(conn),
            "threads": storage.count_all_threads(conn),
        }

    @fastapp.get("/api/users")
    async def list_users(conn: Conn) -> dict[str, Any]:
        users = storage.load_users(conn)
        return {"user_count": len(users), "users": [_user_payload(u) for u in users]}

    @fastapp.get("/api/channels")
    async def list_channels(conn: Conn) -> dict[str, Any]:
        channels = storage.list_channel_summaries(conn)
        return {
            "channel_count": len(channels),
            "channels": [asdict(c) for c in channels],
        }

    @fastapp.get("/api/channels/{channel_id}/messages")
    async def channel_messages(
        channel_id: str,
        conn: Conn,
        before: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        # The channel row may be absent (e.g. search cached messages for a
        # channel whose row was never fetched); serve what we know.
        channel = storage.get_channel(conn, channel_id)
        roots = storage.load_channel_thread_roots(conn, channel_id, before=before, limit=limit)
        names = _resolve_names(conn, [m.user for m in roots])
        return {
            "channel": {
                "id": channel_id,
                "name": channel.name if channel else None,
                "is_private": channel.is_private if channel else None,
            },
            "messages": _with_names(roots, names),
            "has_more": len(roots) == limit,
        }

    @fastapp.get("/api/channels/{channel_id}/threads/{thread_ts}")
    async def thread_messages(channel_id: str, thread_ts: str, conn: Conn) -> dict[str, Any]:
        channel = storage.get_channel(conn, channel_id)
        messages = storage.load_thread_messages(conn, channel_id, thread_ts)
        if not messages:
            raise HTTPException(
                status_code=404,
                detail=f"No cached messages for thread {thread_ts!r} in {channel_id!r}",
            )
        names = _resolve_names(conn, [m.user for m in messages])
        return {
            "channel": {
                "id": channel_id,
                "name": channel.name if channel else None,
                "is_private": channel.is_private if channel else None,
            },
            "thread_ts": thread_ts,
            "messages": _with_names(messages, names),
        }

    @fastapp.get("/api/search")
    async def search(
        conn: Conn,
        q: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        hits = storage.search_messages(conn, q, limit=limit)
        names = _resolve_names(conn, [h.user for h in hits])
        channels = {
            c.id: c.name
            for c in (storage.get_channel(conn, h.channel) for h in hits)
            if c is not None
        }
        return {
            "query": q,
            "hits": [
                {
                    "channel": h.channel,
                    "channel_name": channels.get(h.channel, h.channel),
                    "thread_ts": h.thread_ts,
                    "ts": h.ts,
                    "user": h.user,
                    "user_name": names.get(h.user or "", h.user),
                    "text": h.text,
                    "snippet": h.snippet,
                }
                for h in hits
            ],
        }

    # ------------------------------------------------------------------
    # Live fetches (POST). Each raises 503 when no credentials are set.
    # ------------------------------------------------------------------

    def _credentials_or_503() -> Credentials:
        credentials = load_credentials(require=False)
        if not credentials.token:
            raise HTTPException(status_code=503, detail=NO_CREDENTIALS_DETAIL)
        return credentials

    @fastapp.post("/api/users/refresh")
    async def refresh_users(conn: Conn) -> dict[str, Any]:
        from slack_cached.cache import fetch_users

        client = _client_for_refresh(fastapp.state.api_base_url, _credentials_or_503())
        try:
            result = await fetch_users(conn, client)
        finally:
            await client.aclose()
        return {"users": asdict(result)}

    @fastapp.post("/api/channels/refresh")
    async def refresh_channels(conn: Conn) -> dict[str, Any]:
        from slack_cached.cache import fetch_channels

        client = _client_for_refresh(fastapp.state.api_base_url, _credentials_or_503())
        try:
            result = await fetch_channels(conn, client)
        finally:
            await client.aclose()
        return {"channels": asdict(result)}

    @fastapp.post("/api/channels/{channel_id}/refresh")
    async def refresh_channel(
        channel_id: str, conn: Conn, full_threads: bool = True
    ) -> dict[str, Any]:
        from slack_cached.cache import fetch_channel_messages

        _require_channel(conn, channel_id)
        client = _client_for_refresh(fastapp.state.api_base_url, _credentials_or_503())
        try:
            result = await fetch_channel_messages(
                conn, client, channel_id, full_threads=full_threads
            )
        finally:
            await client.aclose()
        return {"channel": asdict(result)}

    @fastapp.post("/api/channels/{channel_id}/threads/{thread_ts}/refresh")
    async def refresh_thread(channel_id: str, thread_ts: str, conn: Conn) -> dict[str, Any]:
        from slack_cached.cache import fetch_thread

        _require_channel(conn, channel_id)
        client = _client_for_refresh(fastapp.state.api_base_url, _credentials_or_503())
        try:
            result = await fetch_thread(conn, client, ThreadRef(channel_id, thread_ts))
        finally:
            await client.aclose()
        return {"thread": asdict(result)}

    # ------------------------------------------------------------------
    # Static UI
    # ------------------------------------------------------------------

    @fastapp.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    fastapp.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return fastapp
