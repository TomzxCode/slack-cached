"""Tests for slack_watcher.fake_llm.

Brings up the ThreadingHTTPServer on an ephemeral port and drives it with
the real :func:`slack_watcher.llm.chat_completion` client so we verify both
sides of the contract together.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from slack_watcher.fake_llm import run_server
from slack_watcher.llm import LLMError, chat_completion


@pytest.fixture
def llm_server():
    """Yield (server, base_url) for a fresh fake LLM on an ephemeral port."""

    def _start(mode: str = "echo", response: str = "OK", latency_ms: int = 0):
        server = run_server(
            host="127.0.0.1",
            port=0,
            mode=mode,
            fixed_response=response,
            latency_ms=latency_ms,
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{port}/v1"

    yield _start


def _complete(base_url: str, prompt: str, *, model: str = "fake-model") -> object:
    """Run the real LLM client against *base_url* synchronously."""
    return asyncio.run(_complete_async(base_url, prompt, model))


async def _complete_async(base_url: str, prompt: str, model: str):
    async with httpx.AsyncClient(timeout=5) as client:
        return await chat_completion(
            client, base_url=base_url, api_key="k", model=model, prompt=prompt
        )


def test_models_endpoint_lists_default_model(llm_server) -> None:
    server, base_url = llm_server()
    try:
        base = base_url.rsplit("/", 1)[0]
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{base}/v1/models")
            assert r.status_code == 200
            data = r.json()
            assert data["object"] == "list"
            assert isinstance(data["data"], list)
            assert data["data"][0]["id"] == "fake-model"
    finally:
        server.shutdown()
        server.server_close()


def test_root_endpoint_reports_mode(llm_server) -> None:
    server, base_url = llm_server(mode="reverse")
    try:
        base = base_url.rsplit("/", 1)[0]
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{base}/")
            assert r.status_code == 200
            body = r.json()
            assert body["service"] == "fake-openai"
            assert body["mode"] == "reverse"
    finally:
        server.shutdown()
        server.server_close()


def test_echo_mode_returns_truncated_prompt(llm_server) -> None:
    server, base_url = llm_server(mode="echo")
    try:
        long_prompt = "x" * 1500
        resp = _complete(base_url, long_prompt)
        assert resp.text.startswith("ECHO: ")
        # Truncation kicks in past 800 chars.
        assert len(resp.text) < len(long_prompt)
        assert "truncated" in resp.text
        assert resp.prompt_tokens is not None and resp.prompt_tokens >= 1
        assert resp.completion_tokens is not None and resp.completion_tokens >= 1
        assert resp.model == "fake-model"
    finally:
        server.shutdown()
        server.server_close()


def test_static_mode_returns_fixed_string(llm_server) -> None:
    server, base_url = llm_server(mode="static", response="summarized!")
    try:
        resp = _complete(base_url, "anything goes")
        assert resp.text == "summarized!"
    finally:
        server.shutdown()
        server.server_close()


def test_reverse_mode_returns_messages_in_reverse(llm_server) -> None:
    server, base_url = llm_server(mode="reverse")
    try:
        # The fake_llm only inspects the messages list inside the request body.
        # The chat_completion client always sends a single user message, so the
        # reverse output contains exactly one "[user]" line.
        resp = _complete(base_url, "only message")
        assert "[user]" in resp.text
        assert "only message" in resp.text
    finally:
        server.shutdown()
        server.server_close()


def test_fail_mode_returns_500_and_client_raises(llm_server) -> None:
    server, base_url = llm_server(mode="fail", response="forced failure")
    try:
        with pytest.raises(LLMError) as exc:
            _complete(base_url, "anything")
        assert "HTTP 500" in str(exc.value)
        assert "forced failure" in str(exc.value)
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_post_path_returns_404(llm_server) -> None:
    server, base_url = llm_server()
    try:
        with httpx.Client(timeout=5) as c:
            r = c.post(f"{base_url}/unknown", json={"messages": []})
            assert r.status_code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_invalid_json_body_returns_400(llm_server) -> None:
    server, base_url = llm_server()
    try:
        with httpx.Client(timeout=5) as c:
            r = c.post(
                f"{base_url}/chat/completions",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_class_attributes_are_persisted_across_requests(llm_server) -> None:
    """Verifies that FakeOpenAIHandler reads its config from class attributes
    set by run_server, not from the request."""
    server, base_url = llm_server(mode="static", response="custom-marker")
    try:
        # Make two sequential calls to ensure config persists across requests.
        r1 = _complete(base_url, "first")
        r2 = _complete(base_url, "second")
        assert r1.text == "custom-marker"
        assert r2.text == "custom-marker"
    finally:
        server.shutdown()
        server.server_close()
