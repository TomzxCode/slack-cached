---
title: "User Caching and Display"
status: draft
---

# Specification: User Caching and Display

## Overview

Users are fetched via SlackClient.iter_users(), which wraps users.list with
cursor-based pagination. The cache layer writes users via storage.upsert_users().
Display name resolution uses storage.load_user_display_names(), which queries only
the specific user ids needed for a given thread.

## Architecture

```
CLI (cli.py)
  |
  +-- fetch-users -> cache.fetch_users() -> slack_api.iter_users()
  |                                      -> storage.upsert_users()
  |
  +-- show-users  -> storage.load_users() -> render
  |
  +-- (during show) storage.load_user_display_names(user_ids)
```

## Data Models

### CachedUser

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | str | PK | Slack user id (e.g. U123) |
| name | str | nullable | User handle |
| real_name | str | nullable | Display/real name |
| fetched_at | float | not null | Unix epoch of last fetch |
| payload | dict | not null | Full Slack user JSON |

### users table

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | TEXT | PK | Slack user id |
| name | TEXT | nullable | Handle |
| real_name | TEXT | nullable | Display name (falls back to profile.real_name) |
| fetched_at | REAL | not null | Unix epoch |
| payload | TEXT | not null | Full JSON |

## API Contracts

### fetch-users

- Input: optional --db, optional -v
- Output (stderr): "processed N users (M added, T total in db)"
- Exit code: 0 on success

### show-users

- Input: optional --db, optional --json, optional --no-fetch, optional -v
- Output (stdout): human-readable text or JSON
- Exit code: 0 on success

## Sequences

### User fetch and display name resolution

```
User -> cli fetch-users
  -> slack_api.iter_users() -> paginated user dicts
  -> storage.upsert_users() -> INSERT OR REPLACE
  -> print summary

During show:
  -> storage.load_user_display_names([U1, U2, ...])
     -> SELECT id, name, real_name FROM users WHERE id IN (...)
     -> format as "Real Name (handle)" / "handle" / "U1"
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| real_name source | Top-level real_name, falling back to profile.real_name | Slack API is inconsistent about where the real name lives |
| Display name format | "Real Name (handle)" | Provides both the human-readable name and the unique handle |
| Chunk size for IN queries | 900 | Stays under SQLite's default 999 bound-variable limit |
| Upsert strategy | INSERT OR REPLACE by id | Idempotent; re-running fetch-users updates stale data |

## Risks and Unknowns

1. Large workspaces with tens of thousands of users may have slow initial fetch
2. Slack API may return different user object shapes across workspace types

## Out of Scope

- User profile pictures
- User presence/status
- User group management
