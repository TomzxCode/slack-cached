---
name: fake-slack-server
description: >-
  Start the fake Slack API server from slackx to provide deterministic,
  realistic workspace data for testing and development. Use when tests or
  commands need a local Slack-compatible API without real credentials.
---

# Fake Slack Server

Start the fake Slack API server bundled in the `slackx` project.
It serves deterministic, realistic workspace data (users, channels, threads)
under the same endpoint paths as the real Slack Web API, so any tool that
talks to `slack.com/api` can be pointed at it.

---

## When to use

- Running integration tests that need Slack API responses.
- Developing or debugging `slackx` CLI commands without real credentials.
- Prototyping skills or scripts that consume Slack data.
- Any situation where you need stable, repeatable Slack-like data locally.

---

## Quick start

### Start the server

From the project root (`/home/tomzx/src/slackx`):

```bash
uv run slack-fake-server --port 8199 --seed 42
```

Or as a Python module:

```bash
uv run python -m slack_cached.fake_slack --port 8199 --seed 42
```

The server binds to `127.0.0.1:8199` by default. It blocks until interrupted
with Ctrl-C.

### Start in the background (for scripts/tests)

```bash
uv run slack-fake-server --port 8199 --seed 42 &
FAKE_PID=$!
# ... run commands against it ...
kill $FAKE_PID 2>/dev/null
```

For test fixtures, start it on an ephemeral port (`--port 0`) and read back
the assigned port from stderr, or use the Python API directly (see below).

---

## Endpoints

All endpoints live under `/api/` and accept the same query parameters as the
real Slack Web API (cursor-based pagination, `limit`, etc.).

| Endpoint | Description |
|----------|-------------|
| `GET /api/users.list` | Paginated list of workspace members. |
| `GET /api/conversations.list` | Paginated list of channels. Supports `types` filter (e.g. `public_channel,private_channel`). |
| `GET /api/conversations.history` | Top-level messages (thread roots) in a channel. Requires `channel`. Supports `oldest`, `latest`, cursor pagination. Each message includes `reply_count` and `latest_reply` metadata. |
| `GET /api/conversations.replies` | Messages in a thread. Requires `channel` and `ts`. Supports `oldest` for incremental fetch and cursor pagination. |

### Example requests

```bash
# Fetch a thread (find a thread_ts from the generated workspace first)
curl -s "http://localhost:8199/api/conversations.replies?channel=C0001&ts=1704067200.000000" | python -m json.tool

# Fetch top-level messages for a channel
curl -s "http://localhost:8199/api/conversations.history?channel=C0001" | python -m json.tool
```

---

## Configuration flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `8199` | Port to listen on. Use `0` for an ephemeral port. |
| `--seed` | `42` | Random seed. Same seed always produces identical data. |
| `--num-users` | `20` | Number of workspace members to generate. |
| `--num-channels` | `13` | Number of channels. |
| `--num-threads` | `30` | Number of conversation threads. |
| `--messages-per-thread` | `3-12` | Message count per thread (`N` for exact, `N-M` for range). |
| `--activity-ratio` | `0.6` | Fraction of users who actively post. |

---

## Using with slackx CLI

Point the CLI at the fake server with `--api-base-url`:

```bash
uv run slackx fetch-users --api-base-url http://localhost:8199/api
uv run slackx fetch-channels --api-base-url http://localhost:8199/api
uv run slackx show-users --api-base-url http://localhost:8199/api
uv run slackx show-channels --api-base-url http://localhost:8199/api
SLACK_TOKEN=xoxb-fake uv run slackx fetch-channel-messages --channel C0001 --api-base-url http://localhost:8199/api
SLACK_TOKEN=xoxb-fake uv run slackx fetch-channel-messages --channel C0001 --full-threads --api-base-url http://localhost:8199/api
```

No real Slack token is needed; the fake server accepts any Authorization header.

For `fetch`/`show` commands that target a specific thread, you need the
channel id and thread timestamp from the generated workspace. Inspect the
generated data by querying the list endpoints first.

---

## Python API

Use the `Workspace` and `run_server` classes/functions directly in Python
(for test fixtures, scripts, etc.):

```python
from slack_cached.fake_slack import Workspace, WorkspaceParams, run_server

# Generate workspace data without starting a server
params = WorkspaceParams(seed=42, num_users=10, num_channels=5, num_threads=15)
workspace = Workspace(params=params)
print(len(workspace.users))   # 10
print(len(workspace.channels))  # 5
print(len(workspace.threads))   # 15

# Start an HTTP server
server = run_server(host="127.0.0.1", port=0, params=params)
port = server.server_address[1]
# ... make requests to http://127.0.0.1:{port}/api/ ...
server.shutdown()
```

### Test fixture pattern

```python
import threading
import time
from http.server import HTTPServer
from slack_cached.fake_slack import FakeSlackHandler, Workspace

workspace = Workspace(seed=42)
FakeSlackHandler.workspace = workspace
server = HTTPServer(("127.0.0.1", 0), FakeSlackHandler)
port = server.server_address[1]
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
time.sleep(0.1)
base_url = f"http://127.0.0.1:{port}"
# use base_url in tests...
server.shutdown()
```

---

## Data stability

The same seed always produces the same workspace. Users, channels, and threads
are fully deterministic. This means:

- Tests are reproducible across runs.
- The same `thread_ts` values appear for a given seed, so hardcoded fixtures
  in tests remain valid.
- Change the seed to get a different workspace without changing the schema.
