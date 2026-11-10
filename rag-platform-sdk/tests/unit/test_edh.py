"""Shared contract + unit test suite for ``sdk.connectors.edh`` and ``sdk.models.edh_models``.

Verifies that all five built-in strategies (``DataLakeStrategy``,
``ObjectStorageHubStrategy``, ``ApiDrivenEdhStrategy``, ``SalesforceEdhStrategy``,
``InMemoryEdhStrategy``) satisfy the Liskov Substitution Principle across the
``EdhStrategyPort`` contract, and tests URI parsing, path-traversal prevention,
S3 SigV4 signing, Salesforce OAuth2 + SOQL injection guards, retry with backoff,
and circuit-breaker state transitions.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    ApiDrivenEdhStrategy,
    CircuitState,
    DataLakeStrategy,
    EdhAuthError,
    EdhConfigError,
    EdhConflictError,
    EdhConnector,
    EdhIntegrityError,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhStrategyPort,
    EdhStrategyRegistry,
    EdhTransientError,
    InMemoryEdhStrategy,
    ObjectStorageHubStrategy,
    RawHttpResponse,
    SalesforceEdhStrategy,
    make_salesforce_oauth_token_getter,
    sign_s3_v4_headers,
)
from sdk.models.edh_models import (
    CircuitBreakerPolicy,
    DataZone,
    EdhConnectorConfig,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
    RetryPolicy,
    compute_sha256,
)
from sdk.logging.context import reset_request_id, set_request_id


class _FakeS3HubServer:
    """In-memory S3/GCS XML Object Storage Hub HTTP simulator for contract tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.last_headers: dict[str, str] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawHttpResponse:
        self.last_headers = dict(headers)
        parsed = urlsplit(url)
        path = unquote(parsed.path).lstrip("/")
        parts = path.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        if method == "HEAD" and not key:
            return RawHttpResponse(200, {"server": "fake-s3"})

        if method == "GET" and not key:
            qs = parse_qs(parsed.query)
            prefix = qs.get("prefix", [""])[0]
            max_keys = int(qs.get("max-keys", ["100"])[0])
            token = qs.get("continuation-token", [None])[0]

            matched = sorted(
                k for k in self.objects if k.startswith(f"{bucket}/{prefix}")
            )
            start = int(token) if token and token.isdigit() else 0
            page = matched[start : start + max_keys]
            next_tok = str(start + max_keys) if (start + max_keys) < len(matched) else None

            contents_xml = []
            for full_k in page:
                obj_key = full_k.split("/", 1)[1]
                payload, hdrs = self.objects[full_k]
                etag = hdrs.get("etag", '""')
                contents_xml.append(
                    f"<Contents><Key>{obj_key}</Key><Size>{len(payload)}</Size><ETag>{etag}</ETag></Contents>"
                )
            next_xml = f"<NextContinuationToken>{next_tok}</NextContinuationToken>" if next_tok else ""
            xml_body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<ListBucketResult>{''.join(contents_xml)}{next_xml}</ListBucketResult>"
            ).encode("utf-8")
            return RawHttpResponse(200, {"content-type": "application/xml"}, xml_body)

        full_key = f"{bucket}/{key}"
        if method == "PUT":
            if headers.get("If-None-Match") == "*" and full_key in self.objects:
                return RawHttpResponse(412, {}, b"Precondition Failed")
            data = body or b""
            sha = headers.get("x-amz-meta-sha256", compute_sha256(data))
            etag = f'"{sha[:32]}"'
            stored_headers = {
                "content-type": headers.get("Content-Type", "application/octet-stream"),
                "content-length": str(len(data)),
                "etag": etag,
                "x-amz-meta-sha256": sha,
            }
            for hk, hv in headers.items():
                if hk.lower().startswith("x-amz-meta-"):
                    stored_headers[hk.lower()] = hv
            self.objects[full_key] = (data, stored_headers)
            return RawHttpResponse(200, {"etag": etag})

        if method == "GET":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"Not Found")
            data, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, data)

        if method == "HEAD":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            _, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, b"")

        if method == "DELETE":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            del self.objects[full_key]
            return RawHttpResponse(204, {}, b"")

        return RawHttpResponse(400, {}, b"Unsupported")


