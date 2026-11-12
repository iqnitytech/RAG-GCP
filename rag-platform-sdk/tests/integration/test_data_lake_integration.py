"""Disk integration tests for ``DataLakeStrategy`` Medallion zone promotion."""

from __future__ import annotations

from sdk.connectors.edh import (
    DataZone,
    EdhConnector,
    EdhConnectorConfig,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)


def test_integration_data_lake_medallion_zone_promotion_on_disk(tmp_path):
    """End-to-end Medallion workflow: QUARANTINE -> RAW -> CURATED (Gzipped CSV & JSONL)."""
    cfg = EdhConnectorConfig(
        strategy="data_lake",
        namespace="enterprise-lake",
        root_path=str(tmp_path / "edh_lake"),
    )

    with EdhConnector(cfg) as lake:
        raw_pdf = b"%PDF-1.4 scanned invoice #9001"
        q_meta = lake.put_bytes(
            "uploads/inv_9001.pdf",
            raw_pdf,
            zone=DataZone.QUARANTINE,
            options=EdhWriteOptions(content_type="application/pdf"),
        )
        scanned_bytes = b"".join(lake.stream_chunks(q_meta.ref, chunk_size=8))
        lake.put_bytes(
            "invoices/2026/inv_9001.pdf",
            scanned_bytes,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="application/pdf",
                expected_sha256=q_meta.sha256,
            ),
        )
        lake.delete(q_meta.ref)

        batch = EdhRecordBatch(
            dataset="invoice_chunks",
            records=(
                {"chunk_id": "inv-1", "amount": "1500.00", "currency": "USD"},
                {"chunk_id": "inv-2", "amount": "250.00", "currency": "USD"},
            ),
            partition_values={"year": "2026", "status": "verified"},
            zone=DataZone.CURATED,
            record_format=RecordFormat.CSV,
        )
        lake.write_records(batch, options=EdhWriteOptions(compress_gzip=True, idempotency_key="run-1"))

        records = list(
            lake.read_records(
                "invoice_chunks",
                zone=DataZone.CURATED,
                partition_filter={"year": "2026", "status": "verified"},
                record_format=RecordFormat.CSV,
            )
        )
        assert len(records) == 2
        assert records[0]["chunk_id"] == "inv-1"
        assert records[1]["amount"] == "250.00"
