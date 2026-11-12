"""Typed error hierarchy and structured error logger for Enterprise Data Hub (EDH) connectors.

Inherits from :class:`sdk.errors.AppError` so unhandled connector errors are
automatically translated into safe HTTP responses with ``request_id`` when
used inside a FastAPI service, while preserving rich internal context,
strategy-specific error codes, and structured logging.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from sdk.errors import (
    EdhConfigError,
    EdhError,
    NotFoundError,
    ServiceUnavailableError,
)
from sdk.logging.context import get_request_id

E = TypeVar("E", bound=EdhError)


class EdhAuthError(EdhError):
    """Authentication or authorization failure against the upstream data hub."""

    status_code = 502
    public_message = "Data hub authentication failed"
    retryable = False
    error_code = "EDH_AUTH_ERROR"


class EdhNotFoundError(NotFoundError, EdhError):
    """Requested dataset, partition, or object does not exist in the data hub."""

    status_code = 404
    public_message = "Data hub resource not found"
    retryable = False
    error_code = "EDH_NOT_FOUND"


class EdhConflictError(EdhError):
    """Precondition failed or resource already exists when overwrite is disabled."""

    status_code = 409
    public_message = "Data hub resource conflict"
    retryable = False
    error_code = "EDH_CONFLICT"


class EdhRateLimitError(ServiceUnavailableError, EdhError):
    """Upstream data hub rate-limited the request (HTTP 429)."""

    status_code = 503
    public_message = "Data hub rate limit exceeded"
    retryable = True
    error_code = "EDH_RATE_LIMITED"

    def __init__(
        self,
        message: str | None = None,
        *,
        public_message: str | None = None,
        retry_after_seconds: float | None = None,
        provider: str = "",
        operation: str = "",
        error_code: str = "",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(
            message,
            public_message=public_message,
            provider=provider,
            operation=operation,
            retryable=True,
            error_code=error_code,
            upstream_status=upstream_status,
        )
        self.retry_after_seconds = retry_after_seconds


class EdhTransientError(ServiceUnavailableError, EdhError):
    """Transient network, timeout, circuit-open, or 5xx error from the data hub."""

    status_code = 503
    public_message = "Data hub is temporarily unavailable"
    retryable = True
    error_code = "EDH_TRANSIENT_ERROR"


class EdhIntegrityError(EdhError):
    """Content SHA-256 or byte-length mismatch detected during transfer."""

    status_code = 502
    public_message = "Data hub payload integrity check failed"
    retryable = False
    error_code = "EDH_INTEGRITY_ERROR"


def log_and_build_edh_error(
    strategy_logger: logging.Logger,
    exc_cls: type[E],
    message: str,
    *,
    provider: str,
    operation: str,
    error_code: str = "",
    public_message: str | None = None,
    upstream_status: int | None = None,
    retry_after_seconds: float | None = None,
    level: int = logging.ERROR,
) -> E:
    """Log a structured EDH error with ``request_id`` and instantiate ``exc_cls``."""
    req_id = get_request_id() or "-"
    effective_code = error_code or getattr(exc_cls, "error_code", "EDH_ERROR")
    strategy_logger.log(
        level,
        "EDH error provider=%s operation=%s code=%s http_status=%s request_id=%s detail=%s",
        provider,
        operation,
        effective_code,
        upstream_status if upstream_status is not None else "-",
        req_id,
        message,
    )
    if issubclass(exc_cls, EdhRateLimitError):
        return exc_cls(  # type: ignore[return-value]
            message,
            public_message=public_message,
            retry_after_seconds=retry_after_seconds,
            provider=provider,
            operation=operation,
            error_code=effective_code,
            upstream_status=upstream_status,
        )
    return exc_cls(
        message,
        public_message=public_message,
        provider=provider,
        operation=operation,
        error_code=effective_code,
        upstream_status=upstream_status,
    )
