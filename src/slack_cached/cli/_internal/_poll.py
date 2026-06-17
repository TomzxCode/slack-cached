"""Async poll loop for the ``poll`` command."""

import json
import sys
import time
from typing import Any

import structlog

from slack_cached.cli._internal._shared import CommonArgs

log = structlog.get_logger(__name__)


async def _poll_loop(
    common: CommonArgs,
    channels: list[str],
    interval_seconds: float,
    concurrency: int,
    last: str,
    full_threads: bool,
    json_output: bool,
) -> None:
    """Run the poll loop with async HTTP and a semaphore for concurrency."""
    import asyncio

    import httpx

    from slack_cached.async_cache import fetch_channel_messages_async
    from slack_cached.async_slack_api import AsyncSlackClient, RateLimitState
    from slack_cached.cli._internal import _client
    from slack_cached.cli._internal._duration import _oldest_ts_from_last
    from slack_cached.config import load_api_base_url, load_credentials
    from slack_cached.slack_api import DEFAULT_API_BASE, REQUEST_TIMEOUT

    base_url = common.api_base_url or load_api_base_url() or DEFAULT_API_BASE
    try:
        credentials = load_credentials()
    except SystemExit:
        if base_url != DEFAULT_API_BASE:
            credentials = load_credentials(require=False)
        else:
            raise

    rate_limit_state = RateLimitState()
    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as httpx_client:
        client = AsyncSlackClient(
            credentials,
            base_url=base_url,
            client=httpx_client,
            rate_limit_state=rate_limit_state,
        )
        semaphore = asyncio.Semaphore(concurrency)
        cycle = 0

        try:
            while True:
                cycle += 1
                cycle_start = time.perf_counter()
                oldest = _oldest_ts_from_last(last)

                async def fetch_one(
                    channel: str,
                    *,
                    _cycle: int = cycle,
                    _oldest: str | None = oldest,
                ) -> dict[str, Any]:
                    async with semaphore:
                        try:
                            with _client._open_db(common) as conn:
                                result = await fetch_channel_messages_async(
                                    conn,
                                    client,
                                    channel,
                                    full_threads=full_threads,
                                    oldest=_oldest,
                                )
                            log.info(
                                "poll_channel_done",
                                cycle=_cycle,
                                channel=channel,
                                fetched=result.fetched_messages,
                                total=result.total_messages,
                            )
                            return {
                                "channel": channel,
                                "fetched": result.fetched_messages,
                                "total": result.total_messages,
                            }
                        except Exception:
                            log.exception(
                                "poll_channel_error",
                                cycle=_cycle,
                                channel=channel,
                            )
                            return {"channel": channel, "error": True}

                cycle_summary = await asyncio.gather(*(fetch_one(ch) for ch in channels))

                elapsed = time.perf_counter() - cycle_start
                fetched_total = sum(s.get("fetched", 0) for s in cycle_summary)
                print(
                    f"cycle {cycle}: {fetched_total} new message(s) across "
                    f"{len(channels)} channel(s) in {elapsed:.1f}s",
                    file=sys.stderr,
                )

                if json_output:
                    payload = {
                        "cycle": cycle,
                        "elapsed_seconds": round(elapsed, 3),
                        "channels": cycle_summary,
                    }
                    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    sys.stdout.flush()

                sleep_seconds = max(0, interval_seconds - elapsed)
                log.info("poll_sleep", cycle=cycle, sleep_seconds=sleep_seconds)
                await asyncio.sleep(sleep_seconds)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("poll_interrupted", cycles=cycle)
            print(f"\npoll stopped after {cycle} cycle(s)", file=sys.stderr)
