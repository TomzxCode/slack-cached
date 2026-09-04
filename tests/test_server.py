"""Tests for the web server (``slackx serve``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slack_cached import storage
from slack_cached.config import Credentials
from slack_cached.server import app as server_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


def _populate(db: Path) -> None:
    conn = storage.connect(db)
    try:
        storage.upsert_users(
            conn,
            [
                {
                    "id": "U1",
                    "name": "alice",
                    "real_name": "Alice Smith",
                    "profile": {
                        "image_512": "https://ca.slack-edge.com/U1-512.png",
                        "image_192": "https://ca.slack-edge.com/U1-192.png",
                    },
                },
                {"id": "U2", "name": "bob", "real_name": ""},
            ],
        )
        storage.upsert_channels(
            conn,
            [
                {"id": "C1", "name": "general", "is_private": False},
                {"id": "C2", "name": "secret", "is_private": True},
            ],
        )
        # Standalone root message.
        storage.record_thread_refresh(conn, "C1", "1700000000.000001", None)
        storage.upsert_messages(
            conn,
            "C1",
            "1700000000.000001",
            [{"ts": "1700000000.000001", "user": "U1", "text": "hello world"}],
        )
        # Threaded root plus one reply.
        storage.record_thread_refresh(conn, "C1", "1700000100.000002", "1700000100.000003")
        storage.upsert_messages(
            conn,
            "C1",
            "1700000100.000002",
            [
                {"ts": "1700000100.000002", "user": "U1", "text": "incident about deploys"},
                {"ts": "1700000100.000003", "user": "U2", "text": "a reply"},
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client(db_path: Path) -> TestClient:
    _populate(db_path)
    fastapp = server_app.create_app(db_path=db_path)
    with TestClient(fastapp) as c:  # context manager runs the lifespan
        return c


def test_summary(client: TestClient) -> None:
    body = client.get("/api/summary").json()
    assert body["users"] == 2
    assert body["channels"] == 2
    assert body["messages"] == 3
    assert body["threads"] == 2
    assert body["db_path"].endswith(".db")


def test_list_users(client: TestClient) -> None:
    body = client.get("/api/users").json()
    assert body["user_count"] == 2
    by_id = {u["id"]: u for u in body["users"]}
    assert by_id["U1"]["display_name"] == "Alice Smith (alice)"
    assert by_id["U2"]["display_name"] == "bob"


def test_list_users_avatar_picks_largest_image(client: TestClient) -> None:
    body = client.get("/api/users").json()
    by_id = {u["id"]: u for u in body["users"]}
    assert by_id["U1"]["avatar"] == "https://ca.slack-edge.com/U1-512.png"
    assert by_id["U2"]["avatar"] is None


def test_list_channels_with_counts(client: TestClient) -> None:
    body = client.get("/api/channels").json()
    assert body["channel_count"] == 2
    by_id = {c["id"]: c for c in body["channels"]}
    assert by_id["C1"]["message_count"] == 3
    assert by_id["C1"]["thread_count"] == 2
    assert by_id["C2"]["message_count"] == 0
    # Channels without messages still appear, sorted by name.
    assert [c["name"] for c in body["channels"]] == ["general", "secret"]


def test_channel_messages_newest_first_with_reply_counts(client: TestClient) -> None:
    body = client.get("/api/channels/C1/messages").json()
    assert body["channel"]["name"] == "general"
    assert [m["ts"] for m in body["messages"]] == ["1700000100.000002", "1700000000.000001"]
    assert body["messages"][0]["reply_count"] == 1
    assert body["messages"][0]["latest_reply_ts"] == "1700000100.000003"
    assert body["messages"][0]["user_name"] == "Alice Smith (alice)"
    assert body["has_more"] is False


def test_channel_messages_pagination(client: TestClient) -> None:
    body = client.get(
        "/api/channels/C1/messages",
        params={"before": "1700000100.000002", "limit": 1},
    ).json()
    assert [m["ts"] for m in body["messages"]] == ["1700000000.000001"]
    assert body["has_more"] is True


def test_thread_messages(client: TestClient) -> None:
    body = client.get("/api/channels/C1/threads/1700000100.000002").json()
    assert [m["ts"] for m in body["messages"]] == ["1700000100.000002", "1700000100.000003"]
    assert body["messages"][1]["user_name"] == "bob"


def test_thread_messages_404(client: TestClient) -> None:
    response = client.get("/api/channels/C1/threads/9999")
    assert response.status_code == 404


def test_channel_messages_unknown_channel_returns_empty(client: TestClient) -> None:
    # Search can cache messages for channels whose row was never fetched.
    body = client.get("/api/channels/CNONE/messages").json()
    assert body["messages"] == []
    assert body["channel"]["name"] is None


def test_search_fts(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "deploys"}).json()
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["ts"] == "1700000100.000002"
    assert hit["channel_name"] == "general"
    assert hit["user_name"] == "Alice Smith (alice)"
    assert "deploys" in (hit["snippet"] or hit["text"]).lower()


def test_search_hostile_query_is_safe(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": 'OR NEAR ("'}).json()
    assert body["hits"] == []


def _without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_app,
        "load_credentials",
        lambda require=True: Credentials(token="", cookie=None),
    )


def test_refresh_channel_without_credentials_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_credentials(monkeypatch)
    response = client.post("/api/channels/C1/refresh")
    assert response.status_code == 503
    assert "No Slack credentials" in response.json()["detail"]


def test_refresh_users_without_credentials_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_credentials(monkeypatch)
    assert client.post("/api/users/refresh").status_code == 503
    assert client.post("/api/channels/refresh").status_code == 503
    assert client.post("/api/channels/C1/threads/1700000100.000002/refresh").status_code == 503


def test_refresh_channel_unknown_channel_404(client: TestClient) -> None:
    assert client.post("/api/channels/CNOPE/refresh").status_code == 404


def test_index_serves_ui(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "vue.global.prod.js" in response.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
