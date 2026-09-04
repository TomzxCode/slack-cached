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
  |
  +-- workspace.py (per-workspace cache database resolution)

Testing / Development:
  fake_slack.py (standalone HTTP server, deterministic workspace data)
    |
    +-- WorkspaceParams (seed, user/channel/thread counts)
    +-- Workspace (pre-generated users, channels, threads)
    +-- RateLimiter (sliding-window per-endpoint)
    +-- FakeSlackHandler (BaseHTTPRequestHandler, 7 API routes)
```

## Key Components

| Component | Responsibility | Technology |
|---|---|---|
| cli.py | CLI entry point, argument parsing, output rendering | cyclopts, structlog |
| cache.py | Fetch/load orchestration, incremental refresh, channel message fetching | dataclasses, structlog |
| slack_api.py | Slack Web API client with pagination, rate-limit retry, configurable base URL | httpx, structlog |
| storage.py | SQLite schema management, CRUD operations, logging cursor | sqlite3, structlog |
| config.py | Credential loading (env vars + config file), path defaults, API base URL | python-dotenv, os |
| urls.py | Slack permalink parsing into ThreadRef dataclass | urllib.parse, dataclasses |
| workspace.py | Per-workspace cache database resolution (auth.test identity, last-used pointer, offline fallback) | pathlib, structlog |
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
cli.py: resolve the workspace database (auth.test, --workspace, or --db)
  then open it via storage.connect() -> Connection
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

### Workspace database resolution

Each workspace caches to its own SQLite file at
`$XDG_CACHE_HOME/slackx/<workspace>/threads.db`, so channel, user, and
timestamp keys from different workspaces never collide.

- Network commands resolve the workspace from the configured credentials, so
  the database always matches them.
- The workspace name is cached on disk keyed by token and API base URL
  (`workspace_names.json`), so auth.test runs only once per credential set.
- The workspace name is the URL subdomain from auth.test (for example `acme`
  from `https://acme.slack.com/`), falling back to the team id.
- The most recently used workspace is recorded in a pointer file
  (`last_workspace`) so read-only commands such as `show --no-fetch` resolve
  their database offline.
- `serve` without `--db` or `--workspace` resolves from the credentials too
  (disk cache, then auth.test), falling back to offline resolution when
  credentials are missing or Slack cannot be reached.
- Offline resolution order: last-used workspace, then the only existing
  workspace database, then the legacy single `threads.db`.
- `--workspace <name>` selects a workspace explicitly; `--db <path>` overrides
  the path entirely and skips workspace resolution.
- With several workspace databases and no last-used pointer, commands fail and
  ask for an explicit `--workspace`.

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
- One SQLite database per workspace prevents channel/user id collisions
  between workspaces
- Workspace identity is discovered via auth.test (URL subdomain preferred,
  team id fallback) rather than parsed from permalinks, so every command
  resolves the same way
- A last-used workspace pointer keeps read-only commands network-free
- The token-to-workspace mapping is cached on disk (hashed key, no secrets),
  making auth.test a once-per-credential-set cost
- The legacy single threads.db is never migrated into the per-workspace
  layout; it stays in place and only serves offline fallback reads
