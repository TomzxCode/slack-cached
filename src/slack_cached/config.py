"""Configuration loading for slack-cached.

Credentials are resolved in this order:
1. Environment variables (SLACK_TOKEN, SLACK_COOKIE).
2. A config file at $XDG_CONFIG_HOME/slack-cached/config or ~/.config/slack-cached/config.
   The config file is a simple KEY=VALUE format, similar to a .env file.

The cache database lives at $XDG_CACHE_HOME/slack-cached/threads.db
or ~/.cache/slack-cached/threads.db by default, and can be overridden via --db.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

CONFIG_FILENAME = "config"
APP_NAME = "slack-cached"
DEFAULT_DB_NAME = "threads.db"


@dataclass(frozen=True)
class Credentials:
    """Slack credentials needed to call the API."""

    token: str
    cookie: str | None


def config_dir() -> Path:
    """Return the directory where the config file is expected to live."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def cache_dir() -> Path:
    """Return the directory where the cache database is expected to live."""
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".cache" / APP_NAME


def default_db_path() -> Path:
    """Return the default path for the SQLite cache database."""
    return cache_dir() / DEFAULT_DB_NAME


def _load_config_file() -> dict[str, str | None]:
    """Load the config file if it exists, returning an empty dict otherwise."""
    path = config_dir() / CONFIG_FILENAME
    if not path.is_file():
        return {}
    return dict(dotenv_values(path))


def load_api_base_url() -> str | None:
    """Return an API base URL override from env var or config file, if any.

    Priority: ``SLACK_API_BASE_URL`` env var, then config file key of the same
    name.  Returns ``None`` when neither is set, which callers should treat as
    "use the default".
    """
    value = os.environ.get("SLACK_API_BASE_URL", "").strip()
    if not value:
        config = _load_config_file()
        value = (config.get("SLACK_API_BASE_URL") or "").strip()
    return value or None


def load_credentials(require: bool = True) -> Credentials:
    """Resolve Slack credentials from env vars or the config file.

    Raises SystemExit if no token can be found and ``require`` is True.
    When ``require`` is False, returns empty credentials instead of exiting.
    """
    token = os.environ.get("SLACK_TOKEN", "").strip()
    cookie = os.environ.get("SLACK_COOKIE", "").strip() or None

    if not token or cookie is None:
        values = _load_config_file()
        if not token:
            token = (values.get("SLACK_TOKEN") or "").strip()
        if cookie is None:
            file_cookie = (values.get("SLACK_COOKIE") or "").strip()
            cookie = file_cookie or None

    if not token:
        if not require:
            return Credentials(token="", cookie=None)
        config_path = config_dir() / CONFIG_FILENAME
        raise SystemExit(
            "No Slack token found.\n"
            "Provide credentials in either way (both values must come from "
            "the same browser session):\n"
            "  - Environment: export SLACK_TOKEN=xoxc-... and "
            "SLACK_COOKIE=xoxd-...\n"
            f"  - Config file ({config_path}):\n"
            "        SLACK_TOKEN=xoxc-...\n"
            "        SLACK_COOKIE=xoxd-...\n"
            "An xoxc- token also requires its xoxd- d cookie (SLACK_COOKIE)."
        )
    if token.startswith("xoxc-") and not cookie:
        raise SystemExit(
            "An xoxc- browser token requires its matching d cookie. "
            "Set SLACK_COOKIE to the xoxd- value from the same browser "
            "session (Authorization uses the xoxc- token, the d cookie "
            "uses the xoxd- value)."
        )
    return Credentials(token=token, cookie=cookie)
