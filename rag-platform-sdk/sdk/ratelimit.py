"""In-process sliding-window rate limiter usable as a FastAPI dependency."""

import time
from collections import OrderedDict, deque
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
        max_identities: Upper bound on tracked identities to keep memory
            bounded under random-key or spoofed-IP sweeps.
    """

    WINDOW_SECONDS = 60.0
    DEFAULT_MAX_IDENTITIES = 10_000

    def __init__(
        self,
        limit_getter: Callable[[], int],
        *,
        max_identities: int = DEFAULT_MAX_IDENTITIES,
    ) -> None:
        self._limit_getter = limit_getter
        self._max_identities = max(1, max_identities)
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    @property
    def tracked_identities(self) -> int:
        with self._lock:
            return len(self._hits)

    def _evict_expired(self, now: float) -> None:
        stale_keys = []
        for key, q in self._hits.items():
            while q and now - q[0] > self.WINDOW_SECONDS:
                q.popleft()
            if not q:
                stale_keys.append(key)
        for key in stale_keys:
            del self._hits[key]

    def check_without_recording(self, identity: str, limit: int | None = None) -> None:
        """Raise 429 if ``identity`` is already at or over ``limit`` without recording a new hit."""
        effective_limit = self._limit_getter() if limit is None else limit
        if effective_limit <= 0:
            return

        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(identity)
            if hits is None:
                return
            while hits and now - hits[0] > self.WINDOW_SECONDS:
                hits.popleft()
            if not hits:
                del self._hits[identity]
                return
            if len(hits) >= effective_limit:
                retry_after = max(1, int(self.WINDOW_SECONDS - (now - hits[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )

    def check(self, identity: str, limit: int | None = None) -> None:
        """Record a hit for ``identity`` or raise 429 if over ``limit``."""
        effective_limit = self._limit_getter() if limit is None else limit
        if effective_limit <= 0:
            return

        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(identity)
            if hits is not None:
                while hits and now - hits[0] > self.WINDOW_SECONDS:
                    hits.popleft()
                if not hits:
                    del self._hits[identity]
                    hits = None

            if hits is None:
                if len(self._hits) >= self._max_identities:
                    self._evict_expired(now)
                while len(self._hits) >= self._max_identities:
                    self._hits.popitem(last=False)
                hits = deque()
                self._hits[identity] = hits
            else:
                self._hits.move_to_end(identity)

            if len(hits) >= effective_limit:
                retry_after = max(1, int(self.WINDOW_SECONDS - (now - hits[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

    def __call__(self, request: Request) -> None:
        identity = request.headers.get("x-api-key") or (
            request.client.host if request.client else "unknown"
        )
        self.check(identity)


