# Caching channels

## Fetching channels

Download all visible conversations (public channels, private channels, MPIMs, and DMs) to the local cache:

```bash
slack-cached fetch-channels
```

The command prints a summary to stderr:

```
processed 45 channels (45 added, 45 total in db)
```

Running it again updates existing records without duplication.

## Showing cached channels

Display all cached channels in human-readable format:

```bash
slack-cached show-channels
```

Output shows channel ID, name, and visibility (public/private).

Display as JSON:

```bash
slack-cached show-channels --json
```

Channels are auto-fetched if the cache is empty (unless `--no-fetch` is given).
