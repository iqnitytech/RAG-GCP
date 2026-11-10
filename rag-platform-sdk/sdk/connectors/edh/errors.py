"""Typed error hierarchy for Enterprise Data Hub (EDH) connectors.

Inherits from :class:`sdk.errors.AppError` so unhandled connector errors are
automatically translated into safe HTTP responses with ``request_id`` when
used inside a FastAPI service, while preserving rich internal context for logs
and retry engines.
"""

from sdk.errors import (
    EdhConfigError,
    EdhError,
    NotFoundError,
    ServiceUnavailableError,
)


class EdhAuthError(EdhError):
    """Authentication or authorization failure against the upstream data hub."""

    status_code = 502
    public_message = "Data hub authentication failed"
    retryable = False



class EdhNotFoundError(NotFoundError, EdhError):
    """Requested dataset, partition, or object does not exist in the data hub."""

    status_code = 404
    public_message = "Data hub resource not found"
    retryable = False


class EdhConflictError(EdhError):
    """Precondition failed or resource already exists when overwrite is disabled."""

    status_code = 409
    public_message = "Data hub resource conflict"
    retryable = False


class EdhRateLimitError(ServiceUnavailableError, EdhError):
    """Upstream data hub rate-limited the request (HTTP 429)."""

    status_code = 503
    public_message = "Data hub rate limit exceeded"
    retryable = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: float | None = None,
        provider: str = "",
        operation: str = "",
    ) -> None:
        super().__init__(message, provider=provider, operation=operation, retryable=True)
        self.retry_after_seconds = retry_after_seconds


class EdhTransientError(ServiceUnavailableError, EdhError):
    """Transient network, timeout, circuit-open, or 5xx error from the data hub."""

    status_code = 503
    public_message = "Data hub is temporarily unavailable"
    retryable = True


class EdhIntegrityError(EdhError):
    """Content SHA-256 or byte-length mismatch detected during transfer."""

    status_code = 502
    public_message = "Data hub payload integrity check failed"
    retryable = False
