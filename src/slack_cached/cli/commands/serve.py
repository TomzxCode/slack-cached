"""``slackx serve`` command: browse the cache in a browser."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import structlog
from cyclopts import Parameter

from slack_cached.cli._internal._shared import (
    ApiBaseUrlArg,
    DbArg,
    VerboseArg,
    _setup,
    app,
)
from slack_cached.config import default_db_path

log = structlog.get_logger(__name__)

HostArg = Annotated[
    str,
    Parameter(help="Interface to bind the web server to."),
]
PortArg = Annotated[
    int,
    Parameter(help="Port to bind the web server to."),
]


@app.command(name="serve")
def serve(
    *,
    host: HostArg = "127.0.0.1",
    port: PortArg = 8280,
    db: DbArg = None,
    api_base_url: ApiBaseUrlArg = None,
    verbose: VerboseArg = False,
) -> int:
    """Serve the cached database through a local web UI.

    Opens a Slack-like interface to browse cached users, channels, messages
    and threads. Ctrl+P opens a palette to jump between channels and
    conversations. Refresh buttons trigger live Slack fetches when
    credentials are configured.
    """
    common = _setup(db, api_base_url, verbose)
    db_path = Path(common.db) if common.db else default_db_path()

    import uvicorn

    from slack_cached.server.app import create_app

    webapp = create_app(db_path=db_path, api_base_url=common.api_base_url)
    log.info("serve_starting", host=host, port=port, db_path=str(db_path))
    uvicorn.run(webapp, host=host, port=port, log_level="debug" if verbose else "warning")
    return 0
