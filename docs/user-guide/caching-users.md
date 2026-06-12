# Caching users

## Fetching users

Download all workspace members to the local cache:

```bash
slack-cached fetch-users
```

The command prints a summary to stderr:

```
processed 150 users (150 added, 150 total in db)
```

Running it again updates existing records without duplication (upsert by user ID).

## Showing cached users

Display all cached users in human-readable format:

```bash
slack-cached show-users
```

Display as JSON:

```bash
slack-cached show-users --json
```

Users are auto-fetched if the cache is empty (unless `--no-fetch` is given).

## Display name resolution

When users are cached, thread and channel output resolves user IDs to display names in the format "Real Name (handle)". For example, a message from user `U123` appears as:

```
[2024-01-15 10:30:00] Alice Smith (alice)
  Hey, I've reviewed the PR.
```

Without cached users, the raw user ID is shown instead.
