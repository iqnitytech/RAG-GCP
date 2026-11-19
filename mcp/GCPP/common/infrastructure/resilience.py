from __future__ import annotations
import builtins
from enum import Enum
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, TypeVar
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
from core.exceptions import (
    AuthenticationError,
    CircuitBreakerOpenError,
    ConnectionError as CoreConnectionError,
    DomainValidationError,
    EntityNotFoundError,
    RateLimitError,
    SdkGatewayError,
    TimeoutError as CoreTimeoutError,
)
from core.logging import get_logger

logger = get_logger(__name__)
_tenacity_logger = logging.getLogger("tenacity.retry")

# Transient exceptions that should be retried. Deterministic errors
# (AuthenticationError, RateLimitError, EntityNotFoundError, SdkGatewayError)
# are explicitly excluded to prevent retrying non-transient failures.
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    builtins.ConnectionError,
    builtins.TimeoutError,
    CoreConnectionError,
    CoreTimeoutError,
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


# ===========================================================================
# RELI-03: Circuit Breaker Pattern (Prevents 30s cascading timeout hangs)
# ===========================================================================

class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Thread-safe Circuit Breaker pattern (RELI-03).
    Transitions CLOSED -> OPEN after consecutive transient failures, fast-failing calls
    under outages instead of accumulating 30s timeout cascades across all callers.
    After recovery_timeout_seconds, transitions to HALF_OPEN to probe downstream recovery.
    """
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 10.0,
        name: str = "default",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_state_change = time.monotonic()
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_state_unlocked()
            return self._state

    def _evaluate_state_unlocked(self) -> None:
        now = time.monotonic()
        if self._state == CircuitState.OPEN:
            if (now - self._last_state_change) >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = now
                logger.info("Circuit breaker transitioning to HALF_OPEN", breaker=self.name)
                try:
                    from core.metrics import update_circuit_breaker_metric
                    update_circuit_breaker_metric(self.name, "HALF_OPEN")
                except Exception:
                    pass

    def before_call(self) -> None:
        with self._lock:
            self._evaluate_state_unlocked()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. Failing fast to prevent cascading timeout."
                )

    def record_success(self) -> None:
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("Circuit breaker reset to CLOSED after successful recovery", breaker=self.name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_state_change = time.monotonic()
            try:
                from core.metrics import update_circuit_breaker_metric
                update_circuit_breaker_metric(self.name, "CLOSED")
            except Exception:
                pass

    def record_failure(self, exc: Exception) -> None:
        # Only trip on transient outages / connection breaks
        if not isinstance(exc, TRANSIENT_EXCEPTIONS) and not isinstance(exc, (CoreConnectionError, CoreTimeoutError)):
            return

        with self._lock:
            self._failure_count += 1
            now = time.monotonic()
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_state_change = now
                logger.error(
                    "Circuit breaker tripped to OPEN — downstream outage detected",
                    breaker=self.name,
                    failures=self._failure_count,
                    threshold=self.failure_threshold,
                )
                try:
                    from core.metrics import update_circuit_breaker_metric
                    update_circuit_breaker_metric(self.name, "OPEN")
                except Exception:
                    pass

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_state_change = time.monotonic()
            try:
                from core.metrics import update_circuit_breaker_metric
                update_circuit_breaker_metric(self.name, "CLOSED")
            except Exception:
                pass


_global_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=10.0, name="sdk-gateway")


def get_circuit_breaker() -> CircuitBreaker:
    return _global_circuit_breaker


# ===========================================================================
# Retry with Backoff
# ===========================================================================

def retry_with_backoff(
    max_attempts: int = 3,
    wait_seconds: float = 1.0,
    retry_exceptions: tuple[type[Exception], ...] | None = None,
):
    """Decorator that retries only on transient network and infrastructure errors
    with exponential back-off. Deterministic errors (EntityNotFoundError,
    DomainValidationError, AuthenticationError, RateLimitError) and general
    SdkGatewayError are intentionally excluded so they propagate immediately.
    """
    exceptions = retry_exceptions if retry_exceptions is not None else TRANSIENT_EXCEPTIONS
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=wait_seconds, min=wait_seconds, max=wait_seconds * 8),
        retry=retry_if_exception_type(exceptions),
        reraise=True,
        before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
    )


# ===========================================================================
# SEC-05: Error sanitization & SDK translation (OWASP A05 Information Leak)
# ===========================================================================

def sanitize_error_message(exc: Exception, correlation_id: str) -> tuple[str, str]:
    """Sanitize error message to prevent leaking SDK/database internals (OWASP A05).
    Domain validation and not-found errors show user-facing messages.
    Infrastructure and internal errors return a safe generic message with the correlation ID.
    """
    error_type = type(exc).__name__
    if isinstance(exc, DomainValidationError):
        return str(exc), error_type
    if isinstance(exc, EntityNotFoundError):
        return str(exc), error_type
    if isinstance(exc, AuthenticationError):
        return "Authentication failure: Invalid or missing API credentials.", error_type
    if isinstance(exc, RateLimitError):
        return "Rate limit exceeded: The service is temporarily throttling requests. Please retry later.", error_type
    if isinstance(exc, CircuitBreakerOpenError):
        return "Service unavailable: Downstream circuit breaker is OPEN. Fast-failing request.", error_type

    # Sanitize any internal SDK, network, SQL, or exception detail
    return (
        f"An internal error occurred while processing the request. Reference correlation_id: {correlation_id}",
        "InternalServerError",
    )


def handle_sdk_exception(exc: Exception, operation: str = "SDK operation") -> None:
    """Translates SDK and HTTP errors into appropriate domain/platform exceptions.
    Ensures AuthenticationError, RateLimitError, and EntityNotFoundError are never
    swallowed by bare generic exception handlers.
    """
    if isinstance(exc, (AuthenticationError, RateLimitError, EntityNotFoundError, DomainValidationError, CircuitBreakerOpenError)):
        raise exc

    # SDK-specific error types
    if EdhAuthError is not None and isinstance(exc, EdhAuthError):
        logger.error("SDK authentication failure", operation=operation, error=str(exc))
        raise AuthenticationError(f"SDK authentication failed during {operation}: {exc}") from exc

    if EdhRateLimitError is not None and isinstance(exc, EdhRateLimitError):
        logger.warning("SDK rate limit exceeded", operation=operation, error=str(exc))
        raise RateLimitError(f"SDK rate limit exceeded during {operation}: {exc}") from exc

    if EdhNotFoundError is not None and isinstance(exc, EdhNotFoundError):
        raise EntityNotFoundError(f"Resource not found during {operation}: {exc}") from exc

    # Status code or string pattern inspection
    msg = str(exc).lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "upstream_status", None)

    if status_code in (401, 403) or "unauthorized" in msg or "forbidden" in msg or "invalid api key" in msg:
        logger.error("SDK authentication failure detected from status/message", operation=operation, error=str(exc))
        raise AuthenticationError(f"SDK authentication failed during {operation}: {exc}") from exc

    if status_code == 429 or "rate limit" in msg or "too many requests" in msg:
        logger.warning("SDK rate limit detected from status/message", operation=operation, error=str(exc))
        raise RateLimitError(f"SDK rate limit exceeded during {operation}: {exc}") from exc

    if status_code == 404 or "not found" in msg:
        raise EntityNotFoundError(f"Resource not found during {operation}: {exc}") from exc

    # If it is a transient error, bubble it up so retry_with_backoff can handle it
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        raise exc

    logger.error("Unhandled SDK error", operation=operation, error=str(exc))
    raise SdkGatewayError(f"SDK error during {operation}: {exc}") from exc


def call_sdk_with_retry(
    func: Callable[..., T],
    *args: Any,
    circuit_breaker: CircuitBreaker | None = None,
    **kwargs: Any,
) -> T:
    """Execute an SDK call protected with CircuitBreaker (RELI-03) and retry_with_backoff.
    Fails fast when the circuit breaker is OPEN, avoiding 30s timeout cascades.
    """
    breaker = circuit_breaker or _global_circuit_breaker
    breaker.before_call()

    @retry_with_backoff(max_attempts=3, wait_seconds=0.2)
    def _invoker() -> T:
        return func(*args, **kwargs)

    try:
        result = _invoker()
        breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure(e)
        handle_sdk_exception(e, operation=getattr(func, "__name__", "sdk_call"))
        raise


class BaseResilientClient(ABC):
    def __init__(self, max_retries: int = 3, timeout_seconds: int = 30) -> None:
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds

    @abstractmethod
    def health_check(self) -> bool: ...
