---
title: "Channel Caching and Display"
status: draft
---

# Requirements: Channel Caching and Display

## Overview

Provides the ability to bulk-fetch all visible Slack channels (conversations)
from the API and cache them locally. Cached channels can be displayed in
human-readable or JSON format.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| CLI user | Wants to browse available channels and their visibility |
| Developer | Needs channel names for constructing thread references |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall fetch all visible conversations via the Slack conversations.list API |
| FR-02 | Must | The system shall store channels with id, name, is_private flag, fetched_at, and full JSON payload |
| FR-03 | Must | The system shall upsert channels by id so repeated fetches update existing records |
| FR-04 | Must | The system shall display cached channels in human-readable format showing id, name, and visibility |
| FR-05 | Must | The system shall display cached channels in JSON format when --json is passed |
| FR-06 | Must | The system shall auto-fetch channels when show-channels is called with an empty cache, unless --no-fetch is given |
| FR-07 | Should | The system shall fetch all channel types (public, private, mpim, im) by default |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Should | Usability | The fetch-channels command shall print a summary (processed, added, total) to stderr |

## Constraints

- Channel visibility (is_private) is stored as a nullable INTEGER (1/0/null)
- Default channel types include public_channel, private_channel, mpim, and im

## Acceptance Criteria

- [ ] FR-01: fetch-channels retrieves all visible conversations from Slack
- [ ] FR-03: Running fetch-channels twice updates existing channels without duplication
- [ ] FR-04: show-channels outputs human-readable text with id, name, and "public"/"private"
- [ ] FR-05: show-channels --json outputs valid JSON with channel_count and channels array

## Open Questions

1. Should archived channels be excluded or marked separately?
2. Should channel membership or topic/description be cached?
