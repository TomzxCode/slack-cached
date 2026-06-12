"""Command-line interface for slack-cached.

Subcommands:
- fetch: cache or refresh a Slack thread silently.
- show: print a cached thread to stdout (human-readable by default, --json for JSON).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .config import default_db_path
from .storage import (
    CachedChannel,
    CachedMessage,
    CachedUser,
    connect,
    get_thread_state,
    load_channels,
    load_thread_messages,
    load_user_display_names,
    load_users,
)
from .urls import ThreadRef, parse_channel_ts, parse_thread_url

if TYPE_CHECKING:
    from .slack_api import SlackClient

log = structlog.get_logger(__name__)


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


def _resolve_ref(args: argparse.Namespace) -> ThreadRef:
    """Build a ThreadRef from either --url or --channel/--ts."""
    if args.url:
        return parse_thread_url(args.url)
    if args.channel and args.ts:
        return parse_channel_ts(args.channel, args.ts)
    raise SystemExit("Provide either a URL or both --channel and --ts.")


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "url",
        nargs="?",
        help="Slack thread permalink (e.g. https://acme.slack.com/archives/C123/p1700000000123456).",
    )
    parser.add_argument("--channel", help="Slack channel id, used with --ts.")
    parser.add_argument(
        "--ts",
        help="Thread root ts (e.g. 1700000000.123456), used with --channel.",
    )
    _add_db_args(parser)


def _add_db_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"SQLite cache path (default: {default_db_path()}).",
    )
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="Slack API base URL (default: https://slack.com/api, use "
        "http://localhost:PORT/api for the fake server).  Can also be set via "
        "the SLACK_API_BASE_URL environment variable.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")


@contextmanager
def _open_db(args: argparse.Namespace) -> Iterator[sqlite3.Connection]:
    db_path = args.db or default_db_path()
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _build_client(args: argparse.Namespace) -> SlackClient:
    # Imported lazily so commands that never hit the network (e.g.
    # `show --no-fetch`) avoid loading the requests-based API client.
    from .config import load_api_base_url, load_credentials
    from .slack_api import DEFAULT_API_BASE, SlackClient

    base_url = args.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise
    return SlackClient(credentials, base_url=base_url)


def cmd_fetch(args: argparse.Namespace) -> int:
    """Cache or refresh a thread, or fetch all messages from a channel."""
    if args.channel and not args.ts and not args.url:
        return _cmd_fetch_channel_messages(args)

    from .cache import fetch_thread

    ref = _resolve_ref(args)
    client = _build_client(args)
    with _open_db(args) as conn:
        result = fetch_thread(conn, client, ref)
    print(
        f"cached {result.total_messages} messages "
        f"({result.fetched_messages} new/updated, "
        f"{'incremental' if result.incremental else 'full'}) "
        f"for {result.channel}/{result.thread_ts}",
        file=sys.stderr,
    )
    return 0


def _cmd_fetch_channel_messages(args: argparse.Namespace) -> int:
    """Fetch messages from a channel."""
    from .cache import fetch_channel_messages

    oldest = _oldest_ts_from_last(args.last)
    client = _build_client(args)
    with _open_db(args) as conn:
        result = fetch_channel_messages(
            conn, client, args.channel, full_threads=args.full_threads, oldest=oldest
        )
    detail = (
        f", {result.threads_with_replies_fetched} threads with replies fetched"
        if args.full_threads
        else ""
    )
    print(
        f"cached {result.total_messages} messages for {result.channel} "
        f"({result.fetched_messages} fetched{detail})",
        file=sys.stderr,
    )
    return 0


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


def _render_json(ref: ThreadRef, messages: list[CachedMessage]) -> str:
    """Render a thread as a pretty-printed JSON string."""
    payload = {
        "channel": ref.channel,
        "thread_ts": ref.thread_ts,
        "message_count": len(messages),
        "messages": [asdict(m) for m in messages],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def cmd_show(args: argparse.Namespace) -> int:
    """Print the cached thread. Human-readable by default, JSON with --json.

    Fetches first if not already cached (unless --no-fetch is given).
    """
    log.debug("cmd_show_start")
    with _timed("resolve_ref"):
        ref = _resolve_ref(args)
    with _open_db(args) as conn:
        state = get_thread_state(conn, ref.channel, ref.thread_ts)
        if state is None and not args.no_fetch:
            log.info("thread_not_cached_fetching", channel=ref.channel, thread_ts=ref.thread_ts)
            if args.verbose:
                print(
                    f"fetching thread {ref.channel}/{ref.thread_ts} from Slack...",
                    file=sys.stderr,
                )
            # Imported lazily so the cached-read path does not pull in the
            # requests-based Slack client (see _build_client).
            from .cache import fetch_thread

            client = _build_client(args)
            fetch_thread(conn, client, ref)
        with _timed("load_thread"):
            messages = load_thread_messages(conn, ref.channel, ref.thread_ts)
        log.debug("loaded_messages", count=len(messages))
        with _timed("build_user_names"):
            user_names = _build_user_names(conn, messages)
        log.debug("loaded_user_names", count=len(user_names))

    with _timed("render", format="json" if args.json else "human", messages=len(messages)):
        output = (
            _render_json(ref, messages) if args.json else _render_human(ref, messages, user_names)
        )
    with _timed("write_output", bytes=len(output)):
        sys.stdout.write(output)
        sys.stdout.flush()
    return 0


def cmd_fetch_users(args: argparse.Namespace) -> int:
    """Fetch and cache every workspace user."""
    from .cache import fetch_users

    client = _build_client(args)
    with _open_db(args) as conn:
        result = fetch_users(conn, client)
    print(
        f"processed {result.processed} users ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0


def cmd_fetch_channels(args: argparse.Namespace) -> int:
    """Fetch and cache every visible conversation."""
    from .cache import fetch_channels

    client = _build_client(args)
    with _open_db(args) as conn:
        result = fetch_channels(conn, client)
    print(
        f"processed {result.processed} channels ({result.added} added, {result.total} total in db)",
        file=sys.stderr,
    )
    return 0


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


def cmd_show_users(args: argparse.Namespace) -> int:
    """Print cached users. Human-readable by default, JSON with --json."""
    with _open_db(args) as conn:
        if not args.no_fetch and not load_users(conn):
            from .cache import fetch_users

            log.info("users_not_cached_fetching")
            fetch_users(conn, _build_client(args))
        users = load_users(conn)

    if args.json:
        payload = {"user_count": len(users), "users": [asdict(u) for u in users]}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(_render_users_human(users))
    return 0


def cmd_show_channels(args: argparse.Namespace) -> int:
    """Print cached channels. Human-readable by default, JSON with --json."""
    with _open_db(args) as conn:
        if not args.no_fetch and not load_channels(conn):
            from .cache import fetch_channels

            log.info("channels_not_cached_fetching")
            fetch_channels(conn, _build_client(args))
        channels = load_channels(conn)

    if args.json:
        payload = {"channel_count": len(channels), "channels": [asdict(c) for c in channels]}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(_render_channels_human(channels))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slack-cached",
        description="Cache Slack threads to a local SQLite database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser(
        "fetch",
        help="Cache or refresh a Slack thread, or fetch all messages from a channel.",
    )
    _add_target_args(fetch)
    fetch.add_argument(
        "--full-threads",
        action="store_true",
        help="When fetching a channel, also fetch all replies for every thread.",
    )
    fetch.add_argument(
        "--last",
        type=str,
        default="1d",
        metavar="DURATION",
        help="When fetching a channel, limit history to the given lookback "
        "(e.g. 24h, 2d5h30m, 90m; default: 1d, use 'all' for full history).",
    )
    fetch.set_defaults(func=cmd_fetch)

    show = sub.add_parser(
        "show",
        help="Print a cached thread to stdout (human-readable by default).",
    )
    _add_target_args(show)
    show.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not auto-fetch when the thread is not yet cached.",
    )
    show.add_argument(
        "--json",
        action="store_true",
        help="Render output as JSON instead of human-readable text.",
    )
    show.set_defaults(func=cmd_show)

    fetch_users_cmd = sub.add_parser("fetch-users", help="Cache or refresh all workspace users.")
    _add_db_args(fetch_users_cmd)
    fetch_users_cmd.set_defaults(func=cmd_fetch_users)

    fetch_channels_cmd = sub.add_parser(
        "fetch-channels", help="Cache or refresh all visible channels."
    )
    _add_db_args(fetch_channels_cmd)
    fetch_channels_cmd.set_defaults(func=cmd_fetch_channels)

    show_users_cmd = sub.add_parser(
        "show-users",
        help="Print cached users to stdout (human-readable by default).",
    )
    _add_db_args(show_users_cmd)
    show_users_cmd.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not auto-fetch when users are not yet cached.",
    )
    show_users_cmd.add_argument(
        "--json",
        action="store_true",
        help="Render output as JSON instead of human-readable text.",
    )
    show_users_cmd.set_defaults(func=cmd_show_users)

    show_channels_cmd = sub.add_parser(
        "show-channels",
        help="Print cached channels to stdout (human-readable by default).",
    )
    _add_db_args(show_channels_cmd)
    show_channels_cmd.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not auto-fetch when channels are not yet cached.",
    )
    show_channels_cmd.add_argument(
        "--json",
        action="store_true",
        help="Render output as JSON instead of human-readable text.",
    )
    show_channels_cmd.set_defaults(func=cmd_show_channels)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    log.debug("dispatch", command=getattr(args, "command", None))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
