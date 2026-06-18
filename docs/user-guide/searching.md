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

The summary reports the breakdown of threads and messages into ones already
present in the cache versus ones newly written on this run:

```
searched 'RFC': 12 match(es), 9 thread(s) (0 existing, 9 new), 17 message(s) (0 existing, 17 new)
```

Running the same command again while nothing changed reports every previously
cached item as existing:

```
searched 'RFC': 12 match(es), 9 thread(s) (9 existing, 0 new), 17 message(s) (17 existing, 0 new)
```

Note: search is always a live API call against Slack. "existing" means the data
we got back matched what was already in the local cache; it is not a true cache
hit (we did not skip the Slack call).

With `--full-threads`, the message count includes every fetched reply (parents
plus thread replies), and the breakdown reflects those too. Slack decorates the
same message slightly differently between `search.messages` and
`conversations.replies` (different `blocks` ids, signed image URLs, team
metadata, search-highlighted `text`, etc.); the comparison normalizes those
away so the same message still counts as existing. If you ever see unexpectedly
high "new" counts on a rerun, run with `--verbose` and look for
`message_payload_diff` log lines, which show the field-level diff for every
message that was considered changed.

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
