"""Workspace container with pre-generated data and pagination helpers."""

import random
import re
import time
from typing import Any

import structlog

from slack_cached.fake_slack._internal._config import WorkspaceParams
from slack_cached.fake_slack._internal._constants import TEAM_ID
from slack_cached.fake_slack._internal._cursor import _decode_cursor, _encode_cursor
from slack_cached.fake_slack._internal._generate import (
    _generate_channels,
    _generate_threads,
    _generate_users,
)

log = structlog.get_logger(__name__)


def _highlight_query_terms(text: str, terms: list[str]) -> str:
    """Wrap each whole-word occurrence of a query term in backticks.

    Mimics Slack's ``search.messages`` behavior of decorating the matched
    query terms in the returned ``text``. Matching is case-insensitive and
    respects word boundaries so partial matches inside other words are not
    highlighted. Longer terms are processed first so a multi-word term wins
    over its single-word sub-terms.
    """
    if not text or not terms:
        return text
    result = text
    for term in sorted({t for t in terms if t}, key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        result = pattern.sub(lambda m: f"`{m.group(0)}`", result)
    return result


class Workspace:
    """Pre-generated fake workspace data with pagination support."""

    def __init__(
        self,
        params: WorkspaceParams | None = None,
        **kwargs: Any,
    ) -> None:
        if params is None and kwargs:
            params = WorkspaceParams(**kwargs)
        self.params = params or WorkspaceParams()
        rng = random.Random(self.params.seed)

        self.users = _generate_users(rng, self.params)
        self.channels = _generate_channels(rng, self.params)
        self.threads = _generate_threads(rng, self.params, self.channels, self.users)
        log.info(
            "workspace_generated",
            users=len(self.users),
            channels=len(self.channels),
            threads=len(self.threads),
            seed=self.params.seed,
        )

    def get_users_page(
        self, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        offset = _decode_cursor(cursor)
        page = self.users[offset : offset + limit]
        next_offset = offset + limit
        next_cursor = _encode_cursor(next_offset) if next_offset < len(self.users) else None
        return page, next_cursor

    def get_channels_page(
        self, cursor: str | None, limit: int, types: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        filtered = self._filter_channels(types)
        offset = _decode_cursor(cursor)
        page = filtered[offset : offset + limit]
        next_offset = offset + limit
        next_cursor = _encode_cursor(next_offset) if next_offset < len(filtered) else None
        return page, next_cursor

    def _filter_channels(self, types: str | None) -> list[dict[str, Any]]:
        if not types:
            return self.channels
        wanted = {t.strip() for t in types.split(",")}
        result: list[dict[str, Any]] = []
        for ch in self.channels:
            ch_type = self._channel_type(ch)
            if ch_type in wanted:
                result.append(ch)
        return result

    @staticmethod
    def _channel_type(ch: dict[str, Any]) -> str:
        if ch.get("is_im"):
            return "im"
        if ch.get("is_mpim"):
            return "mpim"
        if ch.get("is_private"):
            return "private_channel"
        return "public_channel"

    def get_thread_messages(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        all_messages = self.threads.get((channel, thread_ts), [])
        if oldest is not None:
            oldest_f = float(oldest)
            all_messages = [m for m in all_messages if float(m["ts"]) >= oldest_f]

        offset = _decode_cursor(cursor)
        page = all_messages[offset : offset + limit]
        next_offset = offset + limit
        has_more = next_offset < len(all_messages)
        next_cursor = _encode_cursor(next_offset) if has_more else None
        return page, has_more, next_cursor

    def thread_exists(self, channel: str, thread_ts: str) -> bool:
        return (channel, thread_ts) in self.threads

    def get_channel_history(
        self,
        channel: str,
        oldest: str | None,
        latest: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Return top-level messages (thread roots) for a channel.

        Each thread's first message is returned, simulating what
        conversations.history returns (standalone messages and thread parents).
        """
        roots: list[dict[str, Any]] = []
        for (ch_id, _ts), messages in self.threads.items():
            if ch_id != channel or not messages:
                continue
            root = dict(messages[0])
            reply_count = len(messages) - 1
            root["reply_count"] = reply_count
            if reply_count > 0:
                root["reply_users"] = list({m["user"] for m in messages[1:] if m.get("user")})
                root["latest_reply"] = messages[-1]["ts"]
            roots.append(root)

        roots.sort(key=lambda m: float(m["ts"]))

        if oldest is not None:
            oldest_f = float(oldest)
            roots = [m for m in roots if float(m["ts"]) >= oldest_f]
        if latest is not None:
            latest_f = float(latest)
            roots = [m for m in roots if float(m["ts"]) <= latest_f]

        offset = _decode_cursor(cursor)
        page = roots[offset : offset + limit]
        next_offset = offset + limit
        has_more = next_offset < len(roots)
        next_cursor = _encode_cursor(next_offset) if has_more else None
        return page, has_more, next_cursor

    def search_messages(
        self,
        query: str,
        count: int,
        page: int,
        sort: str,
        sort_dir: str,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Return (page_matches, total_count, page_count) for a text search.

        Mimics the shape of real Slack ``search.messages``:

        - ``text`` is returned with each query term wrapped in backticks
          (Slack's search-highlight markup). The same message fetched via
          ``conversations.replies`` has plain text, so this drift is what
          the canonical-payload comparison in ``storage.upsert_messages``
          has to survive.
        - ``channel`` is returned as an object ``{"id", "name"}`` rather
          than the bare id used by ``conversations.*``.
        - ``permalink``, ``channel_previous`` and ``channel_is_prev`` are
          search-only fields.
        - Thread parents come back without ``thread_ts`` or
          ``parent_user_id`` (Slack omits them when ``ts == thread_ts``).
        - ``edited`` is omitted; ``conversations.replies`` includes it.
        """
        terms = [t.lower() for t in query.split() if t]
        channel_names = {c["id"]: c["name"] for c in self.channels}

        matches: list[dict[str, Any]] = []
        for (channel, _thread_ts), messages in self.threads.items():
            ch_name = channel_names.get(channel, channel)
            for msg in messages:
                text = (msg.get("text") or "").lower()
                if terms and not all(term in text for term in terms):
                    continue
                match = dict(msg)
                # search.messages highlights matched query terms with backticks.
                match["text"] = _highlight_query_terms(match.get("text", ""), terms)
                # ``channel`` as object, plus permalink and channel-history
                # metadata that conversations.* does not return.
                match["channel"] = {"id": channel, "name": ch_name}
                match["channel_previous"] = {"name": ch_name, "id": channel}
                match["channel_is_prev"] = False
                match["permalink"] = (
                    f"https://acme.slack.com/archives/{channel}/"
                    f"p{msg['ts'].replace('.', '').ljust(16, '0')}"
                )
                # Real search.messages drops thread_ts/parent_user_id on
                # parents and never surfaces ``edited``.
                if match.get("thread_ts") == match.get("ts"):
                    match.pop("thread_ts", None)
                match.pop("parent_user_id", None)
                match.pop("edited", None)
                matches.append(match)

        reverse = sort_dir == "desc"
        if sort == "timestamp":
            matches.sort(key=lambda m: float(m["ts"]), reverse=reverse)
        else:
            matches.sort(key=lambda m: float(m["ts"]), reverse=True)

        total = len(matches)
        page_count = max(1, (total + count - 1) // count) if count > 0 else 1
        effective_page = max(1, page)
        start = (effective_page - 1) * count
        end = start + count
        return matches[start:end], total, page_count

    def post_message(
        self,
        channel: str,
        text: str,
        user: str | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Add a message to the workspace, either as a new thread or a reply.

        Returns the created message dict (matching the Slack ``chat.postMessage``
        response shape).
        """
        ts = f"{time.time():.6f}"
        if user is None:
            user = self.users[0]["id"] if self.users else "UPOST"

        msg: dict[str, Any] = {
            "type": "message",
            "user": user,
            "text": text,
            "ts": ts,
            "blocks": [],
            "files": [],
            "upload": False,
            "display_as_bot": False,
            "is_starred": False,
            "source_team": TEAM_ID,
            "user_team": TEAM_ID,
        }

        if thread_ts:
            key = (channel, thread_ts)
            if key not in self.threads:
                return {"ok": False, "error": "thread_not_found"}
            msg["thread_ts"] = thread_ts
            msg["parent_user_id"] = self.threads[key][0]["user"]
            self.threads[key].append(msg)
        else:
            msg["thread_ts"] = ts
            self.threads[(channel, ts)] = [msg]

        return {
            "ok": True,
            "ts": ts,
            "channel": channel,
            "message": msg,
        }
