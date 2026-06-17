"""Parse Slack thread URLs into (channel_id, thread_ts) pairs.

Supported permalink forms:
- https://<workspace>.slack.com/archives/<CHANNEL_ID>/p<PTS>
- https://<workspace>.slack.com/archives/<CHANNEL_ID>/p<PTS>?thread_ts=<TS>&...

Where <PTS> is a 16-digit integer that corresponds to a Slack timestamp with
the dot removed (e.g. p1700000000123456 -> 1700000000.123456).

When the URL points to a reply, the message timestamp encoded in the path is the
reply's ts, and the actual thread root ts is in the thread_ts query parameter.
We return the thread root ts in that case so that consumers can fetch the entire
thread via conversations.replies.
"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

CHANNEL_PREFIXES = ("C", "G", "D")


@dataclass(frozen=True)
class ThreadRef:
    """Reference to a Slack thread, identified by channel and root ts."""

    channel: str
    thread_ts: str


def _pts_to_ts(pts: str) -> str:
    """Convert a 'pXXXXXXXXXXXXXXXX' permalink timestamp to 'XXXXXXXXXX.XXXXXX'."""
    if not pts.startswith("p"):
        raise ValueError(f"permalink ts must start with 'p', got {pts!r}")
    digits = pts[1:]
    if not digits.isdigit() or len(digits) < 7:
        raise ValueError(f"permalink ts has unexpected shape: {pts!r}")
    return f"{digits[:-6]}.{digits[-6:]}"


def parse_thread_url(url: str) -> ThreadRef:
    """Parse a Slack thread permalink into a ThreadRef.

    Raises ValueError if the URL does not look like a Slack thread permalink.
    """
    parsed = urlparse(url)
    if not parsed.netloc.endswith(".slack.com"):
        raise ValueError(f"not a slack.com URL: {url!r}")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3 or parts[0] != "archives":
        raise ValueError(f"unexpected slack URL path: {parsed.path!r}")

    channel = parts[1]
    if not channel or channel[0] not in CHANNEL_PREFIXES:
        raise ValueError(f"unexpected channel id in URL: {channel!r}")

    message_ts = _pts_to_ts(parts[2])

    query = parse_qs(parsed.query)
    thread_ts_values = query.get("thread_ts") or []
    thread_ts = thread_ts_values[0] if thread_ts_values else message_ts

    return ThreadRef(channel=channel, thread_ts=thread_ts)


def parse_channel_ts(channel: str, ts: str) -> ThreadRef:
    """Build a ThreadRef from explicit --channel/--ts arguments.

    Raises ValueError when inputs are obviously malformed.
    """
    channel = channel.strip()
    ts = ts.strip()
    if not channel or channel[0] not in CHANNEL_PREFIXES:
        raise ValueError(f"channel id should start with one of {CHANNEL_PREFIXES}: {channel!r}")
    if "." not in ts or not ts.replace(".", "").isdigit():
        raise ValueError(f"ts should look like 1700000000.123456, got {ts!r}")
    return ThreadRef(channel=channel, thread_ts=ts)
