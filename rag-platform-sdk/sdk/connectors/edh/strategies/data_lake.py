"""Data Lake Strategy built purely with Python standard library modules.

Organizes datasets into Medallion zones (``landing``, ``quarantine``, ``raw``,
``curated``, ``gold``) with Hive-style partitioned directories
(``<zone>/<dataset>/col1=val1/col2=val2/part-<hash>.jsonl[.gz]``), atomic
temp-file + ``os.replace`` commits, sidecar metadata manifests, and SHA-256
integrity verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from sdk.connectors.edh.error_constants.DataLakeErrorConstants import DataLakeErrorConstants
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

logger = logging.getLogger("sdk.edh.data_lake")


class DataLakeStrategy:
    """Zone- and Hive-partition-aware Data Lake strategy using Python core libraries."""

    STRATEGY_NAME = "data_lake"

    def __init__(self, config: EdhConnectorConfig) -> None:
        if not config.root_path or not config.root_path.strip():
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                DataLakeErrorConstants.MSG_MISSING_ROOT_PATH,
                provider=DataLakeErrorConstants.PROVIDER,
                operation="init",
                error_code=DataLakeErrorConstants.ERR_MISSING_ROOT_PATH,
                public_message=DataLakeErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        self._config = config
        self._root = Path(config.root_path).resolve() / normalize_object_key(config.namespace)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    def _resolve_paths(self, ref: EdhObjectRef) -> tuple[Path, Path]:
        if ref.namespace != self._config.namespace:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                DataLakeErrorConstants.MSG_NAMESPACE_MISMATCH.format(
                    expected=self._config.namespace,
                    actual=ref.namespace,
                ),
                provider=self.STRATEGY_NAME,
                operation="resolve_path",
                error_code=DataLakeErrorConstants.ERR_NAMESPACE_MISMATCH,
                public_message=DataLakeErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        rel_path = normalize_object_key(ref.qualified_key)
        target = (self._root / rel_path).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                DataLakeErrorConstants.MSG_PATH_ESCAPE.format(key=ref.key),
                provider=self.STRATEGY_NAME,
                operation="resolve_path",
                error_code=DataLakeErrorConstants.ERR_PATH_ESCAPE,
                public_message=DataLakeErrorConstants.PUBLIC_CONFIG_ERROR,
            ) from exc
        meta_path = target.with_name(f".{target.name}.edhmeta.json")
        return target, meta_path

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
                DataLakeErrorConstants.MSG_SHA256_MISMATCH_WRITE.format(
                    uri=ref.uri,
                    expected=opts.expected_sha256,
                    actual=digest,
                ),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=DataLakeErrorConstants.ERR_SHA256_MISMATCH_WRITE,
                public_message=DataLakeErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )

        target, meta_path = self._resolve_paths(ref)
        if not opts.overwrite and target.exists():
            raise log_and_build_edh_error(
                logger,
                EdhConflictError,
                DataLakeErrorConstants.MSG_CONFLICT.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=DataLakeErrorConstants.ERR_CONFLICT,
                public_message=DataLakeErrorConstants.PUBLIC_CONFLICT,
                level=logging.WARNING,
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        short_id = uuid4().hex[:8]
        tmp_data = target.with_name(f".{target.name}.{short_id}.tmp")
        tmp_meta = meta_path.with_name(f"{meta_path.name}.{short_id}.tmp")

        now = datetime.now(timezone.utc)
        meta_doc = {
            "namespace": ref.namespace,
            "key": ref.key,
            "zone": ref.zone.value,
            "version": opts.idempotency_key or ref.version or digest[:16],
            "size_bytes": len(payload),
            "content_type": content_type,
            "sha256": digest,
            "etag": f'"{digest[:32]}"',
            "updated_at": now.isoformat(),
            "custom_metadata": dict(opts.custom_metadata),
        }

        try:
            tmp_data.write_bytes(payload)
            tmp_meta.write_text(json.dumps(meta_doc, sort_keys=True), encoding="utf-8")
            os.replace(tmp_data, target)
            os.replace(tmp_meta, meta_path)
        finally:
            if tmp_data.exists():
                tmp_data.unlink(missing_ok=True)
            if tmp_meta.exists():
                tmp_meta.unlink(missing_ok=True)

        return self._metadata_from_doc(ref, meta_doc)

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        target, meta_path = self._resolve_paths(ref)
        if not target.is_file():
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                DataLakeErrorConstants.MSG_NOT_FOUND.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
                error_code=DataLakeErrorConstants.ERR_NOT_FOUND,
                public_message=DataLakeErrorConstants.PUBLIC_NOT_FOUND,
                level=logging.WARNING,
            )
        data = target.read_bytes()
        if meta_path.is_file():
            meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_sha = meta_doc.get("sha256", "")
            if expected_sha and compute_sha256(data) != expected_sha:
                raise log_and_build_edh_error(
                    logger,
                    EdhIntegrityError,
                    DataLakeErrorConstants.MSG_SHA256_MISMATCH_READ.format(uri=ref.uri),
                    provider=self.STRATEGY_NAME,
                    operation="get_bytes",
                    error_code=DataLakeErrorConstants.ERR_SHA256_MISMATCH_READ,
                    public_message=DataLakeErrorConstants.PUBLIC_INTEGRITY_ERROR,
                )
        return data

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                DataLakeErrorConstants.MSG_INVALID_CHUNK_SIZE.format(chunk_size=chunk_size),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=DataLakeErrorConstants.ERR_INVALID_CHUNK_SIZE,
                public_message=DataLakeErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        target, meta_path = self._resolve_paths(ref)
        if not target.is_file():
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                DataLakeErrorConstants.MSG_NOT_FOUND.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=DataLakeErrorConstants.ERR_NOT_FOUND,
                public_message=DataLakeErrorConstants.PUBLIC_NOT_FOUND,
                level=logging.WARNING,
            )
        expected_sha = ""
        if meta_path.is_file():
            expected_sha = json.loads(meta_path.read_text(encoding="utf-8")).get("sha256", "")

        hasher = hashlib.sha256()
        with target.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                yield chunk

        if expected_sha and hasher.hexdigest() != expected_sha:
            raise log_and_build_edh_error(
                logger,
                EdhIntegrityError,
                DataLakeErrorConstants.MSG_SHA256_MISMATCH_STREAM.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=DataLakeErrorConstants.ERR_SHA256_MISMATCH_STREAM,
                public_message=DataLakeErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        target, meta_path = self._resolve_paths(ref)
        if not target.is_file():
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                DataLakeErrorConstants.MSG_NOT_FOUND.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="head",
                error_code=DataLakeErrorConstants.ERR_NOT_FOUND,
                public_message=DataLakeErrorConstants.PUBLIC_NOT_FOUND,
                level=logging.WARNING,
            )
        if meta_path.is_file():
            meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
            return self._metadata_from_doc(ref, meta_doc)

        stat = target.stat()
        digest = compute_sha256(target.read_bytes())
        return EdhObjectMetadata(
            ref=ref,
            size_bytes=stat.st_size,
            sha256=digest,
            etag=f'"{digest[:32]}"',
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def delete(self, ref: EdhObjectRef) -> bool:
        target, meta_path = self._resolve_paths(ref)
        existed = target.is_file()
        if existed:
            target.unlink(missing_ok=True)
        if meta_path.is_file():
            meta_path.unlink(missing_ok=True)
        return existed

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
                DataLakeErrorConstants.MSG_INVALID_PAGE_SIZE.format(page_size=page_size),
                provider=self.STRATEGY_NAME,
                operation="list_objects",
                error_code=DataLakeErrorConstants.ERR_INVALID_PAGE_SIZE,
                public_message=DataLakeErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        zone_dir = self._root / zone.value
        if not zone_dir.is_dir():
            return EdhListPage(items=(), next_page_token=None)

        clean_prefix = normalize_object_key(prefix) if prefix.strip() else ""
        matched: list[EdhObjectMetadata] = []

        for file_path in sorted(zone_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.name.startswith(".") and (
                file_path.name.endswith(".edhmeta.json") or file_path.name.endswith(".tmp")
            ):
                continue
            rel_to_zone = file_path.relative_to(zone_dir).as_posix()
            if clean_prefix and not rel_to_zone.startswith(clean_prefix):
                continue
            ref = EdhObjectRef(namespace=self._config.namespace, key=rel_to_zone, zone=zone)
            matched.append(self.head(ref))

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
        filename = f"part-{part_id}.{ext}"

        key_parts = [normalize_object_key(batch.dataset), *part_segments, filename]
        key = "/".join(key_parts)
        ref = EdhObjectRef(namespace=self._config.namespace, key=key, zone=batch.zone)

        merged_meta = {
            **opts.custom_metadata,
            "dataset": batch.dataset,
            "record_count": str(len(batch.records)),
            "record_format": batch.record_format.value,
            "schema_version": batch.schema_version,
        }
        return self.put_bytes(
            ref,
            payload,
            options=EdhWriteOptions(
                content_type=content_type,
                custom_metadata=merged_meta,
                overwrite=opts.overwrite,
                expected_sha256=digest,
                idempotency_key=opts.idempotency_key,
                compress_gzip=False,  # already compressed if requested
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
        page_token: str | None = None

        while True:
            page = self.list_objects(
                zone=zone,
                prefix=clean_dataset,
                page_size=200,
                page_token=page_token,
            )
            for meta in page.items:
                if not self._matches_partitions(meta.ref.key, filters):
                    continue
                payload = self.get_bytes(meta.ref)
                yield from deserialize_records(payload, record_format)
            if not page.next_page_token:
                break
            page_token = page.next_page_token

    def ping(self) -> bool:
        return self._root.is_dir() and os.access(self._root, os.W_OK | os.R_OK)

    def close(self) -> None:
        return None

    @staticmethod
    def _matches_partitions(key: str, partition_filter: Mapping[str, str]) -> bool:
        if not partition_filter:
            return True
        segments = set(key.split("/"))
        for col, val in partition_filter.items():
            if f"{col}={val}" not in segments:
                return False
        return True

    @staticmethod
    def _metadata_from_doc(ref: EdhObjectRef, doc: Mapping[str, Any]) -> EdhObjectMetadata:
        versioned_ref = EdhObjectRef(
            namespace=ref.namespace,
            key=ref.key,
            zone=ref.zone,
            version=doc.get("version") or ref.version,
        )
        return EdhObjectMetadata(
            ref=versioned_ref,
            size_bytes=int(doc["size_bytes"]),
            content_type=str(doc.get("content_type", "application/octet-stream")),
            sha256=str(doc.get("sha256", "")),
            etag=str(doc.get("etag", "")),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
            custom_metadata=dict(doc.get("custom_metadata") or {}),
        )
