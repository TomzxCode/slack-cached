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
slack-cached show [URL] [--channel CHANNEL] [--ts TS] [--json] [--no-fetch] [--last DURATION]
```

| Argument | Description |
|---|---|
| `URL` | Slack thread permalink URL |
| `--channel CHANNEL` | Channel ID (shows all channel messages without `--ts`) |
| `--ts TS` | Thread root timestamp |
| `--json` | Output as JSON |
| `--no-fetch` | Do not auto-fetch if not cached |
| `--last DURATION` | Lookback period for channel display (default: `1d`) |

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
slack-cached show-users [--json] [--no-fetch]
```

### show-channels

Print cached channels.

```bash
slack-cached show-channels [--json] [--no-fetch]
```

### poll

Poll channels in a loop for new messages.

```bash
slack-cached poll --channels CHANNELS [--interval DURATION] [--last DURATION] [--full-threads] [--json]
```

| Argument | Description |
|---|---|
| `--channels CHANNELS` | Comma-separated list of channel IDs (required) |
| `--interval DURATION` | Time between poll cycles (default: `5m`) |
| `--last DURATION` | Lookback period per cycle (default: `5m`, use `all` for full history) |
| `--full-threads` | Also fetch all thread replies for every threaded message |
| `--json` | Emit per-cycle JSON summaries to stdout |

Polls all channels sequentially each cycle, reusing a single database connection and API client. Stops gracefully on `Ctrl+C`. When `--json` is passed, each cycle emits a JSON line to stdout with the cycle number, elapsed time, and per-channel fetch counts.

## Duration format

The `--last` flag accepts duration strings:

| Format | Example | Meaning |
|---|---|---|
| `Nh` | `3h` | N hours |
| `Nd` | `7d` | N days |
| `Nm` | `90m` | N minutes |
| Combined | `2d5h30m` | 2 days, 5 hours, 30 minutes |
| `all` | `all` | No time limit |
