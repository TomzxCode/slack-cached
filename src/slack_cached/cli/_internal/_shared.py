"""Shared CLI infrastructure: the cyclopts app, parameter aliases, and setup helpers."""

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import structlog
from cyclopts import App, Parameter

from slack_cached.config import default_db_path

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
    Parameter(
        help="Slack channel id (e.g. C001), bare name (e.g. general), or "
        "'#'-prefixed name (e.g. #general). Names are resolved against the "
        "cached channels. Used with --ts, or alone to target a whole channel.",
    ),
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
