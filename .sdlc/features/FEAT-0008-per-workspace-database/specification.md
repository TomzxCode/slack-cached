---
title: "Per-Workspace Cache Database"
status: draft
---

# Specification: Per-Workspace Cache Database

## Overview

Workspace resolution lives in workspace.py, a small module beside config.py.
The CLI client helpers (`_client.py`) resolve the database path before opening
a connection: an explicit `--db` wins, then `--workspace`, then auth.test when
a Slack client is available, then offline resolution from the last-used
pointer.

## Architecture

```
workspace.py
  |
  +-- workspace_dir(name)          (<cache>/<sanitized name>/)
  +-- workspace_db_path(name)      (<cache>/<name>/threads.db)
  +-- workspace_name_from_auth()   (auth.test payload -> workspace name)
  +-- remember_last_workspace() / last_workspace()
  +-- claim_workspace_db()         (path + pointer, never moves files)
  +-- existing_workspace_dbs()
  +-- offline_db_path()            (pointer -> single db -> legacy threads.db)

_client.py
  +-- _resolve_db_path(common, client)   (db > workspace > auth.test > offline)
  +-- _open_db(common, client)           (async context manager, resolved path)
  +-- _open_db_at(path)                  (known path, e.g. the poll loop)
```

## Data Models

### Cache layout

| Path | Purpose |
|---|---|
| `~/.cache/slackx/<workspace>/threads.db` | One SQLite cache per workspace |
| `~/.cache/slackx/last_workspace` | Pointer file holding the last-used workspace name |
| `~/.cache/slackx/workspace_names.json` | Token+base-URL hash to workspace name, avoiding repeated auth.test calls |

### auth.test payload fields used

| Field | Use |
|---|---|
| `url` | Subdomain extraction (`acme` from `https://acme.slack.com/`) |
| `team_id` | Fallback workspace name when no subdomain is available |

## Resolution Rules

1. `--db <path>`: use the path as given; no workspace logic.
2. `--workspace <name>`: use `<name>/threads.db` and remember it as last used.
3. Credentials configured: look up the workspace name in
   `workspace_names.json` (keyed by token and API base URL hash); on a hit,
   claim that workspace's database immediately.
4. Cache miss: one auth.test call, store the resulting name, then claim it.
5. Otherwise (no credentials, or auth.test unreachable for `serve`): offline
   resolution, last-used workspace, then the only existing workspace
   database, then the legacy single `threads.db`.
6. Multiple workspace databases with no pointer: exit with an error listing
   the known workspace names.

## Sequences

### Fetch (network command)

```
slackx fetch --channel C1 --ts T
  -> _open_client() builds SlackClient
  -> _open_db(common, client)
     -> client.auth_test() (cached per client)
     -> workspace_name_from_auth() -> "acme"
     -> claim_workspace_db("acme") -> cache/acme/threads.db + pointer
  -> fetch_thread writes into acme's database
```

### Offline read

```
slackx show --no-fetch --channel C1 --ts T
  -> _open_db(common) without client
  -> last_workspace() -> "acme" -> cache/acme/threads.db
  -> read thread state and messages; no network
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Workspace identity | auth.test subdomain, team id fallback | Same resolution for every command; permalink subdomains only cover URL-based commands |
| auth.test caching | Once per credential set on disk (hashed key) | A token belongs to exactly one workspace, so later calls resolve without network |
| Pointer file | Plain text `last_workspace` | Trivially readable; enables offline database resolution |
| Directory per workspace | `<workspace>/threads.db` | Keeps the familiar filename and leaves room for per-workspace sidecar files |
| No migration | Legacy `threads.db` is never moved | Avoids mislabeling data when credentials may belong to any workspace; users can move it manually |
| Ambiguity | Fail with workspace list | Silent fallback to a stale database would be worse than an explicit `--workspace` |

## Risks and Unknowns

1. Invalid or expired credentials fail auth.test before any fetch, which is
   detectable but changes the failure surface of network commands
2. Renaming a workspace subdomain in Slack creates a second cache directory
3. A stale workspace_names.json entry persists after the cache directory is
   wiped by hand; the next successful auth.test rewrites it

## Out of Scope

- Multi-token management (several credential sets configured at once)
- Migrating or merging the legacy single `threads.db`
- A `workspaces` listing command
