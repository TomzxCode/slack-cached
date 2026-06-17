---
title: "Fake Slack Server"
status: draft
---

# Specification: Fake Slack Server

## Overview

The fake Slack server is implemented in fake_slack.py as a standalone module
with its own CLI entry point (slack-fake-server). It generates a Workspace
object from a seed, then serves API responses via FakeSlackHandler (a
BaseHTTPRequestHandler subclass).

## Architecture

```
fake_slack.py
  |
  +-- WorkspaceParams (dataclass: seed, counts, activity_ratio, rate_limits)
  |
  +-- Workspace (pre-generated data)
  |     +-- users: list[dict]          (generated from name/role pools)
  |     +-- channels: list[dict]       (generated from channel definitions)
  |     +-- threads: dict[(ch, ts)] -> list[dict]  (generated from templates)
  |     +-- get_users_page()           (paginated)
  |     +-- get_channels_page()        (paginated, type-filtered)
  |     +-- get_thread_messages()      (paginated, oldest-filtered)
  |     +-- get_channel_history()      (paginated, oldest/latest-filtered)
  |
  +-- RateLimiter (sliding-window per endpoint)
  |     +-- check(path) -> (allowed, retry_after)
  |
  +-- FakeSlackHandler (BaseHTTPRequestHandler)
  |     +-- GET /api/conversations.replies
  |     +-- GET /api/conversations.history
  |     +-- GET /api/users.list
  |     +-- GET /api/conversations.list
  |     +-- POST /api/chat.postMessage
  |
  +-- run_server() -> HTTPServer
  +-- main() (CLI entry point with cyclopts)
```

## Data Models

### WorkspaceParams

| Field | Type | Default | Description |
|---|---|---|---|
| seed | int | 42 | Random seed for deterministic generation |
| num_users | int | 20 | Number of workspace members |
| num_channels | int | 13 | Number of channels |
| num_threads | int | 30 | Number of conversation threads |
| min_messages_per_thread | int | 3 | Minimum messages per thread |
| max_messages_per_thread | int | 12 | Maximum messages per thread |
| activity_ratio | float | 0.6 | Fraction of users who actively participate |
| rate_limits | bool | False | Enable Slack-compatible rate limiting |

### Endpoint rate limits

| Endpoint | Requests per minute |
|---|---|
| conversations.replies | 50 |
| conversations.history | 50 |
| chat.postMessage | 50 |
| users.list | 20 |
| conversations.list | 20 |

## API Contracts

The server exposes Slack-compatible JSON responses:

### GET /api/conversations.replies
- Params: channel, ts, oldest, cursor, limit
- Response: `{ok, messages, has_more, response_metadata: {next_cursor}}`

### GET /api/conversations.history
- Params: channel, oldest, latest, cursor, limit
- Response: `{ok, messages, has_more, response_metadata: {next_cursor}}`

### GET /api/users.list
- Params: cursor, limit
- Response: `{ok, members, response_metadata: {next_cursor}}`

### GET /api/conversations.list
- Params: types, cursor, limit
- Response: `{ok, channels, response_metadata: {next_cursor}}`

### POST /api/chat.postMessage
- Params: channel, text, thread_ts (optional)
- Response: `{ok, message: {ts, channel, user, text, thread_ts, ...}}`
- Behavior: Creates a new message in the specified channel (or thread if thread_ts is given), appends it to the workspace's in-memory thread data, and returns the full message dict with a generated ts

## Sequences

### Server startup

```
python -m slack_cached.fake_slack --port 8199 --seed 42
  -> Parse CLI args -> WorkspaceParams
  -> Generate Workspace(seed=42, ...)
     -> _generate_users() -> 20 realistic user dicts
     -> _generate_channels() -> 13 channel dicts
     -> _generate_threads() -> 30 thread dicts with template-based messages
  -> Create FakeSlackHandler with Workspace and optional RateLimiter
  -> HTTPServer.serve_forever()
```

### Paginated request

```
Client -> GET /api/users/list?limit=5
  -> FakeSlackHandler.do_GET()
  -> Workspace.get_users_page(cursor=None, limit=5)
     -> offset=0, page=users[0:5]
     -> next_cursor=base64("5")
  -> Response: {ok: true, members: [...], response_metadata: {next_cursor: "NQ=="}}

Client -> GET /api/users/list?cursor=NQ==&limit=5
  -> offset=5, page=users[5:10]
  -> ...
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| stdlib http.server | No external web framework | Zero additional dependencies; sufficient for a test server |
| Deterministic generation | Seeded random.Random | Same seed always produces same data, enabling reproducible tests |
| Cursor encoding | base64-encoded offset | Simple, opaque cursor that maps directly to array index |
| Sliding-window rate limiter | Thread-safe defaultdict with lock | Accurate per-endpoint limiting; thread-safe for concurrent requests |
| Template-based messages | Topic-specific conversation patterns | Generates realistic-looking conversations for testing display and parsing |

## Risks and Unknowns

1. Single-threaded HTTPServer may not handle concurrent requests well
2. No authentication validation; any token is accepted
3. Generated data may not cover all edge cases present in real Slack responses

## Out of Scope

- WebSocket/Events API support
- File upload/download simulation
- Real-time message delivery
