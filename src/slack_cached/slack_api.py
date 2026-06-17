"""Minimal Slack Web API client for conversations.replies.

The client supports both bot tokens (which only need an Authorization header)
and the browser xoxc/d-cookie scheme used by Slack's web client (which also
needs the d cookie). The cookie is sent when available and otherwise omitted.

``base_url`` can be overridden to point at a different API server (e.g. a
local fake server).  Set ``SlackClient(credentials, base_url=...)`` or the
``SLACK_API_BASE_URL`` environment variable.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import requests
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


class SlackClient:
    """Thin wrapper around `requests` for the small subset of Slack we need."""

    def __init__(
        self,
        credentials: Credentials,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = DEFAULT_API_BASE,
    ) -> None:
        self._credentials = credentials
        self._session = session or requests.Session()
        self._sleep = sleep
        self._base_url = base_url.rstrip("/")

        self._replies_url = f"{self._base_url}/conversations.replies"
        self._users_list_url = f"{self._base_url}/users.list"
        self._conversations_list_url = f"{self._base_url}/conversations.list"
        self._search_messages_url = f"{self._base_url}/search.messages"

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._credentials.token}"}
        if self._credentials.cookie:
            headers["Cookie"] = f"d={self._credentials.cookie}"
        return headers

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET with retry/backoff for HTTP 429 and Slack 'ratelimited' errors."""
        for attempt in range(1, MAX_RETRIES + 1):
            response = self._session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "5"))
                log.warning(
                    "slack_rate_limited",
                    attempt=attempt,
                    retry_after=retry_after,
                )
                self._sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("error") == "ratelimited":
                retry_after = float(response.headers.get("Retry-After", "5"))
                log.warning(
                    "slack_rate_limited_body",
                    attempt=attempt,
                    retry_after=retry_after,
                )
                self._sleep(retry_after)
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

    def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Iterator[dict[str, Any]]:
        """Yield messages in a thread, paginating through cursors.

        When `oldest` is provided, Slack only returns messages with ts >= oldest;
        we still always include the thread root (ts == thread_ts) so that the
        cached root stays in sync, and we deduplicate against the caller's
        existing state.
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

            data = self._get(self._replies_url, params)
            yield from data.get("messages", [])

            if not data.get("has_more"):
                return
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    def iter_channel_history(
        self,
        channel: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Iterator[dict[str, Any]]:
        """Yield top-level messages in a channel via conversations.history.

        This returns standalone messages and thread parents, but not thread
        replies. Use iter_thread_replies to fetch the full thread.
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

            data = self._get(f"{self._base_url}/conversations.history", params)
            yield from data.get("messages", [])

            if not data.get("has_more"):
                return
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    def _iter_cursor(
        self,
        url: str,
        key: str,
        params: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """Yield items under `key`, following cursor-based pagination.

        Slack list endpoints page via response_metadata.next_cursor, returning
        an empty string (or omitting the cursor) when there is nothing more.
        """
        cursor: str | None = None
        page = 0
        seen = 0
        while True:
            call_params = dict(params)
            if cursor:
                call_params["cursor"] = cursor

            data = self._get(url, call_params)
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
            yield from items

            if not cursor:
                return

    def iter_users(self, limit: int = DEFAULT_LIST_LIMIT) -> Iterator[dict[str, Any]]:
        """Yield every member of the workspace via users.list."""
        return self._iter_cursor(self._users_list_url, "members", {"limit": limit})

    def iter_channels(
        self,
        types: str = DEFAULT_CHANNEL_TYPES,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Iterator[dict[str, Any]]:
        """Yield every conversation visible to the token via conversations.list."""
        return self._iter_cursor(
            self._conversations_list_url,
            "channels",
            {"limit": limit, "types": types},
        )

    def iter_search_messages(
        self,
        query: str,
        count: int = DEFAULT_SEARCH_COUNT,
        sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> Iterator[dict[str, Any]]:
        """Yield messages matching *query* via search.messages.

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
            data = self._get(self._search_messages_url, params)
            block = data.get("messages") or {}
            matches = block.get("matches", [])
            page += 1
            yield from matches

            pagination = block.get("pagination") or {}
            page_count = int(pagination.get("page_count", 0) or 0)
            if not matches or page > page_count:
                return
