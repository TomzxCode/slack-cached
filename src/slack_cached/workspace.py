"""Per-workspace cache database resolution.

Each Slack workspace gets its own cache directory under
``$XDG_CACHE_HOME/slackx/<workspace>/`` holding its ``threads.db``, so
channel ids, user ids and timestamps from different workspaces never collide.

The workspace name is discovered from ``auth.test``: the URL subdomain
(``acme`` in ``https://acme.slack.com/``) when available, otherwise the team
id. The name is cached on disk keyed by credentials, so auth.test runs once
per credential set, and the most recently used workspace is recorded in a
pointer file so read-only commands can find their database without touching
the network.
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import structlog

from slack_cached.config import DEFAULT_DB_NAME, cache_dir

log = structlog.get_logger(__name__)

LAST_WORKSPACE_FILENAME = "last_workspace"
WORKSPACE_NAMES_FILENAME = "workspace_names.json"
_FALLBACK_WORKSPACE_NAME = "workspace"
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_workspace_name(name: str) -> str:
    """Reduce a workspace name to characters safe for a directory name."""
    sanitized = _SANITIZE_RE.sub("_", name).strip(".")
    return sanitized or _FALLBACK_WORKSPACE_NAME


def workspace_dir(name: str) -> Path:
    """Return the cache directory for a workspace name."""
    return cache_dir() / sanitize_workspace_name(name)


def workspace_db_path(name: str) -> Path:
    """Return the cache database path for a workspace name."""
    return workspace_dir(name) / DEFAULT_DB_NAME


def last_workspace_path() -> Path:
    """Return the path of the last-used workspace pointer file."""
    return cache_dir() / LAST_WORKSPACE_FILENAME


def workspace_name_from_auth(data: dict) -> str:
    """Extract a workspace name from an ``auth.test`` response payload.

    Prefers the URL subdomain (e.g. ``acme`` from ``https://acme.slack.com/``)
    and falls back to ``team_id``, then to a generic name.
    """
    url = (data.get("url") or "").strip()
    if url:
        host = urlparse(url).hostname or ""
        if host.endswith(".slack.com"):
            subdomain = host[: -len(".slack.com")]
            if subdomain:
                return subdomain
    team_id = (data.get("team_id") or "").strip()
    if team_id:
        return team_id
    return _FALLBACK_WORKSPACE_NAME


def remember_last_workspace(name: str) -> None:
    """Record the most recently used workspace for offline resolution."""
    path = last_workspace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sanitize_workspace_name(name)}\n", encoding="utf-8")


def last_workspace() -> str | None:
    """Return the last-used workspace name, if any."""
    path = last_workspace_path()
    if not path.is_file():
        return None
    name = path.read_text(encoding="utf-8").strip()
    return name or None


def _workspace_cache_key(token: str, base_url: str) -> str:
    """Hash credentials and API base URL into a cache key (no secrets stored)."""
    digest = hashlib.sha256(f"{base_url}\n{token}".encode()).hexdigest()
    return digest[:16]


def _load_workspace_names() -> dict[str, str]:
    path = cache_dir() / WORKSPACE_NAMES_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cached_workspace_name(token: str, base_url: str) -> str | None:
    """Return the workspace name remembered for these credentials, if any.

    A token belongs to exactly one workspace, so a hit lets commands resolve
    the workspace without an auth.test call.
    """
    name = _load_workspace_names().get(_workspace_cache_key(token, base_url))
    return name or None


def remember_workspace_name(name: str, token: str, base_url: str) -> None:
    """Record the workspace name for a set of credentials."""
    path = cache_dir() / WORKSPACE_NAMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_workspace_names()
    data[_workspace_cache_key(token, base_url)] = sanitize_workspace_name(name)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def existing_workspace_dbs() -> list[Path]:
    """Return the workspace cache databases that currently exist."""
    base = cache_dir()
    if not base.is_dir():
        return []
    return sorted(
        directory / DEFAULT_DB_NAME
        for directory in base.iterdir()
        if directory.is_dir() and (directory / DEFAULT_DB_NAME).is_file()
    )


def legacy_db_path() -> Path:
    """Return the pre-workspace single-database cache path."""
    return cache_dir() / DEFAULT_DB_NAME


def claim_workspace_db(name: str) -> Path:
    """Resolve and remember the database for a workspace.

    Called once the workspace identity has been confirmed via ``auth.test``.
    Existing caches are never migrated or moved: a pre-workspace
    ``threads.db`` is left in place.
    """
    path = workspace_db_path(name)
    remember_last_workspace(name)
    return path


def offline_db_path() -> Path:
    """Resolve the cache database without touching the network.

    Order: the last-used workspace, then the only workspace database when
    exactly one exists, then the legacy single ``threads.db``, then the legacy
    default path (created fresh, matching the pre-workspace behaviour).
    """
    name = last_workspace()
    if name is not None:
        path = workspace_db_path(name)
        if path.exists():
            return path

    dbs = existing_workspace_dbs()
    if len(dbs) == 1:
        return dbs[0]
    if len(dbs) > 1:
        names = ", ".join(p.parent.name for p in dbs)
        raise SystemExit(
            "Multiple workspace caches found but no last-used workspace is "
            f"recorded. Pass --workspace with one of: {names}."
        )

    legacy = legacy_db_path()
    if legacy.exists():
        log.debug("workspace_db_legacy_fallback", path=str(legacy))
    return legacy
