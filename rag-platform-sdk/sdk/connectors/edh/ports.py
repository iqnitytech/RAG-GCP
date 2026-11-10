"""Role-segregated strategy ports (Protocols) for Enterprise Data Hub connectors.

Following the **Interface Segregation** and **Dependency Inversion** principles:
- Read-only consumers depend only on :class:`EdhReadStrategyPort`.
- Ingestion / write pipelines depend on :class:`EdhWriteStrategyPort` or
  :class:`EdhRecordStrategyPort`.
- Full connectors implement :class:`EdhStrategyPort`.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Protocol, runtime_checkable

from sdk.models.edh_models import (
    DataZone,
    EdhListPage,
    EdhObjectMetadata,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)


@runtime_checkable
class EdhReadStrategyPort(Protocol):
    """Read-only operations against an Enterprise Data Hub backend."""

    @property
    def strategy_name(self) -> str:
        """Stable identifier for this strategy (e.g. ``data_lake``, ``object_storage_hub``, ``api_hub``)."""
        ...

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        """Download and return the complete payload for ``ref``."""
        ...

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        """Yield payload chunks for ``ref`` without buffering the full object in memory."""
        ...

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        """Return object metadata without downloading payload bytes."""
        ...

    def list_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        """List objects under ``zone`` and ``prefix``."""
        ...


@runtime_checkable
class EdhWriteStrategyPort(Protocol):
    """Write and delete operations against an Enterprise Data Hub backend."""

    def put_bytes(
        self,
        ref: EdhObjectRef,
        data: bytes,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Store ``data`` at ``ref`` and return verified object metadata."""
        ...

    def delete(self, ref: EdhObjectRef) -> bool:
        """Delete ``ref``. Returns ``True`` if an object existed and was deleted."""
        ...


@runtime_checkable
class EdhRecordStrategyPort(Protocol):
    """Structured dataset/table record operations for Data Lake & API hubs."""

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Write a batch of structured records into a partitioned dataset."""
        ...

    def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> Iterator[ dict[str, Any]]:
        """Stream structured records matching ``dataset`` and ``partition_filter``."""
        ...


@runtime_checkable
class EdhLifecyclePort(Protocol):
    """Health probe and resource cleanup contract."""

    def ping(self) -> bool:
        """Return ``True`` when the underlying data hub is reachable."""
        ...

    def close(self) -> None:
        """Release any open file handles, sockets, or pools."""
        ...


@runtime_checkable
class EdhStrategyPort(
    EdhReadStrategyPort,
    EdhWriteStrategyPort,
    EdhRecordStrategyPort,
    EdhLifecyclePort,
    Protocol,
):
    """Composite strategy contract supporting blob, record, and lifecycle operations."""
    ...
