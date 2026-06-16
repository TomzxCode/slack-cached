"""End-to-end integration test for the watcher app.

Brings up the in-process fake Slack server, an in-process fake OpenAI-style
endpoint, then drives the FastAPI app with the TestClient to create a query
and trigger a manual run. Asserts runs are persisted with the LLM response.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from slack_cached.config import Credentials
from slack_cached.fake_slack import FakeSlackHandler, Workspace
from slack_watcher.app import create_app
from slack_watcher.storage import connect as connect_watcher
from slack_watcher.storage import set_setting


@pytest.fixture
def fake_slack() -> tuple[ThreadingHTTPServer, Workspace]:
    """Start the fake Slack server on an ephemeral port."""
    workspace = Workspace(seed=42, num_users=10, num_channels=4, num_threads=8)
    FakeSlackHandler.workspace = workspace
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSlackHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Attach port as an attribute for convenience.
    workspace._port = port  # type: ignore[attr-defined]
    yield server, workspace
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake_llm() -> tuple[ThreadingHTTPServer, list[dict[str, Any]]]:
    """Start a fake OpenAI-style chat-completion server.

    Yields (server, calls) where *calls* is the list of received JSON bodies
    so tests can inspect what was posted.
    """
    calls: list[dict[str, Any]] = []

    class LLMHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode() if length else ""
            try:
                calls.append(json.loads(payload))
            except ValueError:
                calls.append({"raw": payload})
            response = {
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "model": "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                },
            }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401,ARG002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, calls
    server.shutdown()
    server.server_close()


@pytest.fixture
def watcher_app(tmp_path: Path, fake_slack, fake_llm):
    """Build the FastAPI app pointed at the fake Slack and fake LLM."""
    slack_server, workspace = fake_slack
    llm_server, _calls = fake_llm
    slack_port = slack_server.server_address[1]
    llm_port = llm_server.server_address[1]

    cache_db = tmp_path / "cache.db"
    watcher_db = tmp_path / "watcher.db"

    # Pre-populate settings.
    wconn = connect_watcher(watcher_db)
    set_setting(wconn, "llm_base_url", f"http://127.0.0.1:{llm_port}/v1")
    set_setting(wconn, "llm_api_key", "test-key")
    set_setting(wconn, "default_model", "fake-model")
    wconn.close()

    app = create_app(
        db_path=watcher_db,
        cache_db_path=cache_db,
        slack_base_url=f"http://127.0.0.1:{slack_port}/api",
        credentials=Credentials(token="xoxb-fake", cookie=None),
        # Pass an empty dir so the SPA mount does not shadow /api during tests.
        web_dist=None,
    )
    return app, workspace, watcher_db


def _wait_for_runs(watcher_db: Path, query_id: str, min_count: int, timeout: float = 10.0) -> int:
    """Poll the DB until *min_count* runs exist for the query or we time out."""
    import time

    from slack_watcher.storage import connect as connect_watcher
    from slack_watcher.storage import list_runs

    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = connect_watcher(watcher_db)
        try:
            runs = list_runs(conn, query_id)
        finally:
            conn.close()
        if len(runs) >= min_count:
            return len(runs)
        time.sleep(0.1)
    return len(runs)


def test_health_endpoint_reports_running_scheduler(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["scheduler_running"] is True


def test_settings_round_trip_through_api(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        # Pre-populated by the fixture.
        r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["default_model"] == "fake-model"
        assert body["llm_api_key"] != "test-key"  # masked, never raw
        assert "*" in body["llm_api_key"]

        # Update base URL; the masked api key "****" should NOT overwrite the key.
        r = client.put(
            "/api/settings",
            json={"llm_base_url": "https://other.example/v1", "llm_api_key": "***"},
        )
        assert r.status_code == 200
        assert r.json()["llm_base_url"] == "https://other.example/v1"


def test_query_crud_lifecycle(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        body = {
            "name": "lifecycle",
            "source_kind": "channels",
            "source_config": {"channel_ids": ["C0001"]},
            "prompt": "Summarize: {{thread}}",
            "interval": "5m",
            "lookback": "all",
            "dedup": "every_cycle",
            "full_threads": True,
            "model": "fake-model",
            "enabled": False,
        }
        r = client.post("/api/queries", json=body)
        assert r.status_code == 201
        qid = r.json()["id"]

        # List and get.
        assert len(client.get("/api/queries").json()) == 1
        fetched = client.get(f"/api/queries/{qid}").json()
        assert fetched["name"] == "lifecycle"

        # Update.
        body["name"] = "renamed"
        r = client.put(f"/api/queries/{qid}", json=body)
        assert r.status_code == 200
        assert r.json()["name"] == "renamed"

        # Delete.
        r = client.delete(f"/api/queries/{qid}")
        assert r.status_code == 204
        assert client.get("/api/queries").json() == []


def test_invalid_source_kind_rejected(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        r = client.post(
            "/api/queries",
            json={
                "name": "bad",
                "source_kind": "bogus",
                "source_config": {},
                "prompt": "x",
                "interval": "5m",
                "lookback": "1h",
                "dedup": "every_cycle",
                "full_threads": False,
                "model": "fake-model",
                "enabled": False,
            },
        )
        assert r.status_code == 422  # pydantic pattern guard


def test_invalid_dedup_rejected(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        r = client.post(
            "/api/queries",
            json={
                "name": "bad",
                "source_kind": "channels",
                "source_config": {"channel_ids": ["C0001"]},
                "prompt": "x",
                "interval": "5m",
                "lookback": "1h",
                "dedup": "bogus",
                "full_threads": False,
                "model": "fake-model",
                "enabled": False,
            },
        )
        assert r.status_code == 422


def test_trigger_run_produces_runs_with_llm_response(watcher_app, fake_llm) -> None:
    app, workspace, watcher_db = watcher_app
    _slack_server, _workspace = (None, workspace)
    _llm_server, calls = fake_llm

    # Pick a real channel that the fake workspace has threads in.
    channel_id = sorted({ch for (ch, _) in workspace.threads})[0]
    expected_thread_count = sum(1 for (c, _) in workspace.threads if c == channel_id)

    with TestClient(app) as client:
        body = {
            "name": "triggered",
            "source_kind": "channels",
            "source_config": {"channel_ids": [channel_id]},
            "prompt": "Summarize: {{thread}}",
            "interval": "5m",
            "lookback": "all",
            "dedup": "every_cycle",
            "full_threads": True,
            "model": "fake-model",
            "enabled": False,
        }
        r = client.post("/api/queries", json=body)
        assert r.status_code == 201
        qid = r.json()["id"]

        # Trigger and wait for runs.
        r = client.post(f"/api/queries/{qid}/run")
        assert r.status_code == 200
        assert r.json()["status"] == "scheduled"

        run_count = _wait_for_runs(watcher_db, qid, expected_thread_count, timeout=15.0)
        assert run_count >= expected_thread_count, (
            f"expected at least {expected_thread_count} runs, got {run_count}"
        )

        # Inspect runs via the API.
        r = client.get(f"/api/runs?query_id={qid}")
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) >= 1
        sample = runs[0]
        assert sample["query_id"] == qid
        assert sample["channel"] == channel_id
        assert sample["response"] == "OK"
        assert sample["error"] is None
        assert sample["model"] == "fake-model"
        assert sample["prompt_tokens"] == 11
        assert sample["completion_tokens"] == 3

        # The LLM was actually called with a rendered prompt (no template tags).
        assert len(calls) >= 1
        sent_prompt = calls[0]["messages"][-1]["content"]
        assert "{{thread}}" not in sent_prompt
        assert sent_prompt.startswith("Summarize:")


def test_delete_query_cascades_to_runs(watcher_app) -> None:
    app, workspace, watcher_db = watcher_app
    channel_id = sorted({ch for (ch, _) in workspace.threads})[0]
    with TestClient(app) as client:
        body = {
            "name": "doomed",
            "source_kind": "channels",
            "source_config": {"channel_ids": [channel_id]},
            "prompt": "x: {{thread}}",
            "interval": "5m",
            "lookback": "all",
            "dedup": "every_cycle",
            "full_threads": True,
            "model": "fake-model",
            "enabled": False,
        }
        r = client.post("/api/queries", json=body)
        qid = r.json()["id"]
        client.post(f"/api/queries/{qid}/run")
        _wait_for_runs(watcher_db, qid, 1, timeout=10.0)
        assert len(client.get(f"/api/runs?query_id={qid}").json()) >= 1

        client.delete(f"/api/queries/{qid}")
        assert client.get(f"/api/runs?query_id={qid}").json() == []


def test_templates_endpoint_returns_known_set(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        r = client.get("/api/templates")
        assert r.status_code == 200
        keys = set(r.json().keys())
        assert {"summarize", "draft_reply", "action_items", "detect_question"}.issubset(keys)


def test_cache_channels_endpoint_uses_shared_cache(watcher_app) -> None:
    app, _workspace, _db = watcher_app
    with TestClient(app) as client:
        # The fake workspace exposes channels via Slack's conversations.list.
        # Our source code only populates the cache by running a query, so do that
        # first against a "channels" source, then read back.
        body = {
            "name": "warming",
            "source_kind": "channels",
            "source_config": {"channel_ids": ["C0001"]},
            "prompt": "warmup: {{thread}}",
            "interval": "5m",
            "lookback": "all",
            "dedup": "every_cycle",
            "full_threads": False,
            "model": "fake-model",
            "enabled": False,
        }
        r = client.post("/api/queries", json=body)
        qid = r.json()["id"]
        client.post(f"/api/queries/{qid}/run")
        # channels endpoint always returns 200 even if empty.
        r = client.get("/api/cache/channels")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