class _FakeApiHubServer:
    """In-memory REST API Data Hub simulator for contract tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.last_headers: dict[str, str] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawHttpResponse:
        self.last_headers = dict(headers)
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/health":
            return RawHttpResponse(200, {"content-type": "application/json"}, b'{"status":"ok"}')

        # /v1/namespaces/{ns}/zones/{zone}/objects[/{key}]
        parts = path.strip("/").split("/")
        ns = parts[2]
        zone = parts[4]
        kind = parts[5]

        if kind == "datasets":
            # /v1/namespaces/{ns}/zones/{zone}/datasets/{ds}/records
            ds_parts = parts[6:-1]
            dataset = "/".join(ds_parts)
            part_segs = [
                f"{k[len('partition.'):]}={v[0]}"
                for k, v in sorted(qs.items())
                if k.startswith("partition.")
            ]
            if method == "POST":
                data = body or b""
                sha = headers.get("X-EDH-SHA256", compute_sha256(data))
                key = "/".join([dataset, *part_segs, f"part-{sha[:16]}.jsonl"])
                full_key = f"{ns}/{zone}/{key}"
                self.objects[full_key] = (
                    data,
                    {
                        "content-type": headers.get("Content-Type", "application/x-ndjson"),
                        "content-length": str(len(data)),
                        "x-edh-sha256": sha,
                        "etag": f'"{sha[:32]}"',
                    },
                )
                return RawHttpResponse(
                    201,
                    {"content-type": "application/json"},
                    json.dumps({"key": key, "etag": f'"{sha[:32]}"'}).encode("utf-8"),
                )
            if method == "GET":
                prefix = f"{ns}/{zone}/{dataset}"
                chunks: list[bytes] = []
                for fk, (payload, _) in sorted(self.objects.items()):
                    if not fk.startswith(prefix):
                        continue
                    segs = set(fk.split("/"))
                    if any(ps not in segs for ps in part_segs):
                        continue
                    chunks.append(payload)
                return RawHttpResponse(200, {"content-type": "application/x-ndjson"}, b"".join(chunks))

        if kind == "objects" and len(parts) == 6:
            # list_objects
            prefix = qs.get("prefix", [""])[0]
            page_size = int(qs.get("page_size", ["100"])[0])
            page_token = qs.get("page_token", [None])[0]
            base_prefix = f"{ns}/{zone}/"
            full_prefix = f"{base_prefix}{prefix}"

            matched = [
                (fk[len(base_prefix) :], payload, hdrs)
                for fk, (payload, hdrs) in sorted(self.objects.items())
                if fk.startswith(full_prefix)
            ]
            start = int(page_token) if page_token and page_token.isdigit() else 0
            sliced = matched[start : start + page_size]
            next_tok = str(start + page_size) if (start + page_size) < len(matched) else None
            doc = {
                "items": [
                    {
                        "key": k,
                        "size_bytes": len(p),
                        "content_type": h.get("content-type", "application/octet-stream"),
                        "sha256": h.get("x-edh-sha256", ""),
                        "etag": h.get("etag", ""),
                    }
                    for k, p, h in sliced
                ],
                "next_page_token": next_tok,
            }
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(doc).encode("utf-8"),
            )

        key = "/".join(parts[6:])
        full_key = f"{ns}/{zone}/{key}"

        if method == "PUT":
            if headers.get("X-EDH-Overwrite") == "false" and full_key in self.objects:
                return RawHttpResponse(409, {}, b"Conflict")
            data = body or b""
            sha = headers.get("X-EDH-SHA256", compute_sha256(data))
            etag = f'"{sha[:32]}"'
            hdrs = {
                "content-type": headers.get("Content-Type", "application/octet-stream"),
                "content-length": str(len(data)),
                "x-edh-sha256": sha,
                "etag": etag,
            }
            for hk, hv in headers.items():
                if hk.lower().startswith("x-edh-meta-"):
                    hdrs[hk.lower()] = hv
            self.objects[full_key] = (data, hdrs)
            return RawHttpResponse(
                200,
                {"content-type": "application/json", "etag": etag},
                json.dumps({"etag": etag, "version": sha[:16]}).encode("utf-8"),
            )

        if method == "GET":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"Not Found")
            data, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, data)

        if method == "HEAD":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            _, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, b"")

        if method == "DELETE":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            del self.objects[full_key]
            return RawHttpResponse(204, {}, b"")

        return RawHttpResponse(400, {}, b"Bad Request")


class _FakeSalesforceServer:
    """In-memory Salesforce REST API / SOQL / ContentVersion simulator for contract tests."""

    def __init__(self) -> None:
        self.content_versions: dict[str, dict[str, Any]] = {}
        self.sobject_rows: dict[str, list[dict[str, Any]]] = {}
        self.last_headers: dict[str, str] = {}
        self._seq = 0

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawHttpResponse:
        self.last_headers = dict(headers)
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path.endswith("/limits") and method == "GET":
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                b'{"DailyApiRequests":{"Max":100000,"Remaining":99999}}',
            )

        if path.endswith("/sobjects/ContentVersion") and method == "POST":
            doc = json.loads((body or b"{}").decode("utf-8"))
            raw_bytes = base64.b64decode(doc.get("VersionData", ""))
            self._seq += 1
            cv_id = f"068000000000{self._seq:03d}"
            # Mark prior versions for the same PathOnClient as IsLatest=False
            for prev in self.content_versions.values():
                if prev["PathOnClient"] == doc.get("PathOnClient"):
                    prev["IsLatest"] = False
            self.content_versions[cv_id] = {
                "Id": cv_id,
                "Title": doc.get("Title", ""),
                "PathOnClient": doc.get("PathOnClient", ""),
                "Description": doc.get("Description", ""),
                "ContentSize": len(raw_bytes),
                "Checksum": compute_sha256(raw_bytes),
                "IsLatest": True,
                "_bytes": raw_bytes,
            }
            return RawHttpResponse(
                201,
                {"content-type": "application/json"},
                json.dumps({"id": cv_id, "success": True, "errors": []}).encode("utf-8"),
            )

        if "/sobjects/ContentVersion/" in path:
            after = path.split("/sobjects/ContentVersion/", 1)[1]
            if after.endswith("/VersionData") and method == "GET":
                cv_id = after.split("/", 1)[0]
                rec = self.content_versions.get(cv_id)
                if not rec:
                    return RawHttpResponse(404, {}, b"Not Found")
                return RawHttpResponse(
                    200,
                    {"content-type": "application/octet-stream"},
                    rec["_bytes"],
                )
            if method == "DELETE":
                cv_id = after.strip("/")
                if cv_id not in self.content_versions:
                    return RawHttpResponse(404, {}, b"Not Found")
                del self.content_versions[cv_id]
                return RawHttpResponse(204, {}, b"")

        if path.endswith("/composite/sobjects") and method == "POST":
            doc = json.loads((body or b"{}").decode("utf-8"))
            results = []
            for row in doc.get("records", []):
                sobj_type = row.get("attributes", {}).get("type", "Custom__c")
                self._seq += 1
                rec_id = f"a01000000000{self._seq:03d}"
                stored = {**row, "Id": rec_id}
                self.sobject_rows.setdefault(sobj_type, []).append(stored)
                results.append({"id": rec_id, "success": True, "errors": []})
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(results).encode("utf-8"),
            )

        if path.endswith("/query") and method == "GET":
            soql = qs.get("q", [""])[0]
            if "FROM ContentVersion" in soql:
                latest = [
                    {k: v for k, v in r.items() if k != "_bytes"}
                    for r in self.content_versions.values()
                    if r.get("IsLatest")
                ]
                m_eq = re.search(r"PathOnClient = '([^']*)'", soql)
                if m_eq:
                    target = m_eq.group(1)
                    matched = [r for r in latest if r["PathOnClient"] == target]
                    return RawHttpResponse(
                        200,
                        {"content-type": "application/json"},
                        json.dumps({"totalSize": len(matched), "done": True, "records": matched[:1]}).encode("utf-8"),
                    )
                m_like = re.search(r"PathOnClient LIKE '([^%]*)%'", soql)
                prefix = m_like.group(1) if m_like else ""
                m_limit = re.search(r"LIMIT (\d+)", soql)
                m_offset = re.search(r"OFFSET (\d+)", soql)
                limit = int(m_limit.group(1)) if m_limit else 100
                offset = int(m_offset.group(1)) if m_offset else 0
                matched = sorted(
                    (r for r in latest if r["PathOnClient"].startswith(prefix)),
                    key=lambda r: r["PathOnClient"],
                )
                sliced = matched[offset : offset + limit]
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps({"totalSize": len(sliced), "done": True, "records": sliced}).encode("utf-8"),
                )

            m_from = re.search(r"FROM ([A-Za-z0-9_]+)", soql)
            sobj_type = m_from.group(1) if m_from else ""
            rows = list(self.sobject_rows.get(sobj_type, []))
            for col, val in re.findall(r"([A-Za-z0-9_]+) = '([^']*)'", soql):
                rows = [r for r in rows if str(r.get(col)) == val]
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"totalSize": len(rows), "done": True, "records": rows}).encode("utf-8"),
            )

        return RawHttpResponse(400, {}, b"Unsupported Salesforce endpoint")


@pytest.fixture(params=["memory", "data_lake", "object_storage_hub", "api_hub", "salesforce"])
def connector(request, tmp_path) -> EdhConnector:
    """Parametrized fixture running the exact same contract suite against all 5 strategies."""
    kind = request.param
    if kind == "memory":
        cfg = EdhConnectorConfig(strategy="memory", namespace="rag-enterprise")
        return EdhConnector(cfg)
    if kind == "data_lake":
        cfg = EdhConnectorConfig(
            strategy="data_lake",
            namespace="rag-enterprise",
            root_path=str(tmp_path / "lake"),
        )
        return EdhConnector(cfg)
    if kind == "object_storage_hub":
        server = _FakeS3HubServer()
        cfg = EdhConnectorConfig(
            strategy="object_storage_hub",
            namespace="rag-enterprise",
            endpoint_url="https://s3.internal.example.com",
            access_key_getter=lambda: "AKIAIOSFODNN7EXAMPLE",
            secret_key_getter=lambda: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        strategy = ObjectStorageHubStrategy(cfg, transport=server)
        return EdhConnector(cfg, strategy=strategy)
    if kind == "api_hub":
        server = _FakeApiHubServer()
        cfg = EdhConnectorConfig(
            strategy="api_hub",
            namespace="rag-enterprise",
            endpoint_url="https://edh-api.internal.example.com",
            auth_token_getter=lambda: "eyJhbGciOiJSUzI1NiJ9.token",
        )
        strategy = ApiDrivenEdhStrategy(cfg, transport=server)
        return EdhConnector(cfg, strategy=strategy)
    if kind == "salesforce":
        server = _FakeSalesforceServer()
        cfg = EdhConnectorConfig(
            strategy="salesforce",
            namespace="rag-enterprise",
            endpoint_url="https://acme.my.salesforce.com",
            auth_token_getter=lambda: "00Dxx0000001gPL!AQ0AQ...",
        )
        strategy = SalesforceEdhStrategy(cfg, transport=server)
        return EdhConnector(cfg, strategy=strategy)
    raise AssertionError(f"Unknown fixture kind: {kind}")


def test_strategy_implements_protocol(connector: EdhConnector):
    assert isinstance(connector.strategy, EdhStrategyPort)
    assert connector.ping() is True


def test_contract_put_get_head_stream_list_delete(connector: EdhConnector):
    payload = b"Enterprise RAG document content for EDH contract test."
    meta = connector.put_bytes(
        "kb-finance/2026/q3_report.txt",
        payload,
        zone=DataZone.RAW,
        options=EdhWriteOptions(
            content_type="text/plain",
            custom_metadata={"tenant": "acme", "source": "erp"},
            idempotency_key="idem-001",
        ),
    )
    assert meta.size_bytes == len(payload)
    assert meta.sha256 == compute_sha256(payload)
    assert meta.ref.uri.startswith("edh://rag-enterprise/raw/kb-finance/2026/q3_report.txt")

    # get_bytes
    assert connector.get_bytes("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) == payload

    # stream_chunks
    chunks = list(
        connector.stream_chunks(
            "kb-finance/2026/q3_report.txt",
            zone=DataZone.RAW,
            chunk_size=16,
        )
    )
    assert b"".join(chunks) == payload
    assert len(chunks) >= 3

    # head
    head_meta = connector.head("kb-finance/2026/q3_report.txt", zone=DataZone.RAW)
    assert head_meta.size_bytes == len(payload)
    assert head_meta.sha256 == compute_sha256(payload)

    # overwrite=False conflict
    with pytest.raises(EdhConflictError):
        connector.put_bytes(
            "kb-finance/2026/q3_report.txt",
            b"new",
            zone=DataZone.RAW,
            options=EdhWriteOptions(overwrite=False),
        )

    # list_objects with pagination
    connector.put_bytes("kb-finance/2026/q4_report.txt", b"q4", zone=DataZone.RAW)
    page1 = connector.list_objects(zone=DataZone.RAW, prefix="kb-finance/2026", page_size=1)
    assert len(page1.items) == 1
    assert page1.next_page_token is not None
    page2 = connector.list_objects(
        zone=DataZone.RAW,
        prefix="kb-finance/2026",
        page_size=1,
        page_token=page1.next_page_token,
    )
    assert len(page2.items) == 1

    # delete is idempotent
    assert connector.delete("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) is True
    assert connector.delete("kb-finance/2026/q3_report.txt", zone=DataZone.RAW) is False
    with pytest.raises(EdhNotFoundError):
        connector.get_bytes("kb-finance/2026/q3_report.txt", zone=DataZone.RAW)


def test_contract_partitioned_records_roundtrip(connector: EdhConnector):
    batch_in = EdhRecordBatch(
        dataset="chunks/hr_policies",
        records=(
            {"chunk_id": "c1", "tenant_id": "t1", "text": "Leave policy section 1"},
            {"chunk_id": "c2", "tenant_id": "t1", "text": "Leave policy section 2"},
        ),
        partition_values={"tenant_id": "t1", "lang": "en"},
        zone=DataZone.CURATED,
        record_format=RecordFormat.JSONL,
    )
    batch_other = EdhRecordBatch(
        dataset="chunks/hr_policies",
        records=({"chunk_id": "c3", "tenant_id": "t2", "text": "Other tenant"},),
        partition_values={"tenant_id": "t2", "lang": "en"},
        zone=DataZone.CURATED,
        record_format=RecordFormat.JSONL,
    )

    connector.write_records(batch_in, options=EdhWriteOptions(idempotency_key="b1"))
    connector.write_records(batch_other, options=EdhWriteOptions(idempotency_key="b2"))

    rows_t1 = list(
        connector.read_records(
            "chunks/hr_policies",
            zone=DataZone.CURATED,
            partition_filter={"tenant_id": "t1"},
        )
    )
    assert [r["chunk_id"] for r in rows_t1] == ["c1", "c2"]


def test_uri_parsing_and_path_traversal_blocked():
    ref = EdhObjectRef.from_uri("edh://my-hub/curated/docs/policy.pdf?version=v2")
    assert ref.namespace == "my-hub"
    assert ref.zone == DataZone.CURATED
    assert ref.key == "docs/policy.pdf"
    assert ref.version == "v2"
    assert ref.uri == "edh://my-hub/curated/docs/policy.pdf?version=v2"

    for bad_key in ("../secrets/key.json", "a/../../etc/passwd", "bad\\path.txt", "a/\x00b"):
        with pytest.raises(EdhConfigError):
            EdhObjectRef(namespace="my-hub", key=bad_key)


def test_integrity_mismatch_raises():
    cfg = EdhConnectorConfig(strategy="memory", namespace="ns")
    conn = EdhConnector(cfg)
    with pytest.raises(EdhIntegrityError):
        conn.put_bytes(
            "doc.txt",
            b"hello",
            options=EdhWriteOptions(expected_sha256="0" * 64),
        )


def test_retry_and_circuit_breaker_opens_on_repeated_failures():
    attempts = 0
    sleeps: list[float] = []

    def flaky_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return RawHttpResponse(503, {}, b"Unavailable")
        return RawHttpResponse(200, {"x-edh-sha256": compute_sha256(b"ok")}, b"ok")

    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.05, jitter_ratio=0.0),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=2, recovery_timeout_seconds=10.0),
    )
    strategy = ApiDrivenEdhStrategy(cfg, transport=flaky_transport)
    conn = EdhConnector(cfg, strategy=strategy, sleep_fn=sleeps.append)

    # First two attempts fail (threshold=2 opens circuit), so 3rd attempt fast-fails from circuit breaker
    with pytest.raises(EdhTransientError):
        conn.get_bytes("file.txt")
    assert conn.circuit_breaker.state == CircuitState.OPEN
    assert conn.ping() is False

    # Reset circuit breaker and increase threshold so 3rd retry succeeds
    cfg2 = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.05, jitter_ratio=0.0),
        circuit_breaker_policy=CircuitBreakerPolicy(failure_threshold=5, recovery_timeout_seconds=10.0),
    )
    attempts = 0
    conn2 = EdhConnector(
        cfg2,
        strategy=ApiDrivenEdhStrategy(cfg2, transport=flaky_transport),
        sleep_fn=sleeps.append,
    )
    assert conn2.get_bytes("file.txt") == b"ok"
    assert attempts == 3


def test_api_hub_propagates_request_id_and_maps_auth_and_rate_limit():
    captured_headers: dict[str, str] = {}
    status_to_return = 401

    def transport(method, url, headers, body, timeout) -> RawHttpResponse:
        captured_headers.update(headers)
        return RawHttpResponse(status_to_return, {"retry-after": "2"}, b"err")

    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        auth_token_getter=lambda: "tok-123",
        retry_policy=RetryPolicy(max_attempts=1),
    )
    conn = EdhConnector(cfg, strategy=ApiDrivenEdhStrategy(cfg, transport=transport))

    token = set_request_id("req-corr-999")
    try:
        with pytest.raises(EdhAuthError):
            conn.get_bytes("doc.txt")
        assert captured_headers["X-Request-ID"] == "req-corr-999"
        assert captured_headers["Authorization"] == "Bearer tok-123"

        status_to_return = 429
        with pytest.raises(EdhRateLimitError) as exc_info:
            conn.get_bytes("doc.txt")
        assert exc_info.value.retry_after_seconds == 2.0
    finally:
        reset_request_id(token)


def test_s3_v4_signing_and_registry_allowlist():
    signed = sign_s3_v4_headers(
        method="PUT",
        url="https://s3.asia-south1.amazonaws.com/bucket/raw/doc.txt",
        headers={"Content-Type": "text/plain"},
        payload_sha256=compute_sha256(b"abc"),
        access_key="AKIA123",
        secret_key="secret456",
        region="asia-south1",
    )
    assert signed["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIA123/")
    assert "x-amz-date" in signed

    reg = EdhStrategyRegistry()
    with pytest.raises(EdhConfigError):
        reg.create(EdhConnectorConfig(strategy="unregistered", namespace="ns"))


def test_salesforce_oauth_token_getter_caches_and_handles_auth_error():
    calls = 0

    def oauth_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        nonlocal calls
        calls += 1
        assert method == "POST"
        assert url == "https://acme.my.salesforce.com/services/oauth2/token"
        form = parse_qs((body or b"").decode("utf-8"))
        if form.get("client_secret") == ["bad"]:
            return RawHttpResponse(401, {}, b'{"error":"invalid_client"}')
        return RawHttpResponse(
            200,
            {"content-type": "application/json"},
            b'{"access_token":"sf-token-xyz","token_type":"Bearer"}',
        )

    getter = make_salesforce_oauth_token_getter(
        instance_url="https://acme.my.salesforce.com",
        client_id_getter=lambda: "cid-1",
        client_secret_getter=lambda: "csec-1",
        ttl_seconds=600.0,
        transport=oauth_transport,
    )
    assert getter() == "sf-token-xyz"
    assert getter() == "sf-token-xyz"
    assert calls == 1  # cached on second call

    bad_getter = make_salesforce_oauth_token_getter(
        instance_url="https://acme.my.salesforce.com",
        client_id_getter=lambda: "cid-1",
        client_secret_getter=lambda: "bad",
        transport=oauth_transport,
    )
    with pytest.raises(EdhAuthError):
        bad_getter()


def test_salesforce_soql_injection_blocked_and_next_records_pagination():
    def paged_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        if "/query?q=" in url:
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "totalSize": 2,
                        "done": False,
                        "nextRecordsUrl": "/services/data/v60.0/query/01gxx-2000",
                        "records": [{"attributes": {"type": "Knowledge__kav"}, "Id": "k1", "Title": "Doc 1"}],
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/services/data/v60.0/query/01gxx-2000"):
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "totalSize": 2,
                        "done": True,
                        "records": [{"attributes": {"type": "Knowledge__kav"}, "Id": "k2", "Title": "Doc 2"}],
                    }
                ).encode("utf-8"),
            )
        return RawHttpResponse(404, {}, b"")

    cfg = EdhConnectorConfig(
        strategy="sfdc",  # alias for salesforce
        namespace="sf-ns",
        endpoint_url="https://acme.my.salesforce.com",
    )
    conn = EdhConnector(cfg, strategy=SalesforceEdhStrategy(cfg, transport=paged_transport))
    rows = list(conn.read_records("Knowledge__kav", partition_filter={"PublishStatus": "Online"}))
    assert [r["Id"] for r in rows] == ["k1", "k2"]

    with pytest.raises(EdhConfigError):
        list(conn.read_records("Account; DROP TABLE"))

    with pytest.raises(EdhConfigError):
        list(conn.read_records("Account", partition_filter={"Name OR 1=1": "Acme"}))

