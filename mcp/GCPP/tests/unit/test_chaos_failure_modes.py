from __future__ import annotations
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from starlette.testclient import TestClient

from client_app.main import app
from common.infrastructure.resilience import (
    CircuitBreaker,
    CircuitState,
    call_sdk_with_retry,
    handle_sdk_exception,
    retry_with_backoff,
)
from core.exceptions import (
    AuthenticationError,
    CircuitBreakerOpenError,
    ConnectionError as CoreConnectionError,
    McpConnectionError,
    McpTimeoutError,
    RateLimitError,
    SdkGatewayError,
    TimeoutError as CoreTimeoutError,
)
from core.metrics import (
    Counter,
    Gauge,
    Histogram,
    get_metrics_registry,
    record_tool_metrics,
    update_circuit_breaker_metric,
)


# ===========================================================================
# Chaos / Resilience Tests (TEST-02)
# ===========================================================================

def test_transient_failure_recovery_via_retry():
    """Simulate transient network packet drops that succeed on retry 3."""
    call_count = 0

    def unreliable_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise McpConnectionError("Simulated socket drop")
        return "success"

    result = call_sdk_with_retry(unreliable_call)
    assert result == "success"
    assert call_count == 3


def test_persistent_failure_exhausts_retries():
    """Simulate severe persistent outage that exhausts retries and raises."""
    call_count = 0

    def failing_call():
        nonlocal call_count
        call_count += 1
        raise McpConnectionError("Host unreachable")

    with pytest.raises(McpConnectionError):
        call_sdk_with_retry(failing_call)
    assert call_count == 3


def test_non_transient_auth_error_fails_fast_without_retrying():
    """Security/Auth errors must never be retried — fail fast."""
    call_count = 0

    @retry_with_backoff(max_attempts=3)
    def auth_failing_function():
        nonlocal call_count
        call_count += 1
        raise AuthenticationError("Invalid API Key")

    with pytest.raises(AuthenticationError):
        auth_failing_function()
    assert call_count == 1, "AuthenticationError should fail immediately on first attempt without retrying"


def test_rate_limit_error_fails_fast_without_retrying():
    """Rate limit 429 errors must not blindly retry without backoff headers."""
    call_count = 0

    @retry_with_backoff(max_attempts=3)
    def rate_limited_function():
        nonlocal call_count
        call_count += 1
        raise RateLimitError("Rate limit exceeded. Retry after 60s.")

    with pytest.raises(RateLimitError):
        rate_limited_function()
    assert call_count == 1, "RateLimitError must propagate immediately to caller without wasteful retries"


def test_circuit_breaker_trips_under_cascade_outage():
    """Test circuit breaker transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED under outage."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0.1, name="chaos-test")
    assert cb.state == CircuitState.CLOSED

    # Simulate 3 consecutive transient failures
    for _ in range(3):
        cb.record_failure(McpConnectionError("Downstream timeout"))

    # State should now be OPEN
    assert cb.state == CircuitState.OPEN

    # Subsequent call must fail fast with CircuitBreakerOpenError without waiting
    with pytest.raises(CircuitBreakerOpenError, match="is OPEN. Failing fast"):
        cb.before_call()

    # Wait for recovery timeout
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN

    # Successful call in HALF_OPEN resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


def test_circuit_breaker_ignores_non_transient_errors():
    """Domain validation and entity not found errors must not trip the circuit breaker."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=5.0, name="validation-test")
    cb.record_failure(ValueError("Bad input"))
    cb.record_failure(AuthenticationError("Bad creds"))
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


# ===========================================================================
# Prometheus Metrics Tests (OBS-03)
# ===========================================================================

def test_prometheus_registry_and_metrics_rendering():
    """Verify registry generates valid Prometheus 0.0.4 text format."""
    registry = get_metrics_registry()
    
    # Record test metric events
    record_tool_metrics(server="test-server", tool="test_tool", duration_seconds=0.045, status="success")
    update_circuit_breaker_metric(breaker_name="test-breaker", state_str="OPEN")

    output = registry.generate_metrics_text()

    assert "# HELP mcp_tool_invocations_total" in output
    assert "# TYPE mcp_tool_invocations_total counter" in output
    assert 'mcp_tool_invocations_total{server="test-server",status="success",tool="test_tool"}' in output

    assert "# HELP mcp_tool_duration_seconds" in output
    assert "# TYPE mcp_tool_duration_seconds histogram" in output
    assert 'mcp_tool_duration_seconds_bucket{server="test-server",tool="test_tool",le="0.05"} 1' in output

    assert "# HELP mcp_circuit_breaker_state" in output
    assert "# TYPE mcp_circuit_breaker_state gauge" in output
    assert 'mcp_circuit_breaker_state{breaker="test-breaker"} 2.0' in output

    assert "# HELP mcp_circuit_breaker_trips_total" in output
    assert "# TYPE mcp_circuit_breaker_trips_total counter" in output


def test_client_app_metrics_endpoint():
    """Verify HTTP GET /metrics on the FastAPI application."""
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")

    body = response.text
    assert "mcp_tool_invocations_total" in body
    assert "mcp_tool_duration_seconds" in body
    assert "mcp_circuit_breaker_state" in body
