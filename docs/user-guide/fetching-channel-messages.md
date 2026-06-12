# Fetching channel messages

## Basic usage

Fetch all top-level messages from a channel:

```bash
slack-cached fetch --channel C01234
```

This uses the Slack `conversations.history` API to retrieve top-level messages (standalone messages and thread parents). Thread replies are not included unless you use `--full-threads`.

## Full thread expansion

Add `--full-threads` to also fetch all replies for every threaded conversation found in the channel:

```bash
slack-cached fetch --channel C01234 --full-threads
```

The summary includes thread expansion stats:

```
cached 42 messages for C01234 (42 fetched, 8 threads with replies fetched)
```

## Time-bounded fetch

Use `--last` to limit the lookback period. Accepts duration strings like `24h`, `2d5h30m`, `90m`, or `all` for full history:

```bash
# Last 3 days
slack-cached fetch --channel C01234 --last 3d

# Last 2 hours
slack-cached fetch --channel C01234 --last 2h

# Full history
slack-cached fetch --channel C01234 --last all
```

The default lookback is `1d` (one day).

## Showing cached channel messages

Display all cached messages for a channel:

```bash
slack-cached show --channel C01234
```

Filter by time with `--last`:

```bash
slack-cached show --channel C01234 --last 7d
```

Display as JSON:

```bash
slack-cached show --channel C01234 --json
```

Channel messages are auto-fetched if the cache is empty (unless `--no-fetch` is given).
