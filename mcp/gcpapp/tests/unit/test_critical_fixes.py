from __future__ import annotations
import asyncio
import json
import os
from unittest.mock import MagicMock, patch
import pytest

from config.settings import Settings
from core.exceptions import (
    AuthenticationError,
    ConnectionError as CoreConnectionError,
    DomainValidationError,
    EntityNotFoundError,
    RateLimitError,
    SdkGatewayError,
    TimeoutError as CoreTimeoutError,
)
from core.factory import GatewayFactory
from common.domain.value_objects import CustomerId, MachineId
from common.infrastructure.resilience import (
    TRANSIENT_EXCEPTIONS,
    call_sdk_with_retry,
    handle_sdk_exception,
    retry_with_backoff,
)
from mcp.customer_mcp.infrastructure.sdk_adapter import SdkCustomerAdapter
from mcp.machine_mcp.infrastructure.sdk_adapter import SdkMachineAdapter
from mcp.telemetry_mcp.infrastructure.sdk_adapter import SdkTelemetryAdapter
from sdk.connectors import EdhAuthError, EdhRateLimitError, EdhTransientError


# ===========================================================================
# SEC-01 Tests: No silent fallback / dev-key bypass
# ===========================================================================

def test_sec_01_customer_sdk_adapter_no_dev_key_fallback():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkCustomerAdapter(settings)
    with pytest.raises(AuthenticationError, match="SDK_API_KEY is not configured or empty"):
        adapter._get_connector()


def test_sec_01_machine_sdk_adapter_no_dev_key_fallback():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkMachineAdapter(settings)
    with pytest.raises(AuthenticationError, match="SDK_API_KEY is not configured or empty"):
        adapter._get_connector()


def test_sec_01_telemetry_sdk_adapter_no_dev_key_fallback():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkTelemetryAdapter(settings)
    with pytest.raises(AuthenticationError, match="SDK_API_KEY is not configured or empty"):
        adapter._get_connector()


def test_sec_01_api_key_getter_strictly_validates_key():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="valid-secret-key-123",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkCustomerAdapter(settings)
    with patch("sdk.connectors.edh.EdhConnector") as mock_conn:
        adapter._get_connector()
        assert mock_conn.called
        config = mock_conn.call_args[0][0]
        # Ensure key getter returns the actual key and never a fallback
        assert config.api_key_getter() == "valid-secret-key-123"


# ===========================================================================
# SEC-02 Tests: .env committed file prevention
# ===========================================================================

def test_sec_02_env_file_not_committed():
    # .env must not exist in workspace root
    assert not os.path.exists(".env"), ".env file must not exist in repository root"
    # .env.example must exist as the clean documentation template
    assert os.path.exists(".env.example"), ".env.example must exist"
    # .gitignore must ignore .env
    with open(".gitignore", "r", encoding="utf-8") as f:
        gitignore_content = f.read()
    assert ".env" in gitignore_content


# ===========================================================================
# SEC-03 Tests: AuthenticationError & RateLimitError not swallowed
# ===========================================================================

@pytest.mark.asyncio
async def test_sec_03_auth_error_not_swallowed_customer():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="test-key",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkCustomerAdapter(settings)
    mock_connector = MagicMock()
    mock_connector.read_records.side_effect = EdhAuthError("401 Unauthorized token")
    adapter._connector = mock_connector

    with pytest.raises(AuthenticationError) as exc_info:
        await adapter.get_by_id(CustomerId("CUST-001001"))
    assert "authentication failed" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_sec_03_rate_limit_error_not_swallowed_customer():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="test-key",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkCustomerAdapter(settings)
    mock_connector = MagicMock()
    mock_connector.read_records.side_effect = EdhRateLimitError("429 Too Many Requests")
    adapter._connector = mock_connector

    with pytest.raises(RateLimitError) as exc_info:
        await adapter.search("Nexus", None, None, 10, 0)
    assert "rate limit exceeded" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_sec_03_telemetry_diagnose_fault_code_preserves_auth_error():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="test-key",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkTelemetryAdapter(settings)
    mock_connector = MagicMock()
    mock_connector.get_bytes.side_effect = EdhAuthError("403 Forbidden")
    adapter._connector = mock_connector

    # Must raise AuthenticationError, NOT EntityNotFoundError!
    with pytest.raises(AuthenticationError):
        await adapter.diagnose_fault_code("E-001")


