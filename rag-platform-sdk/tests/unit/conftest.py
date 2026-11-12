"""Shared Liskov Substitution contract assertions for all EDH strategies."""

from __future__ import annotations

import pytest

from sdk.connectors.edh import (
    EdhConflictError,
    EdhConnector,
    EdhNotFoundError,
    EdhStrategyPort,
)
from sdk.models.edh_models import (
    DataZone,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
    compute_sha256,
)


def assert_strategy_implements_protocol(connector: EdhConnector) -> None:
    """Verify the connector's strategy implements EdhStrategyPort and passes ping()."""
    assert isinstance(connector.strategy, EdhStrategyPort)
    assert connector.ping() is True


def assert_blob_crud_and_pagination_contract(connector: EdhConnector) -> None:
    """Verify put_bytes, get_bytes, stream_chunks, head, conflict, list_objects, and delete."""
    payload = b"Enterprise RAG document content for EDH contract test."
    meta = connector.put_bytes(
        "kb-finance/2026/q3_report.txt",
        payload,
        zone=DataZone.RAW,
        options=EdhWriteOptions(
            content_type="text/plain",
            custom_metadata={"tenant": "acme", "source": "erp"},
            idempotency_key="idem-001",
        ),
    )
    assert meta.size_bytes == len(payload)
    assert meta.sha256 == compute_sha256(payload)
    assert meta.ref.uri.startswith("edh://rag-enterprise/raw/kb-finance/2026/q3_report.txt")

    assert connector.get_bytes("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) == payload

    chunks = list(
        connector.stream_chunks(
            "kb-finance/2026/q3_report.txt",
            zone=DataZone.RAW,
            chunk_size=16,
        )
    )
    assert b"".join(chunks) == payload
    assert len(chunks) >= 3

    head_meta = connector.head("kb-finance/2026/q3_report.txt", zone=DataZone.RAW)
    assert head_meta.size_bytes == len(payload)
    assert head_meta.sha256 == compute_sha256(payload)

    with pytest.raises(EdhConflictError):
        connector.put_bytes(
            "kb-finance/2026/q3_report.txt",
            b"new",
            zone=DataZone.RAW,
            options=EdhWriteOptions(overwrite=False),
        )

    connector.put_bytes("kb-finance/2026/q4_report.txt", b"q4", zone=DataZone.RAW)
    page1 = connector.list_objects(zone=DataZone.RAW, prefix="kb-finance/2026", page_size=1)
    assert len(page1.items) == 1
    assert page1.next_page_token is not None
    page2 = connector.list_objects(
        zone=DataZone.RAW,
        prefix="kb-finance/2026",
        page_size=1,
        page_token=page1.next_page_token,
    )
    assert len(page2.items) == 1

    assert connector.delete("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) is True
    assert connector.delete("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) is False
    with pytest.raises(EdhNotFoundError):
        connector.get_bytes("kb-finance/2026/q3_report.txt", zone=DataZone.RAW)


def assert_partitioned_records_contract(connector: EdhConnector) -> None:
    """Verify partitioned write_records and read_records filtering."""
    batch_in = EdhRecordBatch(
        dataset="chunks/hr_policies",
        records=(
            {"chunk_id": "c1", "tenant_id": "t1", "text": "Leave policy section 1"},
            {"chunk_id": "c2", "tenant_id": "t1", "text": "Leave policy section 2"},
        ),
        partition_values={"tenant_id": "t1", "lang": "en"},
        zone=DataZone.CURATED,
        record_format=RecordFormat.JSONL,
    )
    batch_other = EdhRecordBatch(
        dataset="chunks/hr_policies",
        records=({"chunk_id": "c3", "tenant_id": "t2", "text": "Other tenant"},),
        partition_values={"tenant_id": "t2", "lang": "en"},
        zone=DataZone.CURATED,
        record_format=RecordFormat.JSONL,
    )

    connector.write_records(batch_in, options=EdhWriteOptions(idempotency_key="b1"))
    connector.write_records(batch_other, options=EdhWriteOptions(idempotency_key="b2"))

    rows_t1 = list(
        connector.read_records(
            "chunks/hr_policies",
            zone=DataZone.CURATED,
            partition_filter={"tenant_id": "t1"},
        )
    )
    assert [r["chunk_id"] for r in rows_t1] == ["c1", "c2"]
