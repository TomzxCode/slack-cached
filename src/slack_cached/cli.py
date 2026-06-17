"""Command-line interface for slack-cached.

Subcommands:
- fetch: cache or refresh a Slack thread silently.
- show: print a cached thread to stdout (human-readable by default, --json for JSON).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from cyclopts import App, Parameter

from .config import default_db_path
from .storage import (
    CachedChannel,
    CachedMessage,
    CachedUser,
    connect,
    get_channel,
    get_thread_state,
    load_channel_messages,
    load_channels,
    load_thread_messages,
    load_user_display_names,
    load_users,
)
from .urls import ThreadRef, parse_channel_ts, parse_thread_url

if TYPE_CHECKING:
    from .slack_api import SlackClient

log = structlog.get_logger(__name__)

app = App(
    name="slack-cached",
    help="Cache Slack threads to a local SQLite database.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Shared parameter annotations (kept once so every command stays in sync)
# ---------------------------------------------------------------------------

DbArg = Annotated[
    Path | None,
    Parameter(help=f"SQLite cache path (default: {default_db_path()})."),
]
ApiBaseUrlArg = Annotated[
    str | None,
    Parameter(
        help="Slack API base URL (default: https://slack.com/api, use "
        "http://localhost:PORT/api for the fake server). Can also be set via "
        "the SLACK_API_BASE_URL environment variable.",
    ),
]
VerboseArg = Annotated[
    bool,
    Parameter(name=["--verbose", "-v"], help="Enable debug logging."),
]
JsonArg = Annotated[
    bool,
    Parameter(name="--json", help="Render output as pretty-printed JSON."),
]
JsonlArg = Annotated[
    bool,
    Parameter(
        name="--jsonl",
        help="Render output as a single compact JSON line (no indentation). "
        "Convenient for piping into jq -c, wc -l, or appending to a .jsonl file.",
    ),
]
NoFetchArg = Annotated[
    bool,
    Parameter(name="--no-fetch", help="Do not auto-fetch when not yet cached."),
]
UrlArg = Annotated[
    str | None,
    Parameter(
        help="Slack thread permalink (e.g. "
        "https://acme.slack.com/archives/C123/p1700000000123456).",
    ),
]
ChannelArg = Annotated[
    str | None,
    Parameter(help="Slack channel id, used with --ts."),
]
TsArg = Annotated[
    str | None,
    Parameter(help="Thread root ts (e.g. 1700000000.123456), used with --channel."),
]


@dataclass
class CommonArgs:
    """Carries the shared db/api-base-url/verbose flags through internal helpers."""

    db: Path | None = None
    api_base_url: str | None = None
    verbose: bool = False


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _setup(db: Path | None, api_base_url: str | None, verbose: bool) -> CommonArgs:
    """Build the CommonArgs carrier and wire up logging in one place."""
    common = CommonArgs(db=db, api_base_url=api_base_url, verbose=verbose)
    _configure_logging(verbose)
    log.debug("dispatch")
    return common


@contextmanager
def _timed(phase: str, **fields: object) -> Iterator[None]:
    """Log how long a block of work takes, at debug level.

    Surfaces only in verbose mode, alongside the per-query SQL timings, so the
    time spent outside the database (deserialization, rendering, output) can be
    attributed to a specific phase.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        log.debug(
            "phase",
            phase=phase,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
            **fields,
        )


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


@contextmanager
def _open_db(common: CommonArgs) -> Iterator[sqlite3.Connection]:
    db_path = common.db or default_db_path()
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _build_client(common: CommonArgs) -> SlackClient:
    # Imported lazily so commands that never hit the network (e.g.
    # `show --no-fetch`) avoid loading the requests-based API client.
    from .config import load_api_base_url, load_credentials
    from .slack_api import DEFAULT_API_BASE, SlackClient

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise
    return SlackClient(credentials, base_url=base_url)


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)([dhms])", re.IGNORECASE)
_DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


def _parse_duration(text: str) -> timedelta | None:
    """Parse a humanized duration string (e.g. ``24h``, ``2d5h30m``, ``90m``).

    Returns *None* for the special value ``"all"`` (meaning no limit).
    Raises ``ValueError`` on unrecognised input.
    """
    if text.lower() == "all":
        return None
    parts = _DURATION_RE.findall(text)
    if not parts or "".join(f"{v}{u}" for v, u in parts) != text:
        raise ValueError(f"invalid duration: {text!r}")
    kwargs: dict[str, float] = {}
    for value, unit in parts:
        kwargs[_DURATION_UNITS[unit.lower()]] = float(value)
    return timedelta(**kwargs)


