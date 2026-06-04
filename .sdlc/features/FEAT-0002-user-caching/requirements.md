---
title: "User Caching and Display"
status: draft
---

# Requirements: User Caching and Display

## Overview

Provides the ability to bulk-fetch all workspace users from the Slack API and
cache them in the local SQLite database. Cached users are used to resolve user
ids to human-readable display names in thread output.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| CLI user | Wants to see real names instead of raw user ids in thread output |
| Developer | Needs a user lookup table for data analysis |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall fetch all workspace members via the Slack users.list API |
| FR-02 | Must | The system shall store users in SQLite with id, name, real_name, fetched_at, and full JSON payload |
| FR-03 | Must | The system shall upsert users by id so repeated fetches update existing records |
| FR-04 | Must | The system shall display cached users in human-readable format showing id, name, and real name |
| FR-05 | Must | The system shall display cached users in JSON format when --json is passed |
| FR-06 | Must | The system shall auto-fetch users when show-users is called with an empty cache, unless --no-fetch is given |
| FR-07 | Must | The system shall resolve user ids to display names in the format "Real Name (handle)" when both are known |
| FR-08 | Should | The system shall follow cursor-based pagination for large workspaces |

## Non-Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Performance | Display name resolution shall only query users that appear in the displayed thread, not the entire workspace |
| NFR-02 | Should | Usability | The fetch-users command shall print a summary (processed, added, total) to stderr |

## Constraints

- User data is stored as full JSON in a payload column for future use
- Display name resolution chunks queries in batches of 900 to stay under SQLite's 999 bound-variable limit

## Acceptance Criteria

- [ ] FR-01: fetch-users retrieves all workspace users and stores them
- [ ] FR-03: Running fetch-users twice does not duplicate users; it updates in place
- [ ] FR-04: show-users outputs human-readable text with id, name, and real name
- [ ] FR-05: show-users --json outputs valid JSON with user_count and users array
- [ ] FR-07: Thread output shows "Alice Smith (alice)" when both real_name and name are cached

## Open Questions

1. Should user data be automatically refreshed on a schedule?
2. Should deleted/deactivated users be removed from the cache?
