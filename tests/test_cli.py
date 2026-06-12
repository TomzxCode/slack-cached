"""Tests for the CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slack_cached import cache as cache_module
from slack_cached import cli
from slack_cached.cache import FetchResult


class StubClient:
    pass


def _populate_single_message(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Cache a single-message thread via a stub client."""

    class FakeClient:
        def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

    monkeypatch.setattr(cli, "_build_client", lambda _: FakeClient())
    monkeypatch.setattr(cache_module, "SlackClient", FakeClient, raising=False)

    rc = cli.main(
        [
            "fetch",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0


def test_show_prints_human_readable_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show defaults to human-readable output without hitting Slack."""
    db_path = tmp_path / "cache.db"
    _populate_single_message(monkeypatch, db_path)

    # --no-fetch ensures we don't call the client again.
    rc = cli.main(
        [
            "show",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
            "--no-fetch",
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    # Should be human-readable text, not JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "C0123ABCDEF/1700000000.000100" in out
    assert "1 message(s)" in out
    assert "U1" in out
    assert "hello" in out


def test_show_renders_user_name_when_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show resolves a message's user id to a cached display name."""
    db_path = tmp_path / "cache.db"
    _populate_single_message(monkeypatch, db_path)

    # Cache a user whose id matches the message author (U1).
    class FakeUsers:
        def iter_users(self, limit: int = 1000):
            yield {
                "id": "U1",
                "name": "alice",
                "real_name": "Alice Smith",
            }

    monkeypatch.setattr(cli, "_build_client", lambda _: FakeUsers())
    assert cli.main(["fetch-users", "--db", str(db_path)]) == 0

    rc = cli.main(
        [
            "show",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
            "--no-fetch",
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    # The author is shown as "Real name (handle)", not the raw user id.
    assert "] Alice Smith (alice)" in out
    assert "hello" in out


def test_show_prints_json_with_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show emits JSON when --json is passed."""
    db_path = tmp_path / "cache.db"
    _populate_single_message(monkeypatch, db_path)

    rc = cli.main(
        [
            "show",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
            "--no-fetch",
            "--json",
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["channel"] == "C0123ABCDEF"
    assert payload["thread_ts"] == "1700000000.000100"
    assert payload["message_count"] == 1
    assert payload["messages"][0]["text"] == "hello"


def test_fetch_with_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    calls: list[str | None] = []

    class FakeClient:
        def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            calls.append(oldest)
            yield {"ts": thread_ts, "user": "U1", "text": "root"}

    monkeypatch.setattr(cli, "_build_client", lambda _: FakeClient())

    rc = cli.main(
        [
            "fetch",
            "https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0
    assert calls == [None]


def test_resolve_ref_requires_url_or_channel_ts() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["fetch"])
    with pytest.raises(SystemExit):
        cli._resolve_ref(args)


def test_fetch_result_dataclass_fields() -> None:
    result = FetchResult(
        channel="C1", thread_ts="1.000", fetched_messages=1, total_messages=2, incremental=True
    )
    assert result.incremental is True
    assert result.total_messages == 2


class FakeListClient:
    """Stub client returning fixed user/channel lists."""

    def iter_users(self, limit: int = 1000):
        yield {"id": "U1", "name": "alice", "real_name": "Alice Smith"}

    def iter_channels(self, types: str = "public_channel", limit: int = 1000):
        yield {"id": "C1", "name": "general", "is_private": False}


def test_fetch_users_then_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["fetch-users", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show-users", "--db", str(db_path), "--no-fetch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 user(s)" in out
    assert "U1" in out
    assert "alice" in out
    assert "Alice Smith" in out


def test_show_users_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-users", "--db", str(db_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_count"] == 1
    assert payload["users"][0]["id"] == "U1"
    assert payload["users"][0]["name"] == "alice"


def test_fetch_channels_then_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show-channels", "--db", str(db_path), "--no-fetch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 channel(s)" in out
    assert "C1" in out
    assert "general" in out
    assert "public" in out


def test_show_channels_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-channels", "--db", str(db_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel_count"] == 1
    assert payload["channels"][0]["id"] == "C1"
    assert payload["channels"][0]["is_private"] is False


def test_show_users_no_fetch_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "cache.db"
    rc = cli.main(["show-users", "--db", str(db_path), "--no-fetch"])
    assert rc == 0
    assert "0 user(s)" in capsys.readouterr().out


class FakeChannelClient:
    """Stub client for channel message fetching."""

    def __init__(self, messages=None, thread_replies=None):
        self._messages = messages or []
        self._thread_replies = thread_replies or {}

    def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
        yield from self._messages

    def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        yield from self._thread_replies.get(thread_ts, [])


def test_fetch_channel_messages_basic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
        ]
    )
    monkeypatch.setattr(cli, "_build_client", lambda args: client)

    rc = cli.main(["fetch", "--channel", "C1", "--db", str(db_path)])
    assert rc == 0


def test_fetch_channel_requires_url_or_channel_ts(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with pytest.raises(SystemExit):
        cli.main(["fetch", "--db", str(db_path)])


def test_fetch_channel_messages_full_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {
                "ts": "1700000000.000100",
                "user": "U1",
                "text": "parent",
                "thread_ts": "1700000000.000100",
                "reply_count": 1,
                "latest_reply": "1700000000.000200",
            },
        ],
        thread_replies={
            "1700000000.000100": [
                {
                    "ts": "1700000000.000100",
                    "user": "U1",
                    "text": "parent",
                    "thread_ts": "1700000000.000100",
                },
                {"ts": "1700000000.000200", "user": "U2", "text": "reply"},
            ],
        },
    )
    monkeypatch.setattr(cli, "_build_client", lambda args: client)

    rc = cli.main(
        [
            "fetch",
            "--channel",
            "C1",
            "--full-threads",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0


class FakeChannelClientWithHistory:
    """Stub client that records oldest passed to iter_channel_history."""

    def __init__(self, messages=None):
        self._messages = messages or []
        self.oldest_seen: str | None = "unset"

    def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
        self.oldest_seen = oldest
        yield from self._messages

    def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        return iter([])


def test_fetch_channel_default_last_is_one_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClientWithHistory(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli, "_build_client", lambda args: client)

    rc = cli.main(["fetch", "--channel", "C1", "--db", str(db_path)])
    assert rc == 0
    assert client.oldest_seen is not None


def test_fetch_channel_last_zero_fetches_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClientWithHistory(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli, "_build_client", lambda args: client)

    rc = cli.main(["fetch", "--channel", "C1", "--last", "all", "--db", str(db_path)])
    assert rc == 0
    assert client.oldest_seen is None


def test_parse_duration() -> None:
    from datetime import timedelta

    assert cli._parse_duration("24h") == timedelta(hours=24)
    assert cli._parse_duration("1d") == timedelta(days=1)
    assert cli._parse_duration("2d5h30m") == timedelta(days=2, hours=5, minutes=30)
    assert cli._parse_duration("90m") == timedelta(minutes=90)
    assert cli._parse_duration("5h23m13s") == timedelta(hours=5, minutes=23, seconds=13)
    assert cli._parse_duration("all") is None
    assert cli._parse_duration("ALL") is None

    with pytest.raises(ValueError, match="invalid duration"):
        cli._parse_duration("abc")
    with pytest.raises(ValueError, match="invalid duration"):
        cli._parse_duration("")
