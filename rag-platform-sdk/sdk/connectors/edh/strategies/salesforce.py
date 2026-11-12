"""Salesforce EDH Strategy built purely with Python standard library modules.

Connects to Salesforce REST API / ContentVersion / SOQL / Composite sObjects
endpoints using ``urllib.request``, ``json``, ``base64``, and ``hashlib``:

- **Blob operations** (``put_bytes``, ``get_bytes``, ``stream_chunks``, ``head``,
  ``delete``, ``list_objects``) map to Salesforce **ContentVersion** documents
  (storing ``EdhObjectRef.qualified_key`` in ``PathOnClient``, custom metadata
  in ``Description`` JSON, and binary payload in ``VersionData``).
- **Record operations** (``write_records``, ``read_records``) map to Salesforce
  **Composite sObjects Collections** (``POST /services/data/<version>/composite/sobjects``)
  and **SOQL Query + nextRecordsUrl pagination** (``GET /services/data/<version>/query?q=...``)
  with strict identifier and value escaping to prevent SOQL injection.
- **OAuth2 helper** (:func:`make_salesforce_oauth_token_getter`) exchanges
  ``client_id`` + ``client_secret`` against ``/services/oauth2/token`` and caches
  the bearer token in memory until expiry.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence
from urllib.parse import quote, urlencode

from sdk.connectors.edh.error_constants import SalesforceErrorConstants
from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhConflictError,
    EdhError,
    EdhIntegrityError,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhTransientError,
    log_and_build_edh_error,
)
from sdk.connectors.edh.strategies._http import (
    HttpConnectionPool,
    RawHttpResponse,
    TransportCallable,
    urllib_transport,
)
from sdk.errors import EdhConfigError
from sdk.logging.context import get_request_id
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

logger = logging.getLogger("sdk.edh.salesforce")
DEFAULT_SALESFORCE_API_VERSION = "v60.0"
_SAFE_SOQL_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(__c|__kav|__r|__x)?$")


def _validate_soql_identifier(
    name: str,
    *,
    kind: str = "identifier",
    operation: str = "validate_soql_identifier",
) -> str:
    """Ensure SObject and field names are safe SOQL identifiers (no injection)."""
    candidate = name.strip().replace("/", "_")
    if not _SAFE_SOQL_IDENTIFIER.match(candidate):
        raise log_and_build_edh_error(
            logger,
            EdhConfigError,
            SalesforceErrorConstants.MSG_UNSAFE_IDENTIFIER.format(kind=kind, name=name),
            provider=SalesforceErrorConstants.PROVIDER,
            operation=operation,
            error_code=SalesforceErrorConstants.ERR_UNSAFE_IDENTIFIER,
            public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
        )
    return candidate


def _extract_salesforce_error_detail(resp: RawHttpResponse) -> tuple[str, str]:
    """Extract ``(sf_error_code, detail_message)`` from a Salesforce HTTP error payload."""
    if not resp.body:
        return (f"HTTP_{resp.status_code}", f"HTTP {resp.status_code}")
    raw_text = resp.body.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], Mapping):
            first = parsed[0]
            sf_code = str(first.get("errorCode") or f"HTTP_{resp.status_code}")
            msg = str(first.get("message") or raw_text)
            fields = first.get("fields")
            if isinstance(fields, list) and fields:
                msg = f"{msg} (fields: {', '.join(str(f) for f in fields)})"
            return (sf_code, msg)
        if isinstance(parsed, Mapping):
            sf_code = str(
                parsed.get("errorCode")
                or parsed.get("error")
                or f"HTTP_{resp.status_code}"
            )
            msg = str(
                parsed.get("message")
                or parsed.get("error_description")
                or raw_text
            )
            return (sf_code, msg)
    except Exception:
        pass
    return (f"HTTP_{resp.status_code}", raw_text[:300] or f"HTTP {resp.status_code}")


def _escape_soql_literal(value: str) -> str:
    """Escape a string literal for a SOQL ``WHERE field = '...'`` clause."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f"'{escaped}'"


