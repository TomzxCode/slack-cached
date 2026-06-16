"""Per-query async scheduler.

The watcher runs one :class:`QueryRunner` asyncio task per enabled query.
Each task owns its own poll loop:

  1. Sleep for the configured interval.
  2. Discover candidate threads via the query's source.
  3. Apply the dedup strategy to filter out threads that should not run.
  4. For each surviving thread, render the prompt and call the LLM.
  5. Persist each result as a run row.

The scheduler holds shared state (httpx client, AsyncSlackClient, settings)
so queries do not each open their own HTTP connection pools.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from slack_cached.async_slack_api import AsyncSlackClient, RateLimitState
from slack_cached.config import Credentials, load_api_base_url, load_credentials
from slack_cached.slack_api import DEFAULT_API_BASE, REQUEST_TIMEOUT
from slack_cached.storage import load_thread_messages, load_user_display_names

from .llm import LLMError, chat_completion
from .sources import run_source
from .storage import (
    QueryRow,
    RunRow,
    each_query_state_for,
    get_all_settings,
    get_query,
    insert_run,
    now_epoch,
    upsert_query_state,
)

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)([dhms])", re.IGNORECASE)
_DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


def parse_duration_seconds(text: str) -> float | None:
    """Parse ``"5m"``/``"2h30m"``/``"all"`` into seconds; None means 'all'."""
    if text.lower() == "all":
        return None
    parts = _DURATION_RE.findall(text)
    if not parts or "".join(f"{v}{u}" for v, u in parts) != text:
        raise ValueError(f"invalid duration: {text!r}")
    total = 0.0
    for value, unit in parts:
        per = {
            "days": 86400.0,
            "hours": 3600.0,
            "minutes": 60.0,
            "seconds": 1.0,
        }[_DURATION_UNITS[unit.lower()]]
        total += float(value) * per
    return total


def oldest_ts_for_lookback(text: str) -> str | None:
    """Convert a lookback duration string to an epoch-seconds ts string, or None."""
    seconds = parse_duration_seconds(text)
    if seconds is None:
        return None
    oldest_dt = datetime.now(tz=UTC).timestamp() - seconds
    return f"{oldest_dt:.6f}"


def render_prompt(template: str, context: dict) -> str:
    """Render a prompt template with simple ``{{key}}`` placeholders.

    Falls back to ``{{!key}}`` for raw-ish debug, but the main use is plain
    substitution. Unknown keys render as the empty string.
    """
    out = template
    for key, value in context.items():
        out = out.replace("{{" + key + "}}", str(value))
    # Sweep unknown placeholders instead of leaking template syntax to the LLM.
    out = re.sub(r"\{\{[^}]*\}\}", "", out)
    return out


def render_thread_text(messages: list, user_names: dict[str, str]) -> str:
    """Render a thread into a compact transcript for the LLM."""
    lines: list[str] = []
    for msg in messages:
        author = user_names.get(msg.user or "", msg.user or "unknown")
        ts_iso = datetime.fromtimestamp(float(msg.ts), tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        text = (msg.text or "").strip()
        lines.append(f"[{ts_iso}] {author}: {text}")
    return "\n".join(lines)


@dataclass
class SchedulerHandle:
    """Handle returned by :func:`start_scheduler` to control the loop."""

    stop: asyncio.Event
    task: asyncio.Task
    httpx_client: httpx.AsyncClient
    slack_client: AsyncSlackClient
    db_path: Path
    cache_db_path: Path


async def run_query_cycle(
    query: QueryRow,
    slack_client: AsyncSlackClient,
    httpx_client: httpx.AsyncClient,
    watcher_db: sqlite3.Connection,
    cache_db_path: Path,
) -> None:
    """Run one poll+LLM cycle for *query*. Called by the per-query task."""
    log.info("query_cycle_start", query_id=query.id, name=query.name)
    settings = get_all_settings(watcher_db)
    base_url = settings["llm_base_url"]
    api_key = settings["llm_api_key"]

    oldest = oldest_ts_for_lookback(query.lookback)
    source_result = await run_source(
        slack_client,
        cache_db_path,
        source_kind=query.source_kind,
        source_config=query.source_config,
        lookback_oldest=oldest,
        full_threads=query.full_threads,
    )
    log.info(
        "query_source_done",
        query_id=query.id,
        channels=len(source_result.channels_polled),
        threads=len(source_result.threads),
    )

    threads = source_result.threads
    if not threads:
        log.info("query_cycle_no_threads", query_id=query.id)
        return

    existing_state = each_query_state_for(
        watcher_db,
        query.id,
        [(t.channel, t.thread_ts) for t in threads],
    )

    sem = asyncio.Semaphore(4)

    async def handle_one(ref) -> None:
        async with sem:
            await _process_thread(
                ref,
                query=query,
                slack_client=slack_client,
                httpx_client=httpx_client,
                watcher_db=watcher_db,
                cache_db_path=cache_db_path,
                base_url=base_url,
                api_key=api_key,
                existing_state=existing_state,
            )

    await asyncio.gather(*(handle_one(t) for t in threads))


async def _process_thread(
    ref,
    *,
    query: QueryRow,
    slack_client: AsyncSlackClient,
    httpx_client: httpx.AsyncClient,
    watcher_db: sqlite3.Connection,
    cache_db_path: Path,
    base_url: str,
    api_key: str,
    existing_state,
) -> None:
    """Apply dedup, render prompt, call LLM, persist run for one thread."""
    from slack_cached.storage import connect as connect_cache

    state = existing_state.get((ref.channel, ref.thread_ts))

    conn = connect_cache(cache_db_path)
    try:
        messages = load_thread_messages(conn, ref.channel, ref.thread_ts)
    finally:
        conn.close()

    if not messages:
        return

    latest_msg_ts = max(float(m.ts) for m in messages)

    # Dedup decision.
    if query.dedup == "once_per_thread" and state is not None and state.processed:
        return
    if query.dedup == "new_messages" and state is not None and state.last_seen_ts is not None:
        try:
            if latest_msg_ts <= float(state.last_seen_ts):
                return
        except ValueError:
            pass

    user_names: dict[str, str] = {}
    conn = connect_cache(cache_db_path)
    try:
        user_names = load_user_display_names(conn, {m.user for m in messages if m.user})
    finally:
        conn.close()

    transcript = render_thread_text(messages, user_names)
    workspace = _workspace_name_for_channel(slack_client)
    channel_mention = ref.channel
    permalink = (
        f"https://{workspace}.slack.com/archives/{ref.channel}/p{_ts_to_permalink(ref.thread_ts)}"
        if workspace
        else f"slack://channel/{ref.channel}/{ref.thread_ts}"
    )

    prompt = render_prompt(
        query.prompt,
        {
            "thread": transcript,
            "channel": channel_mention,
            "thread_ts": ref.thread_ts,
            "permalink": permalink,
        },
    )

    start = asyncio.get_event_loop().time()
    log.info(
        "query_thread_llm_start",
        query_id=query.id,
        channel=ref.channel,
        thread_ts=ref.thread_ts,
        model=query.model,
        prompt_chars=len(prompt),
    )

    response_text: str | None = None
    error_text: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    try:
        resp = await chat_completion(
            httpx_client,
            base_url=base_url,
            api_key=api_key,
            model=query.model,
            prompt=prompt,
        )
        response_text = resp.text
        prompt_tokens = resp.prompt_tokens
        completion_tokens = resp.completion_tokens
    except LLMError as exc:
        error_text = str(exc)
        log.warning(
            "query_thread_llm_error",
            query_id=query.id,
            channel=ref.channel,
            thread_ts=ref.thread_ts,
            error=error_text,
        )

    elapsed_ms = int((asyncio.get_event_loop().time() - start) * 1000)
    run = RunRow(
        id=str(uuid.uuid4()),
        query_id=query.id,
        channel=ref.channel,
        thread_ts=ref.thread_ts,
        prompt=prompt,
        response=response_text,
        error=error_text,
        model=query.model,
        elapsed_ms=elapsed_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        ran_at=now_epoch(),
    )
    insert_run(watcher_db, run)
    upsert_query_state(
        watcher_db,
        query.id,
        ref.channel,
        ref.thread_ts,
        last_seen_ts=f"{latest_msg_ts:.6f}",
        last_run_at=now_epoch(),
        processed=True,
    )


def _ts_to_permalink(ts: str) -> str:
    """Convert ``1700000000.123456`` -> ``1700000000123456`` for Slack permalinks."""
    return ts.replace(".", "")


def _workspace_name_for_channel(client: AsyncSlackClient) -> str | None:
    """Best-effort workspace name. Cached on the client instance."""
    cached = getattr(client, "_watcher_workspace", None)
    if cached is not None:
        return cached
    return None


async def _query_task(
    query_id: str,
    scheduler: SchedulerHandle,
) -> None:
    """One long-lived task per query. Reloads its row each cycle so edits
    to the query (interval, prompt, enabled) take effect without a restart.
    """
    log.info("query_task_start", query_id=query_id)
    try:
        while not scheduler.stop.is_set():
            watcher_conn = _open_watcher(scheduler.db_path)
            try:
                row = get_query(watcher_conn, query_id)
                if row is None:
                    log.info("query_task_missing", query_id=query_id)
                    return
                if not row.enabled:
                    log.info("query_task_disabled_skip", query_id=query_id)
                    # Sleep a small amount so disabled queries do not busy-loop.
                    await asyncio.sleep(15)
                    continue

                interval_s = parse_duration_seconds(row.interval)
                if interval_s is None or interval_s <= 0:
                    interval_s = 60.0

                try:
                    await run_query_cycle(
                        row,
                        slack_client=scheduler.slack_client,
                        httpx_client=scheduler.httpx_client,
                        watcher_db=watcher_conn,
                        cache_db_path=scheduler.cache_db_path,
                    )
                except Exception:
                    log.exception("query_cycle_failed", query_id=query_id)
            finally:
                watcher_conn.close()

            # Sleep in small slices so stop signal is responsive.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(scheduler.stop.wait(), timeout=interval_s)
    except asyncio.CancelledError:
        log.info("query_task_cancelled", query_id=query_id)
        raise
    finally:
        log.info("query_task_exit", query_id=query_id)


def _open_watcher(db_path: Path) -> sqlite3.Connection:
    """Open a short-lived connection to the watcher DB.

    SQLite + asyncio works if every connection is opened and closed inside
    a single task. We open one per cycle to avoid cross-task sharing.
    """
    from .storage import connect as watcher_connect

    return watcher_connect(db_path)


async def _reconcile_tasks(scheduler: SchedulerHandle) -> None:
    """Periodically reconcile per-query tasks with the queries table.

    Starts tasks for new/enabled queries and lets existing tasks notice
    disable/delete on their own (they re-read their row every cycle).
    """
    tasks: dict[str, asyncio.Task] = {}
    try:
        while not scheduler.stop.is_set():
            conn = _open_watcher(scheduler.db_path)
            try:
                from .storage import list_queries

                rows = list_queries(conn)
            finally:
                conn.close()

            seen_ids = {q.id for q in rows}
            # Start tasks for any query we don't know about yet.
            for q in rows:
                if q.id in tasks:
                    continue
                tasks[q.id] = asyncio.create_task(
                    _query_task(q.id, scheduler), name=f"query:{q.id}"
                )
            # Cancel tasks for queries that no longer exist.
            for qid in list(tasks):
                if qid not in seen_ids:
                    tasks[qid].cancel()
                    del tasks[qid]
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(scheduler.stop.wait(), timeout=10)
    finally:
        for task in tasks.values():
            task.cancel()
        for task in tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _run_scheduler(
    db_path: Path,
    cache_db_path: Path,
    slack_base_url: str,
    credentials: Credentials,
) -> SchedulerHandle:
    """Create the shared clients and start the reconcile loop."""
    rate_limit_state = RateLimitState()
    httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT))
    slack_client = AsyncSlackClient(
        credentials,
        base_url=slack_base_url,
        client=httpx_client,
        rate_limit_state=rate_limit_state,
    )
    stop_event = asyncio.Event()
    handle = SchedulerHandle(
        stop=stop_event,
        task=asyncio.current_task(),  # set later
        httpx_client=httpx_client,
        slack_client=slack_client,
        db_path=db_path,
        cache_db_path=cache_db_path,
    )
    handle.task = asyncio.create_task(_reconcile_tasks(handle), name="scheduler:reconcile")
    return handle


def start_scheduler(
    db_path: Path,
    cache_db_path: Path,
    *,
    slack_base_url: str | None = None,
    credentials: Credentials | None = None,
) -> SchedulerHandle:
    """Synchronous wrapper that schedules :func:`_run_scheduler` on the running loop."""
    if slack_base_url is None:
        slack_base_url = load_api_base_url() or DEFAULT_API_BASE
    if credentials is None:
        try:
            credentials = load_credentials()
        except SystemExit:
            if slack_base_url != DEFAULT_API_BASE:
                credentials = load_credentials(require=False)
            else:
                raise
    loop = asyncio.get_event_loop()
    return loop.create_task(_run_scheduler(db_path, cache_db_path, slack_base_url, credentials))


async def shutdown_scheduler(handle: asyncio.Task | SchedulerHandle) -> None:
    """Cancel and await the scheduler.

    Accepts either the asyncio.Task returned by start_scheduler (older callers)
    or a SchedulerHandle.
    """
    if isinstance(handle, SchedulerHandle):
        handle.stop.set()
        if handle.task:
            handle.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle.task
        await handle.httpx_client.aclose()
    else:
        handle.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await handle


# URL helpers re-exported for the API layer.
__all__ = [
    "SchedulerHandle",
    "parse_duration_seconds",
    "oldest_ts_for_lookback",
    "render_prompt",
    "run_query_cycle",
    "shutdown_scheduler",
    "start_scheduler",
]
