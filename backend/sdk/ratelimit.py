"""In-process sliding-window rate limiter usable as a FastAPI dependency."""

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    """Limit requests per caller (API key, else client IP) per minute.

    Counters are per process, so with N instances the effective limit is N x
    the configured value; use an API gateway / Cloud Armor for a global limit.

    Args:
        limit_getter: Returns the current per-minute limit (0 disables). Taking
            a callable lets the limit follow runtime configuration.
    """

    WINDOW_SECONDS = 60.0

    def __init__(self, limit_getter: Callable[[], int]) -> None:
        self._limit_getter = limit_getter
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def __call__(self, request: Request) -> None:
        limit = self._limit_getter()
        if limit <= 0:
            return

        identity = request.headers.get("x-api-key") or (
            request.client.host if request.client else "unknown"
        )
        now = time.monotonic()
        with self._lock:
            hits = self._hits[identity]
            while hits and now - hits[0] > self.WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, int(self.WINDOW_SECONDS - (now - hits[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

