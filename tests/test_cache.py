"""Tests for the cache fetch/load orchestration layer."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from slack_cached.cache import fetch_channels, fetch_thread, fetch_users, load_thread
from slack_cached.storage import (
    connect,
    get_channel,
    get_thread_state,
    get_user,
    load_channels,
    load_users,
)
from slack_cached.urls import ThreadRef


class FakeClient:
    """In-memory stand-in for SlackClient that records calls."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = batches
        self.calls: list[dict[str, Any]] = []

    def iter_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(
            {"channel": channel, "thread_ts": thread_ts, "oldest": oldest, "limit": limit}
        )
        batch = self._batches.pop(0) if self._batches else []
        yield from batch


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

    result = fetch_thread(conn, client, ref)

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

    fetch_thread(conn, client, ref)
    result = fetch_thread(conn, client, ref)

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

    fetch_thread(conn, client, ref)
    result = fetch_thread(conn, client, ref)

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

    fetch_thread(conn, client, ref)
    fetch_thread(conn, client, ref)

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

    def iter_users(self, limit: int = 1000) -> Iterator[dict[str, Any]]:
        yield from self._users

    def iter_channels(
        self, types: str = "public_channel", limit: int = 1000
    ) -> Iterator[dict[str, Any]]:
        yield from self._channels


def test_fetch_users_caches_all(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    client = FakeListClient(
        users=[
            {"id": "U1", "name": "alice"},
            {"id": "U2", "name": "bob"},
        ]
    )

    result = fetch_users(conn, client)

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

    result = fetch_channels(conn, client)

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

    first = fetch_users(conn, client)
    assert first.added == 1

    result = fetch_users(conn, client)
    # The second pass re-processes the same record but adds nothing new.
    assert result.processed == 1
    assert result.added == 0
    assert result.total == 1
