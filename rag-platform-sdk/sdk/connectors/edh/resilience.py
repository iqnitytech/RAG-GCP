"""Resilience primitives using only Python standard library modules.

Provides a thread-safe :class:`CircuitBreaker` and retry-with-jitter executor
for idempotent Enterprise Data Hub operations.
"""

from __future__ import annotations

import logging
import random
import time
from enum import Enum
from threading import Lock
from typing import Callable, TypeVar

from sdk.connectors.edh.errors import EdhError, EdhRateLimitError, EdhTransientError
from sdk.models.edh_models import CircuitBreakerPolicy, RetryPolicy

logger = logging.getLogger("sdk.edh.resilience")
T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe state machine (CLOSED -> OPEN -> HALF_OPEN) using ``time.monotonic``."""

    def __init__(self, policy: CircuitBreakerPolicy, *, provider: str = "edh") -> None:
        self._policy = policy
        self._provider = provider
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_recovery_unlocked(time.monotonic())
            return self._state

    def _check_recovery_unlocked(self, now: float) -> None:
        if (
            self._state == CircuitState.OPEN
            and (now - self._opened_at) >= self._policy.recovery_timeout_seconds
        ):
            self._state = CircuitState.HALF_OPEN

    def before_call(self, operation: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._check_recovery_unlocked(now)
            if self._state == CircuitState.OPEN:
                raise EdhTransientError(
                    f"Circuit breaker OPEN for provider={self._provider} on operation={operation}",
                    provider=self._provider,
                    operation=operation,
                )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures += 1
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failures >= self._policy.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = now

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = 0.0


def execute_with_resilience(
    operation_name: str,
    func: Callable[[], T],
    *,
    retry_policy: RetryPolicy,
    circuit_breaker: CircuitBreaker | None = None,
    idempotent: bool = True,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Execute ``func`` with circuit-breaker protection and exponential backoff + jitter."""
    max_attempts = retry_policy.max_attempts if idempotent else 1
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        if circuit_breaker is not None:
            circuit_breaker.before_call(operation_name)
        try:
            result = func()
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            return result
        except EdhError as exc:
            last_exc = exc
            if exc.retryable and circuit_breaker is not None:
                circuit_breaker.record_failure()
            if not exc.retryable or attempt >= max_attempts:
                raise

            delay = _compute_delay(attempt, retry_policy, exc)
            logger.warning(
                "EDH retryable failure on %s (attempt %d/%d, delay=%.2fs): %s",
                operation_name,
                attempt,
                max_attempts,
                delay,
                exc,
            )
            sleep_fn(delay)

    assert last_exc is not None  # pragma: no cover
    raise last_exc


def _compute_delay(attempt: int, policy: RetryPolicy, exc: EdhError) -> float:
    if isinstance(exc, EdhRateLimitError) and exc.retry_after_seconds is not None:
        return min(exc.retry_after_seconds, policy.max_delay_seconds)
    exp = min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** (attempt - 1)))
    if policy.jitter_ratio <= 0:
        return exp
    delta = exp * policy.jitter_ratio
    return max(0.0, exp + random.uniform(-delta, delta))  # noqa: S311
