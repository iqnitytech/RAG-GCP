"""Shared-secret API key authentication via the ``X-API-Key`` header."""

from secrets import compare_digest
from typing import Callable

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from sdk.ratelimit import RateLimiter

DEFAULT_AUTH_FAILURE_LIMIT_PER_MINUTE = 20


def make_api_key_dependency(
    key_getter: Callable[[], str],
    header_name: str = "X-API-Key",
    *,
    failure_limit_getter: Callable[[], int] = lambda: DEFAULT_AUTH_FAILURE_LIMIT_PER_MINUTE,
    max_tracked_ips: int = RateLimiter.DEFAULT_MAX_IDENTITIES,
) -> Callable[..., None]:
    """Build a FastAPI dependency that enforces an API key.

    Failed authentication attempts are counted per client IP and throttled
    before comparing the secret key to prevent high-speed brute-forcing.

    Args:
        key_getter: Returns the expected key at request time. An empty value
            disables the check (development only; production configuration
            should refuse to start without a key).
        header_name: Header carrying the key.
        failure_limit_getter: Max failed attempts per client IP per minute
            before returning 429 (0 disables failure throttling).
        max_tracked_ips: Upper bound on client IPs tracked by the failure limiter.
    """
    header = APIKeyHeader(name=header_name, auto_error=False)
    failure_limiter = RateLimiter(
        failure_limit_getter,
        max_identities=max_tracked_ips,
    )

    def require_api_key(
        request: Request,
        provided: str | None = Security(header),
    ) -> None:
        expected = key_getter()
        if not expected:
            return

        client_ip = request.client.host if request.client else "unknown"
        limit = failure_limit_getter()
        if limit > 0:
            # Reject already-throttled IPs before doing the secret comparison.
            failure_limiter.check_without_recording(client_ip, limit=limit)

        if not provided or not compare_digest(provided.encode(), expected.encode()):
            if limit > 0:
                failure_limiter.check(client_ip, limit=limit)
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    require_api_key.reset_failure_limiter = failure_limiter.reset  # type: ignore[attr-defined]
    require_api_key.failure_limiter = failure_limiter  # type: ignore[attr-defined]
    return require_api_key


