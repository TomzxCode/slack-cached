# Architecture

## System Overview

slack-cached is a single-process CLI tool with a layered architecture: CLI
(cyclopts) at the top, a cache orchestration layer in the middle, and a
storage layer (SQLite) plus a Slack API client at the bottom.

```
User
  |
  v
cli.py (cyclopts subcommands)
  |
  +-- cache.py (fetch/load orchestration)
  |     |
  |     +-- slack_api.py (SlackClient, HTTP + pagination, configurable base_url)
  |     |
  |     +-- storage.py (SQLite schema, upsert, query)
  |
  +-- urls.py (permalink parsing)
  |
  +-- config.py (credential + path + API base URL resolution)

Testing / Development:
  fake_slack.py (standalone HTTP server, deterministic workspace data)
    |
    +-- WorkspaceParams (seed, user/channel/thread counts)
    +-- Workspace (pre-generated users, channels, threads)
    +-- RateLimiter (sliding-window per-endpoint)
    +-- FakeSlackHandler (BaseHTTPRequestHandler, 4 API routes)
```

## Key Components

| Component | Responsibility | Technology |
|---|---|---|
| cli.py | CLI entry point, argument parsing, output rendering | cyclopts, structlog |
| cache.py | Fetch/load orchestration, incremental refresh, channel message fetching | dataclasses, structlog |
| slack_api.py | Slack Web API client with pagination, rate-limit retry, configurable base URL | requests, structlog |
| storage.py | SQLite schema management, CRUD operations, logging cursor | sqlite3, structlog |
| config.py | Credential loading (env vars + config file), path defaults, API base URL | python-dotenv, os |
| urls.py | Slack permalink parsing into ThreadRef dataclass | urllib.parse, dataclasses |
| fake_slack.py | Fake Slack API server for testing/development (includes chat.postMessage) | http.server, random, structlog |

## Data Flow

### Thread fetch flow

```
User runs: slack-cached fetch <URL>
  |
  v
cli.py: parse URL via urls.py -> ThreadRef(channel, thread_ts)
  |
  v
cli.py: load credentials via config.py -> Credentials(token, cookie)
  |
  v
cli.py: open SQLite via storage.connect() -> Connection
  |
  v
cache.py: fetch_thread(conn, client, ref)
  |
  +-- storage.get_thread_state() -> ThreadState or None
  |     (decides: full fetch or incremental)
  |
  +-- slack_api.iter_thread_replies(channel, thread_ts, oldest?)
  |     -> yields message dicts, follows cursor pagination
  |
  +-- storage.record_thread_refresh() (upsert thread row)
  +-- storage.upsert_messages() (INSERT OR REPLACE by ts)
  |
  v
Print summary to stderr
```

### Thread show flow

```
User runs: slack-cached show <URL>
  |
  v
cli.py: load thread from cache (auto-fetch if missing)
  |
  +-- storage.load_thread_messages() -> list[CachedMessage]
  +-- storage.load_user_display_names() -> {id: display_name}
  |
  v
Render to stdout (human-readable or JSON)
```

### User/channel fetch flow

```
User runs: slack-cached fetch-users (or fetch-channels)
  |
  v
slack_api.iter_users() / iter_channels()
  -> yields dicts via cursor-based pagination
  |
  v
storage.upsert_users() / upsert_channels() -> count written
  |
  v
Print summary to stderr
```

### Channel message fetch flow

```
User runs: slack-cached fetch --channel C1 [--full-threads]
  |
  v
slack_api.iter_channel_history(channel)
  -> yields top-level messages via conversations.history
  |
  v
For each message: storage.record_thread_refresh() + upsert_messages()
  |
  v
If --full-threads:
  For each threaded message:
    slack_api.iter_thread_replies(channel, thread_ts) -> replies
    storage.record_thread_refresh() + upsert_messages()
  |
  v
storage.count_channel_messages(channel) -> total
  |
  v
Print summary to stderr
```

## Infrastructure

No CI/CD configuration was found in the repository. The project uses local
development tooling only:

- Package manager: uv (pyproject.toml with uv_build backend)
- Linting/formatting: ruff (line-length 100, rules E/F/I/B/UP/SIM)
- Testing: pytest with -ra flag, tests in tests/
- Python version: 3.13 (pinned in .python-version)
- No Docker, no deployment configuration, no hosted infrastructure

## Architecture Decisions

Key decisions visible in the code:

- SQLite chosen as the local cache store for zero-config, single-file storage
  with upsert semantics via INSERT OR REPLACE
- Full Slack message JSON stored in a `payload` column alongside extracted
  fields (ts, user, text) for efficient querying without re-parsing
- Logging cursor wrapper (_LoggingCursor) provides per-query SQL timing at
  debug level for performance diagnostics
- Slack client separated from CLI so network-free commands (show --no-fetch)
  avoid importing requests
- XDG base directory convention followed for config and cache paths
- Frozen dataclasses used throughout for immutable value objects
  (ThreadRef, Credentials, FetchResult, CachedMessage, etc.)
- SlackClient accepts a configurable base_url, enabling the fake server for
  integration testing without code changes
- Fake Slack server generates deterministic workspace data from a seeded RNG,
  providing reproducible test environments
- Fake Slack server supports chat.postMessage for simulating message creation
