---
title: "Per-Workspace Cache Database"
status: draft
---

# Requirements: Per-Workspace Cache Database

## Overview

Each Slack workspace caches to its own SQLite database under
`~/.cache/slackx/<workspace>/threads.db`, so channel ids, user ids, and
timestamps from different workspaces never collide in one database.
The workspace is discovered automatically via auth.test, and the most
recently used workspace is remembered so read-only commands work offline.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| Developer | Caches several Slack workspaces without data collisions |
| Future readers of the cache | Expect each database to hold data from a single workspace |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall store each workspace's cache in its own database at `<cache>/<workspace>/threads.db` |
| FR-02 | Must | The system shall resolve the workspace identity via auth.test, preferring the URL subdomain and falling back to the team id |
| FR-03 | Must | The system shall record the most recently used workspace so read-only commands can resolve their database without network access |
| FR-04 | Must | The system shall accept `--workspace <name>` on every command to select a workspace database explicitly |
| FR-05 | Must | The system shall treat `--db <path>` as a full override that bypasses workspace resolution |
| FR-06 | Must | The system shall fail with guidance when several workspace databases exist and no last-used workspace is recorded |
| FR-07 | Must | The system shall never migrate, move, or rename an existing pre-workspace `threads.db` into the per-workspace layout |
| FR-08 | Should | Offline resolution shall fall back to the legacy single `threads.db` only while no workspace database exists |
| FR-09 | Should | The fake Slack server shall expose an auth.test endpoint returning the generated workspace identity |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Correctness | Cached rows from different workspaces shall never mix in one database |
| NFR-02 | Should | Performance | `show --no-fetch` shall make no network requests |
| NFR-03 | Should | Performance | Workspace resolution shall add at most one cached auth.test call per command invocation |

## Constraints

- One set of credentials is configured at a time; switching workspaces means switching credentials
- The SQLite single-writer constraint applies per workspace database, unchanged

## Acceptance Criteria

- [ ] FR-01: A fetch without `--db` writes to `~/.cache/slackx/<workspace>/threads.db`
- [ ] FR-02: An auth.test response with `url: https://acme.slack.com/` selects the `acme` database
- [ ] FR-03: `show --no-fetch` after a fetch reads the last-used workspace database without building a client
- [ ] FR-06: Two workspace databases without a pointer make offline reads fail listing the known workspaces
- [ ] FR-07: A pre-existing legacy `threads.db` is still in place, untouched, after workspace-aware fetches

## Open Questions

1. Should a `slackx workspaces` command list known workspace databases?
2. Should the web UI offer a workspace switcher to browse across databases?
