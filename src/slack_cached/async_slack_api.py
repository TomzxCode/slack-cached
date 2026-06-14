"""Async Slack Web API client using httpx.

Mirrors the sync SlackClient interface but uses httpx.AsyncClient for
concurrent, non-blocking HTTP requests. Shares the same retry/backoff
logic for HTTP 429 and Slack 'ratelimited' errors.

Additionally captures X-RateLimit-* headers for proactive throttling.
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
from .slack_api import (
    DEFAULT_API_BASE,
    DEFAULT_CHANNEL_TYPES,
    DEFAULT_LIMIT,
    DEFAULT_LIST_LIMIT,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    SlackAPIError,
)

log = structlog.get_logger(__name__)


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


class AsyncSlackClient:
    """Async wrapper around httpx for the small subset of Slack we need."""

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

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> AsyncIterator[dict[str, Any]]:
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

            data = await self._get(f"{self._base_url}/conversations.history", params)
            for msg in data.get("messages", []):
                yield msg

            if not data.get("has_more"):
                return
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    async def iter_users(self, limit: int = DEFAULT_LIST_LIMIT) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor

            data = await self._get(self._users_list_url, params)
            items = data.get("members", [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            for item in items:
                yield item
            if not cursor:
                return

    async def iter_channels(
        self,
        types: str = DEFAULT_CHANNEL_TYPES,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit, "types": types}
            if cursor:
                params["cursor"] = cursor

            data = await self._get(self._conversations_list_url, params)
            items = data.get("channels", [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            for item in items:
                yield item
            if not cursor:
                return
