"""Fake OpenAI-compatible chat completion server.

Serves ``POST /v1/chat/completions`` (and a couple of related endpoints) so
the watcher SPA can be developed end-to-end without real OpenAI credentials.

Modes
-----

``--mode echo`` (default)
    Returns the last user message back, prefixed with ``ECHO: ``. Useful for
    verifying the rendered prompt reached the LLM.
``--mode static``
    Always returns the string passed via ``--response`` (default: ``OK``).
``--mode reverse``
    Returns the messages array reversed, formatted as Markdown. Useful for
    eyeballing thread transcript rendering.
``--mode fail``
    Always returns HTTP 500 with the ``--response`` body, so error handling
    in the SPA can be exercised.

Every response includes a synthetic usage object so the watcher's
``prompt_tokens`` / ``completion_tokens`` columns are populated.

Run it as:

    uv run slack-fake-openai --port 8240
    uv run slack-fake-openai --mode echo --port 0    # ephemeral
    uv run slack-fake-openai --mode static --response "summarized" --port 8240
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _truncate(text: str, limit: int = 800) -> str:
    """Trim a string to *limit* chars, adding an ellipsis when it was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _build_response(
    model: str,
    content: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _error_body(message: str, code: str = "internal_error") -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "server_error",
            "code": code,
        }
    }


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the small subset the watcher's LLM client uses.

    Class attributes are populated by :func:`run_server` so a single server
    instance can be configured without re-instantiating the handler class.
    """

    mode: str = "echo"
    fixed_response: str = "OK"
    default_model: str = "fake-model"
    latency_ms: int = 0
    server_version: str = "slack-fake-openai/0.1"

    # --- helpers --------------------------------------------------------

    def _write_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - http.server API
        # Mirror structlog style for consistency.
        log.info("fake_llm_request", line=self.address_string(), message=fmt % args)

    # --- routes ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path in ("/", "/v1", "/v1/"):
            self._write_json(200, {"service": "fake-openai", "mode": self.mode})
            return
        if self.path in ("/v1/models", "/models"):
            self._write_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.default_model,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "slack-fake-openai",
                        }
                    ],
                },
            )
            return
        self._write_json(404, _error_body(f"unknown path: {self.path}", "not_found"))

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        # Be lenient about trailing slash or missing /v1 prefix.
        if self.path != "/v1/chat/completions" and self.path.rstrip("/") not in {
            "/v1/chat/completions",
            "/chat/completions",
        }:
            self._write_json(404, _error_body(f"unknown path: {self.path}", "not_found"))
            return

        raw = self._read_body()
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except ValueError:
            self._write_json(400, _error_body("invalid JSON body", "bad_request"))
            return

        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

        if self.mode == "fail":
            self._write_json(500, _error_body(self.fixed_response, "forced_failure"))
            return

        messages = payload.get("messages") or []
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        model = payload.get("model") or self.default_model

        if self.mode == "static":
            content = self.fixed_response
        elif self.mode == "reverse":
            lines = [f"[{m.get('role', '?')}] {m.get('content', '')}" for m in reversed(messages)]
            content = "\n".join(lines)
        else:  # echo (default)
            content = "ECHO: " + _truncate(str(last_user))

        # Rough token estimate so the UI shows non-zero numbers.
        prompt_tokens = max(1, len(str(last_user)) // 4)
        completion_tokens = max(1, len(content) // 4)

        body = _build_response(
            model,
            content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        log.info(
            "fake_llm_completion",
            mode=self.mode,
            model=model,
            prompt_chars=len(str(last_user)),
            response_chars=len(content),
        )
        self._write_json(200, body)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8240,
    *,
    mode: str = "echo",
    fixed_response: str = "OK",
    default_model: str = "fake-model",
    latency_ms: int = 0,
) -> ThreadingHTTPServer:
    """Start the fake OpenAI server. Caller is responsible for ``serve_forever``."""
    FakeOpenAIHandler.mode = mode
    FakeOpenAIHandler.fixed_response = fixed_response
    FakeOpenAIHandler.default_model = default_model
    FakeOpenAIHandler.latency_ms = latency_ms
    server = ThreadingHTTPServer((host, port), FakeOpenAIHandler)
    bound_port = server.server_address[1]
    log.info(
        "fake_openai_starting",
        host=host,
        port=bound_port,
        mode=mode,
        default_model=default_model,
    )
    return server


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slack-fake-openai",
        description=(
            "Fake OpenAI-compatible /v1/chat/completions server for developing "
            "the watcher SPA without real credentials."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    parser.add_argument(
        "--port", type=int, default=8240, help="Listen port (default 8240, use 0 for ephemeral)."
    )
    parser.add_argument(
        "--mode",
        choices=("echo", "static", "reverse", "fail"),
        default="echo",
        help=(
            "Response strategy: echo (return the prompt), static (always the same), "
            "reverse (reversed transcript), fail (always HTTP 500). Default: echo."
        ),
    )
    parser.add_argument(
        "--response",
        default="OK",
        help=(
            "Body to return in 'static' mode, or the error message in 'fail' mode (default: OK)."
        ),
    )
    parser.add_argument(
        "--default-model",
        default="fake-model",
        help="Model id to echo back in responses (default: fake-model).",
    )
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=0,
        help="Artificial latency per request in ms (default: 0).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    server = run_server(
        host=args.host,
        port=args.port,
        mode=args.mode,
        fixed_response=args.response,
        default_model=args.default_model,
        latency_ms=args.latency_ms,
    )
    bound = server.server_address[1]
    print(
        f"slack-fake-openai listening on http://{args.host}:{bound} (mode={args.mode})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nslack-fake-openai stopped", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