# ===========================================================================
# ARCH-01 Tests: GatewayFactory singleton & connection pool reuse
# ===========================================================================

def test_arch_01_gateway_factory_singletons():
    GatewayFactory.clear_cache()
    settings = Settings(DATA_PROVIDER_MODE="MOCK")

    c1 = GatewayFactory.get_customer_gateway(settings)
    c2 = GatewayFactory.get_customer_gateway(settings)
    assert c1 is c2

    m1 = GatewayFactory.get_machine_gateway(settings)
    m2 = GatewayFactory.get_machine_gateway(settings)
    assert m1 is m2

    t1 = GatewayFactory.get_telemetry_gateway(settings)
    t2 = GatewayFactory.get_telemetry_gateway(settings)
    assert t1 is t2


@pytest.mark.asyncio
async def test_arch_01_tools_register_without_repo_uses_singleton():
    from mcp.customer_mcp.tools import register_tools
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        from mcp.server.fastmcp import FastMCP

    GatewayFactory.clear_cache()
    settings = Settings(DATA_PROVIDER_MODE="MOCK")
    app = FastMCP("test-arch01")

    # repo is optional, defaults to GatewayFactory singleton
    register_tools(app, settings, repo=None)
    tools = await app.list_tools()
    assert len(tools) == 4


# ===========================================================================
# RELI-01 Tests: retry_with_backoff transient errors only
# ===========================================================================

def test_reli_01_retry_retries_transient_error():
    attempts = 0

    @retry_with_backoff(max_attempts=3, wait_seconds=0.01)
    def transient_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Connection reset by peer")
        return "success"

    result = transient_call()
    assert result == "success"
    assert attempts == 3


def test_reli_01_retry_does_not_retry_non_transient_error():
    attempts = 0

    @retry_with_backoff(max_attempts=3, wait_seconds=0.01)
    def non_transient_call():
        nonlocal attempts
        attempts += 1
        raise AuthenticationError("Bad credentials")

    with pytest.raises(AuthenticationError):
        non_transient_call()

    # Must fail immediately on attempt 1 without wasting retry budget
    assert attempts == 1


def test_reli_01_retry_does_not_retry_rate_limit_or_validation():
    attempts = 0

    @retry_with_backoff(max_attempts=3, wait_seconds=0.01)
    def validation_call():
        nonlocal attempts
        attempts += 1
        raise DomainValidationError("Invalid machine ID format")

    with pytest.raises(DomainValidationError):
        validation_call()
    assert attempts == 1


def test_reli_01_call_sdk_with_retry_succeeds_after_transient_retry():
    calls = 0

    def flaky_sdk_operation():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise EdhTransientError("Service temporarily unavailable")
        return [{"id": "rec-1"}]

    res = call_sdk_with_retry(flaky_sdk_operation)
    assert res == [{"id": "rec-1"}]
    assert calls == 2


# ===========================================================================
# RELI-02 Tests: update_operational_state preserves real previous_state
# ===========================================================================

