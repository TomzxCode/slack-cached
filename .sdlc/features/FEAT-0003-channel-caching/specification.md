---
title: "Channel Caching and Display"
status: draft
---

# Specification: Channel Caching and Display

## Overview

Channels are fetched via SlackClient.iter_channels(), which wraps
conversations.list with cursor-based pagination and configurable channel types.
The cache layer writes channels via storage.upsert_channels().

## Architecture

```
CLI (cli.py)
  |
  +-- fetch-channels -> cache.fetch_channels() -> slack_api.iter_channels()
  |                                            -> storage.upsert_channels()
  |
  +-- show-channels  -> storage.load_channels() -> render
```

## Data Models

### CachedChannel

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | str | PK | Slack channel id (e.g. C123) |
| name | str | nullable | Channel name |
| is_private | bool | nullable | True if private, False if public, null if unknown |
| fetched_at | float | not null | Unix epoch of last fetch |
| payload | dict | not null | Full Slack channel JSON |

### channels table

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | TEXT | PK | Slack channel id |
| name | TEXT | nullable | Channel name |
| is_private | INTEGER | nullable | 1=private, 0=public, null=unknown |
| fetched_at | REAL | not null | Unix epoch |
| payload | TEXT | not null | Full JSON |

## API Contracts

### fetch-channels

- Input: optional --db, optional -v
- Output (stderr): "processed N channels (M added, T total in db)"
- Exit code: 0 on success

### show-channels

- Input: optional --db, optional --json, optional --no-fetch, optional -v
- Output (stdout): human-readable text or JSON
- Exit code: 0 on success

## Sequences

### Channel fetch

```
User -> cli fetch-channels
  -> slack_api.iter_channels(types="public_channel,private_channel,mpim,im")
     -> paginated channel dicts via cursor
  -> storage.upsert_channels() -> INSERT OR REPLACE
  -> print summary
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Default channel types | All types (public, private, mpim, im) | Maximizes coverage; users see every conversation visible to their token |
| is_private storage | Nullable INTEGER | Matches SQLite's lack of a native boolean; null handles unknown visibility |
| Shared pagination logic | _iter_cursor() helper in SlackClient | Users and channels both use cursor-based pagination with identical structure |

## Risks and Unknowns

1. Workspaces with thousands of channels may have slow initial fetch
2. Direct message channels (im/mpim) may expose sensitive conversation metadata

## Out of Scope

- Channel creation or modification
- Channel membership tracking
- Message posting to channels
