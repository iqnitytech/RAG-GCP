"""Disk integration tests for ``DataLakeStrategy`` using ``constants.py`` and ``.env.integration``."""

from __future__ import annotations

from sdk.connectors.edh import (
    DataZone,
    EdhConnector,
    EdhConnectorConfig,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)
from tests.integration.constants import (
    DATA_LAKE_DATASET,
    DATA_LAKE_PARTITION_VALUES,
    DATA_LAKE_QUARANTINE_KEY,
    DATA_LAKE_RAW_KEY,
    DATA_LAKE_RECORDS,
    DATA_LAKE_SAMPLE_PDF_BYTES,
    DEFAULT_NAMESPACE,
)


def test_integration_data_lake_medallion_zone_promotion_on_disk(
    tmp_path,
    integration_env: dict[str, str],
):
    """End-to-end Medallion workflow: QUARANTINE -> RAW -> CURATED using constants.py."""
    namespace = integration_env.get("EDH_NAMESPACE", DEFAULT_NAMESPACE)
    cfg = EdhConnectorConfig(
        strategy="data_lake",
        namespace=namespace,
        root_path=str(tmp_path / "edh_lake"),
    )

    with EdhConnector(cfg) as lake:
        q_meta = lake.put_bytes(
            DATA_LAKE_QUARANTINE_KEY,
            DATA_LAKE_SAMPLE_PDF_BYTES,
            zone=DataZone.QUARANTINE,
            options=EdhWriteOptions(content_type="application/pdf"),
        )
        scanned_bytes = b"".join(lake.stream_chunks(q_meta.ref, chunk_size=8))
        lake.put_bytes(
            DATA_LAKE_RAW_KEY,
            scanned_bytes,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="application/pdf",
                expected_sha256=q_meta.sha256,
            ),
        )
        lake.delete(q_meta.ref)

        batch = EdhRecordBatch(
            dataset=DATA_LAKE_DATASET,
            records=DATA_LAKE_RECORDS,
            partition_values=DATA_LAKE_PARTITION_VALUES,
            zone=DataZone.CURATED,
            record_format=RecordFormat.CSV,
        )
        lake.write_records(batch, options=EdhWriteOptions(compress_gzip=True, idempotency_key="run-1"))

        records = list(
            lake.read_records(
                DATA_LAKE_DATASET,
                zone=DataZone.CURATED,
                partition_filter=DATA_LAKE_PARTITION_VALUES,
                record_format=RecordFormat.CSV,
            )
        )
        assert len(records) == len(DATA_LAKE_RECORDS)
        assert records[0]["chunk_id"] == DATA_LAKE_RECORDS[0]["chunk_id"]
