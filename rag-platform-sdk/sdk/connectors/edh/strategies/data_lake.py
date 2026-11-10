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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from sdk.connectors.edh.errors import (
    EdhConfigError,
    EdhConflictError,
    EdhIntegrityError,
    EdhNotFoundError,
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


class DataLakeStrategy:
    """Zone- and Hive-partition-aware Data Lake strategy using Python core libraries."""

    STRATEGY_NAME = "data_lake"

    def __init__(self, config: EdhConnectorConfig) -> None:
        if not config.root_path or not config.root_path.strip():
            raise EdhConfigError("DataLakeStrategy requires EdhConnectorConfig.root_path")
        self._config = config
        self._root = Path(config.root_path).resolve() / normalize_object_key(config.namespace)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    def _resolve_paths(self, ref: EdhObjectRef) -> tuple[Path, Path]:
        if ref.namespace != self._config.namespace:
            raise EdhConfigError(
                f"Namespace mismatch: connector bound to {self._config.namespace!r}, got {ref.namespace!r}",
                provider=self.STRATEGY_NAME,
                operation="resolve_path",
            )
        rel_path = normalize_object_key(ref.qualified_key)
        target = (self._root / rel_path).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise EdhConfigError(
                f"Resolved path escapes data lake root: {ref.key!r}",
                provider=self.STRATEGY_NAME,
                operation="resolve_path",
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
            raise EdhIntegrityError(
                f"SHA-256 mismatch on put_bytes for {ref.uri}: expected {opts.expected_sha256}, got {digest}",
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
            )

        target, meta_path = self._resolve_paths(ref)
        if not opts.overwrite and target.exists():
            raise EdhConflictError(
                f"Object already exists and overwrite=False: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
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
            raise EdhNotFoundError(
                f"Data lake object not found: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
            )
        data = target.read_bytes()
        if meta_path.is_file():
            meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_sha = meta_doc.get("sha256", "")
            if expected_sha and compute_sha256(data) != expected_sha:
                raise EdhIntegrityError(
                    f"Stored object failed SHA-256 verification: {ref.uri}",
                    provider=self.STRATEGY_NAME,
                    operation="get_bytes",
                )
        return data

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise EdhConfigError("chunk_size must be > 0")
        target, meta_path = self._resolve_paths(ref)
        if not target.is_file():
            raise EdhNotFoundError(
                f"Data lake object not found: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
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
            raise EdhIntegrityError(
                f"Streamed object failed SHA-256 verification: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
            )

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        target, meta_path = self._resolve_paths(ref)
        if not target.is_file():
            raise EdhNotFoundError(
                f"Data lake object not found: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="head",
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
            raise EdhConfigError("page_size must be > 0")
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
