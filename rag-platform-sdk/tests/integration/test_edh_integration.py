"""Integration tests for ``sdk.connectors.edh`` over live loopback HTTP servers and disk.

Exercises the real ``urllib.request`` HTTP client (no mocked transports),
S3 Signature v4 signing, OAuth2 Bearer headers, ``X-Request-ID`` propagation,
HTTP 503 retry recovery, and multi-zone Data Lake promotion workflows.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    DataZone,
    EdhConflictError,
    EdhConnector,
    EdhConnectorConfig,
    EdhNotFoundError,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
    RetryPolicy,
    compute_sha256,
    make_salesforce_oauth_token_getter,
)
from sdk.logging.context import reset_request_id, set_request_id


class _LiveHubState:
    def __init__(self) -> None:
        self.s3_objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.api_objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.request_headers_log: list[dict[str, str]] = []
        self.fail_next_get_count: int = 0


def _make_handler(state: _LiveHubState):
    class LiveHubHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return  # keep test output clean

        def _record_headers(self) -> dict[str, str]:
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            state.request_headers_log.append(hdrs)
            return hdrs

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length > 0 else b""

        def do_HEAD(self) -> None:
            self._record_headers()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                _, meta = state.api_objects[full_key]
                self.send_response(200)
                for k, v in meta.items():
                    self.send_header(k, v)
                self.end_headers()
                return

            # S3 Hub HEAD
            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            if len(parts) == 1:
                self.send_response(200)
                self.end_headers()
                return
            if stripped not in state.s3_objects:
                self.send_response(404)
                self.end_headers()
                return
            _, meta = state.s3_objects[stripped]
            self.send_response(200)
            for k, v in meta.items():
                self.send_header(k, v)
            self.end_headers()

        def do_PUT(self) -> None:
            hdrs = self._record_headers()
            body = self._read_body()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if hdrs.get("x-edh-overwrite") == "false" and full_key in state.api_objects:
                    self.send_response(409)
                    self.end_headers()
                    return
                sha = hdrs.get("x-edh-sha256", compute_sha256(body))
                etag = f'"{sha[:32]}"'
                meta = {
                    "Content-Type": hdrs.get("content-type", "application/octet-stream"),
                    "Content-Length": str(len(body)),
                    "X-EDH-SHA256": sha,
                    "ETag": etag,
                }
                for hk, hv in hdrs.items():
                    if hk.startswith("x-edh-meta-"):
                        meta[hk] = hv
                state.api_objects[full_key] = (body, meta)
                resp_bytes = json.dumps({"etag": etag, "version": sha[:16]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(resp_bytes)
                return

            # S3 Hub PUT
            full_key = path.lstrip("/")
            if hdrs.get("if-none-match") == "*" and full_key in state.s3_objects:
                self.send_response(412)
                self.end_headers()
                return
            sha = hdrs.get("x-amz-meta-sha256", compute_sha256(body))
            etag = f'"{sha[:32]}"'
            meta = {
                "Content-Type": hdrs.get("content-type", "application/octet-stream"),
                "Content-Length": str(len(body)),
                "ETag": etag,
                "x-amz-meta-sha256": sha,
            }
            for hk, hv in hdrs.items():
                if hk.startswith("x-amz-meta-"):
                    meta[hk] = hv
            state.s3_objects[full_key] = (body, meta)
            self.send_response(200)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            hdrs = self._record_headers()
            body = self._read_body()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            # /v1/namespaces/{ns}/zones/{zone}/datasets/{ds}/records
            parts = path.strip("/").split("/")
            ns, zone = parts[2], parts[4]
            dataset = "/".join(parts[6:-1])
            part_segs = [
                f"{k[len('partition.'):]}={v[0]}"
                for k, v in sorted(qs.items())
                if k.startswith("partition.")
            ]
            sha = hdrs.get("x-edh-sha256", compute_sha256(body))
            key = "/".join([dataset, *part_segs, f"part-{sha[:16]}.jsonl"])
            full_key = f"{ns}/{zone}/{key}"
            state.api_objects[full_key] = (
                body,
                {
                    "Content-Type": hdrs.get("content-type", "application/x-ndjson"),
                    "Content-Length": str(len(body)),
                    "X-EDH-SHA256": sha,
                    "ETag": f'"{sha[:32]}"',
                },
            )
            resp_bytes = json.dumps({"key": key, "etag": f'"{sha[:32]}"'}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        def do_GET(self) -> None:
            self._record_headers()
            if state.fail_next_get_count > 0:
                state.fail_next_get_count -= 1
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            if path == "/health":
                payload = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                ns, zone, kind = parts[2], parts[4], parts[5]

                if kind == "datasets":
                    dataset = "/".join(parts[6:-1])
                    part_segs = [
                        f"{k[len('partition.'):]}={v[0]}"
                        for k, v in sorted(qs.items())
                        if k.startswith("partition.")
                    ]
                    prefix = f"{ns}/{zone}/{dataset}"
                    out = b"".join(
                        payload
                        for fk, (payload, _) in sorted(state.api_objects.items())
                        if fk.startswith(prefix) and all(ps in set(fk.split("/")) for ps in part_segs)
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return

                if kind == "objects" and len(parts) == 6:
                    prefix = qs.get("prefix", [""])[0]
                    base = f"{ns}/{zone}/"
                    items = [
                        {
                            "key": fk[len(base) :],
                            "size_bytes": len(p),
                            "content_type": m.get("Content-Type", "application/octet-stream"),
                            "sha256": m.get("X-EDH-SHA256", ""),
                            "etag": m.get("ETag", ""),
                        }
                        for fk, (p, m) in sorted(state.api_objects.items())
                        if fk.startswith(f"{base}{prefix}")
                    ]
                    out = json.dumps({"items": items, "next_page_token": None}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return

                full_key = f"{ns}/{zone}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload, meta = state.api_objects[full_key]
                self.send_response(200)
                for k, v in meta.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)
                return

            # S3 Hub GET (list or object)
            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""

            if not key:
                prefix = qs.get("prefix", [""])[0]
                matched = sorted(
                    k for k in state.s3_objects if k.startswith(f"{bucket}/{prefix}")
                )
                contents = []
                for fk in matched:
                    obj_key = fk.split("/", 1)[1]
                    payload, meta = state.s3_objects[fk]
                    etag = meta.get("ETag", '""')
                    contents.append(
                        f"<Contents><Key>{obj_key}</Key><Size>{len(payload)}</Size><ETag>{etag}</ETag></Contents>"
                    )
                xml_bytes = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f"<ListBucketResult>{''.join(contents)}</ListBucketResult>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(xml_bytes)))
                self.end_headers()
                self.wfile.write(xml_bytes)
                return

            if stripped not in state.s3_objects:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload, meta = state.s3_objects[stripped]
            self.send_response(200)
            for k, v in meta.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def do_DELETE(self) -> None:
            self._record_headers()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                del state.api_objects[full_key]
                self.send_response(204)
                self.end_headers()
                return

            full_key = path.lstrip("/")
            if full_key not in state.s3_objects:
                self.send_response(404)
                self.end_headers()
                return
            del state.s3_objects[full_key]
            self.send_response(204)
            self.end_headers()

    return LiveHubHandler


@pytest.fixture
def live_hub():
    """Spin up a real loopback ThreadingHTTPServer for HTTP integration tests."""
    state = _LiveHubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()


def test_integration_api_hub_over_live_http_with_retry_and_request_id(live_hub):
    base_url, state = live_hub
    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="acme-hub",
        endpoint_url=base_url,
        auth_token_getter=lambda: "live-jwt-token",
        api_key_getter=lambda: "live-api-key",
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )

    token = set_request_id("trace-live-001")
    try:
        with EdhConnector(cfg) as edh:
            assert edh.ping() is True

            # 1. Put raw document bytes over live HTTP
            payload = b"%PDF-1.4 live integration test payload"
            meta = edh.put_bytes(
                "contracts/2026/nda.pdf",
                payload,
                zone=DataZone.RAW,
                options=EdhWriteOptions(
                    content_type="application/pdf",
                    custom_metadata={"owner": "legal"},
                    overwrite=False,
                ),
            )
            assert meta.sha256 == compute_sha256(payload)
            assert state.request_headers_log[-1]["x-request-id"] == "trace-live-001"
            assert state.request_headers_log[-1]["authorization"] == "Bearer live-jwt-token"
            assert state.request_headers_log[-1]["x-api-key"] == "live-api-key"

            # 2. Conflict when overwrite=False
            with pytest.raises(EdhConflictError):
                edh.put_bytes(
                    "contracts/2026/nda.pdf",
                    b"duplicate",
                    zone=DataZone.RAW,
                    options=EdhWriteOptions(overwrite=False),
                )

            # 3. Inject transient 503 failure on next GET -> connector retries and succeeds
            state.fail_next_get_count = 2
            downloaded = edh.get_bytes("contracts/2026/nda.pdf", zone=DataZone.RAW)
            assert downloaded == payload

            # 4. Write & read structured records over live HTTP
            batch = EdhRecordBatch(
                dataset="chunks/legal",
                records=(
                    {"id": "1", "clause": "Confidentiality"},
                    {"id": "2", "clause": "Governing Law"},
                ),
                partition_values={"tenant": "acme"},
                zone=DataZone.CURATED,
            )
            edh.write_records(batch)
            rows = list(
                edh.read_records(
                    "chunks/legal",
                    zone=DataZone.CURATED,
                    partition_filter={"tenant": "acme"},
                )
            )
            assert [r["id"] for r in rows] == ["1", "2"]

            # 5. Delete & verify 404
            assert edh.delete("contracts/2026/nda.pdf", zone=DataZone.RAW) is True
            with pytest.raises(EdhNotFoundError):
                edh.get_bytes("contracts/2026/nda.pdf", zone=DataZone.RAW)
    finally:
        reset_request_id(token)


def test_integration_object_storage_hub_over_live_http_with_sigv4(live_hub):
    base_url, state = live_hub
    cfg = EdhConnectorConfig(
        strategy="object_storage_hub",
        namespace="s3-rag-bucket",
        endpoint_url=base_url,
        region="asia-south1",
        access_key_getter=lambda: "AKIAINTEGRATIONTEST",
        secret_key_getter=lambda: "supersecretintegrationkey",
    )

    with EdhConnector(cfg) as edh:
        assert edh.ping() is True

        data = b"Object Storage Hub live HTTP + SigV4 test"
        meta = edh.put_bytes(
            "manuals/install_guide.txt",
            data,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="text/plain",
                custom_metadata={"dept": "eng"},
            ),
        )
        assert meta.size_bytes == len(data)
        # Verify AWS4-HMAC-SHA256 Authorization header was sent over the wire
        last_auth = state.request_headers_log[-1].get("authorization", "")
        assert last_auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAINTEGRATIONTEST/")

        assert edh.get_bytes("manuals/install_guide.txt", zone=DataZone.RAW) == data
        head = edh.head("manuals/install_guide.txt", zone=DataZone.RAW)
        assert head.size_bytes == len(data)
        assert head.custom_metadata.get("dept") == "eng"

        page = edh.list_objects(zone=DataZone.RAW, prefix="manuals")
        assert len(page.items) == 1
        assert page.items[0].ref.key == "manuals/install_guide.txt"


def test_integration_data_lake_medallion_zone_promotion_on_disk(tmp_path):
    """End-to-end Medallion workflow: QUARANTINE -> RAW -> CURATED (Gzipped CSV & JSONL)."""
    cfg = EdhConnectorConfig(
        strategy="data_lake",
        namespace="enterprise-lake",
        root_path=str(tmp_path / "edh_lake"),
    )

    with EdhConnector(cfg) as lake:
        raw_pdf = b"%PDF-1.4 scanned invoice #9001"
        # Stage 1: Land in QUARANTINE
        q_meta = lake.put_bytes(
            "uploads/inv_9001.pdf",
            raw_pdf,
            zone=DataZone.QUARANTINE,
            options=EdhWriteOptions(content_type="application/pdf"),
        )
        # Stage 2: Scan & promote to RAW, then remove from QUARANTINE
        scanned_bytes = b"".join(lake.stream_chunks(q_meta.ref, chunk_size=8))
        lake.put_bytes(
            "invoices/2026/inv_9001.pdf",
            scanned_bytes,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="application/pdf",
                expected_sha256=q_meta.sha256,
            ),
        )
        lake.delete(q_meta.ref)

        # Stage 3: Write extracted chunks into CURATED zone with Gzip compression in CSV format
        batch = EdhRecordBatch(
            dataset="invoice_chunks",
            records=(
                {"chunk_id": "inv-1", "amount": "1500.00", "currency": "USD"},
                {"chunk_id": "inv-2", "amount": "250.00", "currency": "USD"},
            ),
            partition_values={"year": "2026", "status": "verified"},
            zone=DataZone.CURATED,
            record_format=RecordFormat.CSV,
        )
        lake.write_records(batch, options=EdhWriteOptions(compress_gzip=True, idempotency_key="run-1"))

        # Verify read_records transparently decompresses Gzipped CSV partitions
        records = list(
            lake.read_records(
                "invoice_chunks",
                zone=DataZone.CURATED,
                partition_filter={"year": "2026", "status": "verified"},
                record_format=RecordFormat.CSV,
            )
        )
        assert len(records) == 2
        assert records[0]["chunk_id"] == "inv-1"
        assert records[1]["amount"] == "250.00"


def test_integration_salesforce_oauth_soql_and_content_version_over_live_http():
    """Verify SalesforceEdhStrategy + OAuth2 client_credentials over a real loopback HTTP server."""
    content_versions: dict[str, dict[str, Any]] = {}
    sobject_store: dict[str, list[dict[str, Any]]] = {}
    oauth_calls = 0

    class SalesforceHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, doc: Any) -> None:
            raw = json.dumps(doc).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            nonlocal oauth_calls
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)

            if path == "/services/oauth2/token":
                oauth_calls += 1
                form = parse_qs(body.decode("utf-8"))
                assert form.get("grant_type") == ["client_credentials"]
                self._send_json(200, {"access_token": "sfdc-live-token-001", "token_type": "Bearer"})
                return

            assert self.headers.get("Authorization") == "Bearer sfdc-live-token-001"

            if path.endswith("/sobjects/ContentVersion"):
                doc = json.loads(body.decode("utf-8"))
                raw_bytes = base64.b64decode(doc["VersionData"])
                cv_id = f"068LIVE{len(content_versions) + 1:04d}"
                content_versions[cv_id] = {
                    "Id": cv_id,
                    "Title": doc["Title"],
                    "PathOnClient": doc["PathOnClient"],
                    "Description": doc["Description"],
                    "ContentSize": len(raw_bytes),
                    "Checksum": compute_sha256(raw_bytes),
                    "IsLatest": True,
                    "_bytes": raw_bytes,
                }
                self._send_json(201, {"id": cv_id, "success": True})
                return

            if path.endswith("/composite/sobjects"):
                doc = json.loads(body.decode("utf-8"))
                out = []
                for idx, rec in enumerate(doc.get("records", []), start=1):
                    stype = rec["attributes"]["type"]
                    sobject_store.setdefault(stype, []).append({**rec, "Id": f"a01LIVE{idx:04d}"})
                    out.append({"id": f"a01LIVE{idx:04d}", "success": True})
                self._send_json(200, out)
                return

            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            assert self.headers.get("Authorization") == "Bearer sfdc-live-token-001"
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            if path.endswith("/limits"):
                self._send_json(200, {"DailyApiRequests": {"Max": 100000, "Remaining": 99990}})
                return

            if "/sobjects/ContentVersion/" in path and path.endswith("/VersionData"):
                cv_id = path.split("/sobjects/ContentVersion/", 1)[1].split("/", 1)[0]
                raw = content_versions[cv_id]["_bytes"]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path.endswith("/query"):
                soql = qs.get("q", [""])[0]
                if "FROM ContentVersion" in soql:
                    m_eq = re.search(r"PathOnClient = '([^']*)'", soql)
                    latest = [
                        {k: v for k, v in r.items() if k != "_bytes"}
                        for r in content_versions.values()
                        if r.get("IsLatest")
                    ]
                    if m_eq:
                        target = m_eq.group(1)
                        matched = [r for r in latest if r["PathOnClient"] == target]
                        self._send_json(200, {"totalSize": len(matched), "done": True, "records": matched[:1]})
                        return
                    self._send_json(200, {"totalSize": len(latest), "done": True, "records": latest})
                    return

                m_from = re.search(r"FROM ([A-Za-z0-9_]+)", soql)
                stype = m_from.group(1) if m_from else ""
                rows = list(sobject_store.get(stype, []))
                for col, val in re.findall(r"([A-Za-z0-9_]+) = '([^']*)'", soql):
                    rows = [r for r in rows if str(r.get(col)) == val]
                self._send_json(200, {"totalSize": len(rows), "done": True, "records": rows})
                return

            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), SalesforceHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token_getter = make_salesforce_oauth_token_getter(
            instance_url=base_url,
            client_id_getter=lambda: "sf-client-id",
            client_secret_getter=lambda: "sf-client-secret",
        )
        cfg = EdhConnectorConfig(
            strategy="salesforce",
            namespace="crm-edh",
            endpoint_url=base_url,
            auth_token_getter=token_getter,
        )
        with EdhConnector(cfg) as sf:
            assert sf.ping() is True

            # ContentVersion put & get over live HTTP
            kb_bytes = b"Salesforce Knowledge Article body for RAG indexing"
            meta = sf.put_bytes(
                "knowledge/case_resolution.txt",
                kb_bytes,
                zone=DataZone.RAW,
                options=EdhWriteOptions(
                    content_type="text/plain",
                    custom_metadata={"article_number": "KA-1001"},
                ),
            )
            assert meta.size_bytes == len(kb_bytes)
            assert sf.get_bytes("knowledge/case_resolution.txt", zone=DataZone.RAW) == kb_bytes

            # Composite sObjects write & SOQL read over live HTTP
            batch = EdhRecordBatch(
                dataset="Knowledge__kav",
                records=(
                    {"ArticleNumber": "KA-1001", "Title": "How to reset MFA"},
                    {"ArticleNumber": "KA-1002", "Title": "VPN troubleshooting"},
                ),
                partition_values={"PublishStatus": "Online", "Language": "en_US"},
                zone=DataZone.CURATED,
            )
            sf.write_records(batch)
            articles = list(
                sf.read_records(
                    "Knowledge__kav",
                    zone=DataZone.CURATED,
                    partition_filter={"PublishStatus": "Online"},
                )
            )
            assert [a["ArticleNumber"] for a in articles] == ["KA-1001", "KA-1002"]
            # OAuth token was fetched once and reused across all requests
            assert oauth_calls == 1
    finally:
        server.shutdown()
        server.server_close()

