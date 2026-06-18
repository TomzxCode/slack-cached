"""Channel-name/id resolution helpers (used by poll and search rendering)."""

import sqlite3
import sys
from collections.abc import Iterable

from slack_cached.cli._internal._shared import CommonArgs
from slack_cached.storage import get_channel, load_channels


def _is_channel_id(token: str) -> bool:
    """Heuristic: decide whether a token is a channel id rather than a name.

    Slack channel names are always lowercase (letters, digits, hyphens,
    underscores), so any token containing an uppercase letter is treated as an
    id. A token with no cased letters at all (e.g. a numeric id) is also
    treated as an id. Everything else is treated as a name.
    """
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    return has_upper or not has_lower


def _channel_name_index(conn: sqlite3.Connection) -> dict[str, str]:
    """Return a {name: id} map of cached channels."""
    return {ch.name: ch.id for ch in load_channels(conn) if ch.name}


def _channel_id_names(conn: sqlite3.Connection, channel_ids: Iterable[str]) -> dict[str, str]:
    """Return a {channel_id: name} map for just the requested channels.

    Uses one lookup per channel rather than loading every cached channel, so
    cost scales with the matches rather than the whole workspace.
    """
    names: dict[str, str] = {}
    for cid in dict.fromkeys(channel_ids):
        if not cid:
            continue
        cached_ch = get_channel(conn, cid)
        if cached_ch and cached_ch.name:
            names[cid] = cached_ch.name
    return names


async def _resolve_poll_channels(common: CommonArgs, raw: str) -> list[str] | None:
    """Resolve a comma-separated --channels value to channel ids.

    Each entry may be a channel id (e.g. C0123456), a bare name (e.g. general),
    or a '#'-prefixed name (e.g. #general). Names are resolved against the
    cached channels; when a name is missing from the cache the channels are
    fetched from Slack once and resolution is retried. Returns None and prints
    an error when a name cannot be resolved.
    """
    # Imported via module reference so monkeypatch on _client._build_client works.
    from slack_cached.cli._internal import _client

    entries = [e.strip().lstrip("#").strip() for e in raw.split(",")]
    entries = [e for e in entries if e]
    if not entries:
        print("error: --channels must contain at least one channel", file=sys.stderr)
        return None

    resolved: list[str] = []
    names = [e for e in entries if not _is_channel_id(e)]
    for entry in entries:
        if _is_channel_id(entry):
            resolved.append(entry)

    if not names:
        return resolved

    from slack_cached.cache import fetch_channels

    with _client._open_db(common) as conn:
        name_to_id = _channel_name_index(conn)
        if any(n not in name_to_id for n in names):
            async with _client._open_client(common) as client:
                await fetch_channels(conn, client)
            name_to_id = _channel_name_index(conn)

        unresolved: list[str] = []
        for name in names:
            channel_id = name_to_id.get(name)
            if channel_id is None:
                unresolved.append(name)
            else:
                resolved.append(channel_id)

    if unresolved:
        joined = ", ".join(unresolved)
        print(
            f"error: could not resolve channel name(s): {joined} "
            "(run 'slack-cached fetch-channels' or check the spelling)",
            file=sys.stderr,
        )
        return None
    return resolved
