---
title: "Channel Message Fetching"
status: draft
---

# Specification: Channel Message Fetching

## Overview

The fetch --channel command uses SlackClient.iter_channel_history() to
fetch top-level messages via conversations.history, then optionally fetches
full thread replies via iter_thread_replies() for each threaded conversation.

## Architecture

```
CLI (cli.py)
  |
  +-- fetch --channel C1 [--full-threads] [--oldest 7d]
       |
       +-- cache.fetch_channel_messages(conn, client, channel, full_threads)
             |
             +-- slack_api.iter_channel_history(channel)
             |     -> paginated top-level messages via conversations.history
             |
             +-- For each message: storage.record_thread_refresh() + upsert_messages()
             |
             +-- If full_threads:
                   For each message with reply_count > 0:
                     +-- slack_api.iter_thread_replies(channel, thread_ts)
                     +-- storage.record_thread_refresh() + upsert_messages()
```

## Data Models

### ChannelFetchResult

| Field | Type | Constraints | Description |
|---|---|---|---|
| channel | str | not null | Channel id |
| fetched_messages | int | not null | Messages written in this fetch |
| total_messages | int | not null | Total cached messages for the channel |
| threads_with_replies_fetched | int | not null | Threads that had full replies fetched |

No schema changes. Reuses the existing threads and messages tables.

## API Contracts

### fetch --channel

- Input: --channel (required), --full-threads (optional), --oldest (optional duration), --db, --api-base-url, -v
- Output (stderr): "cached N messages for CHANNEL (M fetched[, T threads with replies fetched])"
- Exit code: 0 on success

## Sequences

### Channel fetch with full threads

```
User -> cli fetch --channel C1 --full-threads
  -> slack_api.iter_channel_history(channel="C1")
     -> paginated top-level messages via cursor
  -> For each top-level message:
     -> storage.record_thread_refresh(channel, thread_ts, None)
     -> storage.upsert_messages(channel, thread_ts, [msg])
  -> Identify messages with reply_count > 0 or latest_reply
  -> For each such thread:
     -> slack_api.iter_thread_replies(channel, thread_ts)
     -> storage.record_thread_refresh(channel, thread_ts, latest)
     -> storage.upsert_messages(channel, thread_ts, replies)
  -> storage.count_channel_messages(channel) -> total
  -> return ChannelFetchResult
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Separate transactions per thread | Each reply fetch gets its own transaction | Avoids a single large transaction that holds a write lock for the entire channel fetch |
| Thread identification | reply_count > 0 or presence of latest_reply | Matches how Slack's conversations.history flags threaded messages |
| storage.count_channel_messages() | Counts across all threads for a channel | Provides total cached message count for the summary |
| --full-threads is opt-in | Default fetches only top-level messages | Fetching all replies can be expensive for active channels; user chooses |
| --oldest duration filtering | Converts duration string to epoch, passes as oldest param | Avoids re-fetching ancient history on repeated runs |
| Duration format | Nh (hours), Nd (days), Nw (weeks) | Simple, memorable syntax for common lookback periods |

## Risks and Unknowns

1. Very active channels may have thousands of threads, making --full-threads slow
2. No incremental channel refresh; every run fetches the full history
3. Rate limiting may cause long run times for large channels with --full-threads

## Out of Scope

- Incremental channel history refresh
- Filtering by message type or author
