"""Tests for slack URL/channel-ts parsing."""

from __future__ import annotations

import pytest

from slack_cached.urls import ThreadRef, parse_channel_ts, parse_thread_url


def test_parse_thread_root_permalink() -> None:
    url = "https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456"
    assert parse_thread_url(url) == ThreadRef(channel="C0123ABCDEF", thread_ts="1700000000.123456")


def test_parse_reply_permalink_uses_thread_ts_query() -> None:
    url = (
        "https://acme.slack.com/archives/C0123ABCDEF/p1700000099654321"
        "?thread_ts=1700000000.123456&cid=C0123ABCDEF"
    )
    assert parse_thread_url(url) == ThreadRef(channel="C0123ABCDEF", thread_ts="1700000000.123456")


def test_parse_dm_channel_id() -> None:
    url = "https://acme.slack.com/archives/D0123ABCDEF/p1700000000000001"
    ref = parse_thread_url(url)
    assert ref.channel == "D0123ABCDEF"
    assert ref.thread_ts == "1700000000.000001"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/archives/C123/p1700000000123456",
        "https://acme.slack.com/messages/C123/p1700000000123456",
        "https://acme.slack.com/archives/X123/p1700000000123456",
        "https://acme.slack.com/archives/C123/x1700000000123456",
    ],
)
def test_parse_thread_url_rejects_bad_inputs(url: str) -> None:
    with pytest.raises(ValueError):
        parse_thread_url(url)


def test_parse_channel_ts_happy_path() -> None:
    ref = parse_channel_ts("C0123ABCDEF", "1700000000.123456")
    assert ref == ThreadRef(channel="C0123ABCDEF", thread_ts="1700000000.123456")


@pytest.mark.parametrize(
    ("channel", "ts"),
    [
        ("X123", "1700000000.123456"),
        ("", "1700000000.123456"),
        ("C123", "1700000000"),
        ("C123", "not-a-ts"),
    ],
)
def test_parse_channel_ts_rejects_bad_inputs(channel: str, ts: str) -> None:
    with pytest.raises(ValueError):
        parse_channel_ts(channel, ts)
