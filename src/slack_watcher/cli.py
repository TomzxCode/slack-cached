"""CLI entry point for slack-watcher.

Starts the FastAPI app with uvicorn, serving both the API and (if built)
the React PWA from web/dist.

Configuration via environment variables mirrors slack-cached itself:

  SLACK_TOKEN, SLACK_COOKIE, SLACK_API_BASE_URL   (slack-cached credentials)
  SLACK_WATCHER_DB                                (override watcher DB path)
  SLACK_WATCHER_HOST, SLACK_WATCHER_PORT          (server bind)
  SLACK_WATCHER_WEB_DIST                          (path to built SPA)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import structlog
import uvicorn

from .app import create_app
from .storage import default_db_path


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slack-watcher",
        description=(
            "Web SPA + scheduler that runs LLM prompts against Slack threads cached "
            "by slack-cached."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8233, help="Bind port (default 8233).")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to watcher SQLite DB (default $XDG_DATA_HOME/slack-cached/watcher.db).",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=None,
        help="Path to slack-cached cache DB (default $XDG_CACHE_HOME/slack-cached/threads.db).",
    )
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="Slack API base URL (default https://slack.com/api).",
    )
    parser.add_argument(
        "--web-dist",
        type=Path,
        default=None,
        help="Path to built SPA (default web/dist in the repo root).",
    )
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    import os

    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    db_path = args.db or Path(os.environ.get("SLACK_WATCHER_DB") or default_db_path())
    cache_db = args.cache_db or Path(
        os.environ.get("SLACK_WATCHER_CACHE_DB") or _default_cache_db_path()
    )
    host = os.environ.get("SLACK_WATCHER_HOST") or args.host
    port = int(os.environ.get("SLACK_WATCHER_PORT") or args.port)
    web_dist = args.web_dist or Path(
        os.environ.get("SLACK_WATCHER_WEB_DIST")
        or (Path(__file__).resolve().parents[3] / "web" / "dist")
    )

    # create_app is called inside uvicorn via the factory string so reload
    # works cleanly. We pass kwargs as env vars because uvicorn's factory
    # callable takes no args.
    os.environ["SLACK_WATCHER_DB"] = str(db_path)
    os.environ["SLACK_WATCHER_CACHE_DB"] = str(cache_db)
    os.environ.setdefault("SLACK_WATCHER_HOST", host)
    os.environ.setdefault("SLACK_WATCHER_PORT", str(port))
    # --api-base-url forwards to the factory via the slack-cached env var.
    # This is read by slack_cached.config.load_api_base_url().
    if args.api_base_url:
        os.environ["SLACK_API_BASE_URL"] = args.api_base_url
    if web_dist.is_dir():
        os.environ["SLACK_WATCHER_WEB_DIST"] = str(web_dist)

    uvicorn.run(
        "slack_watcher.cli:create_app_from_env",
        factory=True,
        host=host,
        port=port,
        reload=args.reload,
        log_level="debug" if args.verbose else "info",
    )
    return 0


def _default_cache_db_path() -> Path:
    from slack_cached.config import default_db_path as slack_default_db

    return slack_default_db()


def create_app_from_env():
    """Factory used by uvicorn so --reload works without re-importing."""
    import os

    db_path = Path(os.environ["SLACK_WATCHER_DB"])
    cache_db_path = Path(os.environ["SLACK_WATCHER_CACHE_DB"])
    web_dist_env = os.environ.get("SLACK_WATCHER_WEB_DIST")
    web_dist = Path(web_dist_env) if web_dist_env else None
    return create_app(
        db_path=db_path,
        cache_db_path=cache_db_path,
        web_dist=web_dist,
    )


if __name__ == "__main__":
    raise SystemExit(main())
