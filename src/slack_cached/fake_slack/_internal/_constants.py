"""Shared constants for the fake Slack server."""

TEAM_ID = "T01FAKEWK"
DEFAULT_EPOCH_BASE = 1704067200.0

ENDPOINT_RATE_LIMITS: dict[str, int] = {
    "conversations.replies": 50,
    "conversations.history": 50,
    "users.list": 20,
    "conversations.list": 20,
    "search.messages": 20,
}

RATE_LIMIT_WINDOW = 60.0