@pytest.mark.asyncio
async def test_reli_02_update_operational_state_preserves_actual_state():
    settings = Settings(
        DATA_PROVIDER_MODE="MOCK",
        SDK_API_KEY="test-key",
        SDK_BASE_URL="https://mock-edh.corp.internal",
    )
    adapter = SdkMachineAdapter(settings)
    mock_connector = MagicMock()
    adapter._connector = mock_connector

    # Simulate get_by_id returning machine with status "MAINTENANCE" (not "ACTIVE")
    fake_machine_record = {
        "machine_id": "MCH-A1B2-C3D4",
        "model_type": "GENERIC-M1",
        "model_series": "SERIES-1",
        "firmware_version": "v1.0.0",
        "commissioning_date": "2023-01-01",
        "status": "MAINTENANCE",  # Current state is MAINTENANCE
        "location": {"site_id": "SITE-A1B2"},
        "hardware_spec": {"cpu_cores": 8},
        "owner_customer_id": "CUST-001001",
        "serial_number": "SN-000000",
        "last_heartbeat": "2026-11-17T00:00:00+00:00",
    }
    mock_connector.read_records.return_value = [fake_machine_record]

    put_bytes_args = []
    mock_connector.put_bytes.side_effect = lambda ref, data: put_bytes_args.append(json.loads(data.decode("utf-8")))

    res = await adapter.update_operational_state(
        MachineId("MCH-A1B2-C3D4"),
        target_state="ACTIVE",
        reason="Maintenance completed successfully",
    )

    # Previous state must be "MAINTENANCE", not hardcoded "ACTIVE"!
    assert res.previous_state == "MAINTENANCE"
    assert res.new_state == "ACTIVE"

    # Verify audit payload written to storage contains actual previous_state
    assert len(put_bytes_args) == 1
    written_payload = put_bytes_args[0]
    assert written_payload["previous_state"] == "MAINTENANCE"
    assert written_payload["target_state"] == "ACTIVE"
    assert written_payload["reason"] == "Maintenance completed successfully"
    assert "audit_id" in written_payload


# ===========================================================================
# SEC-04 Tests: Query length limits
# ===========================================================================

@pytest.mark.asyncio
async def test_sec_04_customer_search_query_length_limit():
    from mcp.customer_mcp.application.use_cases import SearchCustomersUseCase
    repo = MagicMock()
    uc = SearchCustomersUseCase(repo)
    long_query = "A" * 101
    with pytest.raises(DomainValidationError, match="Query length cannot exceed 100 characters"):
        await uc.execute(query=long_query)


@pytest.mark.asyncio
async def test_sec_04_fault_code_length_limit():
    from mcp.telemetry_mcp.application.use_cases import DiagnoseFaultCodeUseCase
    repo = MagicMock()
    uc = DiagnoseFaultCodeUseCase(repo)
    long_code = "E-" + "1" * 50
    with pytest.raises(DomainValidationError, match="fault_code cannot exceed 50 characters"):
        await uc.execute(long_code)


@pytest.mark.asyncio
async def test_sec_04_operational_state_reason_length_limit():
    from mcp.machine_mcp.application.use_cases import UpdateOperationalStateUseCase
    repo = MagicMock()
    uc = UpdateOperationalStateUseCase(repo)
    long_reason = "R" * 501
    with pytest.raises(DomainValidationError, match="reason length cannot exceed 500 characters"):
        await uc.execute("MCH-A1B2-C3D4", "MAINTENANCE", long_reason)


# ===========================================================================
# SEC-05 Tests: Error sanitization prevents leaking SDK internals (OWASP A05)
# ===========================================================================

def test_sec_05_internal_error_sanitization():
    from common.infrastructure.resilience import sanitize_error_message
    from core.exceptions import SdkGatewayError

    # Simulate an internal SDK exception leaking internal infrastructure details
    leak_error = SdkGatewayError("Connection failed to http://internal-db:5432/secrets?token=supersecret123")
    safe_msg, error_type = sanitize_error_message(leak_error, correlation_id="corr-999")

    # Safe message must not contain credentials, internal URLs, or database names
    assert "supersecret123" not in safe_msg
    assert "5432" not in safe_msg
    assert "internal-db" not in safe_msg
    assert "Reference correlation_id: corr-999" in safe_msg
    assert error_type == "InternalServerError"


def test_sec_05_domain_validation_message_preserved():
    from common.infrastructure.resilience import sanitize_error_message
    val_err = DomainValidationError("Machine status must be ACTIVE or MAINTENANCE")
    safe_msg, error_type = sanitize_error_message(val_err, correlation_id="corr-111")
    assert safe_msg == "Machine status must be ACTIVE or MAINTENANCE"
    assert error_type == "DomainValidationError"


# ===========================================================================
# ARCH-02 Tests: Open/Closed Principle for GatewayFactory
# ===========================================================================

