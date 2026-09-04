"""Tests for per-workspace cache database resolution."""

from __future__ import annotations

import pytest

from slack_cached.config import DEFAULT_DB_NAME
from slack_cached.storage import connect
from slack_cached.workspace import (
    claim_workspace_db,
    last_workspace,
    legacy_db_path,
    offline_db_path,
    sanitize_workspace_name,
    workspace_db_path,
    workspace_name_from_auth,
)


@pytest.fixture(autouse=True)
def _cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


class TestWorkspaceName:
    def test_sanitize_replaces_unsafe_characters(self) -> None:
        assert sanitize_workspace_name("acme corp!") == "acme_corp_"

    def test_sanitize_falls_back_when_empty(self) -> None:
        assert sanitize_workspace_name("...") == "workspace"
        assert sanitize_workspace_name("") == "workspace"

    def test_name_from_auth_prefers_url_subdomain(self) -> None:
        data = {"url": "https://acme.slack.com/", "team_id": "T123"}
        assert workspace_name_from_auth(data) == "acme"

    def test_name_from_auth_falls_back_to_team_id(self) -> None:
        assert workspace_name_from_auth({"team_id": "T123"}) == "T123"

    def test_name_from_auth_generic_fallback(self) -> None:
        assert workspace_name_from_auth({}) == "workspace"


class TestWorkspaceDbPath:
    def test_path_is_per_workspace_directory(self) -> None:
        path = workspace_db_path("acme")
        assert path.parent.name == "acme"
        assert path.name == DEFAULT_DB_NAME

    def test_remember_and_read_last_workspace(self) -> None:
        assert last_workspace() is None
        claim_workspace_db("acme")
        assert last_workspace() == "acme"


class TestClaimWorkspaceDb:
    def test_remembers_workspace_and_returns_path(self) -> None:
        path = claim_workspace_db("acme")

        assert path == workspace_db_path("acme")
        assert last_workspace() == "acme"

    def test_does_not_migrate_legacy_db(self) -> None:
        connect(legacy_db_path()).close()

        claim_workspace_db("acme")

        assert legacy_db_path().exists()
        assert not workspace_db_path("acme").exists()


class TestOfflineResolution:
    def test_prefers_last_used_workspace(self) -> None:
        connect(workspace_db_path("alpha")).close()
        connect(workspace_db_path("beta")).close()
        claim_workspace_db("beta")

        assert offline_db_path() == workspace_db_path("beta")

    def test_uses_single_existing_workspace(self) -> None:
        connect(workspace_db_path("alpha")).close()

        assert offline_db_path() == workspace_db_path("alpha")

    def test_ambiguous_workspaces_require_explicit_choice(self) -> None:
        connect(workspace_db_path("alpha")).close()
        connect(workspace_db_path("beta")).close()

        with pytest.raises(SystemExit, match="alpha, beta"):
            offline_db_path()

    def test_falls_back_to_legacy_db_path(self) -> None:
        connect(legacy_db_path()).close()

        assert offline_db_path() == legacy_db_path()

    def test_defaults_to_legacy_db_path_when_empty(self) -> None:
        assert offline_db_path() == legacy_db_path()
