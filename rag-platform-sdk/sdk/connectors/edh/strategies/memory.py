"""In-memory fake EDH strategy for fast unit and contract testing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator, Mapping

from sdk.connectors.edh.error_constants.MemoryErrorConstants import MemoryErrorConstants
from sdk.connectors.edh.errors import (
    EdhConfigError,
    EdhConflictError,
    EdhIntegrityError,
    EdhNotFoundError,
    log_and_build_edh_error,
)
from sdk.models.edh_models import (
    DataZone,
    EdhConnectorConfig,
    EdhListPage,
    EdhObjectMetadata,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
    compute_sha256,
    normalize_object_key,
)
from sdk.connectors.edh.strategies._serde import deserialize_records, serialize_records

logger = logging.getLogger("sdk.edh.memory")


class InMemoryEdhStrategy:
    """Thread-safe in-memory strategy passing the full EDH contract test suite."""

    STRATEGY_NAME = "memory"

    def __init__(self, config: EdhConnectorConfig) -> None:
        self._config = config
        self._lock = Lock()
        self._store: dict[str, tuple[bytes, EdhObjectMetadata]] = {}
        self.healthy = True

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    def _check_namespace(self, ref: EdhObjectRef) -> str:
        if ref.namespace != self._config.namespace:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                MemoryErrorConstants.MSG_NAMESPACE_MISMATCH.format(
                    expected=self._config.namespace,
                    actual=ref.namespace,
                ),
                provider=self.STRATEGY_NAME,
                operation="check_namespace",
                error_code=MemoryErrorConstants.ERR_NAMESPACE_MISMATCH,
                public_message=MemoryErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        return ref.qualified_key

    def put_bytes(
        self,
        ref: EdhObjectRef,
        data: bytes,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        opts = options or EdhWriteOptions()
        payload = data
        content_type = opts.content_type
        if opts.compress_gzip and not content_type.endswith("+gzip"):
            import gzip
            payload = gzip.compress(data)
            content_type = f"{content_type}+gzip"

        digest = compute_sha256(payload)
        if opts.expected_sha256 and opts.expected_sha256.lower() != digest:
            raise log_and_build_edh_error(
                logger,
                EdhIntegrityError,
                MemoryErrorConstants.MSG_SHA256_MISMATCH_WRITE.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=MemoryErrorConstants.ERR_SHA256_MISMATCH_WRITE,
                public_message=MemoryErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )

        qkey = self._check_namespace(ref)
        with self._lock:
            if not opts.overwrite and qkey in self._store:
                raise log_and_build_edh_error(
                    logger,
                    EdhConflictError,
                    MemoryErrorConstants.MSG_CONFLICT.format(uri=ref.uri),
                    provider=self.STRATEGY_NAME,
                    operation="put_bytes",
                    error_code=MemoryErrorConstants.ERR_CONFLICT,
                    public_message=MemoryErrorConstants.PUBLIC_CONFLICT,
                    level=logging.WARNING,
                )
            version = opts.idempotency_key or ref.version or digest[:16]
            meta = EdhObjectMetadata(
                ref=EdhObjectRef(
                    namespace=ref.namespace,
                    key=ref.key,
                    zone=ref.zone,
                    version=version,
                ),
                size_bytes=len(payload),
                content_type=content_type,
                sha256=digest,
                etag=f'"{digest[:32]}"',
                updated_at=datetime.now(timezone.utc),
                custom_metadata=dict(opts.custom_metadata),
            )
            self._store[qkey] = (payload, meta)
            return meta

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        qkey = self._check_namespace(ref)
        with self._lock:
            entry = self._store.get(qkey)
            if entry is None:
                raise log_and_build_edh_error(
                    logger,
                    EdhNotFoundError,
                    MemoryErrorConstants.MSG_NOT_FOUND.format(uri=ref.uri),
                    provider=self.STRATEGY_NAME,
                    operation="get_bytes",
                    error_code=MemoryErrorConstants.ERR_NOT_FOUND,
                    public_message=MemoryErrorConstants.PUBLIC_NOT_FOUND,
                    level=logging.WARNING,
                )
            return entry[0]

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                MemoryErrorConstants.MSG_INVALID_CHUNK_SIZE.format(chunk_size=chunk_size),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=MemoryErrorConstants.ERR_INVALID_CHUNK_SIZE,
                public_message=MemoryErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        payload = self.get_bytes(ref)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        qkey = self._check_namespace(ref)
        with self._lock:
            entry = self._store.get(qkey)
            if entry is None:
                raise log_and_build_edh_error(
                    logger,
                    EdhNotFoundError,
                    MemoryErrorConstants.MSG_NOT_FOUND.format(uri=ref.uri),
                    provider=self.STRATEGY_NAME,
                    operation="head",
                    error_code=MemoryErrorConstants.ERR_NOT_FOUND,
                    public_message=MemoryErrorConstants.PUBLIC_NOT_FOUND,
                    level=logging.WARNING,
                )
            return entry[1]

    def delete(self, ref: EdhObjectRef) -> bool:
        qkey = self._check_namespace(ref)
        with self._lock:
            return self._store.pop(qkey, None) is not None

    def list_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        if page_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                MemoryErrorConstants.MSG_INVALID_PAGE_SIZE.format(page_size=page_size),
                provider=self.STRATEGY_NAME,
                operation="list_objects",
                error_code=MemoryErrorConstants.ERR_INVALID_PAGE_SIZE,
                public_message=MemoryErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        clean_prefix = normalize_object_key(prefix) if prefix.strip() else ""
        zone_prefix = f"{zone.value}/"
        full_prefix = f"{zone_prefix}{clean_prefix}" if clean_prefix else zone_prefix

        with self._lock:
            matched = [
                meta
                for qkey, (_, meta) in sorted(self._store.items())
                if qkey.startswith(full_prefix)
            ]

        start = int(page_token) if page_token and page_token.isdigit() else 0
        slice_items = matched[start : start + page_size]
        next_token = str(start + page_size) if (start + page_size) < len(matched) else None
        return EdhListPage(items=tuple(slice_items), next_page_token=next_token)

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        opts = options or EdhWriteOptions()
        payload, content_type = serialize_records(
            batch.records,
            batch.record_format,
            compress_gzip=opts.compress_gzip,
        )
        digest = compute_sha256(payload)
        part_segments = [f"{k}={v}" for k, v in batch.partition_values.items()]
        ext = "jsonl" if batch.record_format == RecordFormat.JSONL else "csv"
        if opts.compress_gzip:
            ext = f"{ext}.gz"
        part_id = (opts.idempotency_key or digest)[:16]
        key = "/".join([normalize_object_key(batch.dataset), *part_segments, f"part-{part_id}.{ext}"])
        ref = EdhObjectRef(namespace=self._config.namespace, key=key, zone=batch.zone)
        return self.put_bytes(
            ref,
            payload,
            options=EdhWriteOptions(
                content_type=content_type,
                custom_metadata={
                    **opts.custom_metadata,
                    "dataset": batch.dataset,
                    "record_count": str(len(batch.records)),
                },
                overwrite=opts.overwrite,
                expected_sha256=digest,
                idempotency_key=opts.idempotency_key,
            ),
        )

    def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> Iterator[dict[str, Any]]:
        clean_dataset = normalize_object_key(dataset)
        filters = dict(partition_filter or {})
        page = self.list_objects(zone=zone, prefix=clean_dataset, page_size=10_000)
        for meta in page.items:
            segments = set(meta.ref.key.split("/"))
            if any(f"{k}={v}" not in segments for k, v in filters.items()):
                continue
            payload = self.get_bytes(meta.ref)
            yield from deserialize_records(payload, record_format)

    def ping(self) -> bool:
        return self.healthy

    def close(self) -> None:
        return None
