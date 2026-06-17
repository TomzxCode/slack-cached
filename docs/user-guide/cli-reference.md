# CLI reference

## Global options

| Option | Description |
|---|---|
| `--db PATH` | SQLite cache database path (default: `~/.cache/slack-cached/threads.db`) |
| `--api-base-url URL` | Slack API base URL (default: `https://slack.com/api`) |
| `-v`, `--verbose` | Enable debug logging |

## Subcommands

### fetch

Cache or refresh a Slack thread, or fetch messages from a channel.

```bash
slack-cached fetch [URL] [--channel CHANNEL] [--ts TS] [--full-threads] [--last DURATION]
```

| Argument | Description |
|---|---|
| `URL` | Slack thread permalink URL |
| `--channel CHANNEL` | Channel ID (use without `--ts` for channel message fetch) |
| `--ts TS` | Thread root timestamp |
| `--full-threads` | Also fetch all thread replies (channel fetch only) |
| `--last DURATION` | Lookback period for channel fetch (default: `1d`) |

### show

Print a cached thread or channel to stdout.

```bash
slack-cached show [URL] [--channel CHANNEL] [--ts TS] [--json | --jsonl] [--no-fetch] [--last DURATION]
```

| Argument | Description |
|---|---|
| `URL` | Slack thread permalink URL |
| `--channel CHANNEL` | Channel ID (shows all channel messages without `--ts`) |
| `--ts TS` | Thread root timestamp |
| `--json` | Output as pretty-printed JSON |
| `--jsonl` | Output as a single compact JSON line (mutually exclusive with `--json`) |
| `--no-fetch` | Do not auto-fetch if not cached |
| `--last DURATION` | Lookback period for channel display (default: `1d`) |

### search

Search Slack via `search.messages` and cache every matched message/thread.

```bash
slack-cached search QUERY [--count N] [--sort score|timestamp] [--sort-dir asc|desc] [--full-threads] [--json | --jsonl]
```

| Argument | Description |
|---|---|
| `QUERY` | Slack search query (same syntax as the Slack search box, required) |
| `--count N` | Maximum results per page (default: `20`) |
| `--sort` | Sort by `score` or `timestamp` (default: `timestamp`) |
| `--sort-dir` | Sort direction, `asc` or `desc` (default: `desc`) |
| `--full-threads` | Also fetch all replies for every thread a match belongs to |
| `--json` | Output as pretty-printed JSON |
| `--jsonl` | Output as a single compact JSON line (mutually exclusive with `--json`) |

Search is always a live API call. Every matched message is cached under its
`(channel, thread_ts)` so it can be revisited later with `show`.

### fetch-users

Cache all workspace users.

```bash
slack-cached fetch-users
```

### fetch-channels

Cache all visible channels.

```bash
slack-cached fetch-channels
```

### show-users

Print cached users.

```bash
slack-cached show-users [--json | --jsonl] [--no-fetch]
```

### show-channels

Print cached channels.

```bash
slack-cached show-channels [--json | --jsonl] [--no-fetch]
```

### poll

Poll channels concurrently in a loop for new messages.

```bash
slack-cached poll --channels CHANNELS [--interval DURATION] [--last DURATION] [--full-threads] [--concurrency N] [--json]
```

| Argument | Description |
|---|---|
| `--channels CHANNELS` | Comma-separated list of channels (required). Each entry may be a channel id (`C001`), a bare name (`general`), or a `#`-prefixed name (`#general`). Names are resolved against the cached channels. |
| `--interval DURATION` | Time between poll cycles (default: `5m`) |
| `--last DURATION` | Lookback period per cycle (default: `5m`, use `all` for full history) |
| `--full-threads` | Also fetch all thread replies for every threaded message |
| `--concurrency N` | Maximum number of channels fetched concurrently (default: `3`) |
| `--json` | Emit per-cycle JSON summaries to stdout |

Uses `httpx.AsyncClient` for non-blocking concurrent HTTP requests. An
`asyncio.Semaphore` caps concurrent in-flight requests to avoid overwhelming
Slack's rate limit. The client also reads `X-RateLimit-Remaining` headers from
every response and proactively waits for the rate limit window to reset when
nearly exhausted.

Stops gracefully on `Ctrl+C`.

## Duration format

The `--last` flag accepts duration strings:

| Format | Example | Meaning |
|---|---|---|
| `Nh` | `3h` | N hours |
| `Nd` | `7d` | N days |
| `Nm` | `90m` | N minutes |
| Combined | `2d5h30m` | 2 days, 5 hours, 30 minutes |
| `all` | `all` | No time limit |
