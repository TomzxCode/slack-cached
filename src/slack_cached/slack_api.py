"""Async Slack Web API client using httpx.

The client supports both bot tokens (which only need an Authorization header)
and the browser xoxc/d-cookie scheme used by Slack's web client (which also
needs the d cookie). The cookie is sent when available and otherwise omitted.

``base_url`` can be overridden to point at a different API server (e.g. a
local fake server).  Set ``SlackClient(credentials, base_url=...)`` or the
``SLACK_API_BASE_URL`` environment variable.

Captures ``X-RateLimit-*`` headers across requests for proactive throttling
so concurrent tasks can coordinate and avoid hitting HTTP 429s.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from .config import Credentials

DEFAULT_API_BASE = "https://slack.com/api"
DEFAULT_LIMIT = 200
DEFAULT_LIST_LIMIT = 1000
DEFAULT_SEARCH_COUNT = 20
DEFAULT_CHANNEL_TYPES = "public_channel,private_channel,mpim,im"
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30

log = structlog.get_logger(__name__)


class SlackAPIError(RuntimeError):
    """Raised when Slack returns a non-ok response we cannot recover from."""


class RateLimitState:
    """Tracks Slack rate-limit headers across requests for proactive throttling.

    Shared across concurrent tasks so they can coordinate and avoid hitting 429s.
    """

    def __init__(self) -> None:
        self._remaining: int | None = None
        self._reset_at: float | None = None
        self._lock = asyncio.Lock()

    async def update_from_headers(self, headers: httpx.Headers) -> None:
        """Update internal state from X-RateLimit-* response headers."""
        async with self._lock:
            remaining = headers.get("x-ratelimit-remaining")
            if remaining is not None:
                with contextlib.suppress(ValueError):
                    self._remaining = int(remaining)
            reset = headers.get("x-ratelimit-reset")
            if reset is not None:
                with contextlib.suppress(ValueError):
                    self._reset_at = float(reset)

    async def should_slow_down(self, min_remaining: int = 5) -> bool:
        """Return True when the rate limit bucket is nearly exhausted."""
        async with self._lock:
            return self._remaining is not None and self._remaining <= min_remaining

    async def wait_for_reset_if_needed(self) -> None:
        """Sleep until the rate limit window resets, if it's nearly exhausted."""
        async with self._lock:
            if self._remaining is not None and self._remaining <= 2 and self._reset_at is not None:
                now = time.time()
                wait = self._reset_at - now
                if wait > 0:
                    log.warning(
                        "rate_limit_proactive_wait",
                        remaining=self._remaining,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    self._remaining = None
                    self._reset_at = None


class SlackClient:
    """Async wrapper around httpx.AsyncClient for the subset of Slack we need.

    Construct with an existing ``httpx.AsyncClient`` for connection reuse
    across many calls (e.g. the poll loop); otherwise a fresh client is
    created lazily on the first request and closed by ``aclose()``.
    """

    def __init__(
        self,
        credentials: Credentials,
        base_url: str = DEFAULT_API_BASE,
        client: httpx.AsyncClient | None = None,
        rate_limit_state: RateLimitState | None = None,
    ) -> None:
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._client = client
        self.rate_limit_state = rate_limit_state or RateLimitState()

        self._replies_url = f"{self._base_url}/conversations.replies"
        self._users_list_url = f"{self._base_url}/users.list"
        self._conversations_list_url = f"{self._base_url}/conversations.list"
        self._conversations_history_url = f"{self._base_url}/conversations.history"
        self._search_messages_url = f"{self._base_url}/search.messages"
        self._auth_test_url = f"{self._base_url}/auth.test"
        self._auth_test_data: dict[str, Any] | None = None

    @property
    def token(self) -> str:
        """Bearer token used for requests."""
        return self._credentials.token

    @property
    def base_url(self) -> str:
        """API base URL requests are sent to."""
        return self._base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._credentials.token}"}
        if self._credentials.cookie:
            headers["Cookie"] = f"d={self._credentials.cookie}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self._client

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET with retry/backoff for HTTP 429 and Slack 'ratelimited' errors."""
        client = await self._get_client()
        for attempt in range(1, MAX_RETRIES + 1):
            await self.rate_limit_state.wait_for_reset_if_needed()

            response = await client.get(url, headers=self._headers(), params=params)

            await self.rate_limit_state.update_from_headers(response.headers)

            if response.status_code == 429:
                retry_after = float(response.headers.get("retry-after", "5"))
                log.warning("slack_rate_limited", attempt=attempt, retry_after=retry_after)
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            data = response.json()
            if data.get("error") == "ratelimited":
                retry_after = float(response.headers.get("retry-after", "5"))
                log.warning("slack_rate_limited_body", attempt=attempt, retry_after=retry_after)
                await asyncio.sleep(retry_after)
                continue

            if not data.get("ok"):
                error = data.get("error", "unknown")
                message = f"Slack API error from {url}: {error}"
                if error == "invalid_auth":
                    if self._credentials.token.startswith("xoxc-") and not self._credentials.cookie:
                        message += (
                            " (an xoxc- token needs its matching xoxd- d cookie; set SLACK_COOKIE)"
                        )
                    else:
                        message += (
                            " (token/cookie rejected; xoxc- and xoxd- values must "
                            "come from the same browser session and not be expired)"
                        )
                raise SlackAPIError(message)
            return data

        raise SlackAPIError(f"Rate limited {MAX_RETRIES} times, giving up on {url}")

    async def auth_test(self) -> dict[str, Any]:
        """Return the ``auth.test`` identity payload (cached per client).

        Identifies which workspace the configured credentials belong to; used
        to pick the per-workspace cache database.
        """
        if self._auth_test_data is None:
            self._auth_test_data = await self._get(self._auth_test_url, {})
        return self._auth_test_data

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield messages in a thread, paginating through cursors.

        When ``oldest`` is provided, Slack only returns messages with ts >= oldest;
        the caller is responsible for de-duplicating against existing state.
        """
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "channel": channel,
                "ts": thread_ts,
                "limit": limit,
                "inclusive": "true",
            }
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor

            data = await self._get(self._replies_url, params)
            for msg in data.get("messages", []):
                yield msg

            if not data.get("has_more"):
                return
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    async def iter_channel_history(
        self,
        channel: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield top-level messages in a channel via conversations.history.

        This returns standalone messages and thread parents, but not thread
        replies. Use ``iter_thread_replies`` to fetch the full thread.
        """
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "channel": channel,
                "limit": limit,
                "inclusive": "true",
            }
            if oldest:
                params["oldest"] = oldest
            if latest:
                params["latest"] = latest
            if cursor:
                params["cursor"] = cursor

            data = await self._get(self._conversations_history_url, params)
            for msg in data.get("messages", []):
                yield msg

            if not data.get("has_more"):
                return
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    async def _iter_cursor(
        self,
        url: str,
        key: str,
        params: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items under ``key``, following cursor-based pagination.

        Slack list endpoints page via ``response_metadata.next_cursor``, returning
        an empty string (or omitting the cursor) when there is nothing more.
        """
        cursor: str | None = None
        page = 0
        seen = 0
        while True:
            call_params = dict(params)
            if cursor:
                call_params["cursor"] = cursor

            data = await self._get(url, call_params)
            items = data.get(key, [])
            page += 1
            seen += len(items)
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            log.info(
                "slack_list_page",
                key=key,
                page=page,
                page_items=len(items),
                total_seen=seen,
                has_more=bool(cursor),
            )
            for item in items:
                yield item

            if not cursor:
                return

    async def iter_users(self, limit: int = DEFAULT_LIST_LIMIT) -> AsyncIterator[dict[str, Any]]:
        """Yield every member of the workspace via users.list."""
        async for item in self._iter_cursor(self._users_list_url, "members", {"limit": limit}):
            yield item

    async def iter_channels(
        self,
        types: str = DEFAULT_CHANNEL_TYPES,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every conversation visible to the token via conversations.list."""
        async for item in self._iter_cursor(
            self._conversations_list_url,
            "channels",
            {"limit": limit, "types": types},
        ):
            yield item

    async def iter_search_messages(
        self,
        query: str,
        count: int = DEFAULT_SEARCH_COUNT,
        sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield messages matching ``query`` via search.messages.

        Slack search uses page-based pagination (page N of page_count), unlike
        the cursor-based pagination used by list endpoints. Each match carries
        its own ``channel``, ``ts`` and ``permalink`` so callers can route it
        back into the per-thread cache.
        """
        page = 1
        seen_pages: set[int] = set()
        while True:
            if page in seen_pages:
                return
            seen_pages.add(page)
            params: dict[str, Any] = {
                "query": query,
                "count": count,
                "page": page,
                "sort": sort,
                "sort_dir": sort_dir,
            }
            data = await self._get(self._search_messages_url, params)
            block = data.get("messages") or {}
            matches = block.get("matches", [])
            page += 1
            for match in matches:
                yield match

            pagination = block.get("pagination") or {}
            page_count = int(pagination.get("page_count", 0) or 0)
            if not matches or page > page_count:
                return
