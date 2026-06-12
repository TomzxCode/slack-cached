---
title: "Fake Slack Server"
status: draft
---

# Requirements: Fake Slack Server

## Overview

A local HTTP server that mimics the Slack Web API, serving deterministically
generated workspace data (users, channels, threads). Used for integration
testing and development without hitting the real Slack API.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| Developer | Needs a local Slack-like API for testing fetch/show commands end-to-end |
| CI pipeline | Requires a fake server for reliable, fast integration tests |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall expose conversations.replies, conversations.history, users.list, conversations.list, and chat.postMessage endpoints |
| FR-02 | Must | The system shall generate deterministic workspace data from a configurable random seed |
| FR-03 | Must | The system shall support cursor-based pagination on all list endpoints |
| FR-04 | Must | The system shall generate realistic users with names, roles, timezones, and profile data |
| FR-05 | Must | The system shall generate realistic channels with names, purposes, and privacy settings |
| FR-06 | Must | The system shall generate realistic conversation threads with topic-specific message templates |
| FR-07 | Must | The system shall support configurable workspace parameters (user count, channel count, thread count, messages per thread, activity ratio) |
| FR-08 | Must | The system shall support optional rate limiting that mirrors Slack's documented tier limits |
| FR-09 | Must | The system shall return HTTP 429 with a Retry-After header when rate limited |
| FR-10 | Should | The system shall support conversations.history for fetching channel message history |
| FR-11 | Must | The system shall expose a POST /api/chat.postMessage endpoint that creates a new message in a specified channel or thread and returns the created message dict |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Reliability | The same seed shall always produce identical workspace data |
| NFR-02 | Should | Performance | Data generation for default parameters (20 users, 13 channels, 30 threads) should complete in under 1 second |
| NFR-03 | Should | Security | The server should bind to 127.0.0.1 by default to avoid exposing fake data on the network |

## Constraints

- Uses Python's built-in http.server (BaseHTTPRequestHandler/HTTPServer), no external web framework
- All data is generated at startup and served from memory; no persistent storage
- Single-threaded request handling (HTTPServer default)

## Acceptance Criteria

- [ ] FR-01: The server responds to /api/conversations.replies, /api/conversations.history, /api/users.list, /api/conversations.list, and /api/chat.postMessage
- [ ] FR-02: Running with the same --seed produces identical responses
- [ ] FR-03: Paginated endpoints return response_metadata.next_cursor and respect cursor parameters
- [ ] FR-08: With --rate-limits, the server returns 429 after exceeding the per-endpoint limit
- [ ] FR-11: POST /api/chat.postMessage creates a message and returns the message dict with a generated ts

## Open Questions

1. Should the server support WebSocket connections for Events API simulation?
2. Should generated data be exportable to a JSON file for reuse?
