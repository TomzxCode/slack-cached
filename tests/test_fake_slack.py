"""Tests for the fake Slack API server."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
import requests
import structlog

from slack_cached.fake_slack import Workspace, WorkspaceParams


@pytest.fixture(autouse=True)
def _silence_logging() -> None:
    structlog.configure(
        processors=[],
        wrapper_class=structlog.make_filtering_bound_logger(structlog.stdlib.logging.CRITICAL),
        logger_factory=structlog.ReturnLoggerFactory(),
    )


@pytest.fixture(scope="module")
def fake_server():
    from http.server import HTTPServer

    from slack_cached.fake_slack import FakeSlackHandler

    workspace = Workspace(seed=42)
    FakeSlackHandler.workspace = workspace
    server = HTTPServer(("127.0.0.1", 0), FakeSlackHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _get(base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = requests.get(f"{base_url}{path}", params=params, timeout=5)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Workspace generation
# ---------------------------------------------------------------------------


class TestWorkspaceGeneration:
    def test_default_workspace_dimensions(self) -> None:
        ws = Workspace(seed=42)
        assert len(ws.users) == 20
        assert len(ws.channels) == 13
        assert len(ws.threads) == 30

    def test_custom_dimensions(self) -> None:
        params = WorkspaceParams(
            seed=42,
            num_users=5,
            num_channels=3,
            num_threads=10,
            min_messages_per_thread=2,
            max_messages_per_thread=4,
        )
        ws = Workspace(params=params)
        assert len(ws.users) == 5
        assert len(ws.channels) == 3
        assert len(ws.threads) == 10

    def test_same_seed_produces_same_data(self) -> None:
        ws1 = Workspace(seed=42)
        ws2 = Workspace(seed=42)
        assert ws1.users[0]["id"] == ws2.users[0]["id"]
        assert ws1.channels[0]["id"] == ws2.channels[0]["id"]
        assert list(ws1.threads.keys()) == list(ws2.threads.keys())

    def test_different_seed_produces_different_data(self) -> None:
        ws1 = Workspace(seed=42)
        ws2 = Workspace(seed=99)
        # Different seeds should produce different user pools
        assert ws1.users != ws2.users

    def test_zero_activity_ratio_minimal_participants(self) -> None:
        ws = Workspace(params=WorkspaceParams(seed=42, activity_ratio=0.1, num_threads=5))
        # Still generates the requested number of threads
        assert len(ws.threads) == 5

    def test_high_num_users(self) -> None:
        ws = Workspace(params=WorkspaceParams(seed=42, num_users=100, num_threads=5))
        assert len(ws.users) == 100


# ---------------------------------------------------------------------------
# Users list endpoint
# ---------------------------------------------------------------------------


class TestUsersList:
    def test_returns_all_users(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/users.list")
        assert data["ok"] is True
        assert len(data["members"]) == 20

    def test_users_have_required_fields(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/users.list")
        for user in data["members"]:
            assert "id" in user
            assert "name" in user
            assert "real_name" in user
            assert "profile" in user
            assert "real_name" in user["profile"]

    def test_pagination(self, fake_server: str) -> None:
        page1 = _get(fake_server, "/api/users.list", {"limit": "8"})
        assert len(page1["members"]) == 8
        assert page1["response_metadata"]["next_cursor"] != ""

        cursor = page1["response_metadata"]["next_cursor"]
        page2 = _get(fake_server, "/api/users.list", {"limit": "8", "cursor": cursor})
        assert len(page2["members"]) == 8

        cursor2 = page2["response_metadata"]["next_cursor"]
        page3 = _get(fake_server, "/api/users.list", {"limit": "8", "cursor": cursor2})
        assert len(page3["members"]) == 4
        assert page3["response_metadata"]["next_cursor"] == ""

    def test_all_pages_cover_every_user(self, fake_server: str) -> None:
        all_ids: set[str] = set()
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, str] = {"limit": "5"}
            if cursor:
                params["cursor"] = cursor
            data = _get(fake_server, "/api/users.list", params)
            for user in data["members"]:
                all_ids.add(user["id"])
            next_cursor = data["response_metadata"]["next_cursor"]
            if not next_cursor:
                break
            cursor = next_cursor
        assert len(all_ids) == 20


# ---------------------------------------------------------------------------
# Conversations list endpoint
# ---------------------------------------------------------------------------


class TestConversationsList:
    def test_returns_all_channels(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/conversations.list")
        assert data["ok"] is True
        assert len(data["channels"]) == 13

    def test_channels_have_required_fields(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/conversations.list")
        for channel in data["channels"]:
            assert "id" in channel
            assert "name" in channel
            assert "is_private" in channel

    def test_filter_public_channels(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/conversations.list", {"types": "public_channel"})
        assert data["ok"] is True
        assert all(not ch["is_private"] for ch in data["channels"])

    def test_filter_private_channels(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/conversations.list", {"types": "private_channel"})
        assert data["ok"] is True
        assert all(ch["is_private"] for ch in data["channels"])

    def test_pagination(self, fake_server: str) -> None:
        page1 = _get(fake_server, "/api/conversations.list", {"limit": "5"})
        assert len(page1["channels"]) == 5
        assert page1["response_metadata"]["next_cursor"] != ""

        cursor = page1["response_metadata"]["next_cursor"]
        page2 = _get(fake_server, "/api/conversations.list", {"limit": "5", "cursor": cursor})
        assert len(page2["channels"]) == 5

    def test_filter_with_type_exclusion(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/conversations.list", {"types": "private_channel,im"})
        assert data["ok"] is True
        for ch in data["channels"]:
            assert ch["is_private"] is True


# ---------------------------------------------------------------------------
# Conversations replies endpoint
# ---------------------------------------------------------------------------


class TestConversationsReplies:
    def _first_thread(self, fake_server: str) -> tuple[str, str]:
        ws = Workspace(seed=42)
        key = next(iter(ws.threads))
        return key[0], key[1]

    def test_returns_thread_messages(self, fake_server: str) -> None:
        channel, thread_ts = self._first_thread(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts},
        )
        assert data["ok"] is True
        assert len(data["messages"]) > 0

    def test_messages_have_required_fields(self, fake_server: str) -> None:
        channel, thread_ts = self._first_thread(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts},
        )
        for msg in data["messages"]:
            assert "ts" in msg
            assert "user" in msg
            assert "text" in msg
            assert "thread_ts" in msg

    def test_oldest_filters_messages(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        key = next(iter(ws.threads))
        channel, thread_ts = key
        messages = ws.threads[key]
        if len(messages) < 3:
            pytest.skip("need at least 3 messages")

        mid_ts = messages[len(messages) // 2]["ts"]
        data = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts, "oldest": mid_ts},
        )
        assert data["ok"] is True
        for msg in data["messages"]:
            assert float(msg["ts"]) >= float(mid_ts)

    def test_nonexistent_thread_returns_error(self, fake_server: str) -> None:
        resp = requests.get(
            f"{fake_server}/api/conversations.replies",
            params={"channel": "C99NOTREAL", "ts": "9999999999.000000"},
            timeout=5,
        )
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_pagination(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        long_threads = [(k, v) for k, v in ws.threads.items() if len(v) > 4]
        if not long_threads:
            pytest.skip("need a thread with more than 4 messages")

        key, all_msgs = long_threads[0]
        channel, thread_ts = key
        page1 = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts, "limit": "2"},
        )
        assert len(page1["messages"]) == 2
        assert page1["has_more"] is True

        cursor = page1["response_metadata"]["next_cursor"]
        page2 = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts, "limit": "2", "cursor": cursor},
        )
        assert len(page2["messages"]) == 2
        assert page2["messages"][0]["ts"] != page1["messages"][0]["ts"]


# ---------------------------------------------------------------------------
# Unknown endpoint
# ---------------------------------------------------------------------------


class TestUnknownEndpoint:
    def test_returns_404(self, fake_server: str) -> None:
        resp = requests.get(f"{fake_server}/api/unknown.method", timeout=5)
        assert resp.status_code == 404
        data = resp.json()
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# Data stability
# ---------------------------------------------------------------------------


class TestDataStability:
    def test_users_stable_across_requests(self, fake_server: str) -> None:
        data1 = _get(fake_server, "/api/users.list")
        data2 = _get(fake_server, "/api/users.list")
        ids1 = [u["id"] for u in data1["members"]]
        ids2 = [u["id"] for u in data2["members"]]
        assert ids1 == ids2

    def test_channels_stable_across_requests(self, fake_server: str) -> None:
        data1 = _get(fake_server, "/api/conversations.list")
        data2 = _get(fake_server, "/api/conversations.list")
        ids1 = [c["id"] for c in data1["channels"]]
        ids2 = [c["id"] for c in data2["channels"]]
        assert ids1 == ids2

    def test_thread_stable_across_requests(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        key = next(iter(ws.threads))
        channel, thread_ts = key

        data1 = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts},
        )
        data2 = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts},
        )
        ts1 = [m["ts"] for m in data1["messages"]]
        ts2 = [m["ts"] for m in data2["messages"]]
        assert ts1 == ts2


# ---------------------------------------------------------------------------
# Parameter combinations
# ---------------------------------------------------------------------------


class TestParameterCombinations:
    def test_large_workspace(self) -> None:
        params = WorkspaceParams(
            seed=42,
            num_users=100,
            num_channels=25,
            num_threads=50,
            min_messages_per_thread=2,
            max_messages_per_thread=8,
        )
        ws = Workspace(params=params)
        assert len(ws.users) == 100
        assert len(ws.channels) == 25
        assert len(ws.threads) == 50

    def test_small_workspace(self) -> None:
        params = WorkspaceParams(
            seed=42,
            num_users=3,
            num_channels=2,
            num_threads=5,
            min_messages_per_thread=2,
            max_messages_per_thread=3,
        )
        ws = Workspace(params=params)
        assert len(ws.users) == 3
        assert len(ws.channels) == 2
        assert len(ws.threads) == 5
        # All threads should have 2-3 messages
        for messages in ws.threads.values():
            assert 2 <= len(messages) <= 3

    def test_single_channel(self, fake_server: str) -> None:
        params = WorkspaceParams(seed=42, num_channels=1, num_threads=5)
        ws = Workspace(params=params)
        # All threads should be in the single channel
        channel_ids = {ch_id for ch_id, _ in ws.threads}
        assert len(channel_ids) == 1

    def test_messages_per_thread_ratio(self) -> None:
        params = WorkspaceParams(
            seed=42,
            num_threads=20,
            min_messages_per_thread=4,
            max_messages_per_thread=4,
        )
        ws = Workspace(params=params)
        for messages in ws.threads.values():
            assert len(messages) >= 4
