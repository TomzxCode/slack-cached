"""Small formatting helpers: timestamps and user-name resolution."""

import sqlite3
from datetime import UTC, datetime

from slack_cached.storage import CachedMessage, load_user_display_names


def _format_ts(ts: str) -> str:
    """Render a Slack 'ts' (epoch seconds as string) as an ISO timestamp.

    Falls back to the raw value if it cannot be parsed as a float.
    """
    try:
        dt = datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError):
        return ts
    return dt.isoformat(timespec="seconds")


def _build_user_names(conn: sqlite3.Connection, messages: list[CachedMessage]) -> dict[str, str]:
    """Map the thread's author ids to human-readable names for rendering.

    Resolves names only for users that actually appear in the given messages,
    rather than loading the entire workspace, which avoids JSON-decoding every
    cached user payload.
    """
    user_ids = {msg.user for msg in messages if msg.user}
    return load_user_display_names(conn, user_ids)
