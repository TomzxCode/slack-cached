# Conventions

## Naming

- **Files:** snake_case for Python modules (e.g. `slack_api.py`, `cache.py`, `test_cli.py`)
- **Variables:** snake_case (e.g. `thread_ts`, `latest_reply`, `user_names`)
- **Functions / Methods:** snake_case (e.g. `fetch_thread`, `load_thread_messages`, `_build_client`)
- **Classes:** PascalCase (e.g. `SlackClient`, `CachedMessage`, `ThreadRef`, `FetchResult`)
- **Constants:** UPPER_SNAKE_CASE (e.g. `SCHEMA`, `DEFAULT_LIMIT`, `MAX_RETRIES`, `CHANNEL_PREFIXES`)
- **Private helpers:** prefixed with underscore (e.g. `_build_client`, `_format_ts`, `_latest_ts`)
- **CLI subcommands:** kebab-case (e.g. `fetch-users`, `show-channels`)

## Directory Structure

```
slack-cached/
  src/
    slack_cached/          # Main package
      __init__.py          # Empty (package marker)
      cli.py               # CLI entry point (main, subcommand handlers)
      cache.py             # Fetch/load orchestration
      config.py            # Credential, path, and API base URL configuration
      fake_slack.py        # Fake Slack API server for testing (standalone entry point)
      slack_api.py         # Slack Web API HTTP client
      storage.py           # SQLite schema and data access
      urls.py              # URL parsing utilities
  tests/
    test_cache.py          # Tests for cache orchestration
    test_cli.py            # Tests for CLI surface
    test_config.py         # Tests for config resolution
    test_fake_slack.py     # Tests for fake Slack server
    test_slack_api.py      # Tests for Slack API client
    test_storage.py        # Tests for SQLite storage
    test_urls.py           # Tests for URL parsing
  pyproject.toml           # Project metadata, deps, tool config
  README.md                # User-facing documentation
```

## Coding Standards

- Use frozen dataclasses for all value objects (ThreadRef, Credentials, FetchResult, etc.)
- Use structlog for all logging; never use the stdlib logging module directly
- Use `from __future__ import annotations` at the top of every module for modern type syntax
- Import SlackClient and cache functions lazily in CLI to avoid loading requests when not needed
- Use context managers for database connections and transactions
- All public functions should have docstrings
- Private helpers should have brief docstrings when behavior is non-obvious
- Use TYPE_CHECKING guard for type-only imports
- Test with pytest; use tmp_path fixture for temporary databases
- Use monkeypatch for environment variable and dependency injection in tests
- Run ruff check and ruff format before committing

## Commit Messages

Based on the 2-commit git history:
- Short, imperative descriptions without conventional-commits prefixes
- Examples: "Initial commit", "Add link to slacrawl"

No formal commit message convention is enforced.

## Branching

Two branches observed: `main` (default) and `sdlc` (current work). No formal
branching convention is documented or evident from the history.

## SDLC Documentation Style

- One sentence per line in markdown files for easier diff and review
- Use sentence case for headings, not title case
- Prefer bullet lists over prose paragraphs
- Use tables for structured data (requirements, specifications, etc.)
- Keep overview paragraphs to one or two sentences