def make_salesforce_oauth_token_getter(
    *,
    instance_url: str,
    client_id_getter: Callable[[], str],
    client_secret_getter: Callable[[], str],
    timeout_seconds: float = 10.0,
    verify_tls: bool = True,
    ttl_seconds: float = 1800.0,
    transport: TransportCallable | None = None,
) -> Callable[[], str]:
    """Build a thread-safe OAuth2 ``client_credentials`` token getter for Salesforce.

    Exchanges ``client_id`` and ``client_secret`` at
    ``<instance_url>/services/oauth2/token`` and caches the token for ``ttl_seconds``.
    """
    base = instance_url.rstrip("/")
    token_url = f"{base}/services/oauth2/token"
    http_call: TransportCallable = transport or (
        lambda method, url, headers, body, timeout: urllib_transport(
            method, url, headers, body, timeout, verify_tls=verify_tls
        )
    )
    lock = Lock()
    cached_token = ""
    expires_at = 0.0

    def get_token() -> str:
        nonlocal cached_token, expires_at
        now = time.monotonic()
        with lock:
            if cached_token and now < expires_at:
                return cached_token

            form_body = urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": client_id_getter(),
                    "client_secret": client_secret_getter(),
                }
            ).encode("utf-8")
            resp = http_call(
                "POST",
                token_url,
                {"Content-Type": "application/x-www-form-urlencoded"},
                form_body,
                timeout_seconds,
            )
            if resp.status_code != 200:
                _, detail = _extract_salesforce_error_detail(resp)
                raise log_and_build_edh_error(
                    logger,
                    EdhAuthError,
                    SalesforceErrorConstants.MSG_OAUTH_FAILED.format(
                        status_code=resp.status_code,
                        detail=detail,
                    ),
                    provider=SalesforceErrorConstants.PROVIDER,
                    operation="oauth2_token",
                    error_code=SalesforceErrorConstants.ERR_OAUTH_TOKEN_EXCHANGE,
                    public_message=SalesforceErrorConstants.PUBLIC_AUTH_ERROR,
                    upstream_status=resp.status_code,
                )
            doc = json.loads(resp.body.decode("utf-8"))
            token = doc.get("access_token", "")
            if not token:
                raise log_and_build_edh_error(
                    logger,
                    EdhAuthError,
                    SalesforceErrorConstants.MSG_OAUTH_MISSING_TOKEN,
                    provider=SalesforceErrorConstants.PROVIDER,
                    operation="oauth2_token",
                    error_code=SalesforceErrorConstants.ERR_OAUTH_MISSING_TOKEN,
                    public_message=SalesforceErrorConstants.PUBLIC_AUTH_ERROR,
                )
            cached_token = str(token)
            expires_at = now + ttl_seconds
            return cached_token

    return get_token


