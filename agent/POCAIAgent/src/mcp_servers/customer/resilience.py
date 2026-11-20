from __future__ import annotations

import builtins
import logging
from typing import Any, Callable, TypeVar

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from domain.errors import AuthenticationError, DomainError, NotFoundError, UpstreamError

logger = logging.getLogger("mcp.customer.resilience")
_tenacity_logger = logging.getLogger("tenacity.retry")

# Transient exceptions to retry.
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    builtins.ConnectionError,
    builtins.TimeoutError,
)

try:
    from sdk.connectors import EdhAuthError, EdhNotFoundError, EdhRateLimitError, EdhTransientError
    from sdk.errors import ServiceUnavailableError
    TRANSIENT_EXCEPTIONS = TRANSIENT_EXCEPTIONS + (EdhTransientError, ServiceUnavailableError)
except ImportError:
    EdhAuthError = None  # type: ignore[assignment, misc]
    EdhRateLimitError = None  # type: ignore[assignment, misc]
    EdhNotFoundError = None  # type: ignore[assignment, misc]
    EdhTransientError = None  # type: ignore[assignment, misc]
    ServiceUnavailableError = None  # type: ignore[assignment, misc]

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 3,
    wait_seconds: float = 0.5,
    retry_exceptions: tuple[type[Exception], ...] | None = None,
):
    """Decorator retrying transient network/infrastructure errors with exponential backoff."""
    exceptions = retry_exceptions if retry_exceptions is not None else TRANSIENT_EXCEPTIONS
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=wait_seconds, min=wait_seconds, max=wait_seconds * 4),
        retry=retry_if_exception_type(exceptions),
        reraise=True,
        before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
    )


def sanitize_error_message(exc: Exception, correlation_id: str) -> tuple[str, str]:
    """Sanitize error message to prevent leaking internal SDK/database details (OWASP A05)."""
    error_type = type(exc).__name__
    if isinstance(exc, (NotFoundError, DomainError)):
        return str(exc), error_type
    if isinstance(exc, AuthenticationError):
        return "Authentication failure: invalid or missing credentials.", error_type
    if EdhRateLimitError is not None and isinstance(exc, EdhRateLimitError):
        return "Rate limit exceeded: service is temporarily throttling requests.", error_type

    logger.error("Internal or upstream error occurred (correlation_id=%s): %s", correlation_id, exc)
    return (
        f"An error occurred while processing the customer request (ref: {correlation_id}).",
        "UpstreamError",
    )


def handle_sdk_exception(exc: Exception, operation: str = "customer operation") -> None:
    """Translates SDK and HTTP errors into appropriate domain/platform exceptions."""
    if isinstance(exc, (AuthenticationError, NotFoundError, DomainError)):
        raise exc

    if EdhAuthError is not None and isinstance(exc, EdhAuthError):
        logger.error("SDK authentication failure during %s: %s", operation, exc)
        raise AuthenticationError(f"SDK authentication failed during {operation}: {exc}") from exc

    if EdhRateLimitError is not None and isinstance(exc, EdhRateLimitError):
        logger.warning("SDK rate limit exceeded during %s: %s", operation, exc)
        raise UpstreamError(f"SDK rate limit exceeded during {operation}") from exc

    if EdhNotFoundError is not None and isinstance(exc, EdhNotFoundError):
        raise NotFoundError(f"Resource not found during {operation}: {exc}") from exc

    msg = str(exc).lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "upstream_status", None)

    if status_code in (401, 403) or "unauthorized" in msg or "forbidden" in msg or "invalid api key" in msg:
        logger.error("SDK authentication failure detected from status/message during %s", operation)
        raise AuthenticationError(f"SDK authentication failed during {operation}") from exc

    if status_code == 404 or "not found" in msg:
        raise NotFoundError(f"Customer not found during {operation}") from exc

    if status_code == 429 or "rate limit" in msg:
        raise UpstreamError(f"SDK rate limit exceeded during {operation}") from exc

    logger.error("SDK exception during %s: %s", operation, exc)
    raise UpstreamError(f"SDK failure during {operation}: {exc}") from exc
