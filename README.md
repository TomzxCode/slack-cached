# slackx

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
uv run slackx --help
```

## Authentication

Credentials are loaded in this order:

1. Environment variables: `SLACK_TOKEN` (and optional `SLACK_COOKIE` for
   xoxc/web-client tokens).
2. A config file at `$XDG_CONFIG_HOME/slackx/config`
   (defaults to `~/.config/slackx/config`).
   It uses a simple `KEY=VALUE` format:
   ```
   SLACK_TOKEN=xoxb-...
   SLACK_COOKIE=...
   SLACK_API_BASE_URL=https://slack.com/api
   ```

## Cache location

Each Slack workspace gets its own cache database at
`$XDG_CACHE_HOME/slackx/<workspace>/threads.db`
(e.g. `~/.cache/slackx/acme/threads.db`). The workspace is determined from
the configured token/cookie via `auth.test` on first use, then cached on disk
so later commands need no extra API call; the most recently used workspace is
also remembered so read-only commands (e.g. `show --no-fetch`) work offline.

Override with `--workspace <name>` to pick a workspace explicitly, or
`--db /path/to/file.db` for a database path outside the per-workspace layout.
An existing pre-workspace `~/.cache/slackx/threads.db` is left in place;
offline reads fall back to it only while no workspace cache exists yet.

## Usage

All commands accept `-v/--verbose` for debug logging on stderr, `--db` to
override the cache location, `--workspace` to select the per-workspace cache
explicitly, and `--api-base-url` to override the Slack API
base URL (defaults to `https://slack.com/api`; also settable via
`SLACK_API_BASE_URL`).

### Threads

Cache or refresh a thread (no thread output, only a summary on stderr):

```bash
slackx fetch https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
```

Or with explicit channel/ts:

```bash
slackx fetch --channel C0123ABCDEF --ts 1700000000.123456
```

Show a cached thread (human-readable by default; use `--json` for JSON, or
`--jsonl` for the whole payload as a single compact JSON line). It auto-fetches
if the thread is missing; pass `--no-fetch` to disable that:

```bash
slackx show https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
slackx show --json https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456
slackx show --jsonl --channel C0123ABCDEF --ts 1700000000.123456 >> threads.jsonl
```

### Channel messages

Fetch all top-level messages in a channel via `conversations.history`:

```bash
slackx fetch --channel C0123ABCDEF
```

Add `--full-threads` to also fetch every reply thread for messages that have
replies:

```bash
slackx fetch --channel C0123ABCDEF --full-threads
```

### Search

Search the workspace with the same query syntax as the Slack search box. Every
matched message is cached under its `(channel, thread_ts)` so it can be revisited
later with `show`. Search is always a live API call:

```bash
slackx search "deploy failed"
slackx search "from:@alice after:2024-01-01" --json
slackx search "incident" --jsonl   # one JSON line per run, easy to append
```

Add `--full-threads` to also fetch every reply for each thread a match belongs to:

```bash
slackx search "incident" --full-threads
```

Tune result paging and ordering with `--count`, `--sort` (`score` or `timestamp`,
default `timestamp`), and `--sort-dir` (`asc` or `desc`, default `desc`).

```bash
slackx search "RFC" --count 5 --sort score --sort-dir asc
```

### Polling channels

Poll multiple channels concurrently for new messages:

```bash
slackx poll --channels C001,#general,random --interval 5m --last 5m --concurrency 3
```

Uses `httpx.AsyncClient` with an `asyncio.Semaphore` for concurrent, non-blocking
HTTP requests. Reads `X-RateLimit-Remaining` headers to proactively throttle
before hitting 429s. Add `--full-threads` to expand threads, and `--json` to get
per-cycle JSON summaries on stdout. Stops gracefully with `Ctrl+C`.

### Web UI

Browse the cache in a browser with a Slack-like interface:

```bash
slackx serve          # then open http://127.0.0.1:8280
```

Lists users and channels, renders channel messages and threads with readable
author names, and offers a `Ctrl+P` palette that full-text searches every
cached conversation (SQLite FTS5) and jumps straight to the matched message.
Refresh buttons can trigger live Slack fetches when credentials are
configured; browsing the cache itself needs none.

### Users and channels

Cache or refresh every workspace user or visible channel:

```bash
slackx fetch-users
slackx fetch-channels
```

Show cached users or channels (human-readable by default, `--json` for pretty
JSON, `--jsonl` for a single compact JSON line; both auto-fetch when empty
unless `--no-fetch` is given):

```bash
slackx show-users
slackx show-channels --json
slackx show-channels --jsonl
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

Point `slackx` at it with:

```bash
slackx --api-base-url http://localhost:8199/api fetch ...
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
