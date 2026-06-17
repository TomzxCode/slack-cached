"""Thread-safe sliding-window rate limiter per API endpoint."""

import threading
import time
from collections import defaultdict
from collections.abc import Callable

from slack_cached.fake_slack._internal._constants import ENDPOINT_RATE_LIMITS, RATE_LIMIT_WINDOW


class RateLimiter:
    """Thread-safe sliding-window rate limiter per API endpoint.

    Uses Slack's documented tier limits.  Only successful (non-429) requests
    count against the window so that retries after a ``Retry-After`` sleep
    don't cascade.
    """

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        window: float = RATE_LIMIT_WINDOW,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._limits = limits if limits is not None else dict(ENDPOINT_RATE_LIMITS)
        self._window = window
        self._now = now or time.time
        self._request_times: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, path: str) -> tuple[bool, int]:
        """Check whether *path* is within its rate limit.

        Returns ``(allowed, retry_after)``.  When *allowed* is ``False``,
        *retry_after* is the number of seconds the client should wait.
        """
        endpoint = self._endpoint_for_path(path)
        if endpoint is None:
            return True, 0

        limit = self._limits[endpoint]
        now = self._now()

        with self._lock:
            times = [t for t in self._request_times[endpoint] if now - t < self._window]
            self._request_times[endpoint] = times

            if len(times) >= limit:
                oldest = min(times)
                retry_after = max(1, int(self._window - (now - oldest)))
                return False, retry_after

            self._request_times[endpoint].append(now)
            return True, 0

    def _endpoint_for_path(self, path: str) -> str | None:
        for endpoint in self._limits:
            if endpoint in path:
                return endpoint
        return None
