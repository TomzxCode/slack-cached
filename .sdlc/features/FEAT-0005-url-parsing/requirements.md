---
title: "URL Parsing"
status: draft
---

# Requirements: URL Parsing

## Overview

Parses Slack thread permalinks into structured ThreadRef objects containing a
channel id and thread root timestamp. Also supports building ThreadRef from
explicit --channel and --ts CLI arguments.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| CLI user | Wants to paste a Slack URL directly as a command argument |
| Developer | Needs a reliable way to convert URLs to internal thread references |

## Functional Requirements

Order rows by priority: Must first, then Should, then May.

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | Must | The system shall parse Slack permalinks in the form https://workspace.slack.com/archives/CHANNEL/pXXXXXXXXXXXXXXXX into a ThreadRef |
| FR-02 | Must | The system shall convert the p-prefixed 16-digit permalink timestamp into a dotted Slack timestamp (e.g. p1700000000123456 -> 1700000000.123456) |
| FR-03 | Must | The system shall detect reply permalinks by checking for a thread_ts query parameter and use that as the thread root |
| FR-04 | Must | The system shall accept channel ids starting with C (public channel), G (private group), or D (direct message) |
| FR-05 | Must | The system shall accept explicit --channel and --ts arguments and build a ThreadRef directly |
| FR-06 | Must | The system shall raise ValueError for malformed URLs, bad channel ids, or invalid timestamps |
| FR-07 | Must | The system shall accept channel ids starting with C, G, or D when using --channel/--ts |
| FR-08 | Must | The system shall validate that --ts looks like a dotted numeric timestamp (e.g. 1700000000.123456) |

## Non-Functional Requirements

| ID | Priority | Category | Requirement |
|---|---|---|---|
| NFR-01 | Must | Reliability | Invalid inputs shall raise descriptive ValueError messages |

## Constraints

- Only slack.com URLs are supported
- Permalink timestamps must be at least 7 digits after removing the "p" prefix

## Acceptance Criteria

- [ ] FR-01: "https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456" parses to ThreadRef("C0123ABCDEF", "1700000000.123456")
- [ ] FR-03: A reply URL with thread_ts query parameter returns the thread root ts, not the reply ts
- [ ] FR-04: URLs with D-prefixed channel ids (DMs) are accepted
- [ ] FR-06: Non-slack.com URLs, bad channel prefixes, and malformed timestamps raise ValueError
- [ ] FR-08: --ts "1700000000" (no dot) and --ts "not-a-ts" are rejected

## Open Questions

1. Should the parser support Slack deep links (e.g. slack://thread)?
2. Should the parser support Slack enterprise-grade URLs (e.g. enterprise.slack.com/archives/...)?