def test_arch_02_factory_open_closed_extension():
    GatewayFactory.clear_cache()

    class CustomMockAdapter:
        def __init__(self):
            self.provider_type = "CUSTOM_PLUGIN"

    # Register new provider dynamically without modifying GatewayFactory class code
    GatewayFactory.register_provider(
        service="customer",
        mode="CUSTOM",
        factory=lambda s: CustomMockAdapter(),
    )

    settings = Settings(DATA_PROVIDER_MODE="CUSTOM")  # type: ignore[arg-type]
    adapter = GatewayFactory.get_customer_gateway(settings)
    assert getattr(adapter, "provider_type") == "CUSTOM_PLUGIN"


# ===========================================================================
# ARCH-03 Tests: Enums as typed, validated values
# ===========================================================================

def test_arch_03_domain_enums():
    from common.domain.enums import CustomerTier, MachineStatus, AlertSeverity

    assert CustomerTier.ENTERPRISE == "ENTERPRISE"
    assert CustomerTier("PREMIUM") == CustomerTier.PREMIUM

    with pytest.raises(ValueError):
        CustomerTier.from_str("NON_EXISTENT_TIER")

    assert MachineStatus.ACTIVE == "ACTIVE"
    with pytest.raises(ValueError):
        MachineStatus.from_str("BROKEN")

    assert AlertSeverity.CRITICAL == "CRITICAL"


# ===========================================================================
# ARCH-04 Tests: PaginationParams value object wiring
# ===========================================================================

def test_arch_04_pagination_params_validation_and_conversion():
    from common.domain.value_objects import PaginationParams

    # Valid page-based construction
    p1 = PaginationParams.from_page(page=2, page_size=15)
    assert p1.page == 2
    assert p1.page_size == 15
    assert p1.offset == 15
    assert p1.limit == 15

    # Valid offset-based construction
    p2 = PaginationParams.from_offset_limit(offset=30, limit=10)
    assert p2.page == 4
    assert p2.offset == 30
    assert p2.limit == 10

    # Boundary validations
    with pytest.raises(DomainValidationError):
        PaginationParams.from_page(page=0)
    with pytest.raises(DomainValidationError):
        PaginationParams.from_page(page_size=101)
    with pytest.raises(DomainValidationError):
        PaginationParams.from_offset_limit(offset=-1)


# ===========================================================================
# RELI-03 Tests: Circuit Breaker pattern
# ===========================================================================

def test_reli_03_circuit_breaker_fast_failure():
    from common.infrastructure.resilience import CircuitBreaker, CircuitState
    from core.exceptions import CircuitBreakerOpenError

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=0.1, name="test-cb")
    assert breaker.state == CircuitState.CLOSED

    # Record 2 transient failures
    breaker.record_failure(ConnectionError("Downstream service unreachable"))
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure(ConnectionError("Downstream service unreachable"))
    assert breaker.state == CircuitState.OPEN

    # While OPEN, before_call must immediately raise CircuitBreakerOpenError without waiting
    with pytest.raises(CircuitBreakerOpenError, match="Failing fast to prevent cascading timeout"):
        breaker.before_call()

    # Wait for recovery timeout to transition to HALF_OPEN
    import time
    time.sleep(0.12)
    assert breaker.state == CircuitState.HALF_OPEN

    # A successful call resets breaker to CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


# ===========================================================================
# OBS-01 & OBS-02 Tests: OpenTelemetry tracing & Health/Readiness
# ===========================================================================

def test_obs_01_opentelemetry_tracing():
    from core.telemetry import get_current_trace_id, trace_mcp_tool

    trace_id = get_current_trace_id()
    assert isinstance(trace_id, str) and len(trace_id) > 0

    with trace_mcp_tool("test_tool", "test_server", "corr-123", extra="val") as span:
        assert True


def test_obs_02_health_and_readiness_endpoints(client):
    # Liveness probe
    res_liveness = client.get("/health")
    assert res_liveness.status_code == 200
    assert res_liveness.json()["status"] == "healthy"

    # Readiness probe
    res_readiness = client.get("/ready")
    assert res_readiness.status_code == 200
    data = res_readiness.json()
    assert data["status"] == "ready"
    assert "customer_mcp" in data["components"]
    assert "machine_mcp" in data["components"]
    assert "telemetry_mcp" in data["components"]
