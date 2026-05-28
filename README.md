# slack-cached

A small Python CLI that caches Slack threads, users, and channels to a local
SQLite database.

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
   ```

## Cache location

The default cache database lives at
`$XDG_CACHE_HOME/slack-cached/threads.db` (or `~/.cache/slack-cached/threads.db`).
Override with `--db /path/to/file.db`.

## Usage

All commands accept `-v/--verbose` for debug logging on stderr and `--db` to
override the cache location.

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
display names instead of raw user ids.

## Refresh behavior

`fetch` always reaches out to Slack.
If the thread is already cached, it requests `conversations.replies` with
`oldest=<latest_cached_ts>` so the API returns only new replies (and any
recent edits at that boundary).
Messages are upserted by `ts`, so edits replace the older version in place.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
```
