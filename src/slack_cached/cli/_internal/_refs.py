"""Resolving thread references and output format from CLI flags."""

import sys

from slack_cached.urls import ThreadRef, parse_channel_ts, parse_thread_url


def _resolve_ref(url: str | None, channel: str | None, ts: str | None) -> ThreadRef:
    """Build a ThreadRef from either a URL or --channel/--ts pair."""
    if url:
        return parse_thread_url(url)
    if channel and ts:
        return parse_channel_ts(channel, ts)
    raise SystemExit("Provide either a URL or both --channel and --ts.")


def _output_format(json_flag: bool, jsonl_flag: bool) -> str:
    """Resolve the requested output format, enforcing --json/--jsonl exclusion.

    Returns 'human', 'json', or 'jsonl'. Raises SystemExit if both flags are set.
    """
    if json_flag and jsonl_flag:
        print("--json and --jsonl are mutually exclusive.", file=sys.stderr)
        raise SystemExit(2)
    if jsonl_flag:
        return "jsonl"
    if json_flag:
        return "json"
    return "human"
