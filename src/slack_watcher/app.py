"""FastAPI app for slack-watcher.

Exposes:

  Queries
    GET    /api/queries                 list
    POST   /api/queries                 create
    GET    /api/queries/{id}            read
    PUT    /api/queries/{id}            update
    DELETE /api/queries/{id}            delete
    POST   /api/queries/{id}/run        trigger an immediate cycle (best-effort)

  Runs
    GET    /api/runs                    list, ?query_id=...&limit=...&offset=...

  Settings
    GET    /api/settings
    PUT    /api/settings                partial update

  Cache helpers
    GET    /api/cache/channels          list cached Slack channels
    GET    /api/cache/users             list cached Slack users (small subset)

  Health
    GET    /api/health

The SPA is served at / by StaticFiles when web/dist exists.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from slack_cached.async_slack_api import AsyncSlackClient, RateLimitState
from slack_cached.config import Credentials, load_api_base_url, load_credentials
from slack_cached.slack_api import DEFAULT_API_BASE, REQUEST_TIMEOUT
from slack_cached.storage import connect as connect_cache

from .scheduler import (
    SchedulerHandle,
    run_query_cycle,
    shutdown_scheduler,
    start_scheduler,
)
from .storage import (
    QueryRow,
    RunRow,
    default_db_path,
    delete_query,
    get_all_settings,
    get_query,
    insert_query,
    list_queries,
    list_runs,
    set_setting,
    update_query,
    validate_dedup,
    validate_source,
)
from .storage import (
    connect as connect_watcher,
)

log = structlog.get_logger(__name__)


# --- pydantic models -------------------------------------------------------


class SourceConfigChannels(BaseModel):
    channel_ids: list[str] = Field(..., min_length=0)


class QueryIn(BaseModel):
    """Payload for creating or updating a query."""

    name: str = Field(..., min_length=1, max_length=200)
    source_kind: str = Field(..., pattern="^(channels|dms|mentions)$")
    source_config: dict[str, Any] = Field(default_factory=dict)
    prompt: str = Field(..., min_length=1)
    interval: str = Field(..., pattern=r"^\d+(\.\d+)?[dhms](\d+(\.\d+)?[dhms])*$|^all$")
    lookback: str = Field(..., pattern=r"^\d+(\.\d+)?[dhms](\d+(\.\d+)?[dhms])*$|^all$")
    dedup: str = Field(..., pattern="^(new_messages|every_cycle|once_per_thread)$")
    full_threads: bool = False
    model: str = Field(..., min_length=1)
    enabled: bool = True


class QueryOut(BaseModel):
    id: str
    name: str
    source_kind: str
    source_config: dict[str, Any]
    prompt: str
    interval: str
    lookback: str
    dedup: str
    full_threads: bool
    model: str
    enabled: bool
    created_at: float
    updated_at: float


class RunOut(BaseModel):
    id: str
    query_id: str
    channel: str
    thread_ts: str
    prompt: str
    response: str | None
    error: str | None
    model: str
    elapsed_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    ran_at: float


class SettingsIn(BaseModel):
    """Partial settings update. All keys optional."""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    default_model: str | None = None


class SettingsOut(BaseModel):
    llm_base_url: str
    llm_api_key: str
    default_model: str


class CacheChannel(BaseModel):
    id: str
    name: str | None
    is_private: bool | None


class HealthOut(BaseModel):
    ok: bool
    scheduler_running: bool
    db_path: str
    cache_db_path: str


def _row_to_out(row: QueryRow) -> QueryOut:
    return QueryOut(
        id=row.id,
        name=row.name,
        source_kind=row.source_kind,
        source_config=row.source_config,
        prompt=row.prompt,
        interval=row.interval,
        lookback=row.lookback,
        dedup=row.dedup,
        full_threads=row.full_threads,
        model=row.model,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_to_out(run: RunRow) -> RunOut:
    return RunOut(
        id=run.id,
        query_id=run.query_id,
        channel=run.channel,
        thread_ts=run.thread_ts,
        prompt=run.prompt,
        response=run.response,
        error=run.error,
        model=run.model,
        elapsed_ms=run.elapsed_ms,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        ran_at=run.ran_at,
    )


# --- app factory -----------------------------------------------------------


def create_app(
    *,
    db_path: Path | None = None,
    cache_db_path: Path | None = None,
    slack_base_url: str | None = None,
    credentials: Credentials | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app and start the scheduler on lifespan startup.

    Most CLI callers pass None for the optional args to get sensible defaults
    from slack-cached's config layer. Tests pass explicit paths to point at
    temp files.
    """
    db_path = db_path or default_db_path()
    cache_db_path = cache_db_path or _default_cache_db_path()
    if slack_base_url is None:
        slack_base_url = load_api_base_url() or DEFAULT_API_BASE
    if credentials is None:
        try:
            credentials = load_credentials()
        except SystemExit:
            # Allow the app to start without Slack credentials. The scheduler
            # will simply fail any source fetches until credentials are
            # provided (typically via env vars when using the fake server).
            log.warning("watcher_no_slack_credentials")
            credentials = Credentials(token="", cookie=None)

    state: dict[str, Any] = {"scheduler_task": None, "handle": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ensure both DBs are initialized.
        connect_watcher(db_path).close()
        connect_cache(cache_db_path).close()

        task = start_scheduler(
            db_path=db_path,
            cache_db_path=cache_db_path,
            slack_base_url=slack_base_url,
            credentials=credentials,
        )
        state["scheduler_task"] = task
        log.info("watcher_app_start", db=str(db_path), cache=str(cache_db_path))
        try:
            yield
        finally:
            await shutdown_scheduler(task)
            log.info("watcher_app_stop")

    app = FastAPI(title="slack-watcher", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.db_path = db_path
    app.state.cache_db_path = cache_db_path
    app.state.slack_base_url = slack_base_url
    app.state.credentials = credentials

    # Routes ---------------------------------------------------------------

    @app.get("/api/health")
    def health() -> HealthOut:
        return HealthOut(
            ok=True,
            scheduler_running=state["scheduler_task"] is not None,
            db_path=str(db_path),
            cache_db_path=str(cache_db_path),
        )

    @app.get("/api/queries")
    def get_queries() -> list[QueryOut]:
        with connect_watcher(db_path) as conn:
            return [_row_to_out(q) for q in list_queries(conn)]

    @app.post("/api/queries", status_code=201)
    def post_queries(body: QueryIn) -> QueryOut:
        validate_source(body.source_kind, body.source_config)
        validate_dedup(body.dedup)
        row = QueryRow(
            id=str(uuid.uuid4()),
            name=body.name,
            source_kind=body.source_kind,
            source_config=body.source_config,
            prompt=body.prompt,
            interval=body.interval,
            lookback=body.lookback,
            dedup=body.dedup,
            full_threads=body.full_threads,
            model=body.model,
            enabled=body.enabled,
            created_at=_now(),
            updated_at=_now(),
        )
        with connect_watcher(db_path) as conn:
            insert_query(conn, row)
            return _row_to_out(row)

    @app.get("/api/queries/{query_id}")
    def get_one_query(query_id: str) -> QueryOut:
        with connect_watcher(db_path) as conn:
            row = get_query(conn, query_id)
        if row is None:
            raise HTTPException(404, "query not found")
        return _row_to_out(row)

    @app.put("/api/queries/{query_id}")
    def put_one_query(query_id: str, body: QueryIn) -> QueryOut:
        validate_source(body.source_kind, body.source_config)
        validate_dedup(body.dedup)
        with connect_watcher(db_path) as conn:
            existing = get_query(conn, query_id)
            if existing is None:
                raise HTTPException(404, "query not found")
            row = QueryRow(
                id=query_id,
                name=body.name,
                source_kind=body.source_kind,
                source_config=body.source_config,
                prompt=body.prompt,
                interval=body.interval,
                lookback=body.lookback,
                dedup=body.dedup,
                full_threads=body.full_threads,
                model=body.model,
                enabled=body.enabled,
                created_at=existing.created_at,
                updated_at=_now(),
            )
            update_query(conn, row)
            return _row_to_out(row)

    @app.delete("/api/queries/{query_id}", status_code=204)
    def delete_one_query(query_id: str) -> None:
        with connect_watcher(db_path) as conn:
            existing = get_query(conn, query_id)
            if existing is None:
                raise HTTPException(404, "query not found")
            delete_query(conn, query_id)

    @app.post("/api/queries/{query_id}/run")
    async def trigger_query(query_id: str) -> dict[str, Any]:
        """Run one cycle immediately. Best-effort: returns 202 once scheduled."""
        with connect_watcher(db_path) as conn:
            row = get_query(conn, query_id)
        if row is None:
            raise HTTPException(404, "query not found")

        handle = await _make_one_shot_clients(app.state)
        watcher_conn = connect_watcher(db_path)

        async def _go():
            try:
                await run_query_cycle(
                    row,
                    slack_client=handle.slack_client,
                    httpx_client=handle.httpx_client,
                    watcher_db=watcher_conn,
                    cache_db_path=cache_db_path,
                )
            finally:
                watcher_conn.close()
                await handle.httpx_client.aclose()

        asyncio.create_task(_go())
        return {"status": "scheduled", "query_id": query_id}

    @app.get("/api/runs")
    def get_runs(
        query_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunOut]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        with connect_watcher(db_path) as conn:
            return [_run_to_out(r) for r in list_runs(conn, query_id, limit, offset)]

    @app.get("/api/settings")
    def get_settings_endpoint() -> SettingsOut:
        with connect_watcher(db_path) as conn:
            s = get_all_settings(conn)
        return SettingsOut(
            llm_base_url=s["llm_base_url"],
            llm_api_key=_mask(s["llm_api_key"]),
            default_model=s["default_model"],
        )

    @app.put("/api/settings")
    def put_settings_endpoint(body: SettingsIn) -> SettingsOut:
        with connect_watcher(db_path) as conn:
            if body.llm_base_url is not None:
                set_setting(conn, "llm_base_url", body.llm_base_url)
            if body.llm_api_key is not None:
                # An empty string clears the key; "********" means "leave alone".
                if body.llm_api_key and body.llm_api_key.startswith("*"):
                    pass
                else:
                    set_setting(conn, "llm_api_key", body.llm_api_key)
            if body.default_model is not None:
                set_setting(conn, "default_model", body.default_model)
            s = get_all_settings(conn)
        return SettingsOut(
            llm_base_url=s["llm_base_url"],
            llm_api_key=_mask(s["llm_api_key"]),
            default_model=s["default_model"],
        )

    @app.get("/api/cache/channels")
    def get_cache_channels() -> list[CacheChannel]:
        from slack_cached.storage import load_channels

        with connect_cache(cache_db_path) as conn:
            return [
                CacheChannel(id=c.id, name=c.name, is_private=c.is_private)
                for c in load_channels(conn)
            ]

    @app.get("/api/cache/users")
    def get_cache_users(limit: int = 200) -> list[dict[str, Any]]:
        from slack_cached.storage import load_users

        with connect_cache(cache_db_path) as conn:
            users = load_users(conn)[:limit]
        return [{"id": u.id, "name": u.name, "real_name": u.real_name} for u in users]

    @app.get("/api/templates")
    def get_templates() -> dict[str, str]:
        return DEFAULT_PROMPT_TEMPLATES

    # Static SPA ------------------------------------------------------------

    if web_dist and web_dist.is_dir():
        _mount_spa(app, web_dist)
    else:
        # Allow override by env so production users can build separately.
        env_web = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
        if env_web.is_dir():
            _mount_spa(app, env_web)

    return app


def _mount_spa(app: FastAPI, web_dist: Path) -> None:
    """Mount the built SPA, falling back to index.html for client routes."""
    assets = web_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa_index(full_path: str) -> Any:
        # Don't shadow /api/* (already declared above; FastAPI matches earlier
        # routes first, but defensive guard here).
        if full_path.startswith("api/"):
            raise HTTPException(404)
        candidate = web_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = web_dist / "index.html"
        if not index.is_file():
            raise HTTPException(404, "web/dist not built; run `npm run build` in web/")
        return FileResponse(index)


async def _make_one_shot_clients(state: Any) -> SchedulerHandle:
    """Build a one-shot SchedulerHandle just for the manual /run endpoint."""
    rate_limit_state = RateLimitState()
    httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT))
    slack_client = AsyncSlackClient(
        state.credentials,
        base_url=state.slack_base_url,
        client=httpx_client,
        rate_limit_state=rate_limit_state,
    )
    handle = SchedulerHandle(
        stop=asyncio.Event(),
        task=asyncio.current_task(),
        httpx_client=httpx_client,
        slack_client=slack_client,
        db_path=state.db_path,
        cache_db_path=state.cache_db_path,
    )
    return handle


def _now() -> float:
    import time

    return time.time()


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 6:
        return "*" * len(secret)
    return secret[:3] + "*" * (len(secret) - 6) + secret[-3:]


def _default_cache_db_path() -> Path:
    from slack_cached.config import default_db_path as slack_default_db

    return slack_default_db()


DEFAULT_PROMPT_TEMPLATES: dict[str, str] = {
    "draft_reply": (
        "You are an expert teammate helping draft a response in a Slack thread.\n\n"
        "Thread so far:\n{{thread}}\n\n"
        "Write a concise, friendly reply that moves the conversation forward. "
        "If you need more information, say so explicitly."
    ),
    "summarize": (
        "Summarize this Slack thread in 3-5 bullet points, capturing decisions "
        "and open questions.\n\nThread:\n{{thread}}"
    ),
    "action_items": (
        "Extract action items from this Slack thread as a markdown checklist. "
        "Include owners where mentioned.\n\nThread:\n{{thread}}"
    ),
    "detect_question": (
        "Does this Slack thread contain a question directed at me? "
        "Reply with 'YES: <one-line summary>' or 'NO'.\n\nThread:\n{{thread}}"
    ),
}


__all__ = ["create_app", "DEFAULT_PROMPT_TEMPLATES"]
