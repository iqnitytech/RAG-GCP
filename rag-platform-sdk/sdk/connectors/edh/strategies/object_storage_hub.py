"""Object Storage Hub Strategy built purely with Python standard library modules.

Connects to S3/GCS/MinIO/Ceph-compatible Object Storage Hubs over HTTP/HTTPS
using ``urllib.request``, ``xml.etree.ElementTree``, ``hashlib``, and ``hmac``.
Supports Bearer/WIF tokens (for GCS XML API / Cloud Storage) as well as S3
Signature v4 signing (for S3/MinIO/Ceph hubs) and SHA-256 payload verification.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from urllib.parse import quote, urlencode

from sdk.connectors.edh.error_constants.ObjectStorageErrorConstants import ObjectStorageErrorConstants
from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhConfigError,
    EdhConflictError,
    EdhError,
    EdhIntegrityError,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhTransientError,
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
from sdk.connectors.edh.strategies._http import (
    RawHttpResponse,
    TransportCallable,
    sign_s3_v4_headers,
    urllib_transport,
)
from sdk.connectors.edh.strategies._serde import deserialize_records, serialize_records

logger = logging.getLogger("sdk.edh.object_storage_hub")

_META_HEADER_PREFIX = "x-amz-meta-"


class ObjectStorageHubStrategy:
    """S3/GCS-compatible Object Storage Hub strategy using Python core libraries."""

    STRATEGY_NAME = "object_storage_hub"

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        transport: TransportCallable | None = None,
    ) -> None:
        if not config.endpoint_url or not config.endpoint_url.strip():
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                ObjectStorageErrorConstants.MSG_MISSING_ENDPOINT,
                provider=ObjectStorageErrorConstants.PROVIDER,
                operation="init",
                error_code=ObjectStorageErrorConstants.ERR_MISSING_ENDPOINT,
                public_message=ObjectStorageErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        self._config = config
        self._endpoint = config.endpoint_url.rstrip("/")
        self._transport: TransportCallable = transport or (
            lambda method, url, headers, body, timeout: urllib_transport(
                method,
                url,
                headers,
                body,
                timeout,
                verify_tls=config.verify_tls,
            )
        )

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    def _object_url(self, ref: EdhObjectRef) -> str:
        if ref.namespace != self._config.namespace:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                ObjectStorageErrorConstants.MSG_NAMESPACE_MISMATCH.format(
                    expected=self._config.namespace,
                    actual=ref.namespace,
                ),
                provider=self.STRATEGY_NAME,
                operation="build_url",
                error_code=ObjectStorageErrorConstants.ERR_NAMESPACE_MISMATCH,
                public_message=ObjectStorageErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        bucket = quote(ref.namespace, safe="-_.")
        key_path = quote(ref.qualified_key, safe="/-_.~")
        return f"{self._endpoint}/{bucket}/{key_path}"

    def _check_response(self, resp: RawHttpResponse, *, operation: str) -> None:
        code = resp.status_code
        if 200 <= code < 300:
            return
        raw = resp.body.decode("utf-8", errors="replace").strip() if resp.body else ""
        s3_code = f"HTTP_{code}"
        detail = raw[:300] or "Object Storage Hub request failed"
        if raw.startswith("<"):
            try:
                root = ET.fromstring(resp.body)  # noqa: S314
                for elem in root.iter():
                    tag = elem.tag.split("}", 1)[-1]
                    if tag == "Code" and elem.text:
                        s3_code = elem.text.strip()
                    elif tag == "Message" and elem.text:
                        detail = elem.text.strip()
            except Exception:
                pass
        formatted = ObjectStorageErrorConstants.MSG_HTTP_ERROR.format(
            operation=operation,
            status_code=code,
            s3_code=s3_code,
            detail=detail,
        )
        if code in (401, 403):
            raise log_and_build_edh_error(
                logger,
                EdhAuthError,
                formatted,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=ObjectStorageErrorConstants.ERR_AUTH_FAILED,
                public_message=ObjectStorageErrorConstants.PUBLIC_AUTH_ERROR,
                upstream_status=code,
            )
        if code == 404:
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                formatted,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=ObjectStorageErrorConstants.ERR_NOT_FOUND,
                public_message=ObjectStorageErrorConstants.PUBLIC_NOT_FOUND,
                upstream_status=code,
                level=logging.WARNING,
            )
        if code in (409, 412):
            raise log_and_build_edh_error(
                logger,
                EdhConflictError,
                formatted,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=ObjectStorageErrorConstants.ERR_CONFLICT,
                public_message=ObjectStorageErrorConstants.PUBLIC_CONFLICT,
                upstream_status=code,
                level=logging.WARNING,
            )
        if code == 429 or s3_code == "SlowDown":
            retry_after_raw = resp.header("retry-after")
            retry_after = float(retry_after_raw) if retry_after_raw.replace(".", "", 1).isdigit() else None
            raise log_and_build_edh_error(
                logger,
                EdhRateLimitError,
                formatted,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=ObjectStorageErrorConstants.ERR_RATE_LIMIT_EXCEEDED,
                public_message=ObjectStorageErrorConstants.PUBLIC_RATE_LIMIT,
                upstream_status=code,
                retry_after_seconds=retry_after,
                level=logging.WARNING,
            )
        if code in (408, 425) or 500 <= code < 600:
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                formatted,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=ObjectStorageErrorConstants.ERR_TRANSIENT_UPSTREAM,
                public_message=ObjectStorageErrorConstants.PUBLIC_TRANSIENT_ERROR,
                upstream_status=code,
            )
        raise log_and_build_edh_error(
            logger,
            EdhError,
            formatted,
            provider=self.STRATEGY_NAME,
            operation=operation,
            error_code=ObjectStorageErrorConstants.ERR_UNEXPECTED_RESPONSE,
            public_message=ObjectStorageErrorConstants.PUBLIC_UNEXPECTED_ERROR,
            upstream_status=code,
        )

    def _bucket_url(self, query: Mapping[str, str] | None = None) -> str:
        bucket = quote(self._config.namespace, safe="-_.")
        base = f"{self._endpoint}/{bucket}"
        if query:
            return f"{base}?{urlencode(dict(sorted(query.items())))}"
        return base

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> RawHttpResponse:
        payload_bytes = body or b""
        payload_sha256 = compute_sha256(payload_bytes)

        req_headers: dict[str, str] = {
            **self._config.default_headers,
            **(headers or {}),
            "x-edh-content-sha256": payload_sha256,
        }

        if self._config.access_key_getter and self._config.secret_key_getter:
            access_key = self._config.access_key_getter()
            secret_key = self._config.secret_key_getter()
            if access_key and secret_key:
                req_headers = sign_s3_v4_headers(
                    method=method,
                    url=url,
                    headers=req_headers,
                    payload_sha256=payload_sha256,
                    access_key=access_key,
                    secret_key=secret_key,
                    region=self._config.region,
                )
        elif self._config.auth_token_getter:
            token = self._config.auth_token_getter()
            if token:
                req_headers["Authorization"] = f"Bearer {token}"

        if self._config.api_key_getter:
            api_key = self._config.api_key_getter()
            if api_key:
                req_headers[self._config.api_key_header] = api_key

        resp = self._transport(
            method,
            url,
            req_headers,
            body,
            self._config.timeout_seconds,
        )
        self._check_response(resp, operation=operation)
        return resp

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
                ObjectStorageErrorConstants.MSG_SHA256_MISMATCH_WRITE.format(
                    uri=ref.uri,
                    expected=opts.expected_sha256,
                    actual=digest,
                ),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=ObjectStorageErrorConstants.ERR_SHA256_MISMATCH_WRITE,
                public_message=ObjectStorageErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )

        headers: dict[str, str] = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
            f"{_META_HEADER_PREFIX}sha256": digest,
        }
        if not opts.overwrite:
            headers["If-None-Match"] = "*"
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key
        for k, v in opts.custom_metadata.items():
            headers[f"{_META_HEADER_PREFIX}{k.lower()}"] = str(v)

        resp = self._request(
            "PUT",
            self._object_url(ref),
            operation="put_bytes",
            headers=headers,
            body=payload,
        )
        etag = resp.header("etag", f'"{digest[:32]}"')
        version = resp.header("x-amz-version-id") or opts.idempotency_key or ref.version
        return EdhObjectMetadata(
            ref=EdhObjectRef(
                namespace=ref.namespace,
                key=ref.key,
                zone=ref.zone,
                version=version,
            ),
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=etag,
            updated_at=datetime.now(timezone.utc),
            custom_metadata=dict(opts.custom_metadata),
        )

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        resp = self._request("GET", self._object_url(ref), operation="get_bytes")
        expected_sha = resp.header(f"{_META_HEADER_PREFIX}sha256")
        if expected_sha and compute_sha256(resp.body) != expected_sha.lower():
            raise log_and_build_edh_error(
                logger,
                EdhIntegrityError,
                ObjectStorageErrorConstants.MSG_SHA256_MISMATCH_READ.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
                error_code=ObjectStorageErrorConstants.ERR_SHA256_MISMATCH_READ,
                public_message=ObjectStorageErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )
        return resp.body

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                ObjectStorageErrorConstants.MSG_INVALID_CHUNK_SIZE.format(chunk_size=chunk_size),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=ObjectStorageErrorConstants.ERR_INVALID_CHUNK_SIZE,
                public_message=ObjectStorageErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        payload = self.get_bytes(ref)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        resp = self._request("HEAD", self._object_url(ref), operation="head")
        size_raw = resp.header("content-length", "0")
        content_type = resp.header("content-type", "application/octet-stream")
        sha256 = resp.header(f"{_META_HEADER_PREFIX}sha256", "")
        etag = resp.header("etag", "")
        version = resp.header("x-amz-version-id") or ref.version

        custom: dict[str, str] = {}
        for k, v in resp.headers.items():
            lower_k = k.lower()
            if lower_k.startswith(_META_HEADER_PREFIX) and lower_k != f"{_META_HEADER_PREFIX}sha256":
                custom[lower_k[len(_META_HEADER_PREFIX) :]] = v

        return EdhObjectMetadata(
            ref=EdhObjectRef(namespace=ref.namespace, key=ref.key, zone=ref.zone, version=version),
            size_bytes=int(size_raw) if size_raw.isdigit() else 0,
            content_type=content_type,
            sha256=sha256,
            etag=etag,
            updated_at=datetime.now(timezone.utc),
            custom_metadata=custom,
        )

    def delete(self, ref: EdhObjectRef) -> bool:
        from sdk.connectors.edh.errors import EdhNotFoundError

        try:
            self._request("DELETE", self._object_url(ref), operation="delete")
            return True
        except EdhNotFoundError:
            return False

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
                ObjectStorageErrorConstants.MSG_INVALID_PAGE_SIZE.format(page_size=page_size),
                provider=self.STRATEGY_NAME,
                operation="list_objects",
                error_code=ObjectStorageErrorConstants.ERR_INVALID_PAGE_SIZE,
                public_message=ObjectStorageErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        clean_prefix = normalize_object_key(prefix) if prefix.strip() else ""
        full_prefix = f"{zone.value}/{clean_prefix}" if clean_prefix else f"{zone.value}/"

        query: dict[str, str] = {
            "list-type": "2",
            "prefix": full_prefix,
            "max-keys": str(page_size),
        }
        if page_token:
            query["continuation-token"] = page_token

        resp = self._request("GET", self._bucket_url(query), operation="list_objects")
        return self._parse_list_xml(resp.body, zone=zone)

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
        token: str | None = None

        while True:
            page = self.list_objects(zone=zone, prefix=clean_dataset, page_size=100, page_token=token)
            for item in page.items:
                segments = set(item.ref.key.split("/"))
                if any(f"{k}={v}" not in segments for k, v in filters.items()):
                    continue
                payload = self.get_bytes(item.ref)
                yield from deserialize_records(payload, record_format)
            if not page.next_page_token:
                break
            token = page.next_page_token

    def ping(self) -> bool:
        try:
            self._request("HEAD", self._bucket_url(), operation="ping")
            return True
        except Exception:
            return False

    def close(self) -> None:
        return None

    def _parse_list_xml(self, xml_bytes: bytes, *, zone: DataZone) -> EdhListPage:
        if not xml_bytes.strip():
            return EdhListPage(items=(), next_page_token=None)

        root = ET.fromstring(xml_bytes)  # noqa: S314 (trusted internal/hub XML response)
        items: list[EdhObjectMetadata] = []
        zone_prefix = f"{zone.value}/"

        for elem in root.iter():
            tag = elem.tag.split("}", 1)[-1]
            if tag == "Contents":
                raw_key = ""
                size_bytes = 0
                etag = ""
                for child in elem:
                    ctag = child.tag.split("}", 1)[-1]
                    if ctag == "Key" and child.text:
                        raw_key = child.text
                    elif ctag == "Size" and child.text and child.text.isdigit():
                        size_bytes = int(child.text)
                    elif ctag == "ETag" and child.text:
                        etag = child.text
                if not raw_key:
                    continue
                logical_key = (
                    raw_key[len(zone_prefix) :]
                    if raw_key.startswith(zone_prefix)
                    else raw_key
                )
                ref = EdhObjectRef(namespace=self._config.namespace, key=logical_key, zone=zone)
                items.append(
                    EdhObjectMetadata(
                        ref=ref,
                        size_bytes=size_bytes,
                        etag=etag,
                    )
                )

        next_token: str | None = None
        for elem in root.iter():
            tag = elem.tag.split("}", 1)[-1]
            if tag == "NextContinuationToken" and elem.text:
                next_token = elem.text
                break

        return EdhListPage(items=tuple(items), next_page_token=next_token)
