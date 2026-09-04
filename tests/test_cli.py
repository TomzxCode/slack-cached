"""Tests for the CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slack_cached import cli
from slack_cached.cache import FetchResult
from slack_cached.config import Credentials as Creds


class StubClient:
    pass


def _populate_single_message(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Cache a single-message thread via a stub client."""

    class FakeClient:
        async def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeClient())

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
        async def iter_users(self, limit: int = 1000):
            yield {
                "id": "U1",
                "name": "alice",
                "real_name": "Alice Smith",
            }

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeUsers())
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


def test_show_prints_jsonl_with_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show emits the whole thread as a single JSON line with --jsonl."""
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
            "--jsonl",
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    # Output is exactly one JSON document on a single line (plus trailing newline).
    lines = out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["channel"] == "C0123ABCDEF"
    assert payload["thread_ts"] == "1700000000.000100"
    assert payload["message_count"] == 1
    assert payload["messages"][0]["text"] == "hello"


def test_show_json_and_jsonl_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json and --jsonl cannot be combined."""
    with pytest.raises(SystemExit):
        cli.main(
            [
                "show",
                "--channel",
                "C1",
                "--ts",
                "1.0",
                "--db",
                str(tmp_path / "cache.db"),
                "--json",
                "--jsonl",
            ]
        )
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_fetch_with_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    calls: list[str | None] = []

    class FakeClient:
        async def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            calls.append(oldest)
            yield {"ts": thread_ts, "user": "U1", "text": "root"}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeClient())

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
    with pytest.raises(SystemExit):
        cli._internal._refs._resolve_ref(None, None, None)


def test_fetch_result_dataclass_fields() -> None:
    result = FetchResult(
        channel="C1", thread_ts="1.000", fetched_messages=1, total_messages=2, incremental=True
    )
    assert result.incremental is True
    assert result.total_messages == 2


class FakeListClient:
    """Stub client returning fixed user/channel lists."""

    async def iter_users(self, limit: int = 1000):
        yield {"id": "U1", "name": "alice", "real_name": "Alice Smith"}

    async def iter_channels(self, types: str = "public_channel", limit: int = 1000):
        yield {"id": "C1", "name": "general", "is_private": False}

    async def aclose(self) -> None:
        pass


def test_fetch_users_then_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

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
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-users", "--db", str(db_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_count"] == 1
    assert payload["users"][0]["id"] == "U1"
    assert payload["users"][0]["name"] == "alice"


def test_show_users_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show-users --jsonl emits a single compact JSON line."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-users", "--db", str(db_path), "--jsonl"])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["user_count"] == 1
    assert payload["users"][0]["id"] == "U1"
    assert payload["users"][0]["name"] == "alice"


def test_fetch_channels_then_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

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
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-channels", "--db", str(db_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel_count"] == 1
    assert payload["channels"][0]["id"] == "C1"
    assert payload["channels"][0]["is_private"] is False


def test_show_channels_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show-channels --jsonl emits a single compact JSON line."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeListClient())

    rc = cli.main(["show-channels", "--db", str(db_path), "--jsonl"])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
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

    async def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
        for m in self._messages:
            yield m

    async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        for m in self._thread_replies.get(thread_ts, []):
            yield m

    async def iter_channels(self, types="public_channel", limit=1000):
        yield {"id": "C1", "name": "general", "is_private": False}

    async def aclose(self) -> None:
        pass


def test_fetch_channel_messages_basic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

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
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

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

    async def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
        self.oldest_seen = oldest
        for m in self._messages:
            yield m

    async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        return
        yield  # pragma: no cover - make this an async generator

    async def aclose(self) -> None:
        pass


def test_fetch_channel_default_last_is_one_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClientWithHistory(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

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
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["fetch", "--channel", "C1", "--last", "all", "--db", str(db_path)])
    assert rc == 0
    assert client.oldest_seen is None


