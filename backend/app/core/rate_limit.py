import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

from app.core.config import get_settings

_WINDOW_SECONDS = 60.0
_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def reset_rate_limiter() -> None:
    """Clear all counters (used by tests)."""
    with _lock:
        _hits.clear()


def rate_limit(request: Request) -> None:
    """Sliding-window limiter keyed by API key (or client IP).

    Limit comes from ``RATE_LIMIT_PER_MINUTE`` (0 disables it). The counters
    are per process, so with N Cloud Run instances the effective limit is N x
    the configured value; use Cloud Armor / an API gateway for a global limit.
    """
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return

    identity = request.headers.get("x-api-key") or (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    with _lock:
        hits = _hits[identity]
        while hits and now - hits[0] > _WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(_WINDOW_SECONDS - (now - hits[0])))
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)

