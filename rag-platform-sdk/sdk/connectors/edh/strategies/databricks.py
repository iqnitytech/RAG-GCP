"""Databricks Unity Catalog & SQL Statement Execution EDH Strategy using Python core libraries.

Connects to Databricks Workspace REST APIs v2.0 purely with ``urllib.request``,
``ssl``, ``json``, ``hashlib``, and ``urllib.parse`` (no ``databricks-sdk`` or
third-party packages required):

- **Blob operations** (``put_bytes``, ``get_bytes``, ``stream_chunks``, ``head``,
  ``delete``, ``list_objects``) map to **Unity Catalog Volumes Files API v2.0**:
  ``/api/2.0/fs/files/Volumes/<catalog>/<schema>/<volume>/<zone>/<key>`` and
  ``/api/2.0/fs/directories/Volumes/<catalog>/<schema>/<volume>/<zone>/<prefix>``.
- **Record operations** (``write_records``, ``read_records``) map to **Databricks
  SQL Statement Execution API v2.0** (``POST /api/2.0/sql/statements`` and chunk
  pagination via ``next_chunk_internal_link``) with strict Unity Catalog identifier
  validation and SQL literal escaping to prevent SQL injection.
- **OAuth2 M2M helper** (:func:`make_databricks_oauth_token_getter`) exchanges a
  Databricks Service Principal ``client_id`` + ``client_secret`` at
  ``<workspace_url>/oidc/v1/token`` and caches the bearer token until expiry.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote, urlencode

from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhIntegrityError,
    EdhNotFoundError,
    EdhTransientError,
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

_SAFE_UC_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_META_HEADER_PREFIX = "x-edh-meta-"


def _validate_uc_identifier(name: str, *, kind: str = "identifier") -> str:
    """Validate a Unity Catalog catalog, schema, table, or column identifier."""
    candidate = name.strip().replace("/", "_").replace("-", "_")
    if not _SAFE_UC_IDENTIFIER.match(candidate):
        raise EdhConfigError(f"Unsafe Databricks Unity Catalog {kind}: {name!r}")
    return candidate


def _escape_sql_literal(value: Any) -> str:
    """Escape a scalar value for a Databricks SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "''")
        .replace("\x00", "")
    )
    return f"'{text}'"


def make_databricks_oauth_token_getter(
    *,
    workspace_url: str,
    client_id_getter: Callable[[], str],
    client_secret_getter: Callable[[], str],
    scope: str = "all-apis",
    timeout_seconds: float = 10.0,
    verify_tls: bool = True,
    ttl_seconds: float = 1800.0,
    transport: TransportCallable | None = None,
) -> Callable[[], str]:
    """Build a thread-safe OAuth2 M2M (``client_credentials``) token getter for Databricks.

    Exchanges a Service Principal ``client_id`` and ``client_secret`` at
    ``<workspace_url>/oidc/v1/token`` and caches the token in memory for ``ttl_seconds``.
    """
    base = workspace_url.rstrip("/")
    token_url = f"{base}/oidc/v1/token"
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
                    "scope": scope,
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
                    f"Databricks OAuth2 token exchange failed with HTTP {resp.status_code}",
                    provider="databricks",
                    operation="oauth2_token",
                )
            doc = json.loads(resp.body.decode("utf-8"))
            token = doc.get("access_token", "")
            if not token:
                raise EdhAuthError(
                    "Databricks OAuth2 response missing access_token",
                    provider="databricks",
                    operation="oauth2_token",
                )
            cached_token = str(token)
            expires_at = now + ttl_seconds
            return cached_token

    return get_token