def _oldest_ts_from_last(text: str) -> str | None:
    """Convert a --last duration string to an epoch-seconds string, or None."""
    delta = _parse_duration(text)
    if delta is None:
        return None
    oldest_dt = datetime.now(tz=UTC) - delta
    return f"{oldest_dt.timestamp():.6f}"


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


# ---------------------------------------------------------------------------
# Renderers (output is independent of how arguments were parsed)
# ---------------------------------------------------------------------------


def _render_human(
    ref: ThreadRef,
    messages: list[CachedMessage],
    user_names: dict[str, str] | None = None,
) -> str:
    """Render a thread as a human-readable string.

    When `user_names` maps a message's user id to a name, that name is shown
    instead of the raw id; unknown ids fall back to the id itself.
    """
    names = user_names or {}
    lines = [
        f"Thread {ref.channel}/{ref.thread_ts}",
        f"{len(messages)} message(s)",
        "",
    ]
    for msg in messages:
        author = names.get(msg.user, msg.user) if msg.user else "(unknown)"
        text = msg.text if msg.text is not None else ""
        lines.append(f"[{_format_ts(msg.ts)}] {author}")
        for text_line in text.splitlines() or [""]:
            lines.append(f"    {text_line}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_json(
    ref: ThreadRef,
    messages: list[CachedMessage],
    user_names: dict[str, str] | None = None,
    channel_name: str | None = None,
    *,
    indent: int | None = 2,
) -> str:
    """Render a thread as a JSON string (pretty-printed by default).

    Pass ``indent=None`` to emit the whole payload as a single line, suitable
    for JSONL output (one record per invocation).
    """
    names = user_names or {}
    enriched: list[dict[str, Any]] = []
    for msg in messages:
        d = asdict(msg)
        if msg.user and msg.user in names:
            d["user_name"] = names[msg.user]
        enriched.append(d)
    payload: dict[str, Any] = {
        "channel": ref.channel,
        "channel_name": channel_name,
        "thread_ts": ref.thread_ts,
        "message_count": len(messages),
        "messages": enriched,
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


def _render_channel_human(
    channel: str,
    messages: list[CachedMessage],
    user_names: dict[str, str] | None = None,
    channel_name: str | None = None,
) -> str:
    names = user_names or {}
    header = channel_name or channel
    lines = [
        f"Channel {header}",
        f"{len(messages)} message(s)",
        "",
    ]
    for msg in messages:
        author = names.get(msg.user, msg.user) if msg.user else "(unknown)"
        text = msg.text if msg.text is not None else ""
        lines.append(f"[{_format_ts(msg.ts)}] {author}")
        for text_line in text.splitlines() or [""]:
            lines.append(f"    {text_line}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_channel_json(
    channel: str,
    messages: list[CachedMessage],
    user_names: dict[str, str] | None = None,
    channel_name: str | None = None,
    *,
    indent: int | None = 2,
) -> str:
    """Render a channel's messages as JSON (pretty-printed by default).

    Pass ``indent=None`` to emit the whole payload as a single line.
    """
    names = user_names or {}
    enriched: list[dict[str, Any]] = []
    for msg in messages:
        d = {"ts": msg.ts, "user": msg.user, "text": msg.text}
        if msg.user and msg.user in names:
            d["user_name"] = names[msg.user]
        enriched.append(d)
    payload: dict[str, Any] = {
        "channel": channel,
        "channel_name": channel_name,
        "message_count": len(messages),
        "messages": enriched,
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


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


def _render_search_human(
    query: str,
    matches: list[dict[str, Any]],
    user_names: dict[str, str] | None = None,
    channel_names: dict[str, str] | None = None,
) -> str:
    """Render search matches as a human-readable string.

    Each match is printed with its channel, an optional permalink, the author
    and the message text, in the same style as `_render_human`.
    """
    names = user_names or {}
    ch_names = channel_names or {}
    lines = [f"Search: {query}", f"{len(matches)} match(es)", ""]
    for msg in matches:
        channel = msg.get("channel") or "?"
        ch_label = ch_names.get(channel, channel)
        ts = msg.get("ts", "?")
        user = msg.get("user")
        author = names.get(user, user) if user else "(unknown)"
        text = msg.get("text") if msg.get("text") is not None else ""
        permalink = msg.get("permalink")
        header = f"[{ch_label}]"
        if permalink:
            header = f"{header} {permalink}"
        lines.append(header)
        lines.append(f"[{_format_ts(ts)}] {author}")
        for text_line in text.splitlines() or [""]:
            lines.append(f"    {text_line}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_search_json(
    query: str,
    matches: list[dict[str, Any]],
    user_names: dict[str, str] | None = None,
    channel_names: dict[str, str] | None = None,
    *,
    indent: int | None = 2,
) -> str:
    """Render search matches as a JSON string (pretty-printed by default).

    Pass ``indent=None`` to emit the whole payload as a single line.
    """
    names = user_names or {}
    ch_names = channel_names or {}
    enriched: list[dict[str, Any]] = []
    for msg in matches:
        channel = msg.get("channel")
        user = msg.get("user")
        entry: dict[str, Any] = {
            "channel": channel,
            "channel_name": ch_names.get(channel) if channel else None,
            "ts": msg.get("ts"),
            "thread_ts": msg.get("thread_ts"),
            "user": user,
            "text": msg.get("text"),
            "permalink": msg.get("permalink"),
        }
        if user and user in names:
            entry["user_name"] = names[user]
        enriched.append(entry)
    payload: dict[str, Any] = {
        "query": query,
        "match_count": len(matches),
        "matches": enriched,
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


def _render_users_human(users: list[CachedUser]) -> str:
    lines = [f"{len(users)} user(s)", ""]
    for user in users:
        name = user.name or "(no name)"
        real_name = f" - {user.real_name}" if user.real_name else ""
        lines.append(f"{user.id}  {name}{real_name}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_channels_human(channels: list[CachedChannel]) -> str:
    lines = [f"{len(channels)} channel(s)", ""]
    for channel in channels:
        name = channel.name or "(no name)"
        visibility = "private" if channel.is_private else "public"
        lines.append(f"{channel.id}  {name} ({visibility})")
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Channel-name resolution helpers (used by poll)
# ---------------------------------------------------------------------------


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


def _resolve_poll_channels(common: CommonArgs, raw: str) -> list[str] | None:
    """Resolve a comma-separated --channels value to channel ids.

    Each entry may be a channel id (e.g. C0123456), a bare name (e.g. general),
    or a '#'-prefixed name (e.g. #general). Names are resolved against the
    cached channels; when a name is missing from the cache the channels are
    fetched from Slack once and resolution is retried. Returns None and prints
    an error when a name cannot be resolved.
    """
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

    from .cache import fetch_channels

    with _open_db(common) as conn:
        name_to_id = _channel_name_index(conn)
        if any(n not in name_to_id for n in names):
            client = _build_client(common)
            fetch_channels(conn, client)
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command
def fetch(
    url: UrlArg = None,
    *,
    channel: ChannelArg = None,
    ts: TsArg = None,
    full_threads: Annotated[
        bool,
        Parameter(help="When fetching a channel, also fetch all replies for every thread."),
    ] = False,
    last: Annotated[
        str,
        Parameter(
            help="When fetching a channel, limit history to the given lookback "
            "(e.g. 24h, 2d5h30m, 90m; default: 1d, use 'all' for full history).",
        ),
    ] = "1d",
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Cache or refresh a Slack thread, or fetch all messages from a channel."""
    common = _setup(db, api_base_url, verbose)
    if channel and not ts and not url:
        return _fetch_channel_messages(common, channel, full_threads, last)

    from .cache import fetch_thread

    ref = _resolve_ref(url, channel, ts)
    client = _build_client(common)
    with _open_db(common) as conn:
        result = fetch_thread(conn, client, ref)
    print(
        f"cached {result.total_messages} messages "
        f"({result.fetched_messages} new/updated, "
        f"{'incremental' if result.incremental else 'full'}) "
        f"for {result.channel}/{result.thread_ts}",
        file=sys.stderr,
    )
    return 0


def _fetch_channel_messages(common: CommonArgs, channel: str, full_threads: bool, last: str) -> int:
    """Fetch messages from a channel."""
    from .cache import fetch_channel_messages

    oldest = _oldest_ts_from_last(last)
    client = _build_client(common)
    with _open_db(common) as conn:
        result = fetch_channel_messages(
            conn, client, channel, full_threads=full_threads, oldest=oldest
        )
    detail = (
        f", {result.threads_with_replies_fetched} threads with replies fetched"
        if full_threads
        else ""
    )
    print(
        f"cached {result.total_messages} messages for {result.channel} "
        f"({result.fetched_messages} fetched{detail})",
        file=sys.stderr,
    )
    return 0


@app.command
def show(
    url: UrlArg = None,
    *,
    channel: ChannelArg = None,
    ts: TsArg = None,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    last: Annotated[
        str,
        Parameter(
            help="When showing a channel, limit history to the given lookback "
            "(e.g. 24h, 2d5h30m, 90m; default: 1d, use 'all' for full history).",
        ),
    ] = "1d",
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print a cached thread or channel to stdout (human-readable by default).

    Fetches first if not already cached (unless --no-fetch is given).

    When --channel is given without --ts, shows all messages for that channel
    (fetching first if needed, unless --no-fetch).
    """
    common = _setup(db, api_base_url, verbose)
    fmt = _output_format(json_output, jsonl_output)

    if channel and not ts and not url:
        return _show_channel(common, channel, no_fetch, last, fmt)

    log.debug("cmd_show_start")
    with _timed("resolve_ref"):
        ref = _resolve_ref(url, channel, ts)
    with _open_db(common) as conn:
        state = get_thread_state(conn, ref.channel, ref.thread_ts)
        if state is None and not no_fetch:
            log.info(
                "thread_not_cached_fetching",
                channel=ref.channel,
                thread_ts=ref.thread_ts,
                thread_ts_iso=_format_ts(ref.thread_ts),
            )
            if verbose:
                print(
                    f"fetching thread {ref.channel}/{ref.thread_ts} from Slack...",
                    file=sys.stderr,
                )
            # Imported lazily so the cached-read path does not pull in the
            # requests-based Slack client (see _build_client).
            from .cache import fetch_thread

            client = _build_client(common)
            fetch_thread(conn, client, ref)
        with _timed("load_thread"):
            messages = load_thread_messages(conn, ref.channel, ref.thread_ts)
        log.debug("loaded_messages", count=len(messages))
        with _timed("build_user_names"):
            user_names = _build_user_names(conn, messages)
        log.debug("loaded_user_names", count=len(user_names))
        cached_ch = get_channel(conn, ref.channel)
        channel_name = cached_ch.name if cached_ch else None

    with _timed("render", format=fmt, messages=len(messages)):
        if fmt in ("json", "jsonl"):
            output = _render_json(
                ref,
                messages,
                user_names,
                channel_name,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_human(ref, messages, user_names)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    return 0


def _show_channel(common: CommonArgs, channel: str, no_fetch: bool, last: str, fmt: str) -> int:
    """Show all messages for a channel, fetching first if needed."""
    oldest = _oldest_ts_from_last(last)
    with _open_db(common) as conn:
        messages = load_channel_messages(conn, channel)
        if not messages and not no_fetch:
            log.info("channel_not_cached_fetching", channel=channel)
            if common.verbose:
                print(
                    f"fetching messages for {channel} from Slack...",
                    file=sys.stderr,
                )
            from .cache import fetch_channel_messages

            client = _build_client(common)
            fetch_channel_messages(conn, client, channel, oldest=oldest)
            messages = load_channel_messages(conn, channel)
        with _timed("build_user_names"):
            user_names = _build_user_names(conn, messages)
        cached_ch = get_channel(conn, channel)
        channel_name = cached_ch.name if cached_ch else None

    with _timed("render", format=fmt, messages=len(messages)):
        if fmt in ("json", "jsonl"):
            output = _render_channel_json(
                channel,
                messages,
                user_names,
                channel_name,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_channel_human(channel, messages, user_names, channel_name)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    return 0


@app.command
def search(
    query: Annotated[
        str,
        Parameter(help="Slack search query (same syntax as the Slack search box)."),
    ],
    *,
    count: Annotated[int, Parameter(help="Maximum results per page (default: 20).")] = 20,
    sort: Annotated[
        Literal["score", "timestamp"],
        Parameter(help="Sort matches by score or timestamp."),
    ] = "timestamp",
    sort_dir: Annotated[Literal["asc", "desc"], Parameter(help="Sort direction.")] = "desc",
    full_threads: Annotated[
        bool,
        Parameter(help="Also fetch all replies for every thread a match belongs to."),
    ] = False,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    verbose: VerboseArg = False,
) -> int:
    """Search Slack via search.messages and cache the matched messages/threads.

    Search is inherently a live operation: every run hits the API. Every
    matched message is cached under its ``(channel, thread_ts)`` so it can be
    revisited later with `show`. Output is human-readable by default, JSON
    with --json.
    """
    from .cache import fetch_search

    common = _setup(db, api_base_url, verbose)
    fmt = _output_format(json_output, jsonl_output)

    log.debug("cmd_search_start", query=query)
    client = _build_client(common)
    with _open_db(common) as conn:
        with _timed("fetch_search", query=query):
            result = fetch_search(
                conn,
                client,
                query=query,
                count=count,
                sort=sort,
                sort_dir=sort_dir,
                full_threads=full_threads,
            )
        matches = result.matches
        log.debug("search_matches", count=len(matches))

        user_ids = {m.get("user") for m in matches if m.get("user")}
        channel_ids = {m.get("channel") for m in matches if m.get("channel")}
        with _timed("build_user_names"):
            user_names = load_user_display_names(conn, user_ids)
        with _timed("build_channel_names"):
            channel_names = _channel_id_names(conn, channel_ids)

    with _timed("render", format=fmt, matches=len(matches)):
        if fmt in ("json", "jsonl"):
            output = _render_search_json(
                query,
                matches,
                user_names,
                channel_names,
                indent=2 if fmt == "json" else None,
            )
        else:
            output = _render_search_human(query, matches, user_names, channel_names)
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    print(
        f"searched {query!r}: {len(matches)} match(es), {result.threads_touched} thread(s) cached",
        file=sys.stderr,
    )
    return 0


@app.command(name="fetch-users")
def fetch_users(
    *,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Fetch and cache every workspace user."""
    from .cache import fetch_users

    common = _setup(db, api_base_url, verbose)
    client = _build_client(common)
    with _open_db(common) as conn:
        result = fetch_users(conn, client)
    print(
        f"processed {result.processed} users ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0


@app.command(name="fetch-channels")
def fetch_channels(
    *,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Fetch and cache every visible conversation."""
    from .cache import fetch_channels

    common = _setup(db, api_base_url, verbose)
    client = _build_client(common)
    with _open_db(common) as conn:
        result = fetch_channels(conn, client)
    print(
        f"processed {result.processed} channels ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0


@app.command(name="show-users")
def show_users(
    *,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print cached users to stdout (human-readable by default)."""
    common = _setup(db, api_base_url, verbose)
    fmt = _output_format(json_output, jsonl_output)
    with _open_db(common) as conn:
        if not no_fetch and not load_users(conn):
            from .cache import fetch_users

            log.info("users_not_cached_fetching")
            fetch_users(conn, _build_client(common))
        users = load_users(conn)

    if fmt in ("json", "jsonl"):
        payload = {"user_count": len(users), "users": [asdict(u) for u in users]}
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False, indent=2 if fmt == "json" else None) + "\n"
        )
    else:
        sys.stdout.write(_render_users_human(users))
    return 0


@app.command(name="show-channels")
def show_channels(
    *,
    no_fetch: NoFetchArg = False,
    json_output: JsonArg = False,
    jsonl_output: JsonlArg = False,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Print cached channels to stdout (human-readable by default)."""
    common = _setup(db, api_base_url, verbose)
    fmt = _output_format(json_output, jsonl_output)
    with _open_db(common) as conn:
        if not no_fetch and not load_channels(conn):
            from .cache import fetch_channels

            log.info("channels_not_cached_fetching")
            fetch_channels(conn, _build_client(common))
        channels = load_channels(conn)

    if fmt in ("json", "jsonl"):
        payload = {"channel_count": len(channels), "channels": [asdict(c) for c in channels]}
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False, indent=2 if fmt == "json" else None) + "\n"
        )
    else:
        sys.stdout.write(_render_channels_human(channels))
    return 0


@app.command
def poll(
    *,
    channels: Annotated[
        str,
        Parameter(
            required=True,
            help="Comma-separated list of channels to poll. Each entry may be a "
            "channel id (e.g. C001), a bare name (e.g. general), or a "
            "'#'-prefixed name (e.g. #general). Names are resolved against the "
            "cached channels (e.g. C001,general,#random).",
        ),
    ],
    interval: Annotated[
        str,
        Parameter(help="Time between poll cycles (e.g. 5m, 10m, 1h; default: 5m)."),
    ] = "5m",
    last: Annotated[
        str,
        Parameter(
            help="Lookback per cycle (e.g. 5m, 10m, 1h; default: 5m, use 'all' for full history).",
        ),
    ] = "5m",
    full_threads: Annotated[
        bool,
        Parameter(help="Also fetch all thread replies for every threaded message."),
    ] = False,
    concurrency: Annotated[
        int, Parameter(help="Maximum number of channels to fetch concurrently (default: 3).")
    ] = 3,
    json_output: Annotated[
        bool, Parameter(name="--json", help="Emit per-cycle JSON summaries to stdout.")
    ] = False,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Poll channels concurrently in a loop for new messages."""
    common = _setup(db, api_base_url, verbose)

    resolved = _resolve_poll_channels(common, channels)
    if not resolved:
        return 1

    interval_delta = _parse_duration(interval)
    if interval_delta is None:
        print("error: --interval must be a finite duration (not 'all')", file=sys.stderr)
        return 1
    interval_seconds = interval_delta.total_seconds()

    concurrency_value = max(1, concurrency)

    log.info(
        "poll_start",
        channels=resolved,
        interval=interval,
        last=last,
        full_threads=full_threads,
        concurrency=concurrency_value,
    )
    print(
        f"polling {len(resolved)} channel(s) every {interval} "
        f"(lookback: {last}, full_threads: {full_threads}, "
        f"concurrency: {concurrency_value})",
        file=sys.stderr,
    )

    import asyncio

    asyncio.run(
        _poll_loop(
            common,
            resolved,
            interval_seconds,
            concurrency_value,
            last,
            full_threads,
            json_output,
        )
    )
    return 0


async def _poll_loop(
    common: CommonArgs,
    channels: list[str],
    interval_seconds: float,
    concurrency: int,
    last: str,
    full_threads: bool,
    json_output: bool,
) -> None:
    """Run the poll loop with async HTTP and a semaphore for concurrency."""
    import asyncio

    import httpx

    from .async_cache import fetch_channel_messages_async
    from .async_slack_api import AsyncSlackClient, RateLimitState
    from .config import load_api_base_url, load_credentials
    from .slack_api import DEFAULT_API_BASE, REQUEST_TIMEOUT

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise

    rate_limit_state = RateLimitState()
    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as httpx_client:
        client = AsyncSlackClient(
            credentials,
            base_url=base_url,
            client=httpx_client,
            rate_limit_state=rate_limit_state,
        )
        semaphore = asyncio.Semaphore(concurrency)
        cycle = 0

        try:
            while True:
                cycle += 1
                cycle_start = time.perf_counter()
                oldest = _oldest_ts_from_last(last)

                async def fetch_one(
                    channel: str,
                    *,
                    _cycle: int = cycle,
                    _oldest: str | None = oldest,
                ) -> dict[str, Any]:
                    async with semaphore:
                        try:
                            with _open_db(common) as conn:
                                result = await fetch_channel_messages_async(
                                    conn,
                                    client,
                                    channel,
                                    full_threads=full_threads,
                                    oldest=_oldest,
                                )
                            log.info(
                                "poll_channel_done",
                                cycle=_cycle,
                                channel=channel,
                                fetched=result.fetched_messages,
                                total=result.total_messages,
                            )
                            return {
                                "channel": channel,
                                "fetched": result.fetched_messages,
                                "total": result.total_messages,
                            }
                        except Exception:
                            log.exception(
                                "poll_channel_error",
                                cycle=_cycle,
                                channel=channel,
                            )
                            return {"channel": channel, "error": True}

                cycle_summary = await asyncio.gather(*(fetch_one(ch) for ch in channels))

                elapsed = time.perf_counter() - cycle_start
                fetched_total = sum(s.get("fetched", 0) for s in cycle_summary)
                print(
                    f"cycle {cycle}: {fetched_total} new message(s) across "
                    f"{len(channels)} channel(s) in {elapsed:.1f}s",
                    file=sys.stderr,
                )

                if json_output:
                    payload = {
                        "cycle": cycle,
                        "elapsed_seconds": round(elapsed, 3),
                        "channels": cycle_summary,
                    }
                    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    sys.stdout.flush()

                sleep_seconds = max(0, interval_seconds - elapsed)
                log.info("poll_sleep", cycle=cycle, sleep_seconds=sleep_seconds)
                await asyncio.sleep(sleep_seconds)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("poll_interrupted", cycles=cycle)
            print(f"\npoll stopped after {cycle} cycle(s)", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by tests and the ``slack-cached`` console script."""
    return app(argv, result_action="return_int_as_exit_code_else_zero")


if __name__ == "__main__":
    raise SystemExit(main())
