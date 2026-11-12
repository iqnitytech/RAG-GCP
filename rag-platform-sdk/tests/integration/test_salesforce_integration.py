"""Live HTTP and ``.env.integration`` integration tests for ``SalesforceEdhStrategy``."""

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
    EdhConnector,
    EdhConnectorConfig,
    EdhRecordBatch,
    EdhWriteOptions,
    compute_sha256,
    make_salesforce_oauth_token_getter,
)
from tests.integration.conftest import INTEGRATION_ENV_FILE, load_integration_env


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
            assert oauth_calls == 1
    finally:
        server.shutdown()
        server.server_close()


def test_live_salesforce_org_from_integration_env():
    """Connect to a live Salesforce org and fetch objects using ``tests/integration/.env.integration``."""
    env = load_integration_env()
    sf_url = env.get("SF_INSTANCE_URL", "")
    sf_client_id = env.get("SF_CLIENT_ID", "")
    sf_client_secret = env.get("SF_CLIENT_SECRET", "")
    sf_sobject = env.get("SF_TEST_SOBJECT", "Account")
    namespace = env.get("EDH_NAMESPACE", "crm-edh")

    if not (sf_url and sf_client_id and sf_client_secret):
        pytest.skip(
            f"Populate SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET in {INTEGRATION_ENV_FILE} "
            "to run live Salesforce org integration tests"
        )

    token_getter = make_salesforce_oauth_token_getter(
        instance_url=sf_url,
        client_id_getter=lambda: sf_client_id,
        client_secret_getter=lambda: sf_client_secret,
    )
    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace=namespace,
        endpoint_url=sf_url,
        auth_token_getter=token_getter,
    )
    with EdhConnector(cfg) as sf:
        assert sf.ping() is True
        files_page = sf.list_objects(zone=DataZone.RAW, page_size=5)
        assert isinstance(files_page.items, tuple)
        records = list(sf.read_records(sf_sobject, zone=DataZone.CURATED))
        assert isinstance(records, list)
