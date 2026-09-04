"""CLI command for running the fake Slack API server."""

import sys
from typing import Annotated

import structlog
from cyclopts import App, Parameter

from slack_cached.fake_slack._internal._config import (
    WorkspaceParams,
    _parse_epoch_base,
    _parse_range_flag,
)
from slack_cached.fake_slack._internal._server import run_server

log = structlog.get_logger(__name__)

fake_server_app = App(
    name="slack-fake-server",
    help="Fake Slack API server with configurable, realistic workspace data.",
    version="0.1.0",
)


@fake_server_app.default
def run_fake_server(
    *,
    host: Annotated[str, Parameter(help="Bind address (default: 127.0.0.1).")] = "127.0.0.1",
    port: Annotated[int, Parameter(help="Port to listen on (default: 8199).")] = 8199,
    seed: Annotated[int, Parameter(help="Random seed for data generation (default: 42).")] = 42,
    num_users: Annotated[int, Parameter(help="Number of workspace members (default: 20).")] = 20,
    num_channels: Annotated[int, Parameter(help="Number of channels (default: 13).")] = 13,
    num_ims: Annotated[
        int,
        Parameter(help="Number of direct message conversations (default: 4)."),
    ] = 4,
    num_threads: Annotated[
        int, Parameter(help="Number of conversation threads (default: 30).")
    ] = 30,
    messages_per_thread: Annotated[
        str,
        Parameter(help="Message count range per thread, e.g. '3-12' or '5' (default: 3-12)."),
    ] = "3-12",
    activity_ratio: Annotated[
        float, Parameter(help="Fraction of users who actively participate (default: 0.6).")
    ] = 0.6,
    rate_limits: Annotated[
        bool, Parameter(help="Enable Slack-compatible rate limiting (disabled by default).")
    ] = False,
    epoch_base: Annotated[
        str | None,
        Parameter(
            help="Base epoch timestamp for generated data. Accepts 'now', an ISO "
            "datetime (e.g. '2025-06-01'), or a unix timestamp. "
            "Default: 1704067200.0 (2024-01-01).",
        ),
    ] = None,
) -> int:
    """Run the fake Slack API server until interrupted."""
    min_mpt, max_mpt = _parse_range_flag(messages_per_thread)
    parsed_epoch_base = _parse_epoch_base(epoch_base)

    params = WorkspaceParams(
        seed=seed,
        num_users=num_users,
        num_channels=num_channels,
        num_ims=num_ims,
        num_threads=num_threads,
        min_messages_per_thread=min_mpt,
        max_messages_per_thread=max_mpt,
        activity_ratio=activity_ratio,
        rate_limits=rate_limits,
        epoch_base=parsed_epoch_base,
    )

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )

    server = run_server(host=host, port=port, params=params)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("fake_slack_server_shutting_down")
        server.shutdown()
    return 0
