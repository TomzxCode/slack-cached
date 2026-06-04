---
title: "Channel Message Fetching"
status: draft
---

# Requirements: Channel Message Fetching

## Overview

Provides the ability to fetch all top-level messages from a Slack channel via
the conversations.history API and cache them locally. Optionally fetches full
thread replies for every threaded conversation found in the channel.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| CLI user | Wants to bulk-archive an entire channel's conversations, not just individual threads |
| Developer | Needs channel-level message retrieval for data analysis or migration |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall accept a --channel argument and fetch top-level messages via the Slack conversations.history API |
| FR-02 | Must | The system shall store fetched messages in the local SQLite cache, one thread row per top-level message |
| FR-03 | Must | The system shall paginate through the channel history using cursor-based pagination |
| FR-04 | Must | The system shall print a summary to stderr showing cached message count, fetched count, and channel id |
| FR-05 | Should | When --full-threads is passed, the system shall also fetch all replies for every threaded conversation in the channel |
| FR-06 | Should | The system shall report how many threads had their replies fetched when --full-threads is used |
| FR-07 | Should | The system shall support --api-base-url to point at a non-default Slack API server |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Reliability | The system shall handle Slack API pagination correctly for channels with many messages |
| NFR-02 | Should | Performance | Each thread's replies shall be fetched and committed in a separate transaction to avoid large single transactions |

## Constraints

- conversations.history only returns top-level messages (standalone messages and
  thread parents), not thread replies
- Thread replies require a separate conversations.replies call per thread
- The existing SQLite schema (threads + messages tables) is reused; no schema changes

## Acceptance Criteria

- [ ] FR-01: fetch-channel-messages --channel C1 retrieves top-level messages from the channel
- [ ] FR-02: Messages are stored in the threads/messages tables with correct channel association
- [ ] FR-05: --full-threads fetches all replies for every thread found in the channel history
- [ ] FR-06: The summary includes the count of threads with replies fetched when --full-threads is used

## Open Questions

1. Should there be --oldest/--latest filters for date-bounding the channel history?
2. Should incremental channel refreshes be supported (only fetching new messages)?