class SalesforceEdhStrategy:
    """Salesforce REST / SOQL / ContentVersion strategy using Python core libraries."""

    STRATEGY_NAME = "salesforce"

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        api_version: str = DEFAULT_SALESFORCE_API_VERSION,
        transport: TransportCallable | None = None,
    ) -> None:
        if not config.endpoint_url or not config.endpoint_url.strip():
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_MISSING_ENDPOINT,
                provider=SalesforceErrorConstants.PROVIDER,
                operation="init",
                error_code=SalesforceErrorConstants.ERR_MISSING_ENDPOINT,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        self._config = config
        self._instance_url = config.endpoint_url.rstrip("/")
        self._api_version = api_version.strip().lstrip("/")
        self._base_data_url = f"{self._instance_url}/services/data/{self._api_version}"
        self._connection_pool = HttpConnectionPool(verify_tls=config.verify_tls)
        self._transport: TransportCallable = transport or self._connection_pool.request

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    @property
    def connection_pool(self) -> HttpConnectionPool:
        return self._connection_pool

    @property
    def is_open(self) -> bool:
        return self._connection_pool.is_open

    def open(self) -> SalesforceEdhStrategy:
        """Open a persistent HTTP connection session."""
        self._connection_pool.open()
        return self

    async def aopen(self) -> SalesforceEdhStrategy:
        """Asynchronously open a persistent HTTP connection session."""
        return await asyncio.to_thread(self.open)

    async def aclose(self) -> None:
        """Asynchronously close all underlying HTTP connections."""
        await asyncio.to_thread(self.close)

    def __enter__(self) -> SalesforceEdhStrategy:
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    async def __aenter__(self) -> SalesforceEdhStrategy:
        return await self.aopen()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _validate_namespace(self, ref: EdhObjectRef) -> None:
        if ref.namespace != self._config.namespace:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_NAMESPACE_MISMATCH.format(
                    expected=self._config.namespace,
                    actual=ref.namespace,
                ),
                provider=self.STRATEGY_NAME,
                operation="validate_namespace",
                error_code=SalesforceErrorConstants.ERR_NAMESPACE_MISMATCH,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

    def _qualified_path(self, ref: EdhObjectRef) -> str:
        self._validate_namespace(ref)
        return f"{ref.namespace}/{ref.qualified_key}"

    def _check_response(self, resp: RawHttpResponse, *, operation: str) -> None:
        """Translate Salesforce HTTP responses and errorCode payloads into logged EdhErrors."""
        code = resp.status_code
        if 200 <= code < 300:
            return

        sf_code, detail = _extract_salesforce_error_detail(resp)
        msg = SalesforceErrorConstants.MSG_HTTP_ERROR.format(
            operation=operation,
            status_code=code,
            sf_code=sf_code,
            detail=detail,
        )

        if code in (401, 403) or sf_code in SalesforceErrorConstants.AUTH_ERROR_CODES:
            err_code = (
                SalesforceErrorConstants.ERR_INSUFFICIENT_ACCESS
                if code == 403 or "INSUFFICIENT_ACCESS" in sf_code
                else SalesforceErrorConstants.ERR_AUTH_FAILED
            )
            raise log_and_build_edh_error(
                logger,
                EdhAuthError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=err_code,
                public_message=SalesforceErrorConstants.PUBLIC_AUTH_ERROR,
                upstream_status=code,
            )
        if code == 404 or sf_code in SalesforceErrorConstants.NOT_FOUND_ERROR_CODES:
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=SalesforceErrorConstants.ERR_RECORD_NOT_FOUND,
                public_message=SalesforceErrorConstants.PUBLIC_NOT_FOUND,
                upstream_status=code,
                level=logging.WARNING,
            )
        if code in (409, 412) or sf_code in SalesforceErrorConstants.CONFLICT_ERROR_CODES:
            raise log_and_build_edh_error(
                logger,
                EdhConflictError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=SalesforceErrorConstants.ERR_DUPLICATE_RECORD,
                public_message=SalesforceErrorConstants.PUBLIC_CONFLICT,
                upstream_status=code,
            )
        if code == 429 or sf_code in SalesforceErrorConstants.RATE_LIMIT_ERROR_CODES:
            retry_raw = resp.header("retry-after")
            retry_after = float(retry_raw) if retry_raw.isdigit() else None
            raise log_and_build_edh_error(
                logger,
                EdhRateLimitError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=SalesforceErrorConstants.ERR_RATE_LIMIT_EXCEEDED,
                public_message=SalesforceErrorConstants.PUBLIC_RATE_LIMIT,
                upstream_status=code,
                retry_after_seconds=retry_after,
                level=logging.WARNING,
            )
        if code >= 500 or code == 408 or sf_code in SalesforceErrorConstants.TRANSIENT_ERROR_CODES:
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=SalesforceErrorConstants.ERR_TRANSIENT_UPSTREAM,
                public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
                upstream_status=code,
            )
        if sf_code in SalesforceErrorConstants.CONFIG_OR_VALIDATION_ERROR_CODES:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                msg,
                provider=self.STRATEGY_NAME,
                operation=operation,
                error_code=SalesforceErrorConstants.ERR_MALFORMED_REQUEST,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
                upstream_status=code,
            )
        raise log_and_build_edh_error(
            logger,
            EdhError,
            msg,
            provider=self.STRATEGY_NAME,
            operation=operation,
            error_code=SalesforceErrorConstants.ERR_UNEXPECTED_RESPONSE,
            public_message=SalesforceErrorConstants.PUBLIC_UNEXPECTED_ERROR,
            upstream_status=code,
        )

    def _request(
        self,
        method: str,
        url_or_path: str,
        *,
        operation: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> RawHttpResponse:
        url = (
            url_or_path
            if url_or_path.startswith("http://") or url_or_path.startswith("https://")
            else f"{self._instance_url}/{url_or_path.lstrip('/')}"
        )
        req_headers: dict[str, str] = {
            "Accept": "application/json",
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

        resp = self._transport(
            method,
            url,
            req_headers,
            body,
            self._config.timeout_seconds,
        )
        self._check_response(resp, operation=operation)
        return resp

    def _query_soql(self, soql: str, *, operation: str = "soql_query") -> dict[str, Any]:
        url = f"{self._base_data_url}/query?{urlencode({'q': soql})}"
        resp = self._request("GET", url, operation=operation)
        return json.loads(resp.body.decode("utf-8")) if resp.body else {"records": [], "done": True}

    def _find_content_version(self, ref: EdhObjectRef) -> dict[str, Any] | None:
        qpath = self._qualified_path(ref)
        soql = (
            "SELECT Id, Title, PathOnClient, ContentSize, Checksum, Description "
            f"FROM ContentVersion WHERE PathOnClient = {_escape_soql_literal(qpath)} "
            "AND IsLatest = true LIMIT 1"
        )
        doc = self._query_soql(soql, operation="find_content_version")
        records = doc.get("records") or []
        return records[0] if records else None

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
                SalesforceErrorConstants.MSG_SHA256_MISMATCH_WRITE.format(
                    uri=ref.uri,
                    expected=opts.expected_sha256,
                    actual=digest,
                ),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=SalesforceErrorConstants.ERR_SHA256_MISMATCH_WRITE,
                public_message=SalesforceErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )

        existing = self._find_content_version(ref)
        if existing is not None and not opts.overwrite:
            raise log_and_build_edh_error(
                logger,
                EdhConflictError,
                SalesforceErrorConstants.MSG_CONTENT_VERSION_CONFLICT.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
                error_code=SalesforceErrorConstants.ERR_CONTENT_VERSION_CONFLICT,
                public_message=SalesforceErrorConstants.PUBLIC_CONFLICT,
            )

        qpath = self._qualified_path(ref)
        envelope_meta = {
            "sha256": digest,
            "content_type": content_type,
            "custom_metadata": dict(opts.custom_metadata),
        }
        cv_payload = json.dumps(
            {
                "Title": ref.key.split("/")[-1],
                "PathOnClient": qpath,
                "Description": json.dumps(envelope_meta, sort_keys=True),
                "VersionData": base64.b64encode(payload).decode("ascii"),
            }
        ).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key

        resp = self._request(
            "POST",
            f"{self._base_data_url}/sobjects/ContentVersion",
            operation="put_bytes",
            headers=headers,
            body=cv_payload,
        )
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        cv_id = str(doc.get("id") or opts.idempotency_key or digest[:16])

        return EdhObjectMetadata(
            ref=EdhObjectRef(
                namespace=ref.namespace,
                key=ref.key,
                zone=ref.zone,
                version=cv_id,
            ),
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=f'"{digest[:32]}"',
            updated_at=datetime.now(timezone.utc),
            custom_metadata=dict(opts.custom_metadata),
        )

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        record = self._find_content_version(ref)
        if record is None:
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                SalesforceErrorConstants.MSG_CONTENT_VERSION_NOT_FOUND.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
                error_code=SalesforceErrorConstants.ERR_CONTENT_VERSION_NOT_FOUND,
                public_message=SalesforceErrorConstants.PUBLIC_NOT_FOUND,
                level=logging.WARNING,
            )
        cv_id = record["Id"]
        resp = self._request(
            "GET",
            f"{self._base_data_url}/sobjects/ContentVersion/{quote(str(cv_id))}/VersionData",
            operation="get_bytes",
            headers={"Accept": "application/octet-stream"},
        )
        desc = self._parse_description(record.get("Description"))
        expected_sha = desc.get("sha256", "")
        if expected_sha and compute_sha256(resp.body) != expected_sha.lower():
            raise log_and_build_edh_error(
                logger,
                EdhIntegrityError,
                SalesforceErrorConstants.MSG_SHA256_MISMATCH_READ.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
                error_code=SalesforceErrorConstants.ERR_SHA256_MISMATCH_READ,
                public_message=SalesforceErrorConstants.PUBLIC_INTEGRITY_ERROR,
            )
        return resp.body

    def stream_chunks(self, ref: EdhObjectRef, *, chunk_size: int = 65536) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_INVALID_CHUNK_SIZE.format(chunk_size=chunk_size),
                provider=self.STRATEGY_NAME,
                operation="stream_chunks",
                error_code=SalesforceErrorConstants.ERR_INVALID_CHUNK_SIZE,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        payload = self.get_bytes(ref)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def head(self, ref: EdhObjectRef) -> EdhObjectMetadata:
        record = self._find_content_version(ref)
        if record is None:
            raise log_and_build_edh_error(
                logger,
                EdhNotFoundError,
                SalesforceErrorConstants.MSG_CONTENT_VERSION_NOT_FOUND.format(uri=ref.uri),
                provider=self.STRATEGY_NAME,
                operation="head",
                error_code=SalesforceErrorConstants.ERR_CONTENT_VERSION_NOT_FOUND,
                public_message=SalesforceErrorConstants.PUBLIC_NOT_FOUND,
                level=logging.WARNING,
            )
        return self._record_to_metadata(ref, record)

    def delete(
        self,
        ref_or_sobject: EdhObjectRef | str,
        record_id: str | None = None,
    ) -> bool:
        if isinstance(ref_or_sobject, str):
            if not record_id:
                raise log_and_build_edh_error(
                    logger,
                    EdhConfigError,
                    SalesforceErrorConstants.MSG_MISSING_DELETE_RECORD_ID,
                    provider=self.STRATEGY_NAME,
                    operation="delete",
                    error_code=SalesforceErrorConstants.ERR_MISSING_DELETE_RECORD_ID,
                    public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
                )
            return self.delete_record(ref_or_sobject, record_id)
        record = self._find_content_version(ref_or_sobject)
        if record is None:
            return False
        cv_id = record["Id"]
        try:
            self._request(
                "DELETE",
                f"{self._base_data_url}/sobjects/ContentVersion/{quote(str(cv_id))}",
                operation="delete",
            )
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
                SalesforceErrorConstants.MSG_INVALID_PAGE_SIZE.format(page_size=page_size),
                provider=self.STRATEGY_NAME,
                operation="list_objects",
                error_code=SalesforceErrorConstants.ERR_INVALID_PAGE_SIZE,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        clean_prefix = normalize_object_key(prefix) if prefix.strip() else ""
        base_prefix = f"{self._config.namespace}/{zone.value}/"
        like_prefix = f"{base_prefix}{clean_prefix}%"

        offset = int(page_token) if page_token and page_token.isdigit() else 0
        soql = (
            "SELECT Id, Title, PathOnClient, ContentSize, Checksum, Description "
            f"FROM ContentVersion WHERE PathOnClient LIKE {_escape_soql_literal(like_prefix)} "
            f"AND IsLatest = true ORDER BY PathOnClient ASC LIMIT {page_size + 1} OFFSET {offset}"
        )
        doc = self._query_soql(soql, operation="list_objects")
        raw_records = list(doc.get("records") or [])
        has_more = len(raw_records) > page_size
        sliced = raw_records[:page_size]

        items: list[EdhObjectMetadata] = []
        for rec in sliced:
            path_on_client = str(rec.get("PathOnClient", ""))
            logical_key = (
                path_on_client[len(base_prefix) :]
                if path_on_client.startswith(base_prefix)
                else path_on_client
            )
            ref = EdhObjectRef(
                namespace=self._config.namespace,
                key=logical_key,
                zone=zone,
                version=str(rec.get("Id", "")),
            )
            items.append(self._record_to_metadata(ref, rec))

        next_token = str(offset + page_size) if has_more else None
        return EdhListPage(items=tuple(items), next_page_token=next_token)

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        opts = options or EdhWriteOptions()
        sobject_name = _validate_soql_identifier(batch.dataset, kind="SObject dataset")

        composite_records = []
        for row in batch.records:
            merged_row = {
                "attributes": {"type": sobject_name},
                **dict(row),
                **dict(batch.partition_values),
            }
            composite_records.append(merged_row)

        payload_bytes = json.dumps(
            {"allOrNone": True, "records": composite_records},
            sort_keys=True,
        ).encode("utf-8")
        digest = compute_sha256(payload_bytes)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key

        self._request(
            "POST",
            f"{self._base_data_url}/composite/sobjects",
            operation="write_records",
            headers=headers,
            body=payload_bytes,
        )

        ref = EdhObjectRef(
            namespace=self._config.namespace,
            key=f"{sobject_name}/batch-{(opts.idempotency_key or digest)[:16]}",
            zone=batch.zone,
            version=opts.idempotency_key or digest[:16],
        )
        return EdhObjectMetadata(
            ref=ref,
            size_bytes=len(payload_bytes),
            content_type="application/json",
            sha256=digest,
            etag=f'"{digest[:32]}"',
            updated_at=datetime.now(timezone.utc),
            custom_metadata={
                **opts.custom_metadata,
                "sobject": sobject_name,
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
        sobject_name = _validate_soql_identifier(dataset, kind="SObject dataset")
        where_clauses = []
        for col, val in (partition_filter or {}).items():
            safe_col = _validate_soql_identifier(col, kind="filter field")
            where_clauses.append(f"{safe_col} = {_escape_soql_literal(str(val))}")

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        soql = f"SELECT FIELDS(ALL) FROM {sobject_name}{where_sql} LIMIT 200"

        doc = self._query_soql(soql, operation="read_records")
        while True:
            for rec in doc.get("records") or []:
                cleaned = {k: v for k, v in rec.items() if k != "attributes"}
                yield cleaned

            next_url = doc.get("nextRecordsUrl")
            if doc.get("done", True) or not next_url:
                break
            resp = self._request("GET", str(next_url), operation="read_records_page")
            doc = json.loads(resp.body.decode("utf-8")) if resp.body else {"records": [], "done": True}

    # ------------------------------------------------------------------
    # Generic Salesforce SObject & Bulk API 2.0 Operations
    # ------------------------------------------------------------------

    @staticmethod
    def _build_soql(
        soql_or_sobject: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
        allow_fields_all: bool = True,
    ) -> str:
        raw = soql_or_sobject.strip()
        if not raw:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_SOQL_OR_SOBJECT,
                provider=SalesforceErrorConstants.PROVIDER,
                operation="build_soql",
                error_code=SalesforceErrorConstants.ERR_EMPTY_SOQL_OR_SOBJECT,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

        if raw.upper().startswith("SELECT "):
            if limit is not None and " LIMIT " not in raw.upper():
                return f"{raw} LIMIT {int(limit)}"
            return raw

        safe_sobject = _validate_soql_identifier(raw, kind="SObject")
        if fields:
            safe_fields = [_validate_soql_identifier(f, kind="field") for f in fields]
            select_clause = ", ".join(safe_fields)
        elif allow_fields_all:
            select_clause = "FIELDS(ALL)"
            if limit is None:
                limit = 200
        else:
            select_clause = "Id, Name"

        where_sql = ""
        if isinstance(where, Mapping) and where:
            clauses: list[str] = []
            for col, val in where.items():
                safe_col = _validate_soql_identifier(col, kind="filter field")
                if val is None:
                    clauses.append(f"{safe_col} = NULL")
                elif isinstance(val, bool):
                    clauses.append(f"{safe_col} = {' TRUE' if val else 'FALSE'}")
                elif isinstance(val, (int, float)):
                    clauses.append(f"{safe_col} = {val}")
                else:
                    clauses.append(f"{safe_col} = {_escape_soql_literal(str(val))}")
            where_sql = f" WHERE {' AND '.join(clauses)}"
        elif isinstance(where, str) and where.strip():
            where_sql = f" WHERE {where.strip()}"

        limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
        return f"SELECT {select_clause} FROM {safe_sobject}{where_sql}{limit_sql}"

    def describe_schema(self, sobject_name: str | None = None) -> dict[str, Any]:
        """1. Describe Schema: global ``/sobjects`` or ``/sobjects/<SObject>/describe``."""
        if sobject_name and sobject_name.strip():
            safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
            url = f"{self._base_data_url}/sobjects/{safe_sobject}/describe"
        else:
            url = f"{self._base_data_url}/sobjects"
        resp = self._request("GET", url, operation="describe_schema")
        return json.loads(resp.body.decode("utf-8")) if resp.body else {}

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
        """2. Query: Execute a single SOQL query (or build one from ``soql_or_sobject``)."""
        soql = self._build_soql(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
        )
        doc = self._query_soql(soql, operation="query")
        return [
            {k: v for k, v in rec.items() if k != "attributes"}
            for rec in (doc.get("records") or [])
        ]

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
        """3. PaginatedQuery: Yield pages of records following ``nextRecordsUrl``."""
        if batch_size <= 0:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_INVALID_BATCH_SIZE.format(batch_size=batch_size),
                provider=self.STRATEGY_NAME,
                operation="paginated_query",
                error_code=SalesforceErrorConstants.ERR_INVALID_BATCH_SIZE,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        soql = self._build_soql(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
        )
        query_headers = {"Sforce-Query-Options": f"batchSize={batch_size}"}
        url = f"{self._base_data_url}/query?{urlencode({'q': soql})}"
        resp = self._request("GET", url, operation="paginated_query", headers=query_headers)
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {"records": [], "done": True}

        while True:
            page = [
                {k: v for k, v in rec.items() if k != "attributes"}
                for rec in (doc.get("records") or [])
            ]
            yield page

            next_url = doc.get("nextRecordsUrl")
            if doc.get("done", True) or not next_url:
                break
            resp = self._request(
                "GET",
                str(next_url),
                operation="paginated_query_page",
                headers=query_headers,
            )
            doc = json.loads(resp.body.decode("utf-8")) if resp.body else {"records": [], "done": True}

    paginatedQuery = paginated_query
    PaginatedQuery = paginated_query

    def get_record_by_id(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """4. GetRecordById: Fetch a single SObject record by ID."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        clean_id = record_id.strip()
        if not clean_id:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_RECORD_ID.format(operation="get_record_by_id"),
                provider=self.STRATEGY_NAME,
                operation="get_record_by_id",
                error_code=SalesforceErrorConstants.ERR_EMPTY_RECORD_ID,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

        query_suffix = ""
        if fields:
            safe_fields = [_validate_soql_identifier(f, kind="field") for f in fields]
            query_suffix = f"?{urlencode({'fields': ','.join(safe_fields)})}"

        url = f"{self._base_data_url}/sobjects/{safe_sobject}/{quote(clean_id)}{query_suffix}"
        resp = self._request("GET", url, operation="get_record_by_id")
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        return {k: v for k, v in doc.items() if k != "attributes"}

    getRecordById = get_record_by_id
    GetRecordById = get_record_by_id

    def create_record(
        self,
        sobject_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """5. Create / createRecord: Create a new SObject record and return ``{'id', 'success', 'errors'}``."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        body = json.dumps(dict(data), sort_keys=True).encode("utf-8")
        resp = self._request(
            "POST",
            f"{self._base_data_url}/sobjects/{safe_sobject}",
            operation="create",
            headers={"Content-Type": "application/json"},
            body=body,
        )
        return json.loads(resp.body.decode("utf-8")) if resp.body else {"success": True, "errors": []}

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
        """6. Update: Update an existing SObject record by ID via ``PATCH``."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        clean_id = record_id.strip()
        if not clean_id:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_RECORD_ID.format(operation="update"),
                provider=self.STRATEGY_NAME,
                operation="update",
                error_code=SalesforceErrorConstants.ERR_EMPTY_RECORD_ID,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        body = json.dumps(dict(data), sort_keys=True).encode("utf-8")
        self._request(
            "PATCH",
            f"{self._base_data_url}/sobjects/{safe_sobject}/{quote(clean_id)}",
            operation="update",
            headers={"Content-Type": "application/json"},
            body=body,
        )
        return True

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
        """7. Upsert: Insert or update an SObject record by external ID field via ``PATCH``."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        safe_ext_field = _validate_soql_identifier(external_id_field, kind="external ID field")
        clean_ext_val = str(external_id_value).strip()
        if not clean_ext_val:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_EXTERNAL_ID_VALUE,
                provider=self.STRATEGY_NAME,
                operation="upsert",
                error_code=SalesforceErrorConstants.ERR_EMPTY_EXTERNAL_ID_VALUE,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

        payload = {k: v for k, v in data.items() if k != safe_ext_field}
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        resp = self._request(
            "PATCH",
            f"{self._base_data_url}/sobjects/{safe_sobject}/{safe_ext_field}/{quote(clean_ext_val)}",
            operation="upsert",
            headers={"Content-Type": "application/json"},
            body=body,
        )
        if resp.body:
            return json.loads(resp.body.decode("utf-8"))
        return {
            "id": clean_ext_val,
            "success": True,
            "created": resp.status_code == 201,
            "errors": [],
        }

    upsert_record = upsert
    upsertRecord = upsert
    UpsertRecord = upsert
    Upsert = upsert

    def delete_record(self, sobject_name: str, record_id: str) -> bool:
        """8. Delete / deleteRecord: Delete an SObject record by ID via ``DELETE``."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        clean_id = record_id.strip()
        if not clean_id:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_RECORD_ID.format(operation="delete_record"),
                provider=self.STRATEGY_NAME,
                operation="delete_record",
                error_code=SalesforceErrorConstants.ERR_EMPTY_RECORD_ID,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        try:
            self._request(
                "DELETE",
                f"{self._base_data_url}/sobjects/{safe_sobject}/{quote(clean_id)}",
                operation="delete_record",
            )
            return True
        except EdhNotFoundError:
            return False

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
        """9. BulkRead: Execute a Salesforce Bulk API 2.0 Query job and parse CSV results."""
        soql = self._build_soql(
            soql_or_sobject,
            fields=fields,
            where=where,
            limit=limit,
            allow_fields_all=False,
        )
        create_body = json.dumps({"operation": "query", "query": soql}).encode("utf-8")
        create_resp = self._request(
            "POST",
            f"{self._base_data_url}/jobs/query",
            operation="bulk_read_create_job",
            headers={"Content-Type": "application/json"},
            body=create_body,
        )
        job_doc = json.loads(create_resp.body.decode("utf-8")) if create_resp.body else {}
        job_id = str(job_doc.get("id") or "")
        if not job_id:
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                SalesforceErrorConstants.MSG_BULK_QUERY_MISSING_JOB_ID,
                provider=self.STRATEGY_NAME,
                operation="bulk_read",
                error_code=SalesforceErrorConstants.ERR_BULK_QUERY_MISSING_JOB_ID,
                public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
            )

        state = str(job_doc.get("state") or "")
        polls = 0
        while state not in ("JobComplete", "Failed", "Aborted"):
            if polls >= max_polls:
                raise log_and_build_edh_error(
                    logger,
                    EdhTransientError,
                    SalesforceErrorConstants.MSG_BULK_QUERY_TIMEOUT.format(
                        job_id=job_id, state=state
                    ),
                    provider=self.STRATEGY_NAME,
                    operation="bulk_read",
                    error_code=SalesforceErrorConstants.ERR_BULK_QUERY_TIMEOUT,
                    public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
                )
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)
            status_resp = self._request(
                "GET",
                f"{self._base_data_url}/jobs/query/{quote(job_id)}",
                operation="bulk_read_poll",
            )
            job_doc = json.loads(status_resp.body.decode("utf-8")) if status_resp.body else {}
            state = str(job_doc.get("state") or "JobComplete")
            polls += 1

        if state in ("Failed", "Aborted"):
            err_msg = job_doc.get("errorMessage") or f"Bulk query job ended in state {state}"
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                SalesforceErrorConstants.MSG_BULK_QUERY_FAILED.format(
                    job_id=job_id, state=state, detail=err_msg
                ),
                provider=self.STRATEGY_NAME,
                operation="bulk_read",
                error_code=SalesforceErrorConstants.ERR_BULK_QUERY_FAILED,
                public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
            )

        results: list[dict[str, Any]] = []
        locator: str | None = None
        while True:
            suffix = f"?{urlencode({'locator': locator})}" if locator else ""
            res_resp = self._request(
                "GET",
                f"{self._base_data_url}/jobs/query/{quote(job_id)}/results{suffix}",
                operation="bulk_read_results",
                headers={"Accept": "text/csv"},
            )
            csv_text = res_resp.body.decode("utf-8") if res_resp.body else ""
            if csv_text.strip():
                reader = csv.DictReader(io.StringIO(csv_text))
                for row in reader:
                    results.append(dict(row))

            next_locator = (
                res_resp.headers.get("sforce-locator")
                or res_resp.headers.get("Sforce-Locator")
            )
            if not next_locator or next_locator.lower() == "null":
                break
            locator = next_locator

        return results

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
        """10. BulkWrite: Execute a Salesforce Bulk API 2.0 Ingest job using CSV."""
        safe_sobject = _validate_soql_identifier(sobject_name, kind="SObject")
        valid_ops = {"insert", "update", "upsert", "delete", "hardDelete"}
        if operation not in valid_ops:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_INVALID_BULK_OPERATION.format(
                    operation=operation, valid_ops=sorted(valid_ops)
                ),
                provider=self.STRATEGY_NAME,
                operation="bulk_write",
                error_code=SalesforceErrorConstants.ERR_INVALID_BULK_OPERATION,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )
        if not records:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_EMPTY_BULK_RECORDS,
                provider=self.STRATEGY_NAME,
                operation="bulk_write",
                error_code=SalesforceErrorConstants.ERR_EMPTY_BULK_RECORDS,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

        job_spec: dict[str, Any] = {
            "object": safe_sobject,
            "operation": operation,
            "contentType": "CSV",
            "lineEnding": "LF",
        }
        if operation == "upsert" or external_id_field:
            if not external_id_field:
                raise log_and_build_edh_error(
                    logger,
                    EdhConfigError,
                    SalesforceErrorConstants.MSG_MISSING_EXTERNAL_ID_FIELD,
                    provider=self.STRATEGY_NAME,
                    operation="bulk_write",
                    error_code=SalesforceErrorConstants.ERR_MISSING_EXTERNAL_ID_FIELD,
                    public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
                )
            job_spec["externalIdFieldName"] = _validate_soql_identifier(
                external_id_field, kind="external ID field"
            )

        create_resp = self._request(
            "POST",
            f"{self._base_data_url}/jobs/ingest",
            operation="bulk_write_create_job",
            headers={"Content-Type": "application/json"},
            body=json.dumps(job_spec, sort_keys=True).encode("utf-8"),
        )
        job_doc = json.loads(create_resp.body.decode("utf-8")) if create_resp.body else {}
        job_id = str(job_doc.get("id") or "")
        if not job_id:
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                SalesforceErrorConstants.MSG_BULK_INGEST_MISSING_JOB_ID,
                provider=self.STRATEGY_NAME,
                operation="bulk_write",
                error_code=SalesforceErrorConstants.ERR_BULK_INGEST_MISSING_JOB_ID,
                public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
            )

        # Serialize records to CSV with LF line endings
        fieldnames: list[str] = []
        for row in records:
            for col in row.keys():
                safe_col = _validate_soql_identifier(col, kind="CSV column")
                if safe_col not in fieldnames:
                    fieldnames.append(safe_col)

        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in records:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        csv_bytes = csv_buf.getvalue().encode("utf-8")

        self._request(
            "PUT",
            f"{self._base_data_url}/jobs/ingest/{quote(job_id)}/batches",
            operation="bulk_write_upload_batch",
            headers={"Content-Type": "text/csv", "Accept": "application/json"},
            body=csv_bytes,
        )

        close_resp = self._request(
            "PATCH",
            f"{self._base_data_url}/jobs/ingest/{quote(job_id)}",
            operation="bulk_write_close_job",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"state": "UploadComplete"}).encode("utf-8"),
        )
        job_doc = json.loads(close_resp.body.decode("utf-8")) if close_resp.body else job_doc

        state = str(job_doc.get("state") or "UploadComplete")
        polls = 0
        while state not in ("JobComplete", "Failed", "Aborted"):
            if polls >= max_polls:
                raise log_and_build_edh_error(
                    logger,
                    EdhTransientError,
                    SalesforceErrorConstants.MSG_BULK_INGEST_TIMEOUT.format(
                        job_id=job_id, state=state
                    ),
                    provider=self.STRATEGY_NAME,
                    operation="bulk_write",
                    error_code=SalesforceErrorConstants.ERR_BULK_INGEST_TIMEOUT,
                    public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
                )
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)
            status_resp = self._request(
                "GET",
                f"{self._base_data_url}/jobs/ingest/{quote(job_id)}",
                operation="bulk_write_poll",
            )
            job_doc = json.loads(status_resp.body.decode("utf-8")) if status_resp.body else {}
            state = str(job_doc.get("state") or "JobComplete")
            polls += 1

        if state in ("Failed", "Aborted"):
            err_msg = job_doc.get("errorMessage") or f"Bulk ingest job ended in state {state}"
            raise log_and_build_edh_error(
                logger,
                EdhTransientError,
                SalesforceErrorConstants.MSG_BULK_INGEST_FAILED.format(
                    job_id=job_id, state=state, detail=err_msg
                ),
                provider=self.STRATEGY_NAME,
                operation="bulk_write",
                error_code=SalesforceErrorConstants.ERR_BULK_INGEST_FAILED,
                public_message=SalesforceErrorConstants.PUBLIC_TRANSIENT_ERROR,
            )

        succ_resp = self._request(
            "GET",
            f"{self._base_data_url}/jobs/ingest/{quote(job_id)}/successfulResults",
            operation="bulk_write_successful_results",
            headers={"Accept": "text/csv"},
        )
        fail_resp = self._request(
            "GET",
            f"{self._base_data_url}/jobs/ingest/{quote(job_id)}/failedResults",
            operation="bulk_write_failed_results",
            headers={"Accept": "text/csv"},
        )

        succ_rows = (
            [dict(r) for r in csv.DictReader(io.StringIO(succ_resp.body.decode("utf-8")))]
            if succ_resp.body and succ_resp.body.strip()
            else []
        )
        fail_rows = (
            [dict(r) for r in csv.DictReader(io.StringIO(fail_resp.body.decode("utf-8")))]
            if fail_resp.body and fail_resp.body.strip()
            else []
        )

        return {
            "job_id": job_id,
            "state": state,
            "number_records_processed": int(job_doc.get("numberRecordsProcessed", len(succ_rows) + len(fail_rows))),
            "number_records_failed": int(job_doc.get("numberRecordsFailed", len(fail_rows))),
            "successful_results": succ_rows,
            "failed_results": fail_rows,
        }

    bulkWrite = bulk_write
    BulkWrite = bulk_write

    def ping(self) -> bool:
        try:
            self._request("GET", f"{self._base_data_url}/limits", operation="ping")
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close all underlying HTTP connections in the pool."""
        self._connection_pool.close()

    # ------------------------------------------------------------------
    # Async Salesforce Strategy Operations
    # ------------------------------------------------------------------

    async def aping(self) -> bool:
        return await asyncio.to_thread(self.ping)

    async def adescribe_schema(self, sobject_name: str | None = None) -> dict[str, Any]:
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
        return await asyncio.to_thread(
            self.upsert, sobject_name, external_id_field, external_id_value, data
        )

    aupsert_record = aupsert
    aupsertRecord = aupsert
    aUpsertRecord = aupsert
    aUpsert = aupsert

    async def adelete_record(self, sobject_name: str, record_id: str) -> bool:
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

    @staticmethod
    def _parse_description(raw: Any) -> dict[str, Any]:
        if not raw or not isinstance(raw, str):
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _record_to_metadata(self, ref: EdhObjectRef, rec: Mapping[str, Any]) -> EdhObjectMetadata:
        desc = self._parse_description(rec.get("Description"))
        sha256 = str(desc.get("sha256") or rec.get("Checksum") or "")
        content_type = str(desc.get("content_type") or "application/octet-stream")
        custom_meta = dict(desc.get("custom_metadata") or {})
        size_bytes = int(rec.get("ContentSize") or 0)
        cv_id = str(rec.get("Id") or ref.version or "")

        return EdhObjectMetadata(
            ref=EdhObjectRef(
                namespace=ref.namespace,
                key=ref.key,
                zone=ref.zone,
                version=cv_id or None,
            ),
            size_bytes=size_bytes,
            content_type=content_type,
            sha256=sha256,
            etag=f'"{sha256[:32]}"' if sha256 else "",
            updated_at=datetime.now(timezone.utc),
            custom_metadata=custom_meta,
        )
