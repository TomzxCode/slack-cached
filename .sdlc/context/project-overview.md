# Project Overview

## Purpose

slack-cached is a Python CLI that caches Slack threads, users, and channels to a
local SQLite database. It solves the problem of needing offline, queryable access
to Slack conversations without relying on Slack's workspace export or paying for
API access at scale. Given a Slack thread URL (or explicit channel id and root
timestamp), it fetches the thread via the conversations.replies API and stores
every message. On subsequent runs it only fetches new replies by passing `oldest`
to the API, making incremental refreshes efficient. It also supports fetching
entire channel message histories via conversations.history, with optional full
thread reply expansion. A fake Slack server is included for integration testing
and development without hitting the real Slack API. Each Slack workspace gets
its own cache database, so several workspaces can be cached without id
collisions.

## Key Stakeholders

| Stakeholder | Role | Interest |
|---|---|---|
| Developer / researcher | Author and primary user | Reliable local cache of Slack threads for analysis, archival, or offline reading |
| Slack workspace members | Data subjects | Their messages are fetched and stored locally |

Named individuals could not be determined from the code alone. The author listed
in pyproject.toml is Tom Rochette.

## Scope

**In scope:**
- Fetching and caching individual Slack threads via permalink or channel/ts
- Incremental refresh of previously cached threads (only new/edited messages)
- Fetching channel message histories via conversations.history with optional full thread reply expansion
- Bulk caching of all workspace users and visible channels
- Human-readable and JSON output of cached threads, users, and channels
- Resolution of user ids to display names in thread output
- Credential management via environment variables and config file
- Per-workspace cache databases with the workspace discovered automatically via auth.test
- Local full-text search over cached messages, plus live search via search.messages
- Configurable API base URL for testing against a fake server
- Fake Slack API server with deterministic workspace data generation for testing

**Out of scope:**
- Writing or sending messages to Slack
- Real-time event streaming (Socket Mode, Events API)
- Simultaneous multi-token management (one set of credentials is configured at a time; each workspace keeps its own cache database)
- Web UI or API server (production)
- Caching of files, attachments, or reactions

## Key Constraints

- Slack API rate limits: the client retries up to 5 times on HTTP 429 with
  exponential backoff based on the Retry-After header
- Authentication requires either a bot token (xoxb) or a browser token (xoxc)
  with its matching d cookie (xoxd), both from the same browser session
- SQLite is single-writer; concurrent writes from multiple processes are not
  supported
- The tool runs as a single-user CLI with no server component (except the fake server for testing)
- Python 3.13+ is required

