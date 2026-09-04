# Fake Slack server

The fake Slack server provides a deterministic, local HTTP server that mimics the Slack Web API for testing and development without hitting the real Slack API.

## Starting the server

```bash
slack-fake-server
```

This starts a server on `127.0.0.1:8199` with default settings (seed 42, 20 users, 13 channels, 30 threads).

## Configuration options

```bash
slack-fake-server [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8199` | Port to listen on |
| `--seed` | `42` | Random seed for deterministic data |
| `--num-users` | `20` | Number of workspace members |
| `--num-channels` | `13` | Number of channels |
| `--num-threads` | `30` | Number of conversation threads |
| `--messages-per-thread` | `3-12` | Messages per thread (`N` or `N-M` range) |
| `--activity-ratio` | `0.6` | Fraction of users who actively post |
| `--rate-limits` | off | Enable Slack-compatible rate limiting |
| `--epoch-base` | `2024-01-01` | Base timestamp (`now`, ISO datetime, or unix timestamp) |

## Supported API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/conversations.replies` | Fetch thread replies |
| GET | `/api/conversations.history` | Fetch channel messages |
| GET | `/api/users.list` | List workspace users |
| GET | `/api/conversations.list` | List channels |
| POST | `/api/chat.postMessage` | Post a new message or reply |

All endpoints return Slack-compatible JSON with cursor-based pagination.

## Usage with slackx

Point slackx at the fake server using `--api-base-url`:

```bash
slack-fake-server &
slackx --api-base-url http://localhost:8199/api fetch --channel C00000001
```

When using a non-default API base URL, credentials are not required.

## Deterministic data

The server uses a seeded random number generator, so the same seed always produces the same workspace data. This is useful for reproducible tests:

```bash
slack-fake-server --seed 42 --num-threads 10
```

## Rate limiting

Enable Slack-compatible rate limiting with `--rate-limits`:

```bash
slack-fake-server --rate-limits
```

When enabled, requests that exceed the per-endpoint rate limit receive HTTP 429 with a `Retry-After` header, matching Slack's documented behavior.

## Posting messages

The fake server supports `POST /api/chat.postMessage` for creating new threads or replying to existing ones:

```bash
curl -X POST http://localhost:8199/api/chat.postMessage \
  -d "channel=C00000001" \
  -d "text=Hello world"
```

Reply to an existing thread:

```bash
curl -X POST http://localhost:8199/api/chat.postMessage \
  -d "channel=C00000001" \
  -d "text=Replying to thread" \
  -d "thread_ts=1704067200.000001"
```
