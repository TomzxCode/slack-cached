"""Tests for the cache fetch/load orchestration layer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from slack_cached.cache import (
    fetch_channel_messages,
    fetch_channels,
    fetch_search,
    fetch_thread,
    fetch_users,
    load_thread,
)
from slack_cached.storage import (
    connect,
    get_channel,
    get_thread_state,
    get_user,
    load_channels,
    load_thread_messages,
    load_users,
)
from slack_cached.urls import ThreadRef


class FakeClient:
    """In-memory stand-in for SlackClient that records calls."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = batches
        self.calls: list[dict[str, Any]] = []

    async def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(
            {"channel": channel, "thread_ts": thread_ts, "oldest": oldest, "limit": limit}
        )
        batch = self._batches.pop(0) if self._batches else []
        for msg in batch:
            yield msg


def _msg(ts: str, text: str = "hi", user: str = "U1") -> dict[str, Any]:
    return {"ts": ts, "text": text, "user": user}


def test_fetch_thread_initial_full_fetch(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    ref = ThreadRef(channel="C1", thread_ts="1700000000.000100")
    client = FakeClient(
        batches=[
            [
                _msg("1700000000.000100", text="root"),
                _msg("1700000000.000200", text="reply1"),
            ]
        ]
    )

    result = asyncio.run(fetch_thread(conn, client, ref))

    assert result.incremental is False
    assert result.fetched_messages == 2
    assert result.total_messages == 2
    assert client.calls[0]["oldest"] is None

    state = get_thread_state(conn, "C1", "1700000000.000100")
    assert state is not None
    assert state.latest_reply == "1700000000.000200"


def test_fetch_thread_incremental_uses_latest_reply(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    ref = ThreadRef(channel="C1", thread_ts="1700000000.000100")
    client = FakeClient(
        batches=[
            [_msg("1700000000.000100", text="root"), _msg("1700000000.000200", text="r1")],
            [_msg("1700000000.000200", text="r1"), _msg("1700000000.000300", text="r2")],
        ]
    )

    asyncio.run(fetch_thread(conn, client, ref))
    result = asyncio.run(fetch_thread(conn, client, ref))

    assert result.incremental is True
    assert client.calls[1]["oldest"] == "1700000000.000200"
    assert result.total_messages == 3

    messages = load_thread(conn, ref)
    assert [m.ts for m in messages] == [
        "1700000000.000100",
        "1700000000.000200",
        "1700000000.000300",
    ]


def test_fetch_thread_incremental_with_no_new_messages_preserves_state(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "cache.db")
    ref = ThreadRef(channel="C1", thread_ts="1700000000.000100")
    client = FakeClient(
        batches=[
            [_msg("1700000000.000100", text="root"), _msg("1700000000.000200", text="r1")],
            [],
        ]
    )

    asyncio.run(fetch_thread(conn, client, ref))
    result = asyncio.run(fetch_thread(conn, client, ref))

    assert result.incremental is True
    assert result.fetched_messages == 0
    assert result.total_messages == 2

    state = get_thread_state(conn, "C1", "1700000000.000100")
    assert state is not None
    assert state.latest_reply == "1700000000.000200"


def test_fetch_thread_updates_edited_message(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    ref = ThreadRef(channel="C1", thread_ts="1700000000.000100")
    client = FakeClient(
        batches=[
            [_msg("1700000000.000100", text="original")],
            [_msg("1700000000.000100", text="edited")],
        ]
    )

    asyncio.run(fetch_thread(conn, client, ref))
    asyncio.run(fetch_thread(conn, client, ref))

    messages = load_thread(conn, ref)
    assert len(messages) == 1
    assert messages[0].text == "edited"


class FakeListClient:
    """In-memory stand-in returning fixed user/channel lists."""

    def __init__(
        self,
        users: list[dict[str, Any]] | None = None,
        channels: list[dict[str, Any]] | None = None,
    ) -> None:
        self._users = users or []
        self._channels = channels or []

    async def iter_users(self, limit: int = 1000) -> AsyncIterator[dict[str, Any]]:
        for u in self._users:
            yield u

    async def iter_channels(
        self, types: str = "public_channel", limit: int = 1000
    ) -> AsyncIterator[dict[str, Any]]:
        for c in self._channels:
            yield c


class FakeChannelClient:
    """In-memory stand-in for channel message fetching."""

    def __init__(
        self,
        history: list[dict[str, Any]] | None = None,
        thread_replies: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._history = history or []
        self._thread_replies = thread_replies or {}
        self.calls: list[dict[str, Any]] = []

    async def iter_channel_history(
        self,
        channel: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(
            {"method": "history", "channel": channel, "oldest": oldest, "latest": latest}
        )
        for msg in self._history:
            yield msg

    async def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(
            {"method": "replies", "channel": channel, "thread_ts": thread_ts, "oldest": oldest}
        )
        for msg in self._thread_replies.get(thread_ts, []):
            yield msg


def test_fetch_users_caches_all(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeListClient(
        users=[
            {"id": "U1", "name": "alice"},
            {"id": "U2", "name": "bob"},
        ]
    )

    result = asyncio.run(fetch_users(conn, client))

    assert result.processed == 2
    assert result.added == 2
    assert result.total == 2
    assert [u.id for u in load_users(conn)] == ["U1", "U2"]
    user = get_user(conn, "U1")
    assert user is not None
    assert user.name == "alice"


def test_fetch_channels_caches_all(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeListClient(
        channels=[
            {"id": "C1", "name": "general", "is_private": False},
            {"id": "C2", "name": "secret", "is_private": True},
        ]
    )

    result = asyncio.run(fetch_channels(conn, client))

    assert result.processed == 2
    assert result.added == 2
    assert result.total == 2
    assert [c.id for c in load_channels(conn)] == ["C1", "C2"]
    channel = get_channel(conn, "C2")
    assert channel is not None
    assert channel.is_private is True


def test_fetch_users_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeListClient(users=[{"id": "U1", "name": "alice"}])

    first = asyncio.run(fetch_users(conn, client))
    assert first.added == 1

    result = asyncio.run(fetch_users(conn, client))
    assert result.processed == 1
    assert result.added == 0
    assert result.total == 1


def test_fetch_channel_messages_stores_top_level_only(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeChannelClient(
        history=[
            _msg("1700000000.000100", text="standalone"),
            {
                "ts": "1700000000.000200",
                "text": "thread parent",
                "user": "U1",
                "thread_ts": "1700000000.000200",
                "reply_count": 2,
            },
            _msg("1700000000.000300", text="another standalone"),
        ],
    )

    result = asyncio.run(fetch_channel_messages(conn, client, "C1"))

    assert result.fetched_messages == 3
    assert result.total_messages == 3
    assert result.threads_with_replies_fetched == 0
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "history"


def test_fetch_channel_messages_full_threads_fetches_replies(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeChannelClient(
        history=[
            _msg("1700000000.000100", text="standalone"),
            {
                "ts": "1700000000.000200",
                "text": "thread parent",
                "user": "U1",
                "thread_ts": "1700000000.000200",
                "reply_count": 1,
                "latest_reply": "1700000000.000400",
            },
        ],
        thread_replies={
            "1700000000.000200": [
                {
                    "ts": "1700000000.000200",
                    "text": "thread parent",
                    "user": "U1",
                    "thread_ts": "1700000000.000200",
                },
                _msg("1700000000.000400", text="reply1"),
            ],
        },
    )

    result = asyncio.run(fetch_channel_messages(conn, client, "C1", full_threads=True))

    # 3 distinct writes: standalone, parent, reply1. The parent appears in
    # both history and replies but is deduplicated by its canonical payload
    # (reply_count/latest_reply are contextual metadata, stripped before
    # comparison), so it is not double-counted.
    assert result.fetched_messages == 3
    assert result.total_messages == 3
    assert result.threads_with_replies_fetched == 1
    history_calls = [c for c in client.calls if c["method"] == "history"]
    replies_calls = [c for c in client.calls if c["method"] == "replies"]
    assert len(history_calls) == 1
    assert len(replies_calls) == 1
    assert replies_calls[0]["thread_ts"] == "1700000000.000200"


def test_fetch_channel_messages_standalone_messages_use_own_ts_as_thread_ts(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeChannelClient(
        history=[
            _msg("1700000000.000100", text="standalone"),
        ],
    )

    asyncio.run(fetch_channel_messages(conn, client, "C1"))

    state = get_thread_state(conn, "C1", "1700000000.000100")
    assert state is not None


# ---------------------------------------------------------------------------
# fetch_search
# ---------------------------------------------------------------------------


class FakeSearchClient:
    """In-memory stand-in returning fixed search matches and thread replies."""

    def __init__(
        self,
        matches: list[dict[str, Any]] | None = None,
        thread_replies: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._matches = matches or []
        self._thread_replies = thread_replies or {}
        self.search_calls: list[dict[str, Any]] = []
        self.replies_calls: list[dict[str, Any]] = []

    async def iter_search_messages(
        self,
        query: str,
        count: int = 20,
        sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> AsyncIterator[dict[str, Any]]:
        self.search_calls.append(
            {"query": query, "count": count, "sort": sort, "sort_dir": sort_dir}
        )
        for m in self._matches:
            yield m

    async def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        self.replies_calls.append({"channel": channel, "thread_ts": thread_ts, "oldest": oldest})
        for msg in self._thread_replies.get((channel, thread_ts), []):
            yield msg


def test_fetch_search_caches_matches_across_threads(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "hello world",
                "channel": "C1",
                "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
            },
            {
                "ts": "1700000000.000200",
                "user": "U2",
                "text": "hello there",
                "channel": "C2",
                "permalink": "https://acme.slack.com/archives/C2/p1700000000000200",
            },
        ]
    )

    result = asyncio.run(fetch_search(conn, client, query="hello"))

    assert len(result.matches) == 2
    assert result.threads_seen == 2
    assert result.threads_new == 2
    assert result.messages_seen == 2
    assert result.messages_new == 2
    assert client.search_calls[0]["query"] == "hello"

    # First match sits in a real thread, second is a standalone (no thread_ts).
    msgs_c1 = load_thread_messages(conn, "C1", "1700000000.000100")
    assert len(msgs_c1) == 1
    assert msgs_c1[0].text == "hello world"
    msgs_c2 = load_thread_messages(conn, "C2", "1700000000.000200")
    assert len(msgs_c2) == 1
    assert msgs_c2[0].text == "hello there"

    state = get_thread_state(conn, "C2", "1700000000.000200")
    assert state is not None


def test_fetch_search_normalizes_object_channel(tmp_path: Path) -> None:
    # Real ``search.messages`` returns ``channel`` as an object, not a bare id.
    conn = connect(tmp_path / "cache.db")
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "hello world",
                "channel": {"id": "C1", "name": "general"},
                "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
            },
        ]
    )

    result = asyncio.run(fetch_search(conn, client, query="hello"))

    assert result.threads_seen == 1
    assert result.threads_new == 1
    assert result.messages_seen == 1
    assert result.messages_new == 1
    # The match's channel is normalised in place to the bare id.
    assert result.matches[0]["channel"] == "C1"
    msgs = load_thread_messages(conn, "C1", "1700000000.000100")
    assert len(msgs) == 1
    assert msgs[0].text == "hello world"


def test_fetch_search_full_threads_expands_replies(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "parent",
                "channel": "C1",
            },
        ],
        thread_replies={
            ("C1", "1700000000.000100"): [
                {
                    "ts": "1700000000.000100",
                    "thread_ts": "1700000000.000100",
                    "user": "U1",
                    "text": "parent",
                },
                {"ts": "1700000000.000300", "user": "U2", "text": "reply"},
            ],
        },
    )

    result = asyncio.run(fetch_search(conn, client, query="parent", full_threads=True))

    assert len(result.matches) == 1
    assert result.threads_seen == 1
    assert result.threads_new == 1
    # In --full-threads mode the match itself is not cached directly; the
    # parent + reply arrive via conversations.replies, both new.
    assert result.messages_seen == 2
    assert result.messages_new == 2
    assert len(client.replies_calls) == 1
    assert client.replies_calls[0]["thread_ts"] == "1700000000.000100"

    msgs = load_thread_messages(conn, "C1", "1700000000.000100")
    assert [m.ts for m in msgs] == ["1700000000.000100", "1700000000.000300"]


def test_fetch_search_no_matches_still_returns_empty(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeSearchClient(matches=[])

    result = asyncio.run(fetch_search(conn, client, query="nothing"))

    assert result.matches == []
    assert result.threads_seen == 0
    assert result.threads_new == 0
    assert result.messages_seen == 0
    assert result.messages_new == 0


def test_fetch_search_second_run_with_no_changes_reports_zero(tmp_path: Path) -> None:
    """Re-running the same search reports 0 threads and 0 messages cached."""
    conn = connect(tmp_path / "cache.db")
    matches = [
        {
            "ts": "1700000000.000100",
            "thread_ts": "1700000000.000100",
            "user": "U1",
            "text": "hello world",
            "channel": "C1",
            "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
        },
        {
            "ts": "1700000000.000200",
            "user": "U2",
            "text": "hello there",
            "channel": "C2",
            "permalink": "https://acme.slack.com/archives/C2/p1700000000000200",
        },
    ]
    client = FakeSearchClient(matches=matches)

    first = asyncio.run(fetch_search(conn, client, query="hello"))
    assert first.threads_seen == 2
    assert first.threads_new == 2
    assert first.messages_seen == 2
    assert first.messages_new == 2

    # Second run sees identical payloads already in cache.
    client = FakeSearchClient(matches=matches)
    second = asyncio.run(fetch_search(conn, client, query="hello"))
    assert len(second.matches) == 2
    assert second.threads_seen == 2
    assert second.threads_new == 0
    assert second.messages_seen == 2
    assert second.messages_new == 0


def test_fetch_search_second_run_with_edited_message_reports_change(
    tmp_path: Path,
) -> None:
    """A second run reports only the thread/message whose payload changed."""
    conn = connect(tmp_path / "cache.db")

    original = [
        {
            "ts": "1700000000.000100",
            "thread_ts": "1700000000.000100",
            "user": "U1",
            "text": "hello world",
            "channel": "C1",
        },
        {
            "ts": "1700000000.000200",
            "user": "U2",
            "text": "hello there",
            "channel": "C2",
        },
    ]
    client = FakeSearchClient(matches=original)
    asyncio.run(fetch_search(conn, client, query="hello"))

    edited = [
        {**original[0], "text": "hello world (edited)"},
        original[1],
    ]
    client = FakeSearchClient(matches=edited)
    result = asyncio.run(fetch_search(conn, client, query="hello"))

    assert result.threads_seen == 2
    assert result.threads_new == 1
    assert result.messages_seen == 2
    assert result.messages_new == 1
    msgs = load_thread_messages(conn, "C1", "1700000000.000100")
    assert msgs[0].text == "hello world (edited)"


def test_fetch_search_full_threads_second_run_reports_zero(tmp_path: Path) -> None:
    """With --full-threads, a second unchanged run reports 0 cached."""
    conn = connect(tmp_path / "cache.db")
    match = {
        "ts": "1700000000.000100",
        "thread_ts": "1700000000.000100",
        "user": "U1",
        "text": "parent",
        "channel": "C1",
    }
    replies = {
        ("C1", "1700000000.000100"): [
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "parent",
            },
            {"ts": "1700000000.000300", "user": "U2", "text": "reply"},
        ],
    }

    first = asyncio.run(
        fetch_search(
            conn,
            FakeSearchClient(matches=[match], thread_replies=replies),
            query="parent",
            full_threads=True,
        )
    )
    assert first.threads_seen == 1
    assert first.threads_new == 1
    # Match is not cached directly in --full-threads mode; both messages
    # arrive via replies, both new on the first run.
    assert first.messages_seen == 2
    assert first.messages_new == 2

    second = asyncio.run(
        fetch_search(
            conn,
            FakeSearchClient(matches=[match], thread_replies=replies),
            query="parent",
            full_threads=True,
        )
    )
    assert second.threads_seen == 1
    assert second.threads_new == 0
    assert second.messages_seen == 2
    assert second.messages_new == 0


def test_fetch_search_full_threads_handles_endpoint_payload_drift(
    tmp_path: Path,
) -> None:
    """Real Slack decorates the same message differently in search.messages
    vs conversations.replies (different ``blocks``, signed URLs in
    ``attachments``, ``team`` metadata, etc.). The second run must still
    recognize every message as a cache hit so the user sees
    ``0 message(s) cached`` rather than oscillating forever.
    """
    conn = connect(tmp_path / "cache.db")

    # The same message as it appears through search.messages.
    match = {
        "ts": "1700000000.000100",
        "thread_ts": "1700000000.000100",
        "user": "U1",
        "text": "lol",
        "channel": "C1",
        "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
        "blocks": [{"type": "rich_text", "block_id": "search1", "elements": []}],
        "attachments": [{"image_url": "https://img.example.com/a.png?sig=search"}],
        "team": "T0",
    }
    # The same message as it appears through conversations.replies (plus a
    # reply). Note the different ``block_id``, differently-signed URL, and
    # thread-metadata fields that only replies carries.
    parent_via_replies = {
        "ts": "1700000000.000100",
        "thread_ts": "1700000000.000100",
        "user": "U1",
        "text": "lol",
        "blocks": [{"type": "rich_text", "block_id": "replies1", "elements": []}],
        "attachments": [{"image_url": "https://img.example.com/a.png?sig=replies"}],
        "source_team": "T0",
        "user_team": "T0",
        "reply_count": 1,
        "latest_reply": "1700000000.000300",
    }
    reply = {"ts": "1700000000.000300", "user": "U2", "text": "haha"}
    replies = {("C1", "1700000000.000100"): [parent_via_replies, reply]}

    first = asyncio.run(
        fetch_search(
            conn,
            FakeSearchClient(matches=[match], thread_replies=replies),
            query="lol",
            full_threads=True,
        )
    )
    # Initial run: matches aren't cached directly in --full-threads mode; both
    # messages (parent + reply) arrive via replies and are new.
    assert first.threads_seen == 1
    assert first.threads_new == 1
    assert first.messages_seen == 2
    assert first.messages_new == 2

    second = asyncio.run(
        fetch_search(
            conn,
            FakeSearchClient(matches=[match], thread_replies=replies),
            query="lol",
            full_threads=True,
        )
    )
    # The fix: skipping match-caching in --full-threads mode plus a stable
    # whitelist means re-running reports cache hits for every reply.
    assert second.threads_seen == 1
    assert second.threads_new == 0
    assert second.messages_seen == 2
    assert second.messages_new == 0
