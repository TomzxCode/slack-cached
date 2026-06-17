"""Database connection and Slack client construction."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from slack_cached.cli._internal._shared import CommonArgs
from slack_cached.config import default_db_path
from slack_cached.storage import connect

if TYPE_CHECKING:
    from slack_cached.slack_api import SlackClient


@contextmanager
def _open_db(common: CommonArgs) -> Iterator[sqlite3.Connection]:
    db_path = common.db or default_db_path()
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _build_client(common: CommonArgs) -> "SlackClient":
    # Imported lazily so commands that never hit the network (e.g.
    # `show --no-fetch`) avoid loading the requests-based API client.
    from slack_cached.config import load_api_base_url, load_credentials
    from slack_cached.slack_api import DEFAULT_API_BASE, SlackClient

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise
    return SlackClient(credentials, base_url=base_url)
