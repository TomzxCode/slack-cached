"""Tests for the fake Slack API server."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
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
    resp = httpx.get(f"{base_url}{path}", params=params, timeout=5)
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
        resp = httpx.get(
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
        resp = httpx.get(f"{fake_server}/api/unknown.method", timeout=5)
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


# ---------------------------------------------------------------------------
# Conversations history endpoint
# ---------------------------------------------------------------------------


class TestConversationsHistory:
    def _first_channel_with_threads(self, fake_server: str) -> str:
        ws = Workspace(seed=42)
        channel_ids = {ch_id for ch_id, _ in ws.threads}
        return next(iter(channel_ids))

    def test_returns_top_level_messages(self, fake_server: str) -> None:
        channel = self._first_channel_with_threads(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel},
        )
        assert data["ok"] is True
        assert len(data["messages"]) > 0

    def test_each_message_is_thread_root(self, fake_server: str) -> None:
        channel = self._first_channel_with_threads(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel},
        )
        for msg in data["messages"]:
            assert "ts" in msg
            assert "thread_ts" in msg
            assert msg["ts"] == msg["thread_ts"]

    def test_reply_count_matches_thread(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        channel = self._first_channel_with_threads(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel},
        )
        for msg in data["messages"]:
            thread_key = (channel, msg["thread_ts"])
            full_thread = ws.threads.get(thread_key, [])
            expected_replies = len(full_thread) - 1
            assert msg["reply_count"] == expected_replies

    def test_message_count_matches_thread_count(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        channel = self._first_channel_with_threads(fake_server)
        expected_count = sum(1 for ch_id, _ in ws.threads if ch_id == channel)
        all_messages: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, str] = {"channel": channel, "limit": "5"}
            if cursor:
                params["cursor"] = cursor
            data = _get(fake_server, "/api/conversations.history", params)
            all_messages.extend(data["messages"])
            next_cursor = data["response_metadata"]["next_cursor"]
            if not next_cursor:
                break
            cursor = next_cursor
        assert len(all_messages) == expected_count

    def test_pagination(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        channel = self._first_channel_with_threads(fake_server)
        total_in_channel = sum(1 for ch_id, _ in ws.threads if ch_id == channel)
        if total_in_channel < 3:
            pytest.skip("need at least 3 threads in channel")

        page1 = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel, "limit": "2"},
        )
        assert len(page1["messages"]) == 2
        assert page1["has_more"] is True

        cursor = page1["response_metadata"]["next_cursor"]
        page2 = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel, "limit": "2", "cursor": cursor},
        )
        assert len(page2["messages"]) >= 1
        assert page2["messages"][0]["ts"] != page1["messages"][0]["ts"]

    def test_empty_channel_returns_empty(self, fake_server: str) -> None:
        data = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": "C9999"},
        )
        assert data["ok"] is True
        assert data["messages"] == []

    def test_messages_sorted_chronologically(self, fake_server: str) -> None:
        channel = self._first_channel_with_threads(fake_server)
        data = _get(
            fake_server,
            "/api/conversations.history",
            {"channel": channel},
        )
        timestamps = [float(m["ts"]) for m in data["messages"]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# search.messages endpoint
# ---------------------------------------------------------------------------


def _all_texts(ws: Workspace) -> list[str]:
    return [(m.get("text") or "") for msgs in ws.threads.values() for m in msgs]


class TestSearchMessages:
    def _known_term(self) -> str:
        ws = Workspace(seed=42)
        # Pick a real lowercase word that appears in the generated workspace.
        for text in _all_texts(ws):
            for tok in text.split():
                tok = tok.strip(".,!?:;\"'()-")
                if len(tok) >= 5 and tok.isalpha():
                    return tok.lower()
        pytest.skip("no searchable token in workspace")

    def test_returns_matches_with_required_fields(self, fake_server: str) -> None:
        term = self._known_term()
        data = _get(fake_server, "/api/search.messages", {"query": term})
        assert data["ok"] is True
        assert data["query"] == term
        messages_block = data["messages"]
        assert messages_block["total"] >= 1
        assert messages_block["pagination"]["page"] == 1
        assert messages_block["pagination"]["page_count"] >= 1
        assert len(messages_block["matches"]) >= 1
        for match in messages_block["matches"]:
            assert "ts" in match
            assert "channel" in match
            assert "permalink" in match

    def test_match_channel_is_real_channel(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        term = self._known_term()
        data = _get(fake_server, "/api/search.messages", {"query": term})
        channel_ids = {c["id"] for c in ws.channels}
        for match in data["messages"]["matches"]:
            # search.messages returns channel as an object, like real Slack.
            assert match["channel"]["id"] in channel_ids

    def test_total_count_matches_workspace(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        term = self._known_term()
        expected = sum(1 for text in _all_texts(ws) if term in text.lower())
        data = _get(fake_server, "/api/search.messages", {"query": term})
        assert data["messages"]["total"] == expected

    def test_unknown_term_returns_zero_matches(self, fake_server: str) -> None:
        data = _get(fake_server, "/api/search.messages", {"query": "zzznotaword12345"})
        assert data["ok"] is True
        assert data["messages"]["total"] == 0
        assert data["messages"]["matches"] == []
        assert data["messages"]["pagination"]["page_count"] == 1

    def test_pagination_splits_matches(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        term = self._known_term()
        total = sum(1 for text in _all_texts(ws) if term in text.lower())
        if total < 3:
            pytest.skip("need at least 3 matches to paginate")
        page1 = _get(
            fake_server, "/api/search.messages", {"query": term, "count": "2", "page": "1"}
        )
        assert len(page1["messages"]["matches"]) == 2
        assert page1["messages"]["pagination"]["page_count"] >= 2
        cursor_page = page1["messages"]["pagination"]["page"]
        page2 = _get(
            fake_server,
            "/api/search.messages",
            {"query": term, "count": "2", "page": str(cursor_page + 1)},
        )
        assert len(page2["messages"]["matches"]) >= 1
        # Pages should not overlap in ts.
        ts1 = {m["ts"] for m in page1["messages"]["matches"]}
        ts2 = {m["ts"] for m in page2["messages"]["matches"]}
        assert not (ts1 & ts2)

    def test_and_semantics_for_multiple_terms(self, fake_server: str) -> None:
        ws = Workspace(seed=42)
        texts = _all_texts(ws)
        # Find two tokens that co-occur in at least one message.
        pair = None
        for text in texts:
            toks = {
                t.strip(".,!?:;\"'()-").lower()
                for t in text.split()
                if len(t.strip(".,!?:;\"'()-")) >= 4
            }
            if len(toks) >= 2:
                pair = sorted(toks)[:2]
                break
        if pair is None:
            pytest.skip("no two-term message in workspace")
        data = _get(fake_server, "/api/search.messages", {"query": " ".join(pair)})
        for match in data["messages"]["matches"]:
            lowered = (match.get("text") or "").lower()
            assert all(term in lowered for term in pair)

    def test_search_highlights_query_terms_with_backticks(self, fake_server: str) -> None:
        """``search.messages`` wraps each query term in backticks (mimics
        real Slack's highlighting), while ``conversations.replies`` returns
        the plain text. The canonical comparison in ``storage.upsert_messages``
        has to survive this drift.
        """
        term = self._known_term()
        search_data = _get(fake_server, "/api/search.messages", {"query": term})
        match = search_data["messages"]["matches"][0]
        # The query term appears wrapped in backticks in the search response.
        assert f"`{term}`" in match["text"].lower() or any(
            f"`{t}`" in match["text"].lower() for t in term.split()
        )
        # The same message fetched via conversations.replies is plain.
        channel = match["channel"]["id"]
        thread_ts = match["ts"]
        replies = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": thread_ts},
        )
        for msg in replies["messages"]:
            if msg["ts"] == match["ts"]:
                assert "`" not in msg["text"]
                break

    def test_search_omits_thread_ts_for_thread_parents(self, fake_server: str) -> None:
        """Real ``search.messages`` drops ``thread_ts`` on thread parents
        (where it would equal ``ts``); ``conversations.replies`` keeps it.
        """
        term = self._known_term()
        data = _get(fake_server, "/api/search.messages", {"query": term})
        for match in data["messages"]["matches"]:
            # Either thread_ts is absent, or it's a reply (ts != thread_ts).
            if "thread_ts" in match:
                assert match["thread_ts"] != match["ts"]

    def test_search_omits_parent_user_id(self, fake_server: str) -> None:
        term = self._known_term()
        data = _get(fake_server, "/api/search.messages", {"query": term})
        for match in data["messages"]["matches"]:
            assert "parent_user_id" not in match

    def test_search_omits_edited_field(self, fake_server: str) -> None:
        """``search.messages`` does not surface ``edited``, even on messages
        that ``conversations.replies`` reports as edited.
        """
        # Find a message that has ``edited`` set in the workspace.
        ws = Workspace(seed=42)
        edited_keys = [
            (ch, ts)
            for (ch, _ts), msgs in ws.threads.items()
            for msg in msgs
            if "edited" in msg
            for ts in [msg["ts"]]
        ]
        if not edited_keys:
            pytest.skip("no edited messages in workspace")
        channel, ts = edited_keys[0]
        # Find a query term that matches the edited message.
        edited_msg = next(m for msgs in ws.threads.values() for m in msgs if m["ts"] == ts)
        term = next(
            (
                tok.strip(".,!?:;\"'()-").lower()
                for tok in edited_msg["text"].split()
                if len(tok.strip(".,!?:;\"'()-")) >= 4
            ),
            None,
        )
        if term is None:
            pytest.skip("edited message has no searchable token")

        data = _get(fake_server, "/api/search.messages", {"query": term})
        for match in data["messages"]["matches"]:
            assert "edited" not in match

        # And confirm conversations.replies DOES include it for the same msg.
        replies = _get(
            fake_server,
            "/api/conversations.replies",
            {"channel": channel, "ts": ts},
        )
        edited_via_replies = [m for m in replies["messages"] if m["ts"] == ts]
        assert edited_via_replies
        assert "edited" in edited_via_replies[0]

    def test_search_response_includes_permalink_and_channel_object(self, fake_server: str) -> None:
        """``search.messages`` decorates matches with ``permalink`` and an
        object form of ``channel`` that ``conversations.*`` does not return.
        """
        term = self._known_term()
        data = _get(fake_server, "/api/search.messages", {"query": term})
        for match in data["messages"]["matches"]:
            assert isinstance(match["channel"], dict)
            assert "id" in match["channel"]
            assert "name" in match["channel"]
            assert match["permalink"].startswith("https://acme.slack.com/archives/")


# ---------------------------------------------------------------------------
# Integration: fetch_channel_messages against fake server
# ---------------------------------------------------------------------------


class TestFetchChannelMessagesIntegration:
    @pytest.fixture()
    def workspace(self) -> Workspace:
        return Workspace(seed=42)

    def test_fetch_top_level_only(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        import asyncio

        from slack_cached.cache import fetch_channel_messages
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect

        channel = next(ch_id for ch_id, _ in workspace.threads)
        expected_threads = sum(1 for ch_id, _ in workspace.threads if ch_id == channel)

        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                result = asyncio.run(fetch_channel_messages(conn, client, channel))
            finally:
                asyncio.run(client.aclose())
            assert result.fetched_messages == expected_threads
            assert result.total_messages == expected_threads
            assert result.threads_with_replies_fetched == 0
        finally:
            conn.close()

    def test_fetch_full_threads(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        import asyncio

        from slack_cached.cache import fetch_channel_messages
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect

        channel = next(ch_id for ch_id, _ in workspace.threads)
        expected_threads = sum(1 for ch_id, _ in workspace.threads if ch_id == channel)
        expected_total = sum(
            len(msgs) for (ch_id, _ts), msgs in workspace.threads.items() if ch_id == channel
        )

        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                result = asyncio.run(
                    fetch_channel_messages(conn, client, channel, full_threads=True)
                )
            finally:
                asyncio.run(client.aclose())
            assert result.fetched_messages == expected_total
            assert result.total_messages == expected_total
            assert result.threads_with_replies_fetched == expected_threads
        finally:
            conn.close()

    def test_fetch_then_show_thread(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        import asyncio

        from slack_cached.cache import fetch_channel_messages, load_thread
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect
        from slack_cached.urls import ThreadRef

        (channel, thread_ts), thread_msgs = next(iter(workspace.threads.items()))

        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                asyncio.run(fetch_channel_messages(conn, client, channel))
            finally:
                asyncio.run(client.aclose())
            ref = ThreadRef(channel=channel, thread_ts=thread_ts)
            cached = load_thread(conn, ref)
            assert len(cached) == 1
            assert cached[0].ts == thread_ts
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Integration: fetch_search against fake server
# ---------------------------------------------------------------------------


class TestFetchSearchIntegration:
    @pytest.fixture()
    def workspace(self) -> Workspace:
        return Workspace(seed=42)

    def _known_term(self, ws: Workspace) -> str:
        for msgs in ws.threads.values():
            for m in msgs:
                for tok in (m.get("text") or "").split():
                    tok = tok.strip(".,!?:;\"'()-")
                    if len(tok) >= 5 and tok.isalpha():
                        return tok.lower()
        pytest.skip("no searchable token in workspace")

    def test_fetch_search_caches_matches(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        import asyncio

        from slack_cached.cache import fetch_search
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect

        term = self._known_term(workspace)
        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                result = asyncio.run(fetch_search(conn, client, query=term))
            finally:
                asyncio.run(client.aclose())
            assert len(result.matches) >= 1
            assert result.threads_new >= 1
            for match in result.matches:
                assert match.get("channel")
                assert match.get("permalink")
        finally:
            conn.close()

    def test_fetch_search_full_threads_then_show(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        import asyncio

        from slack_cached.cache import fetch_search
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect, load_thread_messages

        term = self._known_term(workspace)
        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                result = asyncio.run(fetch_search(conn, client, query=term, full_threads=True))
            finally:
                asyncio.run(client.aclose())
            # At least one matched thread should now have more than the single
            # search match cached (i.e. its replies were expanded).
            expanded = False
            for match in result.matches:
                channel = match["channel"]
                thread_ts = match.get("thread_ts") or match["ts"]
                cached = load_thread_messages(conn, channel, thread_ts)
                if len(cached) > 1:
                    expanded = True
                    break
            assert expanded
        finally:
            conn.close()

    def test_fetch_search_full_threads_second_run_reports_cache_hits(
        self, fake_server: str, tmp_path: Path, workspace: Workspace
    ) -> None:
        """End-to-end regression: re-running ``search --full-threads`` against
        the (now realistic) fake server reports cache hits on the second run.
        The fake server mimics real Slack's drift between ``search.messages``
        and ``conversations.replies`` (search highlighting in ``text``,
        omitted ``thread_ts``/``parent_user_id``/``edited`` on parents); the
        canonical comparison in ``storage.upsert_messages`` plus
        ``fetch_search`` skipping match-caching in full-threads mode have to
        absorb all of that.
        """
        import asyncio

        from slack_cached.cache import fetch_search
        from slack_cached.config import Credentials
        from slack_cached.slack_api import SlackClient
        from slack_cached.storage import connect

        term = self._known_term(workspace)
        conn = connect(tmp_path / "cache.db")
        try:
            client = SlackClient(
                Credentials(token="xoxb-fake", cookie=None),
                base_url=f"{fake_server}/api",
            )
            try:
                first = asyncio.run(fetch_search(conn, client, query=term, full_threads=True))
                second = asyncio.run(fetch_search(conn, client, query=term, full_threads=True))
            finally:
                asyncio.run(client.aclose())

            assert first.matches
            assert first.threads_new >= 1
            assert first.messages_new >= 1

            # The second run sees the same data unchanged: every previously
            # cached message is a cache hit, nothing new is written.
            assert len(second.matches) == len(first.matches)
            assert second.threads_seen == first.threads_seen
            assert second.threads_new == 0
            assert second.messages_seen == first.messages_seen
            assert second.messages_new == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_default_disabled(self) -> None:
        from slack_cached.fake_slack import RateLimiter

        limiter = RateLimiter(limits={"/api/test": 2})
        allowed, _ = limiter.check("/api/test")
        assert allowed is True

    def test_returns_false_when_exceeded(self) -> None:
        from slack_cached.fake_slack import RateLimiter

        limiter = RateLimiter(limits={"/api/test": 2}, now=lambda: 0.0)
        limiter.check("/api/test")
        limiter.check("/api/test")
        allowed, retry_after = limiter.check("/api/test")
        assert allowed is False
        assert retry_after >= 1

    def test_unknown_path_always_allowed(self) -> None:
        from slack_cached.fake_slack import RateLimiter

        limiter = RateLimiter(limits={"/api/test": 1}, now=lambda: 0.0)
        limiter.check("/api/test")
        for _ in range(20):
            allowed, _ = limiter.check("/api/other")
            assert allowed is True

    def test_per_endpoint_independent(self) -> None:
        from slack_cached.fake_slack import RateLimiter

        limiter = RateLimiter(
            limits={"/api/a": 1, "/api/b": 1},
            now=lambda: 0.0,
        )
        limiter.check("/api/a")
        allowed_a, _ = limiter.check("/api/a")
        allowed_b, _ = limiter.check("/api/b")
        assert allowed_a is False
        assert allowed_b is True

    def test_succeeds_after_window_expires(self) -> None:
        from slack_cached.fake_slack import RateLimiter

        clock = [0.0]
        limiter = RateLimiter(limits={"/api/test": 1}, now=lambda: clock[0])
        limiter.check("/api/test")
        allowed, retry_after = limiter.check("/api/test")
        assert allowed is False
        clock[0] += retry_after
        allowed, _ = limiter.check("/api/test")
        assert allowed is True

    def test_real_slack_tiers(self) -> None:
        from slack_cached.fake_slack import ENDPOINT_RATE_LIMITS

        assert ENDPOINT_RATE_LIMITS["conversations.replies"] == 50
        assert ENDPOINT_RATE_LIMITS["users.list"] == 20
        assert ENDPOINT_RATE_LIMITS["conversations.list"] == 20


@pytest.fixture(scope="module")
def rate_limited_server():
    from http.server import HTTPServer

    from slack_cached.fake_slack import FakeSlackHandler, RateLimiter, Workspace

    workspace = Workspace(seed=42)
    FakeSlackHandler.workspace = workspace
    FakeSlackHandler.rate_limiter = RateLimiter()
    server = HTTPServer(("127.0.0.1", 0), FakeSlackHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    FakeSlackHandler.rate_limiter = None


class TestRateLimitedServer:
    def test_normal_requests_succeed(self, rate_limited_server: str) -> None:
        resp = httpx.get(
            f"{rate_limited_server}/api/users.list",
            params={"limit": "5"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_returns_429_when_limit_exceeded(self, rate_limited_server: str) -> None:
        url = f"{rate_limited_server}/api/users.list"
        for _ in range(20):
            httpx.get(url, params={"limit": "1"}, timeout=5)
        resp = httpx.get(url, params={"limit": "1"}, timeout=5)
        assert resp.status_code == 429
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "ratelimited"
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) >= 1

    def test_rate_limit_is_per_endpoint(self, rate_limited_server: str) -> None:
        for _ in range(20):
            httpx.get(f"{rate_limited_server}/api/users.list", params={"limit": "1"}, timeout=5)
        resp_users = httpx.get(
            f"{rate_limited_server}/api/users.list", params={"limit": "1"}, timeout=5
        )
        assert resp_users.status_code == 429
        resp_channels = httpx.get(
            f"{rate_limited_server}/api/conversations.list", params={"limit": "1"}, timeout=5
        )
        assert resp_channels.status_code == 200


# ---------------------------------------------------------------------------
# Epoch base parameterization
# ---------------------------------------------------------------------------


class TestEpochBase:
    def test_default_epoch_base_is_jan_2024(self) -> None:
        from datetime import UTC, datetime

        from slack_cached.fake_slack import DEFAULT_EPOCH_BASE

        assert DEFAULT_EPOCH_BASE == 1704067200.0
        dt = datetime.fromtimestamp(DEFAULT_EPOCH_BASE, tz=UTC)
        assert dt.year == 2024 and dt.month == 1 and dt.day == 1

    def test_custom_epoch_base_shifts_thread_timestamps(self) -> None:
        ws_old = Workspace(seed=42)
        ws_new = Workspace(seed=42, epoch_base=1750000000.0)
        old_keys = list(ws_old.threads.keys())
        new_keys = list(ws_new.threads.keys())
        assert len(old_keys) == len(new_keys)
        assert old_keys[0][0] == new_keys[0][0]
        assert float(new_keys[0][1]) - float(old_keys[0][1]) == pytest.approx(
            1750000000.0 - 1704067200.0, abs=1.0
        )

    def test_recent_epoch_base_produces_recent_messages(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(tz=UTC)
        ws = Workspace(seed=42, epoch_base=(now - timedelta(hours=1)).timestamp())
        for (_ch, ts), _messages in ws.threads.items():
            msg_dt = datetime.fromtimestamp(float(ts), tz=UTC)
            assert abs((now - msg_dt).total_seconds()) < 86400 * 7

    def test_parse_epoch_base_now(self) -> None:
        from datetime import UTC, datetime

        from slack_cached.fake_slack import _parse_epoch_base

        result = _parse_epoch_base("now")
        expected = datetime.now(tz=UTC).timestamp()
        assert abs(result - expected) < 2.0

    def test_parse_epoch_base_iso_date(self) -> None:
        from slack_cached.fake_slack import _parse_epoch_base

        result = _parse_epoch_base("2025-06-01")
        assert result == pytest.approx(1748736000.0)

    def test_parse_epoch_base_numeric(self) -> None:
        from slack_cached.fake_slack import _parse_epoch_base

        result = _parse_epoch_base("1750000000")
        assert result == 1750000000.0

    def test_parse_epoch_base_none_returns_default(self) -> None:
        from slack_cached.fake_slack import DEFAULT_EPOCH_BASE, _parse_epoch_base

        assert _parse_epoch_base(None) == DEFAULT_EPOCH_BASE

    def test_parse_epoch_base_invalid_raises(self) -> None:
        from slack_cached.fake_slack import _parse_epoch_base

        with pytest.raises(ValueError, match="cannot parse"):
            _parse_epoch_base("not-a-date")


# ---------------------------------------------------------------------------
# chat.postMessage
# ---------------------------------------------------------------------------


class TestPostMessage:
    def test_post_new_thread_via_api(self, fake_server: str) -> None:
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": "C0001", "text": "hello from post"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["channel"] == "C0001"
        assert data["message"]["text"] == "hello from post"
        assert data["message"]["thread_ts"] == data["ts"]

    def test_posted_thread_appears_in_history(self, fake_server: str) -> None:
        post = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": "C0001", "text": "new thread msg"},
            timeout=5,
        )
        ts = post.json()["ts"]
        history = _get(fake_server, "/api/conversations.history", {"channel": "C0001"})
        ts_set = {m["ts"] for m in history["messages"]}
        assert ts in ts_set

    def test_post_reply_via_api(self, fake_server: str) -> None:
        from slack_cached.fake_slack import FakeSlackHandler

        keys = list(FakeSlackHandler.workspace.threads.keys())
        ch, thread_ts = keys[0]
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": ch, "text": "a reply", "thread_ts": thread_ts},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["message"]["thread_ts"] == thread_ts
        assert data["message"]["parent_user_id"] is not None

    def test_post_reply_to_nonexistent_thread(self, fake_server: str) -> None:
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": "C0001", "text": "ghost", "thread_ts": "9999999999.000000"},
            timeout=5,
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "thread_not_found"

    def test_post_without_channel_returns_error(self, fake_server: str) -> None:
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"text": "no channel"},
            timeout=5,
        )
        assert resp.status_code == 400

    def test_post_without_text_returns_error(self, fake_server: str) -> None:
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": "C0001"},
            timeout=5,
        )
        assert resp.status_code == 400

    def test_post_message_with_custom_user(self, fake_server: str) -> None:
        resp = httpx.post(
            f"{fake_server}/api/chat.postMessage",
            data={"channel": "C0001", "text": "bot says hi", "user": "U0099"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["message"]["user"] == "U0099"

    def test_post_message_directly_on_workspace(self) -> None:
        ws = Workspace(seed=42)
        result = ws.post_message(channel="C0001", text="direct post")
        assert result["ok"] is True
        key = (result["channel"], result["ts"])
        assert key in ws.threads
        assert ws.threads[key][0]["text"] == "direct post"

    def test_post_reply_directly_on_workspace(self) -> None:
        ws = Workspace(seed=42)
        key = list(ws.threads.keys())[0]
        ch, thread_ts = key
        before = len(ws.threads[key])
        result = ws.post_message(channel=ch, text="direct reply", thread_ts=thread_ts)
        assert result["ok"] is True
        assert len(ws.threads[key]) == before + 1
