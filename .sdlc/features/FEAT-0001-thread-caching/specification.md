---
title: "Thread Caching and Display"
status: draft
---

# Specification: Thread Caching and Display

## Overview

Threads are fetched via SlackClient.iter_thread_replies(), which handles cursor
pagination and rate-limit retry. The cache layer (cache.py) decides whether to
do a full or incremental fetch based on stored ThreadState, then writes messages
via storage.upsert_messages(). Display is handled by the CLI layer, which renders
either human-readable text or JSON.

## Architecture

```
CLI (cli.py)
  |
  +-- fetch command -> cache.fetch_thread() -> slack_api.iter_thread_replies()
  |                                         -> storage.upsert_messages()
  |                                         -> storage.record_thread_refresh()
  |
  +-- show command  -> storage.load_thread_messages()
                       storage.load_user_display_names()
                       render (_render_human or _render_json)
```

## Data Models

### ThreadState

| Field | Type | Constraints | Description |
|---|---|---|---|
| channel | str | PK part | Slack channel id |
| thread_ts | str | PK part | Thread root message timestamp |
| last_fetched | float | not null | Unix epoch of last fetch |
| latest_reply | str | nullable | Highest ts seen so far |

### CachedMessage

| Field | Type | Constraints | Description |
|---|---|---|---|
| ts | str | PK part | Message timestamp |
| user | str | nullable | Author user id |
| text | str | nullable | Message text content |
| payload | dict | not null | Full Slack message JSON |

### messages table

| Column | Type | Constraints | Description |
|---|---|---|---|
| channel | TEXT | PK part, FK -> threads | Channel id |
| thread_ts | TEXT | PK part, FK -> threads | Thread root ts |
| ts | TEXT | PK part | Message ts |
| user | TEXT | nullable | Author id |
| text | TEXT | nullable | Extracted text |
| payload | TEXT | not null | Full JSON |

## API Contracts

No REST API is exposed. The CLI subcommands serve as the interface:

### fetch

- Input: URL or --channel/--ts, optional --db, optional -v
- Output (stderr): "cached N messages (M new/updated, incremental/full) for CHANNEL/TS"
- Exit code: 0 on success, SystemExit on error

### show

- Input: URL or --channel/--ts, optional --db, optional --json, optional --no-fetch, optional -v
- Output (stdout): human-readable text or JSON
- Exit code: 0 on success

## Sequences

### Incremental thread refresh

```
User -> cli fetch <URL>
  -> urls.parse_thread_url() -> ThreadRef
  -> storage.get_thread_state() -> ThreadState(latest_reply="...")
  -> slack_api.iter_thread_replies(oldest="...") -> new messages
  -> storage.record_thread_refresh(latest_reply=...)
  -> storage.upsert_messages(new_messages)
  -> print summary
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Storage backend | SQLite | Zero-config, single-file, supports upsert via INSERT OR REPLACE |
| Message storage | Extracted fields + full JSON payload | Efficient querying on ts/user/text without re-parsing, payload preserves all data |
| Incremental strategy | Pass oldest=latest_reply to API | Minimizes data transfer on refresh; Slack returns new replies + possibly one edit |
| Lazy import of SlackClient | Import in cmd_fetch/cmd_show only | show --no-fetch avoids loading requests entirely |

## Risks and Unknowns

1. Slack API may change pagination behavior or rate-limit policies
2. Very large threads (thousands of messages) may be slow to fetch/display
3. No transaction isolation if multiple processes write to the same database

## Out of Scope

- Message search or filtering
- Attachment or file caching
- Real-time updates (WebSockets/Events API)
