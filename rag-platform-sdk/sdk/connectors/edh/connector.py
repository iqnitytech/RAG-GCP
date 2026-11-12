"""Generic Enterprise Data Hub (EDH) Connector context (Strategy Pattern).

Wraps any :class:`EdhStrategyPort` with unified resilience (circuit breaker +
exponential backoff with jitter), telemetry logging with ``request_id``,
input validation, synchronous and asynchronous execution, and explicit
connection open/close lifecycle management.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence

from sdk.connectors.edh.ports import EdhStrategyPort
from sdk.connectors.edh.registry import EdhStrategyRegistry, default_strategy_registry
from sdk.connectors.edh.resilience import CircuitBreaker, execute_with_resilience
from sdk.errors import EdhConfigError
from sdk.models.edh_models import (
    DataZone,
    EdhConnectorConfig,
    EdhListPage,
    EdhObjectMetadata,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)

logger = logging.getLogger("sdk.edh.connector")


class EdhConnector:
    """Production-ready Enterprise Data Hub connector delegating to a pluggable strategy.

    Supports both synchronous (``with connector:``) and asynchronous
    (``async with connector:``) connection lifecycle management. When a method
    is invoked outside an explicit ``with`` / ``open()`` session, the connector
    automatically opens a connection for the operation and closes it in a
    ``finally`` block when done.
    """

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        strategy: EdhStrategyPort | None = None,
        registry: EdhStrategyRegistry = default_strategy_registry,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._strategy: EdhStrategyPort = strategy or registry.create(config)
        self._circuit_breaker = CircuitBreaker(
            config.circuit_breaker_policy,
            provider=self._strategy.strategy_name,
        )
        self._sleep_fn = sleep_fn
        self._is_open = False

    @property
    def config(self) -> EdhConnectorConfig:
        return self._config

    @property
    def strategy(self) -> EdhStrategyPort:
        return self._strategy

    @property
    def strategy_name(self) -> str:
        return self._strategy.strategy_name

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def is_open(self) -> bool:
        """Return ``True`` if the connector has an active open connection session."""
        strategy_open = getattr(self._strategy, "is_open", None)
        if isinstance(strategy_open, bool):
            return self._is_open and strategy_open
        return self._is_open

    def open(self) -> EdhConnector:
        """Explicitly open the underlying strategy connection."""
        open_fn = getattr(self._strategy, "open", None)
        if callable(open_fn):
            open_fn()
        self._is_open = True
        return self

    def close(self) -> None:
        """Explicitly close the underlying strategy connection."""
        try:
            self._strategy.close()
        finally:
            self._is_open = False

    async def aopen(self) -> EdhConnector:
        """Asynchronously open the underlying strategy connection."""
        aopen_fn = getattr(self._strategy, "aopen", None)
        if callable(aopen_fn):
            await aopen_fn()
            self._is_open = True
            return self
        return await asyncio.to_thread(self.open)

    async def aclose(self) -> None:
        """Asynchronously close the underlying strategy connection."""
        aclose_fn = getattr(self._strategy, "aclose", None)
        try:
            if callable(aclose_fn):
                await aclose_fn()
            else:
                await asyncio.to_thread(self._strategy.close)
        finally:
            self._is_open = False

    @contextmanager
    def _managed_connection(self) -> Iterator[None]:
        """Ensure connection is opened before work and closed when done if not already open."""
        already_open = self.is_open
        if not already_open:
            self.open()
        try:
            yield
        finally:
            if not already_open:
                self.close()

    @asynccontextmanager
    async def _amanaged_connection(self) -> AsyncIterator[None]:
        """Async version of :meth:`_managed_connection`."""
        already_open = self.is_open
        if not already_open:
            await self.aopen()
        try:
            yield
        finally:
            if not already_open:
                await self.aclose()

    def ref(
        self,
        key: str,
        *,
        zone: DataZone = DataZone.RAW,
        version: str | None = None,
    ) -> EdhObjectRef:
        """Build an :class:`EdhObjectRef` scoped to this connector's namespace."""
        return EdhObjectRef(
            namespace=self._config.namespace,
            key=key,
            zone=zone,
            version=version,
        )

    def put_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        data: bytes,
        *,
        zone: DataZone = DataZone.RAW,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Store raw bytes at ``ref_or_key`` with retry/circuit-breaker protection."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        opts = options or EdhWriteOptions()
        is_idempotent = opts.overwrite or bool(opts.idempotency_key)
        started = time.perf_counter()

        with self._managed_connection():
            meta = execute_with_resilience(
                "put_bytes",
                lambda: self._strategy.put_bytes(ref, data, options=opts),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=is_idempotent,
                sleep_fn=self._sleep_fn,
            )
        logger.info(
            "EDH put_bytes strategy=%s uri=%s size=%d sha256=%s (%.1f ms)",
            self.strategy_name,
            meta.ref.uri,
            meta.size_bytes,
            meta.sha256[:12],
            (time.perf_counter() - started) * 1000,
        )
        return meta

    def get_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bytes:
        """Download and verify raw bytes for ``ref_or_key``."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        with self._managed_connection():
            return execute_with_resilience(
                "get_bytes",
                lambda: self._strategy.get_bytes(ref),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    def stream_chunks(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        """Stream payload chunks for ``ref_or_key``."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        with self._managed_connection():
            self._circuit_breaker.before_call("stream_chunks")
            try:
                for chunk in self._strategy.stream_chunks(ref, chunk_size=chunk_size):
                    yield chunk
                self._circuit_breaker.record_success()
            except Exception:
                self._circuit_breaker.record_failure()
                raise

    def head(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> EdhObjectMetadata:
        """Inspect object metadata without downloading payload bytes."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        with self._managed_connection():
            return execute_with_resilience(
                "head",
                lambda: self._strategy.head(ref),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    def delete(
        self,
        ref_or_key: EdhObjectRef | str,
        record_id: str | None = None,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bool:
        """Delete ``ref_or_key`` (or ``delete(sobject_name, record_id)`` when ``record_id`` is given)."""
        if record_id is not None and isinstance(ref_or_key, str):
            return self.delete_record(ref_or_key, record_id)
        ref = self._coerce_ref(ref_or_key, zone=zone)
        with self._managed_connection():
            return execute_with_resilience(
                "delete",
                lambda: self._strategy.delete(ref),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    def list_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        """List objects under ``zone`` and ``prefix``."""
        with self._managed_connection():
            return execute_with_resilience(
                "list_objects",
                lambda: self._strategy.list_objects(
                    zone=zone,
                    prefix=prefix,
                    page_size=page_size,
                    page_token=page_token,
                ),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Write a structured record batch to a partitioned dataset."""
        opts = options or EdhWriteOptions()
        is_idempotent = opts.overwrite or bool(opts.idempotency_key)
        with self._managed_connection():
            return execute_with_resilience(
                "write_records",
                lambda: self._strategy.write_records(batch, options=opts),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=is_idempotent,
                sleep_fn=self._sleep_fn,
            )

    def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> Iterator[dict[str, Any]]:
        """Stream structured records from ``dataset`` matching ``partition_filter``."""
        with self._managed_connection():
            self._circuit_breaker.before_call("read_records")
            try:
                for row in self._strategy.read_records(
                    dataset,
                    zone=zone,
                    partition_filter=partition_filter,
                    record_format=record_format,
                ):
                    yield row
                self._circuit_breaker.record_success()
            except Exception:
                self._circuit_breaker.record_failure()
                raise

    # ------------------------------------------------------------------
    # Generic Record / Schema / Bulk Operations (e.g. Salesforce)
    # ------------------------------------------------------------------

    def _require_strategy_method(self, method_name: str) -> Callable[..., Any]:
        fn = getattr(self._strategy, method_name, None)
        if not callable(fn):
            raise EdhConfigError(
                f"Strategy {self.strategy_name!r} does not support {method_name!r}",
                provider=self.strategy_name,
                operation=method_name,
            )
        return fn

    def describe_schema(self, sobject_name: str | None = None) -> dict[str, Any]:
        """1. Describe Schema (global or for a specific ``sobject_name``)."""
        fn = self._require_strategy_method("describe_schema")
        with self._managed_connection():
            return execute_with_resilience(
                "describe_schema",
                lambda: fn(sobject_name),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    describeSchema = describe_schema
    DescribeSchema = describe_schema

    def query(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """2. Query records via SOQL or structured SObject parameters."""
        fn = self._require_strategy_method("query")
        with self._managed_connection():
            return execute_with_resilience(
                "query",
                lambda: fn(soql_or_sobject, fields=fields, where=where, limit=limit),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    Query = query

    def paginated_query(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        batch_size: int = 2000,
    ) -> Iterator[list[dict[str, Any]]]:
        """3. PaginatedQuery yielding record pages across ``nextRecordsUrl``."""
        fn = self._require_strategy_method("paginated_query")
        with self._managed_connection():
            self._circuit_breaker.before_call("paginated_query")
            try:
                for page in fn(
                    soql_or_sobject,
                    fields=fields,
                    where=where,
                    limit=limit,
                    batch_size=batch_size,
                ):
                    yield page
                self._circuit_breaker.record_success()
            except Exception:
                self._circuit_breaker.record_failure()
                raise

    paginatedQuery = paginated_query
    PaginatedQuery = paginated_query

    def get_record_by_id(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """4. GetRecordById for ``sobject_name`` and ``record_id``."""
        fn = self._require_strategy_method("get_record_by_id")
        with self._managed_connection():
            return execute_with_resilience(
                "get_record_by_id",
                lambda: fn(sobject_name, record_id, fields=fields),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    getRecordById = get_record_by_id
    GetRecordById = get_record_by_id

    def create_record(
        self,
        sobject_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """5. Create / createRecord: Create a single record in ``sobject_name``."""
        fn = self._require_strategy_method("create")
        with self._managed_connection():
            return execute_with_resilience(
                "create_record",
                lambda: fn(sobject_name, data),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=False,
                sleep_fn=self._sleep_fn,
            )

    create = create_record
    Create = create_record
    createRecord = create_record
    CreateRecord = create_record

    def update(
        self,
        sobject_name: str,
        record_id: str,
        data: Mapping[str, Any],
    ) -> bool:
        """6. Update a single record in ``sobject_name`` by ``record_id``."""
        fn = self._require_strategy_method("update")
        with self._managed_connection():
            return execute_with_resilience(
                "update",
                lambda: fn(sobject_name, record_id, data),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    update_record = update
    updateRecord = update
    UpdateRecord = update
    Update = update

    def upsert(
        self,
        sobject_name: str,
        external_id_field: str,
        external_id_value: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """7. Upsert a single record in ``sobject_name`` by external ID."""
        fn = self._require_strategy_method("upsert")
        with self._managed_connection():
            return execute_with_resilience(
                "upsert",
                lambda: fn(sobject_name, external_id_field, external_id_value, data),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    upsert_record = upsert
    upsertRecord = upsert
    UpsertRecord = upsert
    Upsert = upsert

    def delete_record(
        self,
        sobject_name: str,
        record_id: str,
    ) -> bool:
        """8. Delete / deleteRecord: Delete a single record in ``sobject_name`` by ``record_id``."""
        fn = self._require_strategy_method("delete_record")
        with self._managed_connection():
            return execute_with_resilience(
                "delete_record",
                lambda: fn(sobject_name, record_id),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    deleteRecord = delete_record
    DeleteRecord = delete_record
    Delete = delete_record

    def bulk_read(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> list[dict[str, Any]]:
        """9. BulkRead records via Bulk API 2.0 Query."""
        fn = self._require_strategy_method("bulk_read")
        with self._managed_connection():
            return execute_with_resilience(
                "bulk_read",
                lambda: fn(
                    soql_or_sobject,
                    fields=fields,
                    where=where,
                    limit=limit,
                    poll_interval_seconds=poll_interval_seconds,
                    max_polls=max_polls,
                ),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=True,
                sleep_fn=self._sleep_fn,
            )

    bulkRead = bulk_read
    BulkRead = bulk_read

    def bulk_write(
        self,
        sobject_name: str,
        records: Sequence[Mapping[str, Any]],
        *,
        operation: str = "insert",
        external_id_field: str | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> dict[str, Any]:
        """10. BulkWrite records via Bulk API 2.0 Ingest."""
        fn = self._require_strategy_method("bulk_write")
        with self._managed_connection():
            return execute_with_resilience(
                "bulk_write",
                lambda: fn(
                    sobject_name,
                    records,
                    operation=operation,
                    external_id_field=external_id_field,
                    poll_interval_seconds=poll_interval_seconds,
                    max_polls=max_polls,
                ),
                retry_policy=self._config.retry_policy,
                circuit_breaker=self._circuit_breaker,
                idempotent=operation in ("update", "upsert", "delete"),
                sleep_fn=self._sleep_fn,
            )

    bulkWrite = bulk_write
    BulkWrite = bulk_write

    def ping(self) -> bool:
        """Readiness probe compatible with ``create_base_app(readiness_check=...)``."""
        if self._circuit_breaker.state.value == "open":
            return False
        with self._managed_connection():
            return self._strategy.ping()

    # ------------------------------------------------------------------
    # Async Methods on EdhConnector
    # ------------------------------------------------------------------

    async def aping(self) -> bool:
        async with self._amanaged_connection():
            return await asyncio.to_thread(self.ping)

    async def aput_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        data: bytes,
        *,
        zone: DataZone = DataZone.RAW,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.put_bytes(ref_or_key, data, zone=zone, options=options)
            )

    async def aget_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bytes:
        async with self._amanaged_connection():
            return await asyncio.to_thread(lambda: self.get_bytes(ref_or_key, zone=zone))

    async def astream_chunks(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
        chunk_size: int = 65536,
    ) -> AsyncIterator[bytes]:
        async with self._amanaged_connection():
            chunks = await asyncio.to_thread(
                lambda: list(self.stream_chunks(ref_or_key, zone=zone, chunk_size=chunk_size))
            )
            for chunk in chunks:
                yield chunk

    async def ahead(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> EdhObjectMetadata:
        async with self._amanaged_connection():
            return await asyncio.to_thread(lambda: self.head(ref_or_key, zone=zone))

    async def adelete(
        self,
        ref_or_key: EdhObjectRef | str,
        record_id: str | None = None,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bool:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.delete(ref_or_key, record_id, zone=zone)
            )

    async def alist_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.list_objects(
                    zone=zone,
                    prefix=prefix,
                    page_size=page_size,
                    page_token=page_token,
                )
            )

    async def awrite_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        async with self._amanaged_connection():
            return await asyncio.to_thread(lambda: self.write_records(batch, options=options))

    async def aread_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._amanaged_connection():
            rows = await asyncio.to_thread(
                lambda: list(
                    self.read_records(
                        dataset,
                        zone=zone,
                        partition_filter=partition_filter,
                        record_format=record_format,
                    )
                )
            )
            for row in rows:
                yield row

    async def adescribe_schema(self, sobject_name: str | None = None) -> dict[str, Any]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(self.describe_schema, sobject_name)

    adescribeSchema = adescribe_schema
    aDescribeSchema = adescribe_schema

    async def aquery(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.query(soql_or_sobject, fields=fields, where=where, limit=limit)
            )

    aQuery = aquery

    async def apaginated_query(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        batch_size: int = 2000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async with self._amanaged_connection():
            pages = await asyncio.to_thread(
                lambda: list(
                    self.paginated_query(
                        soql_or_sobject,
                        fields=fields,
                        where=where,
                        limit=limit,
                        batch_size=batch_size,
                    )
                )
            )
            for page in pages:
                yield page

    apaginatedQuery = apaginated_query
    aPaginatedQuery = apaginated_query

    async def aget_record_by_id(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.get_record_by_id(sobject_name, record_id, fields=fields)
            )

    agetRecordById = aget_record_by_id
    aGetRecordById = aget_record_by_id

    async def acreate_record(
        self,
        sobject_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(self.create_record, sobject_name, data)

    acreate = acreate_record
    aCreate = acreate_record
    acreateRecord = acreate_record
    aCreateRecord = acreate_record

    async def aupdate(
        self,
        sobject_name: str,
        record_id: str,
        data: Mapping[str, Any],
    ) -> bool:
        async with self._amanaged_connection():
            return await asyncio.to_thread(self.update, sobject_name, record_id, data)

    aupdate_record = aupdate
    aupdateRecord = aupdate
    aUpdateRecord = aupdate
    aUpdate = aupdate

    async def aupsert(
        self,
        sobject_name: str,
        external_id_field: str,
        external_id_value: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                self.upsert, sobject_name, external_id_field, external_id_value, data
            )

    aupsert_record = aupsert
    aupsertRecord = aupsert
    aUpsertRecord = aupsert
    aUpsert = aupsert

    async def adelete_record(
        self,
        sobject_name: str,
        record_id: str,
    ) -> bool:
        async with self._amanaged_connection():
            return await asyncio.to_thread(self.delete_record, sobject_name, record_id)

    adeleteRecord = adelete_record
    aDeleteRecord = adelete_record
    aDelete = adelete_record

    async def abulk_read(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> list[dict[str, Any]]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.bulk_read(
                    soql_or_sobject,
                    fields=fields,
                    where=where,
                    limit=limit,
                    poll_interval_seconds=poll_interval_seconds,
                    max_polls=max_polls,
                )
            )

    abulkRead = abulk_read
    aBulkRead = abulk_read

    async def abulk_write(
        self,
        sobject_name: str,
        records: Sequence[Mapping[str, Any]],
        *,
        operation: str = "insert",
        external_id_field: str | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> dict[str, Any]:
        async with self._amanaged_connection():
            return await asyncio.to_thread(
                lambda: self.bulk_write(
                    sobject_name,
                    records,
                    operation=operation,
                    external_id_field=external_id_field,
                    poll_interval_seconds=poll_interval_seconds,
                    max_polls=max_polls,
                )
            )

    abulkWrite = abulk_write
    aBulkWrite = abulk_write

    def __enter__(self) -> EdhConnector:
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    async def __aenter__(self) -> EdhConnector:
        return await self.aopen()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _coerce_ref(self, ref_or_key: EdhObjectRef | str, *, zone: DataZone) -> EdhObjectRef:
        if isinstance(ref_or_key, EdhObjectRef):
            return ref_or_key
        return self.ref(ref_or_key, zone=zone)


class AsyncEdhConnector:
    """Asynchronous Enterprise Data Hub connector with automatic connection open/close lifecycle.

    Usage::

        async with AsyncEdhConnector(config) as sf:
            schema = await sf.describeSchema("Account")
            created = await sf.createRecord("Account", {"Name": "Acme"})
            rec = await sf.getRecordById("Account", created["id"])
            await sf.updateRecord("Account", created["id"], {"Name": "Acme Corp"})
            await sf.deleteRecord("Account", created["id"])
    """

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        strategy: EdhStrategyPort | None = None,
        registry: EdhStrategyRegistry = default_strategy_registry,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sync = EdhConnector(
            config,
            strategy=strategy,
            registry=registry,
            sleep_fn=sleep_fn,
        )

    @property
    def sync_connector(self) -> EdhConnector:
        return self._sync

    @property
    def config(self) -> EdhConnectorConfig:
        return self._sync.config

    @property
    def strategy(self) -> EdhStrategyPort:
        return self._sync.strategy

    @property
    def strategy_name(self) -> str:
        return self._sync.strategy_name

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._sync.circuit_breaker

    @property
    def is_open(self) -> bool:
        return self._sync.is_open

    def ref(
        self,
        key: str,
        *,
        zone: DataZone = DataZone.RAW,
        version: str | None = None,
    ) -> EdhObjectRef:
        return self._sync.ref(key, zone=zone, version=version)

    async def open(self) -> AsyncEdhConnector:
        await self._sync.aopen()
        return self

    async def close(self) -> None:
        await self._sync.aclose()

    async def __aenter__(self) -> AsyncEdhConnector:
        return await self.open()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def ping(self) -> bool:
        return await self._sync.aping()

    async def put_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        data: bytes,
        *,
        zone: DataZone = DataZone.RAW,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        return await self._sync.aput_bytes(ref_or_key, data, zone=zone, options=options)

    async def get_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bytes:
        return await self._sync.aget_bytes(ref_or_key, zone=zone)

    async def stream_chunks(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
        chunk_size: int = 65536,
    ) -> AsyncIterator[bytes]:
        async for chunk in self._sync.astream_chunks(ref_or_key, zone=zone, chunk_size=chunk_size):
            yield chunk

    async def head(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> EdhObjectMetadata:
        return await self._sync.ahead(ref_or_key, zone=zone)

    async def delete(
        self,
        ref_or_key: EdhObjectRef | str,
        record_id: str | None = None,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bool:
        return await self._sync.adelete(ref_or_key, record_id, zone=zone)

    async def list_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        return await self._sync.alist_objects(
            zone=zone,
            prefix=prefix,
            page_size=page_size,
            page_token=page_token,
        )

    async def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        return await self._sync.awrite_records(batch, options=options)

    async def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> AsyncIterator[dict[str, Any]]:
        async for row in self._sync.aread_records(
            dataset,
            zone=zone,
            partition_filter=partition_filter,
            record_format=record_format,
        ):
            yield row

    async def describe_schema(self, sobject_name: str | None = None) -> dict[str, Any]:
        return await self._sync.adescribe_schema(sobject_name)

    describeSchema = describe_schema
    DescribeSchema = describe_schema

    async def query(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._sync.aquery(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
        )

    Query = query

    async def paginated_query(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        batch_size: int = 2000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._sync.apaginated_query(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
            batch_size=batch_size,
        ):
            yield page

    paginatedQuery = paginated_query
    PaginatedQuery = paginated_query

    async def get_record_by_id(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return await self._sync.aget_record_by_id(sobject_name, record_id, fields=fields)

    getRecordById = get_record_by_id
    GetRecordById = get_record_by_id

    async def create_record(
        self,
        sobject_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._sync.acreate_record(sobject_name, data)

    create = create_record
    Create = create_record
    createRecord = create_record
    CreateRecord = create_record

    async def update(
        self,
        sobject_name: str,
        record_id: str,
        data: Mapping[str, Any],
    ) -> bool:
        return await self._sync.aupdate(sobject_name, record_id, data)

    update_record = update
    updateRecord = update
    UpdateRecord = update
    Update = update

    async def upsert(
        self,
        sobject_name: str,
        external_id_field: str,
        external_id_value: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._sync.aupsert(
            sobject_name,
            external_id_field,
            external_id_value,
            data,
        )

    upsert_record = upsert
    upsertRecord = upsert
    UpsertRecord = upsert
    Upsert = upsert

    async def delete_record(
        self,
        sobject_name: str,
        record_id: str,
    ) -> bool:
        return await self._sync.adelete_record(sobject_name, record_id)

    deleteRecord = delete_record
    DeleteRecord = delete_record
    Delete = delete_record

    async def bulk_read(
        self,
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._sync.abulk_read(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
        )

    bulkRead = bulk_read
    BulkRead = bulk_read

    async def bulk_write(
        self,
        sobject_name: str,
        records: Sequence[Mapping[str, Any]],
        *,
        operation: str = "insert",
        external_id_field: str | None = None,
        poll_interval_seconds: float = 0.05,
        max_polls: int = 100,
    ) -> dict[str, Any]:
        return await self._sync.abulk_write(
            sobject_name,
            records,
            operation=operation,
            external_id_field=external_id_field,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
        )

    bulkWrite = bulk_write
    BulkWrite = bulk_write
