"""Tests for the Slack Web API client's list pagination."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import structlog

from slack_cached.config import Credentials
from slack_cached.slack_api import SlackClient


@pytest.fixture(autouse=True)
def _silence_logging() -> None:
    """Drop log records so structlog does not write to a closed capture file.

    The pagination generators log per page; without this the default
    PrintLogger targets pytest's captured stderr, which can already be closed
    when the generator is consumed.
    """
    structlog.configure(
        processors=[],
        wrapper_class=structlog.make_filtering_bound_logger(structlog.stdlib.logging.CRITICAL),
        logger_factory=structlog.ReturnLoggerFactory(),
    )


class FakeTransport:
    """Returns queued JSON payloads via httpx MockTransport interface."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append({"url": request.url.path, "params": params})
        payload = self._payloads.pop(0)
        return httpx.Response(200, json=payload)


def _client(transport: FakeTransport) -> SlackClient:
    httpx_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return SlackClient(
        Credentials(token="xoxb-test", cookie=None),
        client=httpx_client,
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


def test_iter_users_follows_cursor() -> None:
    transport = FakeTransport(
        [
            {
                "ok": True,
                "members": [{"id": "U1"}, {"id": "U2"}],
                "response_metadata": {"next_cursor": "next"},
            },
            {"ok": True, "members": [{"id": "U3"}], "response_metadata": {"next_cursor": ""}},
        ]
    )
    client = _client(transport)

    users = asyncio.run(_collect(client.iter_users()))

    assert [u["id"] for u in users] == ["U1", "U2", "U3"]
    assert transport.calls[0]["url"] == "/api/users.list"
    assert "cursor" not in transport.calls[0]["params"]
    assert transport.calls[1]["params"]["cursor"] == "next"


def test_iter_channels_passes_types_and_stops_without_cursor() -> None:
    transport = FakeTransport(
        [
            {"ok": True, "channels": [{"id": "C1"}], "response_metadata": {}},
        ]
    )
    client = _client(transport)

    channels = asyncio.run(_collect(client.iter_channels(types="public_channel")))

    assert [c["id"] for c in channels] == ["C1"]
    assert transport.calls[0]["url"] == "/api/conversations.list"
    assert transport.calls[0]["params"]["types"] == "public_channel"
    assert len(transport.calls) == 1


def test_iter_channel_history_follows_cursor() -> None:
    transport = FakeTransport(
        [
            {
                "ok": True,
                "has_more": True,
                "messages": [{"ts": "1.0"}, {"ts": "2.0"}],
                "response_metadata": {"next_cursor": "page2"},
            },
            {"ok": True, "messages": [{"ts": "3.0"}], "response_metadata": {}},
        ]
    )
    client = _client(transport)

    msgs = asyncio.run(_collect(client.iter_channel_history(channel="C1")))

    assert [m["ts"] for m in msgs] == ["1.0", "2.0", "3.0"]
    assert transport.calls[0]["url"] == "/api/conversations.history"
    assert transport.calls[0]["params"]["channel"] == "C1"
    assert "cursor" not in transport.calls[0]["params"]
    assert transport.calls[1]["params"]["cursor"] == "page2"


def test_iter_channel_history_stops_without_has_more() -> None:
    transport = FakeTransport(
        [
            {"ok": True, "messages": [{"ts": "1.0"}]},
        ]
    )
    client = _client(transport)

    msgs = asyncio.run(_collect(client.iter_channel_history(channel="C1")))

    assert len(msgs) == 1
    assert len(transport.calls) == 1


def test_iter_search_messages_paginates_by_page() -> None:
    transport = FakeTransport(
        [
            {
                "ok": True,
                "messages": {
                    "total": 3,
                    "pagination": {
                        "total_count": 3,
                        "page": 1,
                        "per_page": 2,
                        "page_count": 2,
                        "first": 1,
                        "last": 2,
                    },
                    "matches": [
                        {"ts": "1.0", "channel": "C1", "text": "a"},
                        {"ts": "2.0", "channel": "C1", "text": "b"},
                    ],
                },
            },
            {
                "ok": True,
                "messages": {
                    "total": 3,
                    "pagination": {
                        "total_count": 3,
                        "page": 2,
                        "per_page": 2,
                        "page_count": 2,
                        "first": 3,
                        "last": 3,
                    },
                    "matches": [
                        {"ts": "3.0", "channel": "C2", "text": "c"},
                    ],
                },
            },
        ]
    )
    client = _client(transport)

    msgs = asyncio.run(_collect(client.iter_search_messages(query="hello", count=2)))

    assert [m["ts"] for m in msgs] == ["1.0", "2.0", "3.0"]
    assert len(transport.calls) == 2
    assert transport.calls[0]["url"] == "/api/search.messages"
    assert transport.calls[0]["params"]["query"] == "hello"
    assert transport.calls[0]["params"]["page"] == "1"
    assert transport.calls[0]["params"]["count"] == "2"
    assert transport.calls[1]["params"]["page"] == "2"


def test_iter_search_messages_stops_on_single_page() -> None:
    transport = FakeTransport(
        [
            {
                "ok": True,
                "messages": {
                    "total": 1,
                    "pagination": {
                        "total_count": 1,
                        "page": 1,
                        "per_page": 20,
                        "page_count": 1,
                        "first": 1,
                        "last": 1,
                    },
                    "matches": [{"ts": "1.0", "channel": "C1", "text": "a"}],
                },
            },
        ]
    )
    client = _client(transport)

    msgs = asyncio.run(_collect(client.iter_search_messages(query="hello")))

    assert [m["ts"] for m in msgs] == ["1.0"]
    assert len(transport.calls) == 1


def test_iter_search_messages_no_matches_returns_empty() -> None:
    transport = FakeTransport(
        [
            {
                "ok": True,
                "messages": {
                    "total": 0,
                    "pagination": {
                        "total_count": 0,
                        "page": 1,
                        "per_page": 20,
                        "page_count": 1,
                        "first": 0,
                        "last": 0,
                    },
                    "matches": [],
                },
            },
        ]
    )
    client = _client(transport)

    msgs = asyncio.run(_collect(client.iter_search_messages(query="nothing")))

    assert msgs == []
    assert len(transport.calls) == 1