def test_parse_duration() -> None:
    from datetime import timedelta

    parse = cli._internal._duration._parse_duration
    assert parse("24h") == timedelta(hours=24)
    assert parse("1d") == timedelta(days=1)
    assert parse("2d5h30m") == timedelta(days=2, hours=5, minutes=30)
    assert parse("90m") == timedelta(minutes=90)
    assert parse("5h23m13s") == timedelta(hours=5, minutes=23, seconds=13)
    assert parse("all") is None
    assert parse("ALL") is None

    with pytest.raises(ValueError, match="invalid duration"):
        parse("abc")
    with pytest.raises(ValueError, match="invalid duration"):
        parse("")


def test_show_channel_without_ts_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
            {"ts": "1700000000.000200", "user": "U2", "text": "world"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["show", "--channel", "C1", "--db", str(db_path)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Channel C1" in out
    assert "2 message(s)" in out
    assert "U1" in out
    assert "hello" in out
    assert "U2" in out
    assert "world" in out


def test_show_channel_without_ts_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["show", "--channel", "C1", "--db", str(db_path), "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["channel"] == "C1"
    assert payload["message_count"] == 1
    assert payload["messages"][0]["text"] == "hello"


def test_show_channel_without_ts_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`show --channel --jsonl` emits a single compact JSON line."""
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
            {"ts": "1700000000.000200", "user": "U2", "text": "world"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["show", "--channel", "C1", "--db", str(db_path), "--jsonl"])
    assert rc == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["channel"] == "C1"
    assert payload["message_count"] == 2
    assert payload["messages"][0]["text"] == "hello"
    assert payload["messages"][1]["text"] == "world"


def test_show_channel_without_ts_uses_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "cached_msg"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["fetch", "--channel", "C1", "--db", str(db_path)])
    assert rc == 0

    class NoCallClient:
        async def iter_channel_history(self, *a, **kw):
            raise AssertionError("should not fetch")

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: NoCallClient())

    rc = cli.main(["show", "--channel", "C1", "--db", str(db_path), "--no-fetch"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "cached_msg" in out


def test_show_channel_with_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[
            {"ts": "1700000000.000100", "user": "U1", "text": "hello"},
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["fetch", "--channel", "C1", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show", "--channel", "C1", "--db", str(db_path), "--no-fetch"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "general" in out


class DirectChannelClient(FakeChannelClient):
    """Stub client whose only channel is a direct message with user U9."""

    async def iter_channels(self, types="public_channel", limit=1000):
        # Like real Slack, IM conversations carry no name, only the peer.
        yield {"id": "D1", "is_im": True, "user": "U9"}

    async def iter_users(self, limit: int = 1000):
        yield {"id": "U9", "name": "tomzx", "real_name": "Tom Rochette"}


def test_show_direct_channel_resolves_peer_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A direct channel shows its peer user's name, not the raw D id."""
    db_path = tmp_path / "cache.db"
    client = DirectChannelClient(
        messages=[{"ts": "1700000000.000100", "user": "U9", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: client)

    rc = cli.main(["fetch-users", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch", "--channel", "D1", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show", "--channel", "D1", "--db", str(db_path), "--no-fetch"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Channel Tom Rochette (tomzx)" in out
    assert "Channel D1" not in out


def test_show_direct_channel_json_resolves_peer_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "cache.db"
    client = DirectChannelClient(
        messages=[{"ts": "1700000000.000100", "user": "U9", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: client)

    rc = cli.main(["fetch-users", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch", "--channel", "D1", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show", "--channel", "D1", "--db", str(db_path), "--no-fetch", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["channel"] == "D1"
    assert payload["channel_name"] == "Tom Rochette (tomzx)"


def test_show_channels_labels_direct_channels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show-channels renders direct channels with their peer's name."""
    db_path = tmp_path / "cache.db"
    client = DirectChannelClient()
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: client)

    rc = cli.main(["fetch-users", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0

    rc = cli.main(["show-channels", "--db", str(db_path), "--no-fetch"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "D1  Tom Rochette (tomzx) (direct)" in out


def test_show_channel_resolves_bare_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show --channel <name> resolves the name to an id via the cached channels."""
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()  # clear seeding output

    rc = cli.main(["show", "--channel", "general", "--db", str(db_path)])
    assert rc == 0

    out = capsys.readouterr().out
    # 'hello' is the message that FakeChannelClient serves for C1; reaching it
    # proves the bare name was resolved to C1.
    assert "hello" in out
    assert "1 message(s)" in out


def test_show_channel_resolves_hash_prefixed_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show --channel #<name> resolves the '#'-prefixed name to an id."""
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()

    rc = cli.main(["show", "--channel", "#general", "--db", str(db_path)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "hello" in out
    assert "1 message(s)" in out


def test_fetch_channel_resolves_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetch --channel <name> resolves the name and fetches that channel."""
    db_path = tmp_path / "cache.db"
    client = FakeChannelClient(
        messages=[{"ts": "1700000000.000100", "user": "U1", "text": "hello"}]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()

    rc = cli.main(["fetch", "--channel", "general", "--db", str(db_path)])
    assert rc == 0

    err = capsys.readouterr().err
    # The summary reports the resolved channel id, not the input name.
    assert "for C1" in err


def test_fetch_thread_resolves_channel_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetch --channel <name> --ts <ts> resolves the name before fetching the thread."""

    class FakeThreadClient:
        async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
            # The resolver must have turned '#general' into the id 'C1'.
            assert channel == "C1", f"expected resolved id C1, got {channel!r}"
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

        async def iter_channels(self, types="public_channel", limit=1000):
            yield {"id": "C1", "name": "general", "is_private": False}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: FakeThreadClient())

    db_path = tmp_path / "cache.db"
    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()

    rc = cli.main(
        [
            "fetch",
            "--channel",
            "#general",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0


def test_show_channel_unresolved_name_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show --channel <unknown> errors out and returns 1."""

    class EmptyClient:
        async def iter_channels(self, types="public_channel", limit=1000):
            return
            yield  # pragma: no cover - make this an async generator

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: EmptyClient())

    db_path = tmp_path / "cache.db"
    rc = cli.main(["show", "--channel", "#nope", "--db", str(db_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not resolve" in err
    assert "nope" in err


class FakeSearchClient:
    """Stub client returning fixed search matches."""

    def __init__(self, matches=None, thread_replies=None):
        self._matches = matches or []
        self._thread_replies = thread_replies or {}
        self.search_calls = []

    async def iter_search_messages(
        self,
        query,
        count=20,
        sort="timestamp",
        sort_dir="desc",
    ):
        self.search_calls.append(
            {"query": query, "count": count, "sort": sort, "sort_dir": sort_dir}
        )
        for m in self._matches:
            yield m

    async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        for m in self._thread_replies.get((channel, thread_ts), []):
            yield m

    async def aclose(self) -> None:
        pass


def test_search_human_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search prints human-readable matches and caches them."""
    db_path = tmp_path / "cache.db"
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
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["search", "hello", "--db", str(db_path)])
    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "Search: hello" in out
    assert "1 match(es)" in out
    assert "C1" in out
    assert "hello world" in out
    assert "acme.slack.com" in out

    err = captured.err
    assert "1 match(es)" in err
    assert "1 thread(s) (0 existing, 1 new)" in err
    assert "1 message(s) (0 existing, 1 new)" in err


def test_search_direct_channel_hit_shows_peer_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A search hit in a direct channel is labelled with the peer's name."""
    db_path = tmp_path / "cache.db"

    class DirectSearchClient(FakeSearchClient):
        async def iter_channels(self, types="public_channel", limit=1000):
            yield {"id": "D1", "is_im": True, "user": "U1"}

        async def iter_users(self, limit: int = 1000):
            yield {"id": "U1", "name": "tomzx", "real_name": "Tom Rochette"}

    client = DirectSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "hello from the dm",
                "channel": "D1",
                "permalink": "https://acme.slack.com/archives/D1/p1700000000000100",
            },
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["fetch-channels", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["fetch-users", "--db", str(db_path)])
    assert rc == 0
    rc = cli.main(["search", "hello", "--db", str(db_path)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "[Tom Rochette (tomzx)]" in out
    assert "[D1]" not in out


def test_search_json_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search emits JSON when --json is passed."""
    db_path = tmp_path / "cache.db"
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "user": "U1",
                "text": "hello",
                "channel": "C1",
                "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
            },
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["search", "hello", "--json", "--db", str(db_path)])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "hello"
    assert payload["match_count"] == 1
    assert payload["matches"][0]["text"] == "hello"
    assert payload["matches"][0]["channel"] == "C1"


def test_search_jsonl_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search emits the whole result set as a single JSON line with --jsonl."""
    db_path = tmp_path / "cache.db"
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "hello",
                "channel": "C1",
                "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
            },
            {
                "ts": "1700000000.000200",
                "thread_ts": "1700000000.000200",
                "user": "U2",
                "text": "world",
                "channel": "C2",
                "permalink": "https://acme.slack.com/archives/C2/p1700000000000200",
            },
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["search", "hello", "--jsonl", "--db", str(db_path)])
    assert rc == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["query"] == "hello"
    assert payload["match_count"] == 2
    assert payload["matches"][0]["text"] == "hello"
    assert payload["matches"][1]["text"] == "world"


def test_search_passes_count_sort_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Search forwards --count, --sort and --sort-dir to the client."""
    db_path = tmp_path / "cache.db"
    client = FakeSearchClient(matches=[])
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(
        [
            "search",
            "deploy",
            "--count",
            "5",
            "--sort",
            "score",
            "--sort-dir",
            "asc",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0
    assert client.search_calls[0]["count"] == 5
    assert client.search_calls[0]["sort"] == "score"
    assert client.search_calls[0]["sort_dir"] == "asc"


def test_search_caches_matches_for_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A message found by search is afterwards retrievable via show --no-fetch."""
    db_path = tmp_path / "cache.db"
    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "thread_ts": "1700000000.000100",
                "user": "U1",
                "text": "cached via search",
                "channel": "C1",
            },
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    assert cli.main(["search", "cached", "--db", str(db_path)]) == 0
    capsys.readouterr()

    rc = cli.main(
        [
            "show",
            "--channel",
            "C1",
            "--ts",
            "1700000000.000100",
            "--db",
            str(db_path),
            "--no-fetch",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cached via search" in out


def test_search_renders_cached_user_and_channel_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search resolves user/channel ids to display names when cached."""
    db_path = tmp_path / "cache.db"

    class SeedClient:
        async def iter_users(self, limit: int = 1000):
            yield {"id": "U1", "name": "alice", "real_name": "Alice Smith"}

        async def iter_channels(self, types="public_channel", limit=1000):
            yield {"id": "C1", "name": "general", "is_private": False}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: SeedClient())
    assert cli.main(["fetch-users", "--db", str(db_path)]) == 0
    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()

    client = FakeSearchClient(
        matches=[
            {
                "ts": "1700000000.000100",
                "user": "U1",
                "text": "hello",
                "channel": "C1",
            },
        ]
    )
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda args: client)

    rc = cli.main(["search", "hello", "--json", "--db", str(db_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matches"][0]["user_name"] == "Alice Smith (alice)"
    assert payload["matches"][0]["channel_name"] == "general"


class FakeAsyncPollClient:
    """Async stub client for poll tests that tracks calls per channel."""

    CREDENTIALS = Creds(token="xoxb-test", cookie=None)

    def __init__(self, credentials=None, base_url=None, client=None, rate_limit_state=None):
        self._credentials = credentials or self.CREDENTIALS
        self._base_url = (base_url or "").rstrip("/")
        self._messages: dict[str, list] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.rate_limit_state = rate_limit_state

    async def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
        self.calls.append((channel, oldest))
        for msg in self._messages.get(channel, []):
            yield msg

    async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
        return
        yield


def _patch_poll(monkeypatch, fake_client_cls, cycle_limit=1):
    """Patch asyncio.sleep and SlackClient for poll tests.

    fake_client_cls is a class (not instance) whose __init__ accepts the
    same args as SlackClient.
    """
    import asyncio as _asyncio

    import httpx

    from slack_cached import slack_api

    monkeypatch.setattr(
        "slack_cached.config.load_credentials",
        lambda require=True: fake_client_cls.CREDENTIALS,
    )

    cycle_count = 0

    async def fake_sleep(seconds):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= cycle_limit:
            raise _asyncio.CancelledError()

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(slack_api, "SlackClient", fake_client_cls)


def test_poll_single_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll runs one cycle then stops."""
    db_path = tmp_path / "cache.db"

    class TestClient(FakeAsyncPollClient):
        def __init__(self, credentials=None, **kwargs):
            super().__init__(credentials=credentials, **kwargs)
            self._messages = {
                "C1": [{"ts": "1700000000.000100", "user": "U1", "text": "hello"}],
                "C2": [{"ts": "1700000000.000200", "user": "U2", "text": "world"}],
            }

    _patch_poll(monkeypatch, TestClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "C1,C2",
            "--interval",
            "5m",
            "--last",
            "5m",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0

    err = capsys.readouterr().err
    assert "polling 2 channel(s)" in err
    assert "cycle 1:" in err
    assert "poll stopped after 1 cycle(s)" in err


def test_poll_json_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll emits JSON per cycle when --json is passed."""
    db_path = tmp_path / "cache.db"

    class TestClient(FakeAsyncPollClient):
        def __init__(self, credentials=None, **kwargs):
            super().__init__(credentials=credentials, **kwargs)
            self._messages = {
                "C1": [{"ts": "1700000000.000100", "user": "U1", "text": "msg1"}],
            }

    _patch_poll(monkeypatch, TestClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "C1",
            "--interval",
            "5m",
            "--last",
            "5m",
            "--json",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload["cycle"] == 1
    assert len(payload["channels"]) == 1
    assert payload["channels"][0]["channel"] == "C1"
    assert payload["channels"][0]["fetched"] == 1


def test_poll_multiple_cycles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll runs multiple cycles before stopping."""
    db_path = tmp_path / "cache.db"

    class TestClient(FakeAsyncPollClient):
        def __init__(self, credentials=None, **kwargs):
            super().__init__(credentials=credentials, **kwargs)
            self._messages = {
                "C1": [{"ts": "1700000000.000100", "user": "U1", "text": "hello"}],
            }

    _patch_poll(monkeypatch, TestClient, cycle_limit=3)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "C1",
            "--interval",
            "1s",
            "--last",
            "1m",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0

    err = capsys.readouterr().err
    assert "cycle 3:" in err
    assert "poll stopped after 3 cycle(s)" in err


def test_poll_requires_channels(tmp_path: Path) -> None:
    """Poll exits with error if --channels is empty."""
    db_path = tmp_path / "cache.db"
    rc = cli.main(["poll", "--channels", "", "--db", str(db_path)])
    assert rc == 1


def test_poll_resolves_channel_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll resolves bare and '#'-prefixed names via the cached channels."""
    db_path = tmp_path / "cache.db"

    class ChannelsClient:
        async def iter_channels(self, types="public_channel", limit=1000):
            yield {"id": "C1", "name": "general", "is_private": False}
            yield {"id": "C2", "name": "random", "is_private": False}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: ChannelsClient())
    assert cli.main(["fetch-channels", "--db", str(db_path)]) == 0
    capsys.readouterr()  # clear seeding output

    class TestClient(FakeAsyncPollClient):
        def __init__(self, credentials=None, **kwargs):
            super().__init__(credentials=credentials, **kwargs)
            self._messages = {
                "C1": [{"ts": "1700000000.000100", "user": "U1", "text": "hi"}],
                "C2": [{"ts": "1700000000.000200", "user": "U2", "text": "yo"}],
                "C3": [{"ts": "1700000000.000300", "user": "U3", "text": "hey"}],
            }

    _patch_poll(monkeypatch, TestClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "#general,random,C3",
            "--interval",
            "5m",
            "--last",
            "5m",
            "--json",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0

    payload = json.loads(capsys.readouterr().out.strip())
    polled = sorted(ch["channel"] for ch in payload["channels"])
    assert polled == ["C1", "C2", "C3"]


def test_poll_unresolved_channel_name_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll errors out when a channel name cannot be resolved."""
    db_path = tmp_path / "cache.db"

    class EmptyClient:
        async def iter_channels(self, types="public_channel", limit=1000):
            return
            yield  # pragma: no cover - make this an async generator

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: EmptyClient())
    _patch_poll(monkeypatch, FakeAsyncPollClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "#nope",
            "--interval",
            "5m",
            "--last",
            "5m",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not resolve" in err
    assert "nope" in err


def test_poll_rejects_interval_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Poll rejects --interval all."""
    db_path = tmp_path / "cache.db"
    _patch_poll(monkeypatch, FakeAsyncPollClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "C1",
            "--interval",
            "all",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 1


def test_poll_handles_channel_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Poll continues when one channel fails."""
    db_path = tmp_path / "cache.db"

    class FlakyClient(FakeAsyncPollClient):
        def __init__(self, credentials=None, **kwargs):
            super().__init__(credentials=credentials, **kwargs)
            self.ok_channel_fetched = False

        async def iter_channel_history(self, channel, oldest=None, latest=None, limit=200):
            if channel == "C_BAD":
                raise RuntimeError("api error")
            self.ok_channel_fetched = True
            yield {"ts": "1700000000.000100", "user": "U1", "text": "ok"}

    _patch_poll(monkeypatch, FlakyClient, cycle_limit=1)

    rc = cli.main(
        [
            "poll",
            "--channels",
            "C_BAD,C_OK",
            "--interval",
            "5m",
            "--last",
            "5m",
            "--json",
            "--db",
            str(db_path),
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    channels = payload["channels"]
    assert any("error" in ch for ch in channels)
    assert any(ch.get("fetched") == 1 for ch in channels)


def test_serve_command_is_registered(capsys: pytest.CaptureFixture[str]) -> None:
    """The serve subcommand appears in --help and accepts --host/--port."""
    rc = cli.main(["--help"])
    assert rc == 0
    assert "serve" in capsys.readouterr().out

    rc = cli.main(["serve", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--host" in out
    assert "--port" in out


# ---------------------------------------------------------------------------
# Per-workspace cache database
# ---------------------------------------------------------------------------


def _stub_workspace_client(
    monkeypatch: pytest.MonkeyPatch,
    auth_payload: dict,
    *,
    token: str = "xoxc-test-token",
    base_url: str = "https://slack.com/api",
) -> None:
    """Stub the Slack client with a fixed auth.test identity."""

    class FakeWorkspaceClient:
        async def auth_test(self):
            return auth_payload

        async def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

        async def aclose(self) -> None:
            pass

    FakeWorkspaceClient.token = token
    FakeWorkspaceClient.base_url = base_url
    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeWorkspaceClient())


def test_fetch_targets_workspace_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without --db, fetch lands in <cache>/<workspace>/threads.db via auth.test."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _stub_workspace_client(
        monkeypatch,
        {"ok": True, "url": "https://acme.slack.com/", "team_id": "TACME"},
    )

    rc = cli.main(["fetch", "--channel", "C0123ABCDEF", "--ts", "1700000000.000100"])

    assert rc == 0
    assert (tmp_path / "slackx" / "acme" / "threads.db").exists()
    assert (tmp_path / "slackx" / "last_workspace").read_text().strip() == "acme"


def test_show_reads_last_workspace_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show --no-fetch finds the last-used workspace database without network."""

    def fail_build(common):
        raise AssertionError("show --no-fetch must not build a client")

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _stub_workspace_client(
        monkeypatch,
        {"ok": True, "url": "https://acme.slack.com/", "team_id": "TACME"},
    )
    rc = cli.main(["fetch", "--channel", "C0123ABCDEF", "--ts", "1700000000.000100"])
    assert rc == 0

    monkeypatch.setattr(cli._internal._client, "_build_client", fail_build)
    rc = cli.main(
        [
            "show",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--no-fetch",
        ]
    )
    assert rc == 0
    assert "hello" in capsys.readouterr().out


def test_workspace_flag_selects_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--workspace names the cache directory without an auth.test call."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    class FakeWorkspaceClient:
        async def auth_test(self):
            raise AssertionError("auth.test must not run when --workspace is given")

        async def iter_thread_replies(
            self,
            channel: str,
            thread_ts: str,
            oldest: str | None = None,
            limit: int = 200,
        ):
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: FakeWorkspaceClient())

    rc = cli.main(
        [
            "fetch",
            "--channel",
            "C0123ABCDEF",
            "--ts",
            "1700000000.000100",
            "--workspace",
            "beta",
        ]
    )

    assert rc == 0
    assert (tmp_path / "slackx" / "beta" / "threads.db").exists()
    assert (tmp_path / "slackx" / "last_workspace").read_text().strip() == "beta"


def test_show_requires_workspace_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Multiple workspace caches without a last-used pointer is an error."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from slack_cached.storage import connect
    from slack_cached.workspace import workspace_db_path

    connect(workspace_db_path("alpha")).close()
    connect(workspace_db_path("beta")).close()

    with pytest.raises(SystemExit, match="alpha, beta"):
        cli.main(
            [
                "show",
                "--channel",
                "C0123ABCDEF",
                "--ts",
                "1700000000.000100",
                "--no-fetch",
            ]
        )


def test_auth_test_runs_once_per_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The first fetch resolves via auth.test; later fetches reuse the disk cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    auth_calls: list[int] = []
    payload = {"ok": True, "url": "https://acme.slack.com/", "team_id": "TACME"}

    class CountingClient:
        token = "xoxc-test-token"
        base_url = "https://slack.com/api"

        async def auth_test(self):
            auth_calls.append(1)
            return payload

        async def iter_thread_replies(self, channel, thread_ts, oldest=None, limit=200):
            yield {"ts": "1700000000.000100", "user": "U1", "text": "hello"}

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(cli._internal._client, "_build_client", lambda _: CountingClient())

    for _ in range(2):
        rc = cli.main(["fetch", "--channel", "C0123ABCDEF", "--ts", "1700000000.000100"])
        assert rc == 0

    assert len(auth_calls) == 1
    names = json.loads((tmp_path / "slackx" / "workspace_names.json").read_text())
    assert len(names) == 1
    assert "acme" in names.values()


def test_serve_sync_resolution_uses_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without --db/--workspace, serve resolves the workspace from credentials."""
    from slack_cached.cli._internal import _client
    from slack_cached.cli._internal._shared import CommonArgs
    from slack_cached.workspace import workspace_db_path

    common = CommonArgs()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("SLACK_TOKEN", "xoxc-test-token")
    monkeypatch.setenv("SLACK_COOKIE", "xoxd-test-cookie")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    # Cache miss: auth.test runs via the (stubbed) client, then is stored.
    _stub_workspace_client(
        monkeypatch,
        {"ok": True, "url": "https://acme.slack.com/", "team_id": "TACME"},
    )
    assert _client._resolve_db_path_sync(common) == workspace_db_path("acme")

    # Cache hit: no client is built and no network call is made.
    def fail_build(common):
        raise AssertionError("cached workspace must not build a client")

    monkeypatch.setattr(cli._internal._client, "_build_client", fail_build)
    assert _client._resolve_db_path_sync(common) == workspace_db_path("acme")


def test_serve_sync_resolution_without_credentials_falls_back_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No credentials: serve falls back to offline resolution."""
    from slack_cached.cli._internal import _client
    from slack_cached.cli._internal._shared import CommonArgs
    from slack_cached.workspace import offline_db_path

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    common = CommonArgs()
    assert _client._resolve_db_path_sync(common) == offline_db_path()
