"""Tests for slack_watcher.storage.

The watcher DB is separate from the slack-cached cache DB. These tests cover
the schema, validation helpers, and CRUD round-trips.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from slack_watcher.storage import (
    DEFAULT_SETTINGS,
    QueryRow,
    RunRow,
    connect,
    delete_query,
    each_query_state_for,
    get_all_settings,
    get_query,
    get_query_state,
    get_setting,
    insert_query,
    insert_run,
    list_queries,
    list_runs,
    set_setting,
    update_query,
    upsert_query_state,
    validate_dedup,
    validate_source,
)


def _sample_query(query_id: str = "q1", **overrides) -> QueryRow:
    base = QueryRow(
        id=query_id,
        name="test",
        source_kind="channels",
        source_config={"channel_ids": ["C001"]},
        prompt="Summarize: {{thread}}",
        interval="5m",
        lookback="1h",
        dedup="new_messages",
        full_threads=True,
        model="gpt-4o-mini",
        enabled=True,
        created_at=1.0,
        updated_at=1.0,
    )
    return replace(base, **overrides) if overrides else base


def test_connect_creates_schema(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    assert {"queries", "runs", "query_state", "settings"}.issubset(names)
    conn.close()


def test_settings_roundtrip(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    # Defaults are present without any write.
    for k, v in DEFAULT_SETTINGS.items():
        assert get_setting(conn, k) == v
    set_setting(conn, "llm_base_url", "https://example.com/v1")
    set_setting(conn, "llm_api_key", "sk-test")
    s = get_all_settings(conn)
    assert s["llm_base_url"] == "https://example.com/v1"
    assert s["llm_api_key"] == "sk-test"
    # Untouched defaults survive.
    assert s["default_model"] == DEFAULT_SETTINGS["default_model"]
    conn.close()


def test_validate_source_channels_requires_list() -> None:
    validate_source("channels", {"channel_ids": ["C001", "C002"]})
    # Empty list is allowed (UI lets you create a query and add channels later).
    validate_source("channels", {"channel_ids": []})
    with pytest.raises(ValueError):
        validate_source("channels", {})
    with pytest.raises(ValueError):
        validate_source("channels", {"channel_ids": ["C001", 5]})
    with pytest.raises(ValueError):
        validate_source("channels", {"channel_ids": "C001"})
    with pytest.raises(ValueError):
        validate_source("channels", {"channel_ids": ["", "C001"]})


def test_validate_source_dms_and_mentions_accept_optional() -> None:
    validate_source("dms", {})
    validate_source("dms", {"include_mpim": True})
    validate_source("mentions", {})


def test_validate_source_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        validate_source("bogus", {})


def test_validate_dedup() -> None:
    for ok in ("new_messages", "every_cycle", "once_per_thread"):
        validate_dedup(ok)
    with pytest.raises(ValueError):
        validate_dedup("never")


def test_insert_get_update_query(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    q = _sample_query()
    insert_query(conn, q)
    assert len(list_queries(conn)) == 1
    fetched = get_query(conn, "q1")
    assert fetched is not None
    assert fetched.source_config == {"channel_ids": ["C001"]}
    assert fetched.full_threads is True
    assert fetched.enabled is True

    q2 = replace(q, name="renamed", enabled=False, updated_at=2.0)
    update_query(conn, q2)
    fetched = get_query(conn, "q1")
    assert fetched is not None
    assert fetched.name == "renamed"
    assert fetched.enabled is False
    assert fetched.updated_at == 2.0
    conn.close()


def test_delete_query_cascades_runs_and_state(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    insert_query(conn, _sample_query("q1"))
    insert_run(conn, _sample_run("r1", "q1"))
    upsert_query_state(conn, "q1", "C001", "1700000000.000000", "1700000000.000100", 1.0, True)
    assert len(list_runs(conn, "q1")) == 1
    assert get_query_state(conn, "q1", "C001", "1700000000.000000") is not None

    delete_query(conn, "q1")
    assert get_query(conn, "q1") is None
    assert list_runs(conn, "q1") == []
    assert get_query_state(conn, "q1", "C001", "1700000000.000000") is None
    conn.close()


def _sample_run(run_id: str = "r1", query_id: str = "q1", **overrides) -> RunRow:
    base = RunRow(
        id=run_id,
        query_id=query_id,
        channel="C001",
        thread_ts="1700000000.000000",
        prompt="hi",
        response="bye",
        error=None,
        model="gpt-4o-mini",
        elapsed_ms=42,
        prompt_tokens=10,
        completion_tokens=5,
        ran_at=1.5,
    )
    return replace(base, **overrides) if overrides else base


def test_list_runs_orders_by_ran_at_desc(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    insert_query(conn, _sample_query("q1"))
    insert_run(conn, _sample_run("r1", "q1", ran_at=1.0))
    insert_run(conn, _sample_run("r2", "q1", ran_at=3.0))
    insert_run(conn, _sample_run("r3", "q1", ran_at=2.0))
    runs = list_runs(conn, "q1")
    assert [r.ran_at for r in runs] == [3.0, 2.0, 1.0]
    # Limit and offset.
    assert len(list_runs(conn, "q1", limit=2)) == 2
    assert list_runs(conn, "q1", limit=2, offset=1)[0].ran_at == 2.0
    conn.close()


def test_list_runs_filter_by_query(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    insert_query(conn, _sample_query("q1"))
    insert_query(conn, _sample_query("q2"))
    insert_run(conn, _sample_run("r1", "q1"))
    insert_run(conn, _sample_run("r2", "q2"))
    assert len(list_runs(conn, "q1")) == 1
    assert len(list_runs(conn, "q2")) == 1
    assert len(list_runs(conn)) == 2
    conn.close()


def test_upsert_query_state_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    insert_query(conn, _sample_query("q1"))
    upsert_query_state(conn, "q1", "C001", "1700000000.000000", "1700000000.000100", 1.0, False)
    upsert_query_state(conn, "q1", "C001", "1700000000.000000", "1700000000.000200", 2.0, True)
    state = get_query_state(conn, "q1", "C001", "1700000000.000000")
    assert state is not None
    assert state.last_seen_ts == "1700000000.000200"
    assert state.last_run_at == 2.0
    assert state.processed is True
    conn.close()


def test_each_query_state_for_bulk_loads_only_matching(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    insert_query(conn, _sample_query("q1"))
    upsert_query_state(conn, "q1", "C001", "1700000000.000000", None, 1.0, False)
    upsert_query_state(conn, "q1", "C002", "1700000000.000000", None, 1.0, False)
    out = each_query_state_for(
        conn, "q1", [("C001", "1700000000.000000"), ("C999", "1700000000.000000")]
    )
    assert ("C001", "1700000000.000000") in out
    assert ("C999", "1700000000.000000") not in out
    assert ("C002", "1700000000.000000") not in out
    conn.close()


def test_each_query_state_handles_empty_input(tmp_path: Path) -> None:
    conn = connect(tmp_path / "w.db")
    assert each_query_state_for(conn, "q1", []) == {}
    conn.close()
