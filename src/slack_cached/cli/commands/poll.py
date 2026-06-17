"""``slack-cached poll`` command."""

import sys
from typing import Annotated

import structlog
from cyclopts import Parameter

from slack_cached.cli._internal._channels import _resolve_poll_channels
from slack_cached.cli._internal._duration import _parse_duration
from slack_cached.cli._internal._poll import _poll_loop
from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    DbArg,
    VerboseArg,
    _setup,
    app,
)

log = structlog.get_logger(__name__)


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