class DatabricksEdhStrategy:
    """Databricks Unity Catalog Volumes & SQL Statement Execution strategy."""

    STRATEGY_NAME = "databricks"

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        catalog: str = "main",
        schema: str = "default",
        warehouse_id: str = "default",
        transport: TransportCallable | None = None,
    ) -> None:
        if not config.endpoint_url or not config.endpoint_url.strip():
            raise EdhConfigError(
                "DatabricksEdhStrategy requires EdhConnectorConfig.endpoint_url (Databricks workspace URL)"
            )
        self._config = config
        self._workspace_url = config.endpoint_url.rstrip("/")
        self._catalog = _validate_uc_identifier(
            config.default_headers.get("X-Databricks-Catalog", catalog),
            kind="catalog",
        )
        self._schema = _validate_uc_identifier(
            config.default_headers.get("X-Databricks-Schema", schema),
            kind="schema",
        )
        self._warehouse_id = config.default_headers.get(
            "X-Databricks-Warehouse-Id", warehouse_id
        )
        self._volume = _validate_uc_identifier(config.namespace, kind="volume")
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

    def _volume_file_path(self, ref: EdhObjectRef) -> str:
        self._validate_namespace(ref)
        quoted_key = "/".join(quote(seg, safe="") for seg in ref.key.split("/"))
        return f"/Volumes/{self._catalog}/{self._schema}/{self._volume}/{ref.zone.value}/{quoted_key}"

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        operation: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> RawHttpResponse:
        url = (
            path_or_url
            if path_or_url.startswith("http://") or path_or_url.startswith("https://")
            else f"{self._workspace_url}/{path_or_url.lstrip('/')}"
        )
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
        }
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key
        for k, v in opts.custom_metadata.items():
            headers[f"X-EDH-Meta-{k}"] = str(v)

        vol_path = self._volume_file_path(ref)
        overwrite_str = "true" if opts.overwrite else "false"
        resp = self._request(
            "PUT",
            f"/api/2.0/fs/files{vol_path}?overwrite={overwrite_str}",
            operation="put_bytes",
            headers=headers,
            body=payload,
        )
        etag = resp.headers.get("etag") or f'"{digest[:32]}"'
        return EdhObjectMetadata(
            ref=EdhObjectRef(
                namespace=ref.namespace,
                key=ref.key,
                zone=ref.zone,
                version=opts.idempotency_key or digest[:16],
            ),
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=etag,
            updated_at=datetime.now(timezone.utc),
            custom_metadata=dict(opts.custom_metadata),
        )

    def get_bytes(self, ref: EdhObjectRef) -> bytes:
        vol_path = self._volume_file_path(ref)
        resp = self._request(
            "GET",
            f"/api/2.0/fs/files{vol_path}",
            operation="get_bytes",
            headers={"Accept": "application/octet-stream"},
        )
        expected_sha = resp.headers.get("x-edh-sha256", "")
        if expected_sha and compute_sha256(resp.body) != expected_sha.lower():
            raise EdhIntegrityError(
                f"Databricks Unity Catalog file SHA-256 mismatch for {ref.uri}",
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
        vol_path = self._volume_file_path(ref)
        resp = self._request("HEAD", f"/api/2.0/fs/files{vol_path}", operation="head")
        custom_meta = {
            k[len(_META_HEADER_PREFIX) :]: v
            for k, v in resp.headers.items()
            if k.lower().startswith(_META_HEADER_PREFIX)
        }
        return EdhObjectMetadata(
            ref=ref,
            size_bytes=int(resp.headers.get("content-length", "0") or 0),
            content_type=resp.headers.get("content-type", "application/octet-stream"),
            sha256=resp.headers.get("x-edh-sha256", ""),
            etag=resp.headers.get("etag", ""),
            updated_at=datetime.now(timezone.utc),
            custom_metadata=custom_meta,
        )

    def delete(self, ref: EdhObjectRef) -> bool:
        vol_path = self._volume_file_path(ref)
        try:
            self._request("DELETE", f"/api/2.0/fs/files{vol_path}", operation="delete")
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
        base_vol_dir = f"/Volumes/{self._catalog}/{self._schema}/{self._volume}/{zone.value}"
        target_dir = f"{base_vol_dir}/{clean_prefix}".rstrip("/")

        query: dict[str, str] = {"page_size": str(page_size)}
        if page_token:
            query["page_token"] = page_token

        resp = self._request(
            "GET",
            f"/api/2.0/fs/directories{target_dir}?{urlencode(query)}",
            operation="list_objects",
            headers={"Accept": "application/json"},
        )
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {"contents": []}
        items: list[EdhObjectMetadata] = []
        base_prefix_with_slash = f"{base_vol_dir}/"
        for entry in doc.get("contents", []):
            if entry.get("is_dir"):
                continue
            full_path = str(entry.get("path", ""))
            logical_key = (
                full_path[len(base_prefix_with_slash) :]
                if full_path.startswith(base_prefix_with_slash)
                else str(entry.get("name", ""))
            )
            items.append(
                EdhObjectMetadata(
                    ref=EdhObjectRef(
                        namespace=self._config.namespace,
                        key=logical_key,
                        zone=zone,
                    ),
                    size_bytes=int(entry.get("file_size", 0) or 0),
                    content_type=str(entry.get("content_type", "application/octet-stream")),
                    sha256=str(entry.get("sha256", "")),
                    etag=str(entry.get("etag", "")),
                    updated_at=datetime.now(timezone.utc),
                    custom_metadata=dict(entry.get("custom_metadata") or {}),
                )
            )
        return EdhListPage(
            items=tuple(items),
            next_page_token=doc.get("next_page_token"),
        )

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        opts = options or EdhWriteOptions()
        table_name = _validate_uc_identifier(batch.dataset, kind="table")
        full_table = f"{self._catalog}.{self._schema}.{table_name}"

        merged_rows: list[dict[str, Any]] = []
        all_columns: list[str] = []
        col_set: set[str] = set()
        for row in batch.records:
            merged = {**dict(row), **dict(batch.partition_values)}
            merged_rows.append(merged)
            for col in merged:
                safe_col = _validate_uc_identifier(col, kind="column")
                if safe_col not in col_set:
                    col_set.add(safe_col)
                    all_columns.append(safe_col)

        values_tuples: list[str] = []
        for merged in merged_rows:
            vals = [_escape_sql_literal(merged.get(col)) for col in all_columns]
            values_tuples.append(f"({', '.join(vals)})")

        sql = (
            f"INSERT INTO {full_table} ({', '.join(all_columns)}) "
            f"VALUES {', '.join(values_tuples)}"
        )
        stmt_payload = json.dumps(
            {
                "warehouse_id": self._warehouse_id,
                "catalog": self._catalog,
                "schema": self._schema,
                "statement": sql,
                "wait_timeout": "30s",
                "records": merged_rows,
            },
            sort_keys=True,
        ).encode("utf-8")
        digest = compute_sha256(stmt_payload)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if opts.idempotency_key:
            headers["Idempotency-Key"] = opts.idempotency_key

        self._request(
            "POST",
            "/api/2.0/sql/statements",
            operation="write_records",
            headers=headers,
            body=stmt_payload,
        )
        return EdhObjectMetadata(
            ref=EdhObjectRef(
                namespace=self._config.namespace,
                key=f"{table_name}/batch-{(opts.idempotency_key or digest)[:16]}",
                zone=batch.zone,
                version=opts.idempotency_key or digest[:16],
            ),
            size_bytes=len(stmt_payload),
            content_type="application/json",
            sha256=digest,
            etag=f'"{digest[:32]}"',
            updated_at=datetime.now(timezone.utc),
            custom_metadata={
                **opts.custom_metadata,
                "uc_table": full_table,
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
        table_name = _validate_uc_identifier(dataset, kind="table")
        full_table = f"{self._catalog}.{self._schema}.{table_name}"
        where_clauses: list[str] = []
        for col, val in (partition_filter or {}).items():
            safe_col = _validate_uc_identifier(col, kind="filter column")
            where_clauses.append(f"{safe_col} = {_escape_sql_literal(str(val))}")

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT * FROM {full_table}{where_sql}"

        stmt_payload = json.dumps(
            {
                "warehouse_id": self._warehouse_id,
                "catalog": self._catalog,
                "schema": self._schema,
                "statement": sql,
                "wait_timeout": "30s",
            }
        ).encode("utf-8")
        resp = self._request(
            "POST",
            "/api/2.0/sql/statements",
            operation="read_records",
            headers={"Content-Type": "application/json"},
            body=stmt_payload,
        )
        doc = json.loads(resp.body.decode("utf-8")) if resp.body else {}
        status_state = doc.get("status", {}).get("state", "SUCCEEDED")
        if status_state in {"FAILED", "CANCELED"}:
            raise EdhTransientError(
                f"Databricks SQL statement ended in state {status_state}",
                provider=self.STRATEGY_NAME,
                operation="read_records",
            )

        columns = [
            col["name"]
            for col in doc.get("manifest", {}).get("schema", {}).get("columns", [])
        ]
        result = doc.get("result") or {}
        while True:
            for row in result.get("data_array") or []:
                if columns and isinstance(row, list):
                    yield dict(zip(columns, row))
                elif isinstance(row, dict):
                    yield dict(row)

            next_link = result.get("next_chunk_internal_link")
            if not next_link:
                break
            chunk_resp = self._request("GET", str(next_link), operation="read_records_chunk")
            result = json.loads(chunk_resp.body.decode("utf-8")) if chunk_resp.body else {}

    def ping(self) -> bool:
        try:
            self._request(
                "GET",
                "/api/2.0/clusters/spark-versions",
                operation="ping",
                headers={"Accept": "application/json"},
            )
            return True
        except Exception:
            return False

    def close(self) -> None:
        return None
