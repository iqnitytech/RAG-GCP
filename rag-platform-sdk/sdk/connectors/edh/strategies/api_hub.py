"""API-Driven Data Hub Strategy built purely with Python standard library modules.

Connects to REST/JSON & Binary Enterprise Data Hub gateways using ``urllib.request``,
``json``, ``hashlib``, and ``sdk.logging.get_request_id`` for automatic
``X-Request-ID`` trace correlation, OAuth2 Bearer / API-key injection, and
idempotency headers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from urllib.parse import quote, urlencode

from sdk.connectors.edh.errors import EdhConfigError, EdhIntegrityError, EdhNotFoundError
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
    raise_for_status,
    urllib_transport,
)
from sdk.connectors.edh.strategies._serde import deserialize_records, serialize_records
from sdk.logging.context import get_request_id

_META_HEADER_PREFIX = "x-edh-meta-"


class ApiDrivenEdhStrategy:
    """REST / API-Driven Enterprise Data Hub strategy using Python core libraries."""

    STRATEGY_NAME = "api_hub"

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        transport: TransportCallable | None = None,
    ) -> None:
        if not config.endpoint_url or not config.endpoint_url.strip():
            raise EdhConfigError("ApiDrivenEdhStrategy requires EdhConnectorConfig.endpoint_url")
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

    def _validate_namespace(self, ref: EdhObjectRef) -> None:
        if ref.namespace != self._config.namespace:
            raise EdhConfigError(
                f"Namespace mismatch: connector bound to {self._config.namespace!r}, got {ref.namespace!r}",
                provider=self.STRATEGY_NAME,
                operation="validate_namespace",
            )

    def _object_url(self, ref: EdhObjectRef) -> str:
        self._validate_namespace(ref)
        ns = quote(ref.namespace, safe="-_.")
        zone = quote(ref.zone.value, safe="-_.")
        key = quote(ref.key, safe="/-_.~")
        return f"{self._endpoint}/v1/namespaces/{ns}/zones/{zone}/objects/{key}"

    def _list_url(self, zone: DataZone, query: Mapping[str, str]) -> str:
        ns = quote(self._config.namespace, safe="-_.")
        z = quote(zone.value, safe="-_.")
        return f"{self._endpoint}/v1/namespaces/{ns}/zones/{z}/objects?{urlencode(dict(sorted(query.items())))}"

    def _records_url(
        self,
        dataset: str,
        zone: DataZone,
        query: Mapping[str, str] | None = None,
    ) -> str:
        ns = quote(self._config.namespace, safe="-_.")
        z = quote(zone.value, safe="-_.")
        ds = quote(normalize_object_key(dataset), safe="/-_.~")
        base = f"{self._endpoint}/v1/namespaces/{ns}/zones/{z}/datasets/{ds}/records"
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
        req_headers: dict[str, str] = {
            **self._config.default_headers,
            **(headers or {}),
        }
        request_id = get_request_id()
        if request_id and "X-Request-ID" not in req_headers:
            req_headers["X-Request-ID"] = request_id

        if self._config.auth_token_getter:
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
        raise_for_status(resp, provider=self.STRATEGY_NAME, operation=operation)
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
            raise EdhIntegrityError(
                f"SHA-256 mismatch on put_bytes for {ref.uri}: expected {opts.expected_sha256}, got {digest}",
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
            )

        headers: dict[str, str] = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
            "X-EDH-SHA256": digest,
            "X-EDH-Overwrite": "true" if opts.overwrite else "false",
        }
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

        version = opts.idempotency_key or ref.version or digest[:16]
        etag = resp.header("etag", f'"{digest[:32]}"')
        if resp.body and resp.header("content-type", "").startswith("application/json"):
            doc = json.loads(resp.body.decode("utf-8"))
            version = doc.get("version") or version
            etag = doc.get("etag") or etag

        return EdhObjectMetadata(
            ref=EdhObjectRef(namespace=ref.namespace, key=ref.key, zone=ref.zone, version=version),
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=etag,
            updated_at=datetime.now(timezone.utc),
            custom_metadata=dict(opts.custom_metadata),
        )

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        resp = self._request("GET", self._object_url(ref), operation="get_bytes")
        expected_sha = resp.header("x-edh-sha256")
        if expected_sha and compute_sha256(resp.body) != expected_sha.lower():
            raise EdhIntegrityError(
                f"API Hub payload SHA-256 mismatch for {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
            )
        return resp.body

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise EdhConfigError("chunk_size must be > 0")
        payload = self.get_bytes(ref)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        resp = self._request("HEAD", self._object_url(ref), operation="head")
        size_raw = resp.header("content-length", "0")
        content_type = resp.header("content-type", "application/octet-stream")
        sha256 = resp.header("x-edh-sha256", "")
        etag = resp.header("etag", "")
        version = resp.header("x-edh-version") or ref.version

        custom: dict[str, str] = {}
        for k, v in resp.headers.items():
            lower_k = k.lower()
            if lower_k.startswith(_META_HEADER_PREFIX):
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
            raise EdhConfigError("page_size must be > 0")
        clean_prefix = normalize_object_key(prefix) if prefix.strip() else ""
        query: dict[str, str] = {"page_size": str(page_size)}
        if clean_prefix:
            query["prefix"] = clean_prefix
        if page_token:
            query["page_token"] = page_token

        resp = self._request("GET", self._list_url(zone, query), operation="list_objects")
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        items: list[EdhObjectMetadata] = []
        for entry in doc.get("items", ()):
            ref = EdhObjectRef(
                namespace=self._config.namespace,
                key=entry["key"],
                zone=zone,
                version=entry.get("version"),
            )
            items.append(
                EdhObjectMetadata(
                    ref=ref,
                    size_bytes=int(entry.get("size_bytes", 0)),
                    content_type=str(entry.get("content_type", "application/octet-stream")),
                    sha256=str(entry.get("sha256", "")),
                    etag=str(entry.get("etag", "")),
                    custom_metadata=dict(entry.get("custom_metadata") or {}),
                )
            )
        return EdhListPage(items=tuple(items), next_page_token=doc.get("next_page_token"))

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
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "X-EDH-SHA256": digest,
            "X-EDH-Schema-Version": batch.schema_version,
        }
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key

        query = {f"partition.{k}": v for k, v in batch.partition_values.items()}
        resp = self._request(
            "POST",
            self._records_url(batch.dataset, batch.zone, query),
            operation="write_records",
            headers=headers,
            body=payload,
        )
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        key = doc.get("key") or f"{normalize_object_key(batch.dataset)}/part-{digest[:16]}.{batch.record_format.value}"
        ref = EdhObjectRef(
            namespace=self._config.namespace,
            key=key,
            zone=batch.zone,
            version=doc.get("version") or opts.idempotency_key or digest[:16],
        )
        return EdhObjectMetadata(
            ref=ref,
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=doc.get("etag", f'"{digest[:32]}"'),
            updated_at=datetime.now(timezone.utc),
            custom_metadata={
                **opts.custom_metadata,
                "dataset": batch.dataset,
                "record_count": str(len(batch.records)),
            },
        )

    def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> Iterator[dict[str, Any]]:
        query: dict[str, str] = {"format": record_format.value}
        for k, v in (partition_filter or {}).items():
            query[f"partition.{k}"] = v
        resp = self._request(
            "GET",
            self._records_url(dataset, zone, query),
            operation="read_records",
        )
        yield from deserialize_records(resp.body, record_format)

    def ping(self) -> bool:
        try:
            self._request("GET", f"{self._endpoint}/health", operation="ping")
            return True
        except Exception:
            return False

    def close(self) -> None:
        return None
