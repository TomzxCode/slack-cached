# slack-cached

A small Python CLI that caches Slack threads, channel messages, users, and
channels to a local SQLite database.

Given a Slack thread URL (or an explicit channel id and root timestamp), it
fetches the thread via `conversations.replies` and stores every message in a
SQLite cache.
On subsequent runs it only fetches new replies (and detects edits) by passing
`oldest` to the API based on the highest cached `ts`.

It can also cache every workspace user and visible channel, so that threads can
be rendered with human-readable author names.

## Install

```bash
uv sync
```

Run it:

```bash
uv run slack-cached --help
```

## Authentication

Credentials are loaded in this order:

1. Environment variables: `SLACK_TOKEN` (and optional `SLACK_COOKIE` for
   xoxc/web-client tokens).
2. A config file at `$XDG_CONFIG_HOME/slack-cached/config`
   (defaults to `~/.config/slack-cached/config`).
   It uses a simple `KEY=VALUE` format:
   ```
   SLACK_TOKEN=xoxb-...
   SLACK_COOKIE=...
   SLACK_API_BASE_URL=https://slack.com/api
   ```

## Cache location

The default cache database lives at
`$XDG_CACHE_HOME/slack-cached/threads.db` (or `~/.cache/slack-cached/threads.db`).
Override with `--db /path/to/file.db`.

## Usage

All commands accept `-v/--verbose` for debug logging on stderr, `--db` to
override the cache location, and `--api-base-url` to override the Slack API
base URL (defaults to `https://slack.com/api`; also settable via
`SLACK_API_BASE_URL`).

### Threads

Cache or refresh a thread (no thread output, only a summary on stderr):

```bash
slack-cached fetch https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
```

Or with explicit channel/ts:

```bash
slack-cached fetch --channel C0123ABCDEF --ts 1700000000.123456
```

Show a cached thread (human-readable by default; use `--json` for JSON). It
auto-fetches if the thread is missing; pass `--no-fetch` to disable that:

```bash
slack-cached show https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
slack-cached show --json https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
```

### Channel messages

Fetch all top-level messages in a channel via `conversations.history`:

```bash
slack-cached fetch --channel C0123ABCDEF
```

Add `--full-threads` to also fetch every reply thread for messages that have
replies:

```bash
slack-cached fetch --channel C0123ABCDEF --full-threads
```

### Polling channels

Poll multiple channels concurrently for new messages:

```bash
slack-cached poll --channels C001,#general,random --interval 5m --last 5m --concurrency 3
```

Uses `httpx.AsyncClient` with an `asyncio.Semaphore` for concurrent, non-blocking
HTTP requests. Reads `X-RateLimit-Remaining` headers to proactively throttle
before hitting 429s. Add `--full-threads` to expand threads, and `--json` to get
per-cycle JSON summaries on stdout. Stops gracefully with `Ctrl+C`.

### Users and channels

Cache or refresh every workspace user or visible channel:

```bash
slack-cached fetch-users
slack-cached fetch-channels
```

Show cached users or channels (human-readable by default, `--json` for JSON;
both auto-fetch when empty unless `--no-fetch` is given):

```bash
slack-cached show-users
slack-cached show-channels --json
```

When a thread's authors are present in the cached users, `show` renders their
real name and handle (e.g. `Alice Smith (alice)`) instead of raw user ids.

## Refresh behavior

`fetch` always reaches out to Slack.
If the thread is already cached, it requests `conversations.replies` with
`oldest=<latest_cached_ts>` so the API returns only new replies (and any
recent edits at that boundary).
Messages are upserted by `ts`, so edits replace the older version in place.

HTTP 429 / `ratelimited` responses are retried automatically with exponential
backoff (up to 5 attempts), respecting the `Retry-After` header.

## Fake Slack server

A built-in fake Slack API server for testing and development:

```bash
uv run slack-fake-server --help
uv run slack-fake-server --port 8199 --num-threads 50
```

It serves deterministic workspace data (`conversations.list`,
`conversations.replies`, `conversations.history`, `users.list`) and can
simulate Slack-tier rate limiting with `--rate-limits`.

Point `slack-cached` at it with:

```bash
slack-cached --api-base-url http://localhost:8199/api fetch ...
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
```

## Related projects

- [slacrawl](https://github.com/openclaw/slacrawl)
