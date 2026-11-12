"""Unit tests for core EDH models, URI/path validation, registry allowlist, retry, and circuit breaker."""

from __future__ import annotations

import pytest

from sdk.connectors.edh import (
    ApiDrivenEdhStrategy,
    CircuitState,
    EdhConfigError,
    EdhConnector,
    EdhIntegrityError,
    EdhStrategyRegistry,
    EdhTransientError,
    RawHttpResponse,
)
from sdk.models.edh_models import (
    CircuitBreakerPolicy,
    DataZone,
    EdhConnectorConfig,
    EdhObjectRef,
    EdhWriteOptions,
    RetryPolicy,
    compute_sha256,
)


def test_uri_parsing_and_path_traversal_blocked():
    ref = EdhObjectRef.from_uri("edh://my-hub/curated/docs/policy.pdf?version=v2")
    assert ref.namespace == "my-hub"
    assert ref.zone == DataZone.CURATED
    assert ref.key == "docs/policy.pdf"
    assert ref.version == "v2"
    assert ref.uri == "edh://my-hub/curated/docs/policy.pdf?version=v2"

    for bad_key in ("../secrets/key.json", "a/../../etc/passwd", "bad\\path.txt", "a/\x00b"):
        with pytest.raises(EdhConfigError):
            EdhObjectRef(namespace="my-hub", key=bad_key)


def test_integrity_mismatch_raises():
    cfg = EdhConnectorConfig(strategy="memory", namespace="ns")
    conn = EdhConnector(cfg)
    with pytest.raises(EdhIntegrityError):
        conn.put_bytes(
            "doc.txt",
            b"hello",
            options=EdhWriteOptions(expected_sha256="0" * 64),
        )


def test_registry_allowlist_rejects_unregistered_strategy():
    reg = EdhStrategyRegistry()
    with pytest.raises(EdhConfigError):
        reg.create(EdhConnectorConfig(strategy="unregistered", namespace="ns"))


def test_retry_and_circuit_breaker_opens_on_repeated_failures():
    attempts = 0
    sleeps: list[float] = []

    def flaky_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return RawHttpResponse(503, {}, b"Unavailable")
        return RawHttpResponse(200, {"x-edh-sha256": compute_sha256(b"ok")}, b"ok")

    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.05, jitter_ratio=0.0),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=2, recovery_timeout_seconds=10.0),
    )
    strategy = ApiDrivenEdhStrategy(cfg, transport=flaky_transport)
    conn = EdhConnector(cfg, strategy=strategy, sleep_fn=sleeps.append)

    with pytest.raises(EdhTransientError):
        conn.get_bytes("file.txt")
    assert conn.circuit_breaker.state == CircuitState.OPEN
    assert conn.ping() is False

    cfg2 = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.05, jitter_ratio=0.0),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=5, recovery_timeout_seconds=10.0),
    )
    attempts = 0
    conn2 = EdhConnector(
        cfg2,
        strategy=ApiDrivenEdhStrategy(cfg2, transport=flaky_transport),
        sleep_fn=sleeps.append,
    )
    assert conn2.get_bytes("file.txt") == b"ok"
    assert attempts == 3
