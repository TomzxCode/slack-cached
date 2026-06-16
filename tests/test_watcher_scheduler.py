"""Tests for slack_watcher.scheduler helpers.

The full scheduler is exercised end-to-end in test_watcher_integration.py
via the FastAPI app. These tests focus on pure functions: duration parsing,
prompt rendering, and thread rendering.
"""

from __future__ import annotations

import pytest

from slack_cached.storage import CachedMessage
from slack_watcher.scheduler import (
    oldest_ts_for_lookback,
    parse_duration_seconds,
    render_prompt,
    render_thread_text,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5m", 300.0),
        ("90s", 90.0),
        ("1h", 3600.0),
        ("2h30m", 9000.0),
        ("1d", 86400.0),
        ("1d2h3m4s", 86400.0 + 7200.0 + 180.0 + 4.0),
        ("1.5h", 5400.0),
    ],
)
def test_parse_duration_seconds(text: str, expected: float) -> None:
    assert parse_duration_seconds(text) == expected


def test_parse_duration_seconds_all_returns_none() -> None:
    assert parse_duration_seconds("all") is None


@pytest.mark.parametrize("bad", ["", "abc", "5", "5y", "h", "1h 2m", "1x2h"])
def test_parse_duration_seconds_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration_seconds(bad)


def test_oldest_ts_for_lookback_returns_epoch_string() -> None:
    oldest = oldest_ts_for_lookback("1h")
    assert oldest is not None
    assert "." in oldest
    # Roughly an hour ago.
    delta = abs(float(oldest) - (oldest_ts_for_lookback("0s") and __import__("time").time() - 3600))
    assert delta < 5.0


def test_oldest_ts_for_lookback_all_returns_none() -> None:
    assert oldest_ts_for_lookback("all") is None


def test_render_prompt_substitutes_known_keys() -> None:
    template = "Thread: {{thread}} | channel={{channel}}"
    out = render_prompt(template, {"thread": "hi", "channel": "C1"})
    assert out == "Thread: hi | channel=C1"


def test_render_prompt_sweeps_unknown_keys() -> None:
    out = render_prompt("leftover {{unknown}} swept {{thread}}", {"thread": "x"})
    assert out == "leftover  swept x"


def test_render_prompt_handles_missing_payload() -> None:
    out = render_prompt("static text", {})
    assert out == "static text"


def test_render_thread_text_includes_timestamp_author_and_body() -> None:
    messages = [
        CachedMessage(
            ts="1700000000.000000",
            user="U001",
            text="hello world",
            payload={},
        ),
        CachedMessage(
            ts="1700000001.000000",
            user="U002",
            text="reply",
            payload={},
        ),
    ]
    out = render_thread_text(messages, {"U001": "alice", "U002": "bob"})
    assert "alice" in out
    assert "bob" in out
    assert "hello world" in out
    assert "reply" in out
    # ts rendered as ISO-ish timestamp.
    assert "2023-11-14" in out
    assert "22:13:20Z" in out


def test_render_thread_text_falls_back_to_user_id() -> None:
    msg = CachedMessage(ts="1700000000.000000", user="U001", text="hi", payload={})
    out = render_thread_text([msg], {})
    assert "U001" in out
