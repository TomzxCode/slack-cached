"""HTTP request handler exposing the Slack-compatible API endpoints."""

import json
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog

from slack_cached.fake_slack._internal._rate_limiter import RateLimiter
from slack_cached.fake_slack._internal._workspace import Workspace

log = structlog.get_logger(__name__)


class FakeSlackHandler(BaseHTTPRequestHandler):
    workspace: Workspace
    rate_limiter: RateLimiter | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if self.rate_limiter is not None:
            allowed, retry_after = self.rate_limiter.check(path)
            if not allowed:
                self._send_json(
                    {"ok": False, "error": "ratelimited"},
                    429,
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return

        routes = {
            "/api/conversations.replies": self._handle_conversations_replies,
            "/api/conversations.history": self._handle_conversations_history,
            "/api/users.list": self._handle_users_list,
            "/api/conversations.list": self._handle_conversations_list,
            "/api/search.messages": self._handle_search_messages,
        }

        handler = routes.get(path)
        if handler is None:
            self._send_json({"ok": False, "error": "unknown_endpoint"}, 404)
            return
        handler(params)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else ""
        params: dict[str, str] = {}
        if body:
            for pair in body.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    from urllib.parse import unquote_plus

                    params[k] = unquote_plus(v)
        if not params:
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if self.rate_limiter is not None:
            allowed, retry_after = self.rate_limiter.check(path)
            if not allowed:
                self._send_json(
                    {"ok": False, "error": "ratelimited"},
                    429,
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return

        routes = {
            "/api/chat.postMessage": self._handle_chat_post_message,
        }

        handler = routes.get(path)
        if handler is None:
            self._send_json({"ok": False, "error": "unknown_endpoint"}, 404)
            return
        handler(params)

    def _handle_conversations_replies(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        thread_ts = params.get("ts", "")
        limit = int(params.get("limit", "200"))
        oldest = params.get("oldest")
        cursor = params.get("cursor")

        if not self.workspace.thread_exists(channel, thread_ts):
            self._send_json({"ok": False, "error": "channel_not_found"}, 404)
            return

        messages, has_more, next_cursor = self.workspace.get_thread_messages(
            channel, thread_ts, oldest, cursor, limit
        )
        response: dict[str, Any] = {
            "ok": True,
            "messages": messages,
            "has_more": has_more,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_conversations_history(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        limit = int(params.get("limit", "200"))
        oldest = params.get("oldest")
        latest = params.get("latest")
        cursor = params.get("cursor")

        messages, has_more, next_cursor = self.workspace.get_channel_history(
            channel, oldest, latest, cursor, limit
        )
        response: dict[str, Any] = {
            "ok": True,
            "messages": messages,
            "has_more": has_more,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_users_list(self, params: dict[str, str]) -> None:
        limit = int(params.get("limit", "1000"))
        cursor = params.get("cursor")

        page, next_cursor = self.workspace.get_users_page(cursor, limit)
        response: dict[str, Any] = {
            "ok": True,
            "members": page,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_conversations_list(self, params: dict[str, str]) -> None:
        limit = int(params.get("limit", "1000"))
        types = params.get("types")
        cursor = params.get("cursor")

        page, next_cursor = self.workspace.get_channels_page(cursor, limit, types)
        response: dict[str, Any] = {
            "ok": True,
            "channels": page,
        }
        if next_cursor is not None:
            response["response_metadata"] = {"next_cursor": next_cursor}
        else:
            response["response_metadata"] = {"next_cursor": ""}

        self._send_json(response)

    def _handle_search_messages(self, params: dict[str, str]) -> None:
        query = params.get("query", "")
        count = max(1, int(params.get("count", "20")))
        page = max(1, int(params.get("page", "1")))
        sort = params.get("sort", "timestamp")
        sort_dir = params.get("sort_dir", "desc")

        page_matches, total, page_count = self.workspace.search_messages(
            query, count, page, sort, sort_dir
        )
        first = (page - 1) * count + 1 if total else 0
        last = first + len(page_matches) - 1 if page_matches else 0
        response: dict[str, Any] = {
            "ok": True,
            "query": query,
            "messages": {
                "total": total,
                "pagination": {
                    "total_count": total,
                    "page": page,
                    "per_page": count,
                    "page_count": page_count,
                    "first": first,
                    "last": last,
                },
                "matches": page_matches,
            },
        }
        self._send_json(response)

    def _handle_chat_post_message(self, params: dict[str, str]) -> None:
        channel = params.get("channel", "")
        text = params.get("text", "")
        user = params.get("user") or params.get("as_user")
        thread_ts = params.get("thread_ts")

        if not channel:
            self._send_json({"ok": False, "error": "invalid_channel"}, 400)
            return
        if not text:
            self._send_json({"ok": False, "error": "no_text"}, 400)
            return

        result = self.workspace.post_message(
            channel=channel, text=text, user=user, thread_ts=thread_ts
        )
        if not result.get("ok"):
            self._send_json(result, 404)
            return
        self._send_json(result)

    def _send_json(
        self,
        data: dict[str, Any],
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        log.debug("http_request", message=format % args)
