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

import base64
import json
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote, urlencode

from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhConflictError,
    EdhIntegrityError,
    EdhNotFoundError,
)
from sdk.connectors.edh.strategies._http import (
    RawHttpResponse,
    TransportCallable,
    raise_for_status,
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

DEFAULT_SALESFORCE_API_VERSION = "v60.0"
_SAFE_SOQL_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(__c|__kav|__r|__x)?$")


def _validate_soql_identifier(name: str, *, kind: str = "identifier") -> str:
    """Ensure SObject and field names are safe SOQL identifiers (no injection)."""
    candidate = name.strip().replace("/", "_")
    if not _SAFE_SOQL_IDENTIFIER.match(candidate):
        raise EdhConfigError(f"Unsafe Salesforce SOQL {kind}: {name!r}")
    return candidate


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
                raise EdhAuthError(
                    f"Salesforce OAuth2 token exchange failed with HTTP {resp.status_code}",
                    provider="salesforce",
                    operation="oauth2_token",
                )
            doc = json.loads(resp.body.decode("utf-8"))
            token = doc.get("access_token", "")
            if not token:
                raise EdhAuthError(
                    "Salesforce OAuth2 response missing access_token",
                    provider="salesforce",
                    operation="oauth2_token",
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
            raise EdhConfigError(
                "SalesforceEdhStrategy requires EdhConnectorConfig.endpoint_url (Salesforce instance URL)"
            )
        self._config = config
        self._instance_url = config.endpoint_url.rstrip("/")
        self._api_version = api_version.strip().lstrip("/")
        self._base_data_url = f"{self._instance_url}/services/data/{self._api_version}"
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

    def _qualified_path(self, ref: EdhObjectRef) -> str:
        self._validate_namespace(ref)
        return f"{ref.namespace}/{ref.qualified_key}"

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
        raise_for_status(resp, provider=self.STRATEGY_NAME, operation=operation)
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
            raise EdhIntegrityError(
                f"SHA-256 mismatch on put_bytes for {ref.uri}: expected {opts.expected_sha256}, got {digest}",
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
            )

        existing = self._find_content_version(ref)
        if existing is not None and not opts.overwrite:
            raise EdhConflictError(
                f"Salesforce ContentVersion already exists and overwrite=False: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="put_bytes",
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
            raise EdhNotFoundError(
                f"Salesforce ContentVersion not found: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="get_bytes",
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
            raise EdhIntegrityError(
                f"Salesforce ContentVersion SHA-256 mismatch for {ref.uri}",
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
        record = self._find_content_version(ref)
        if record is None:
            raise EdhNotFoundError(
                f"Salesforce ContentVersion not found: {ref.uri}",
                provider=self.STRATEGY_NAME,
                operation="head",
            )
        return self._record_to_metadata(ref, record)

    def delete(self, ref: EdhObjectRef) -> bool:
        record = self._find_content_version(ref)
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
            raise EdhConfigError("page_size must be > 0")
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

    def ping(self) -> bool:
        try:
            self._request("GET", f"{self._base_data_url}/limits", operation="ping")
            return True
        except Exception:
            return False

    def close(self) -> None:
        return None

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
