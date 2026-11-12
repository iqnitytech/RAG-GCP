"""Live HTTP integration tests for ``ApiDrivenEdhStrategy`` with retry and X-Request-ID."""

from __future__ import annotations

import pytest

from sdk.connectors.edh import (
    DataZone,
    EdhConflictError,
    EdhConnector,
    EdhConnectorConfig,
    EdhNotFoundError,
    EdhRecordBatch,
    EdhWriteOptions,
    RetryPolicy,
    compute_sha256,
)
from sdk.logging.context import reset_request_id, set_request_id


def test_integration_api_hub_over_live_http_with_retry_and_request_id(live_hub):
    base_url, state = live_hub
    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="acme-hub",
        endpoint_url=base_url,
        auth_token_getter=lambda: "live-jwt-token",
        api_key_getter=lambda: "live-api-key",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )

    token = set_request_id("trace-live-001")
    try:
        with EdhConnector(cfg) as edh:
            assert edh.ping() is True

            payload = b"%PDF-1.4 live integration test payload"
            meta = edh.put_bytes(
                "contracts/2026/nda.pdf",
                payload,
                zone=DataZone.RAW,
                options=EdhWriteOptions(
                    content_type="application/pdf",
                    custom_metadata={"owner": "legal"},
                    overwrite=False,
                ),
            )
            assert meta.sha256 == compute_sha256(payload)
            assert state.request_headers_log[-1]["x-request-id"] == "trace-live-001"
            assert state.request_headers_log[-1]["authorization"] == "Bearer live-jwt-token"
            assert state.request_headers_log[-1]["x-api-key"] == "live-api-key"

            with pytest.raises(EdhConflictError):
                edh.put_bytes(
                    "contracts/2026/nda.pdf",
                    b"duplicate",
                    zone=DataZone.RAW,
                    options=EdhWriteOptions(overwrite=False),
                )

            state.fail_next_get_count = 2
            downloaded = edh.get_bytes("contracts/2026/nda.pdf", zone=DataZone.RAW)
            assert downloaded == payload

            batch = EdhRecordBatch(
                dataset="chunks/legal",
                records=(
                    {"id": "1", "clause": "Confidentiality"},
                    {"id": "2", "clause": "Governing Law"},
                ),
                partition_values={"tenant": "acme"},
                zone=DataZone.CURATED,
            )
            edh.write_records(batch)
            rows = list(
                edh.read_records(
                    "chunks/legal",
                    zone=DataZone.CURATED,
                    partition_filter={"tenant": "acme"},
                )
            )
            assert [r["id"] for r in rows] == ["1", "2"]

            assert edh.delete("contracts/2026/nda.pdf", zone=DataZone.RAW) is True
            with pytest.raises(EdhNotFoundError):
                edh.get_bytes("contracts/2026/nda.pdf", zone=DataZone.RAW)
    finally:
        reset_request_id(token)
