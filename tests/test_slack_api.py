"""Tests for the Slack Web API client's list pagination."""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from slack_cached.config import Credentials
from slack_cached.slack_api import DEFAULT_API_BASE, SlackClient


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


class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    """Returns queued responses and records the params of each call."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        timeout: int,
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": dict(params)})
        return FakeResponse(self._responses.pop(0))


def _client(session: FakeSession) -> SlackClient:
    return SlackClient(Credentials(token="xoxb-test", cookie=None), session=session)


def test_iter_users_follows_cursor() -> None:
    session = FakeSession(
        [
            {
                "ok": True,
                "members": [{"id": "U1"}, {"id": "U2"}],
                "response_metadata": {"next_cursor": "next"},
            },
            {"ok": True, "members": [{"id": "U3"}], "response_metadata": {"next_cursor": ""}},
        ]
    )
    client = _client(session)

    users = list(client.iter_users())

    assert [u["id"] for u in users] == ["U1", "U2", "U3"]
    assert session.calls[0]["url"] == f"{DEFAULT_API_BASE}/users.list"
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["cursor"] == "next"


def test_iter_channels_passes_types_and_stops_without_cursor() -> None:
    session = FakeSession(
        [
            {"ok": True, "channels": [{"id": "C1"}], "response_metadata": {}},
        ]
    )
    client = _client(session)

    channels = list(client.iter_channels(types="public_channel"))

    assert [c["id"] for c in channels] == ["C1"]
    assert session.calls[0]["url"] == f"{DEFAULT_API_BASE}/conversations.list"
    assert session.calls[0]["params"]["types"] == "public_channel"
    assert len(session.calls) == 1


def test_iter_channel_history_follows_cursor() -> None:
    session = FakeSession(
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
    client = _client(session)

    msgs = list(client.iter_channel_history(channel="C1"))

    assert [m["ts"] for m in msgs] == ["1.0", "2.0", "3.0"]
    assert session.calls[0]["url"] == f"{DEFAULT_API_BASE}/conversations.history"
    assert session.calls[0]["params"]["channel"] == "C1"
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["cursor"] == "page2"


def test_iter_channel_history_stops_without_has_more() -> None:
    session = FakeSession(
        [
            {"ok": True, "messages": [{"ts": "1.0"}]},
        ]
    )
    client = _client(session)

    msgs = list(client.iter_channel_history(channel="C1"))

    assert len(msgs) == 1
    assert len(session.calls) == 1
