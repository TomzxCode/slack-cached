"""Tests for the SQLite storage layer."""

from __future__ import annotations

from pathlib import Path

import structlog

from slack_cached.storage import (
    connect,
    count_channels,
    count_messages,
    count_users,
    get_channel,
    get_thread_state,
    get_user,
    load_channels,
    load_thread_messages,
    load_user_display_names,
    load_users,
    record_thread_refresh,
    upsert_channels,
    upsert_messages,
    upsert_users,
)


def test_upsert_and_load_messages(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    messages = [
        {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
        {"ts": "1700000000.000200", "user": "U2", "text": "world"},
    ]
    record_thread_refresh(conn, "C1", "1700000000.000100", "1700000000.000200")
    written = upsert_messages(conn, "C1", "1700000000.000100", messages)
    conn.commit()

    assert written == 2
    assert count_messages(conn, "C1", "1700000000.000100") == 2

    loaded = load_thread_messages(conn, "C1", "1700000000.000100")
    assert [m.ts for m in loaded] == ["1700000000.000100", "1700000000.000200"]
    assert loaded[0].user == "U1"
    assert loaded[1].text == "world"
    assert loaded[0].payload["text"] == "hello"


def test_upsert_replaces_existing_message(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    record_thread_refresh(conn, "C1", "1700000000.000100", "1700000000.000100")
    upsert_messages(
        conn,
        "C1",
        "1700000000.000100",
        [{"ts": "1700000000.000100", "user": "U1", "text": "original"}],
    )
    upsert_messages(
        conn,
        "C1",
        "1700000000.000100",
        [{"ts": "1700000000.000100", "user": "U1", "text": "edited"}],
    )
    conn.commit()

    loaded = load_thread_messages(conn, "C1", "1700000000.000100")
    assert len(loaded) == 1
    assert loaded[0].text == "edited"


def test_thread_state_missing_returns_none(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    assert get_thread_state(conn, "C1", "1700000000.000100") is None


def test_record_thread_refresh_updates_latest_reply(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    record_thread_refresh(conn, "C1", "1700000000.000100", "1700000000.000100", now=1.0)
    record_thread_refresh(conn, "C1", "1700000000.000100", "1700000000.000900", now=2.0)
    conn.commit()

    state = get_thread_state(conn, "C1", "1700000000.000100")
    assert state is not None
    assert state.latest_reply == "1700000000.000900"
    assert state.last_fetched == 2.0


def test_upsert_and_load_users(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    users = [
        {"id": "U2", "name": "bob", "profile": {"real_name": "Bob Jones"}},
        {"id": "U1", "name": "alice", "real_name": "Alice Smith"},
    ]
    written = upsert_users(conn, users, now=1.0)
    conn.commit()

    assert written == 2
    assert count_users(conn) == 2

    loaded = load_users(conn)
    # Ordered by id.
    assert [u.id for u in loaded] == ["U1", "U2"]
    assert loaded[0].name == "alice"
    assert loaded[0].real_name == "Alice Smith"
    assert loaded[0].fetched_at == 1.0
    assert loaded[0].payload["name"] == "alice"
    # real_name falls back to the profile when absent at the top level.
    assert loaded[1].real_name == "Bob Jones"


def test_get_user_missing_returns_none(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    assert get_user(conn, "U1") is None


def test_load_user_display_names_resolves_only_requested(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    upsert_users(
        conn,
        [
            {"id": "U1", "name": "alice", "real_name": "Alice Smith"},
            {"id": "U2", "name": "bob", "real_name": "Bob Jones"},
            {"id": "U3", "name": "carol"},
        ],
    )
    conn.commit()

    names = load_user_display_names(conn, ["U1", "U2"])
    # Only the requested ids are resolved.
    assert names == {"U1": "Alice Smith (alice)", "U2": "Bob Jones (bob)"}


def test_load_user_display_names_formatting(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    upsert_users(
        conn,
        [
            {"id": "U1", "name": "handle", "real_name": "Real Name"},
            {"id": "U2", "name": "only-handle"},
            {"id": "U3"},
        ],
    )
    conn.commit()

    names = load_user_display_names(conn, ["U1", "U2", "U3"])
    assert names == {
        "U1": "Real Name (handle)",  # "Real name (handle)" when both are known
        "U2": "only-handle",  # falls back to the handle alone
        "U3": "U3",  # falls back to the id when nothing else is available
    }


def test_load_user_display_names_empty_input(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    assert load_user_display_names(conn, []) == {}


def test_load_user_display_names_dedupes_ids(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    upsert_users(conn, [{"id": "U1", "name": "alice"}])
    conn.commit()
    assert load_user_display_names(conn, ["U1", "U1", "U1"]) == {"U1": "alice"}


def test_upsert_users_replaces_existing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    upsert_users(conn, [{"id": "U1", "name": "alice"}], now=1.0)
    upsert_users(conn, [{"id": "U1", "name": "alice-renamed"}], now=2.0)
    conn.commit()

    user = get_user(conn, "U1")
    assert user is not None
    assert user.name == "alice-renamed"
    assert user.fetched_at == 2.0
    assert count_users(conn) == 1


def test_upsert_and_load_channels(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    channels = [
        {"id": "C2", "name": "random", "is_private": False},
        {"id": "C1", "name": "general", "is_private": True},
        {"id": "C3", "name": "no-flag"},
    ]
    written = upsert_channels(conn, channels, now=5.0)
    conn.commit()

    assert written == 3
    assert count_channels(conn) == 3

    loaded = load_channels(conn)
    assert [c.id for c in loaded] == ["C1", "C2", "C3"]
    assert loaded[0].name == "general"
    assert loaded[0].is_private is True
    assert loaded[1].is_private is False
    # Missing is_private stays None.
    assert loaded[2].is_private is None
    assert loaded[0].fetched_at == 5.0
    assert loaded[0].payload["name"] == "general"


def test_get_channel_missing_returns_none(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    assert get_channel(conn, "C1") is None


def test_upsert_channels_replaces_existing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    upsert_channels(conn, [{"id": "C1", "name": "general", "is_private": False}], now=1.0)
    upsert_channels(conn, [{"id": "C1", "name": "general-v2", "is_private": True}], now=2.0)
    conn.commit()

    channel = get_channel(conn, "C1")
    assert channel is not None
    assert channel.name == "general-v2"
    assert channel.is_private is True
    assert channel.fetched_at == 2.0
    assert count_channels(conn) == 1


def test_upsert_empty_lists_return_zero(tmp_path: Path) -> None:
    conn = connect(tmp_path / "cache.db")
    assert upsert_users(conn, []) == 0


def test_sql_statements_are_logged(tmp_path: Path) -> None:
    cap = structlog.testing.LogCapture()
    structlog.reset_defaults()
    structlog.configure(processors=[cap], cache_logger_on_first_use=False)
    try:
        conn = connect(tmp_path / "cache.db")
        record_thread_refresh(conn, "C1", "1700000000.000100", None)
        conn.commit()
    finally:
        structlog.reset_defaults()

    sql_events = [e for e in cap.entries if e["event"] == "sql"]
    statements = [e["statement"] for e in sql_events]
    assert any("INSERT INTO threads" in s for s in statements)
    assert all(e["log_level"] == "debug" for e in sql_events)
    # Each completed query reports its duration in milliseconds.
    assert all(isinstance(e["duration_ms"], float) for e in sql_events)
    assert all(e["duration_ms"] >= 0 for e in sql_events)
