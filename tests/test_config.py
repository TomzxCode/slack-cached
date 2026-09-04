"""Tests for credentials/config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from slack_cached import config


def test_load_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_COOKIE", "cookie-value")
    creds = config.load_credentials()
    assert creds.token == "xoxb-test"
    assert creds.cookie == "cookie-value"


def test_load_credentials_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent")
    creds = config.load_credentials()
    assert creds.token == "xoxb-test"
    assert creds.cookie is None


def test_load_credentials_from_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg_dir = tmp_path / "slackx"
    cfg_dir.mkdir()
    (cfg_dir / "config").write_text("SLACK_TOKEN=xoxc-file\nSLACK_COOKIE=cookie-file\n")

    creds = config.load_credentials()
    assert creds.token == "xoxc-file"
    assert creds.cookie == "cookie-file"


def test_load_credentials_env_overrides_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-env")
    monkeypatch.delenv("SLACK_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg_dir = tmp_path / "slackx"
    cfg_dir.mkdir()
    (cfg_dir / "config").write_text("SLACK_TOKEN=xoxc-file\nSLACK_COOKIE=cookie-file\n")

    creds = config.load_credentials()
    assert creds.token == "xoxb-env"
    assert creds.cookie == "cookie-file"


def test_load_credentials_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(SystemExit):
        config.load_credentials()


def test_default_db_path_uses_xdg_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert config.default_db_path() == tmp_path / "slackx" / "threads.db"
