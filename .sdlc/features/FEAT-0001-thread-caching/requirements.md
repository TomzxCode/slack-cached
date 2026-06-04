---
title: "Thread Caching and Display"
status: draft
---

# Requirements: Thread Caching and Display

## Overview

Provides the core capability to fetch Slack conversation threads from the API,
store them in a local SQLite cache, and display them in human-readable or JSON
format. Supports incremental refreshes that only fetch new or edited messages.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| Developer / researcher | Needs reliable, offline access to Slack thread content |
| CLI user | Wants to quickly view thread content without opening Slack |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall accept a Slack thread permalink URL and parse it into a channel id and thread root timestamp |
| FR-02 | Must | The system shall accept explicit --channel and --ts arguments as an alternative to a URL |
| FR-03 | Must | The system shall fetch all messages in a thread via the Slack conversations.replies API |
| FR-04 | Must | The system shall store fetched messages in a local SQLite database, upserting by (channel, thread_ts, ts) |
| FR-05 | Must | The system shall record the thread's latest reply timestamp and last-fetched time in the database |
| FR-06 | Must | The system shall perform incremental fetches when a thread is already cached, passing oldest=latest_reply to the API |
| FR-07 | Must | The system shall display cached threads in a human-readable format with timestamp, author, and message text |
| FR-08 | Must | The system shall display cached threads in JSON format when --json is passed |
| FR-09 | Must | The system shall auto-fetch a thread when show is called for a thread not yet cached, unless --no-fetch is given |
| FR-10 | Should | The system shall resolve user ids to display names when cached users are available |
| FR-11 | Should | The system shall handle Slack API pagination via cursor-based iteration |
| FR-12 | Should | The system shall retry on HTTP 429 rate-limit responses using the Retry-After header |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Performance | Incremental fetches shall only transfer new/edited messages from the API |
| NFR-02 | Should | Reliability | The system shall handle API errors with clear, actionable error messages |
| NFR-03 | Should | Usability | The fetch command shall print a summary to stderr (total, new, incremental/full) |

## Constraints

- Messages are stored as full JSON in a `payload` column alongside extracted fields
- SQLite single-writer limitation means concurrent fetches are not supported

## Acceptance Criteria

- [ ] FR-01: Given a valid Slack permalink, the system parses it into a ThreadRef
- [ ] FR-03: fetch retrieves all messages from Slack and stores them in SQLite
- [ ] FR-04: Running fetch twice on the same thread upserts messages without duplication
- [ ] FR-06: A second fetch passes oldest=latest_reply and only receives new messages
- [ ] FR-07: show outputs human-readable text with timestamps, authors, and message text
- [ ] FR-08: show --json outputs valid JSON with channel, thread_ts, message_count, and messages
- [ ] FR-10: User ids in thread output are replaced with display names when cached users exist

## Open Questions

1. Should there be a limit on how many messages are stored per thread?
2. Should there be a TTL or automatic eviction of old cached threads?
