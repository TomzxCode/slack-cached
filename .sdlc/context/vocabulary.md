# Vocabulary

<!--
This file defines domain-specific terms, acronyms, and abbreviations used across
the project. Keeping definitions here avoids ambiguity in requirements, specs,
and discussions. Add new terms as they are introduced.
-->

## Domain Terms

| Term | Definition |
|---|---|
| thread | A Slack conversation thread, identified by a channel id and root message timestamp |
| permalink | A Slack URL in the form https://workspace.slack.com/archives/CHANNEL/pXXXXXXXXXXXXXXXX |
| incremental fetch | A refresh that passes `oldest` to the API to only get new/edited messages |
| upsert | INSERT OR REPLACE SQLite operation that inserts new rows or replaces existing ones |
| fake server | Local HTTP server (fake_slack.py) that mimics the Slack Web API for testing |
| workspace | A Slack workspace containing users, channels, and messages |
| channel | A Slack conversation (public channel, private channel, MPIM, or DM) |
| conversation | Slack's unified term for channels, DMs, and group messages (used in API endpoint names) |

## Technical Terms

| Term | Definition |
|---|---|
| ts | Slack timestamp string in epoch seconds with microseconds, e.g. "1700000000.123456" |
| ThreadRef | Internal dataclass holding a channel id and thread_ts pair |
| CachedMessage | Dataclass representing a stored message with ts, user, text, and payload fields |
| CachedUser | Dataclass representing a stored user with id, name, real_name, and payload fields |
| CachedChannel | Dataclass representing a stored channel with id, name, is_private, and payload fields |
| ThreadState | Dataclass tracking the fetch state of a thread (latest_reply, last_fetched) |
| Credentials | Frozen dataclass holding a Slack token and optional d cookie |
| FetchResult | Frozen dataclass holding fetch statistics (channel, thread_ts, total, fetched, is_incremental) |
| ChannelFetchResult | Frozen dataclass holding channel fetch statistics (channel, fetched_messages, total_messages, threads_with_replies_fetched) |
| xoxb token | A Slack bot token starting with "xoxb-", requiring only an Authorization header |
| xoxc token | A Slack browser/web-client token starting with "xoxc-", also requiring its matching xoxd d cookie |
| d cookie | The xoxd- cookie value paired with an xoxc- token for browser-style authentication |
| conversations.history | Slack API endpoint that returns top-level messages in a channel |
| conversations.replies | Slack API endpoint that returns all replies in a thread |
| conversations.list | Slack API endpoint that returns all visible conversations/channels |
| users.list | Slack API endpoint that returns all workspace members |
| chat.postMessage | Slack API endpoint that sends a message to a channel or thread |
| cursor | Opaque pagination token returned by Slack APIs for fetching the next page of results |
| XDG base directory | freedesktop.org specification for config, cache, and data file locations |

## Acronyms and Abbreviations

| Abbreviation | Expansion |
|---|---|
| MPIM | Multi-Party Instant Message (Slack group DM) |
| DM | Direct Message (Slack 1:1 conversation) |
| NFR | Non-Functional Requirement |
| FR | Functional Requirement |
| RNG | Random Number Generator |
