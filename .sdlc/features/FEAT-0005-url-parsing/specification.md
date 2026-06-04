---
title: "URL Parsing"
status: draft
---

# Specification: URL Parsing

## Overview

URL parsing is implemented in urls.py as two pure functions: parse_thread_url()
for Slack permalinks and parse_channel_ts() for explicit CLI arguments. Both
return a frozen ThreadRef dataclass.

## Architecture

```
urls.py
  |
  +-- parse_thread_url(url: str) -> ThreadRef
  |     -> urlparse() to extract host, path, query
  |     -> validate .slack.com domain
  |     -> extract channel from path segment
  |     -> _pts_to_ts() to convert p-prefixed timestamp
  |     -> check thread_ts query param (for reply links)
  |
  +-- parse_channel_ts(channel, ts) -> ThreadRef
        -> validate channel prefix (C, G, D)
        -> validate ts format (dotted numeric)
        -> return ThreadRef
```

## Data Models

### ThreadRef

| Field | Type | Constraints | Description |
|---|---|---|---|
| channel | str | not null | Slack channel id (C..., G..., or D...) |
| thread_ts | str | not null | Thread root message timestamp |

## API Contracts

### parse_thread_url(url: str) -> ThreadRef

- Raises ValueError if URL is not a slack.com permalink, channel prefix is
  invalid, or permalink timestamp is malformed

### parse_channel_ts(channel: str, ts: str) -> ThreadRef

- Raises ValueError if channel prefix is not C/G/D or ts is not a valid dotted
  numeric timestamp

## Sequences

### Permalink parsing

```
Input: "https://acme.slack.com/archives/C123/p1700000000123456?thread_ts=1700000000.123456&cid=C123"
  -> urlparse: host="acme.slack.com", path="/archives/C123/p1700000000123456"
  -> validate: host ends with ".slack.com" -> OK
  -> extract: parts=["archives", "C123", "p1700000000123456"]
  -> channel="C123", validate prefix C -> OK
  -> _pts_to_ts("p1700000000123456") -> "1700000000.123456"
  -> query has thread_ts -> thread_ts="1700000000.123456"
  -> return ThreadRef(channel="C123", thread_ts="1700000000.123456")
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Frozen dataclass | ThreadRef | Immutable value object; safe to pass around without defensive copies |
| ValueError for errors | Consistent with Python conventions | Callers (CLI) can catch and convert to SystemExit |
| Separate parse functions | URL vs CLI args have different validation needs | Clean separation of concerns |
| p-prefix conversion | Insert dot 6 from the right | Slack permalinks encode the timestamp as a 16-digit integer with the dot removed |

## Risks and Unknowns

1. Slack may introduce new URL formats that the parser does not handle
2. Enterprise Slack URLs may have different host patterns

## Out of Scope

- Slack deep links (slack://)
- Message links that are not thread roots
- File or channel links (only thread/message links)
