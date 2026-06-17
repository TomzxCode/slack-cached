# Searching messages

## Basic usage

Search the workspace using the same query syntax as the Slack search box. Every
matched message is cached under its `(channel, thread_ts)` so it can be revisited
later with `show`:

```bash
slack-cached search "deploy failed"
```

Search is always a live API call against `search.messages`. It both prints the
matches and writes them to the cache.

## Output formats

Human-readable by default:

```bash
slack-cached search "incident"
```

JSON with `--json`:

```bash
slack-cached search "incident" --json
```

Each match includes its `channel`, `channel_name` (when cached), `ts`, `thread_ts`,
`user`, `user_name` (when cached), `text`, and `permalink`.

## Full thread expansion

Add `--full-threads` to fetch all replies for every thread a match belongs to:

```bash
slack-cached search "RFC" --full-threads
```

The summary reports how many distinct threads were expanded:

```
searched 'RFC': 12 match(es), 9 thread(s) cached
```

## Paging and ordering

Control result paging and ordering:

```bash
# Up to 5 results per page, ranked by relevance, oldest first
slack-cached search "RFC" --count 5 --sort score --sort-dir asc
```

| Flag | Values | Default |
|---|---|---|
| `--count N` | Max results per page | `20` |
| `--sort` | `score` or `timestamp` | `timestamp` |
| `--sort-dir` | `asc` or `desc` | `desc` |

## Revisiting matched threads

Because each match is cached, you can re-open it later without searching again:

```bash
slack-cached search "outage" --json   # note the channel and thread_ts
slack-cached show --channel C01234 --ts 1700000000.123456
```

When `--full-threads` was used, the entire thread (not just the matching
message) is already in the cache.
