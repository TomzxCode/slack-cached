"""Command-line interface for slack-cached.

Subcommands:
- fetch: cache or refresh a Slack thread silently.
- show: print a cached thread to stdout (human-readable by default, --json for JSON).
"""

from collections.abc import Sequence

from slack_cached.cli._internal._shared import app

# Import command modules so their @app.command decorators register them.
from slack_cached.cli.commands import (  # noqa: F401
    fetch,
    fetch_channels,
    fetch_users,
    poll,
    search,
    show,
    show_channels,
    show_users,
)

__all__ = ["app", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by tests and the ``slack-cached`` console script."""
    return app(argv, result_action="return_int_as_exit_code_else_zero")


if __name__ == "__main__":
    raise SystemExit(main())
