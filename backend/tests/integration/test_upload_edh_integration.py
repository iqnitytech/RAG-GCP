"""Backend integration tests combining FastAPI upload routes, security middleware, and the EDH Data Lake connector.

Verifies the full end-to-end flow:
1. Authenticated ``POST /documents/upload`` -> request validation -> storage adapter backed by ``EdhConnector(DataLakeStrategy)`` on disk.
2. Verification that uploaded documents are persisted in the Data Lake ``RAW`` zone with intact SHA-256 digests and custom metadata.
3. Brute-force failed API key attempts across multiple requests trigger HTTP 429 throttling before multipart body parsing.
4. Readiness probe (``GET /ready``) reflects the underlying storage/EDH health state.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.connectors import create_edh_connector
from app.core.config import Settings, get_integration_settings, get_settings
from app.core.security import reset_rate_limiter
from app.dependencies import get_storage_service_dependency
from app.main import app
from app.models.edh_models import DataZone, EdhWriteOptions, compute_sha256
from app.models.service_models import UploadResult
from app.services.storage.storage_service import StorageServiceError


class EdhBackedStorageService:
    """Integration adapter bridging ``StorageService`` to a real disk-backed ``EdhConnector``."""

    def __init__(self, edh_connector) -> None:
        self.edh = edh_connector
        self.fail = False

    def upload_file(
        self,
        file_object: BinaryIO,
        destination_name: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> UploadResult:
        if self.fail:
            raise StorageServiceError("EDH storage unavailable")
        file_object.seek(0)
        data = file_object.read()
        meta = self.edh.put_bytes(
            destination_name,
            data,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type=content_type,
                custom_metadata=metadata or {},
                overwrite=False,
            ),
        )
        return UploadResult(
            bucket=meta.ref.namespace,
            object_name=meta.ref.qualified_key,
            gs_url=meta.ref.uri,
        )

    def ping(self) -> bool:
        if self.fail:
            return False
        return self.edh.ping()


@pytest.fixture
def edh_storage(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gcs_document_bucket", "rag-docs")
    connector = create_edh_connector(
        settings,
        strategy="data_lake",
        root_path=str(tmp_path / "lake"),
    )
    return EdhBackedStorageService(connector)


@pytest.fixture
def integration_client(edh_storage, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_key", SecretStr("integration-secret-key"))
    monkeypatch.setattr(settings, "rate_limit_per_minute", 10)
    reset_rate_limiter()
    app.dependency_overrides[get_storage_service_dependency] = lambda: edh_storage
    monkeypatch.setattr("app.main.get_storage_service", lambda: edh_storage)
    yield TestClient(app)
    app.dependency_overrides.clear()
    reset_rate_limiter()


def test_end_to_end_upload_persists_into_edh_data_lake(integration_client, edh_storage):
    pdf_bytes = b"%PDF-1.7\nEnterprise architecture specification document."
    resp = integration_client.post(
        "/documents/upload",
        files={"file": ("Architecture Spec 2026.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers={"X-API-Key": "integration-secret-key", "X-Request-ID": "int-req-101"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.headers["x-request-id"] == "int-req-101"

    body = resp.json()
    assert body["filename"] == "Architecture Spec 2026.pdf"
    assert body["size_bytes"] == len(pdf_bytes)
    assert body["storage"]["gs_url"].startswith("edh://")

    # Verify the file is physically stored in the EDH Data Lake RAW zone with matching SHA-256
    page = edh_storage.edh.list_objects(zone=DataZone.RAW, prefix="documents/")
    assert len(page.items) == 1
    stored_meta = page.items[0]
    assert stored_meta.size_bytes == len(pdf_bytes)
    assert stored_meta.sha256 == compute_sha256(pdf_bytes)
    assert stored_meta.custom_metadata.get("original_filename") == "Architecture Spec 2026.pdf"
    assert edh_storage.edh.get_bytes(stored_meta.ref) == pdf_bytes


def test_integration_ready_probe_and_failure_handling(integration_client, edh_storage):
    assert integration_client.get("/health").status_code == 200
    assert integration_client.get("/ready").status_code == 200

    # Simulate downstream storage outage
    edh_storage.fail = True
    assert integration_client.get("/ready").status_code == 503

    r_fail = integration_client.post(
        "/documents/upload",
        files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")},
        headers={"X-API-Key": "integration-secret-key"},
    )
    assert r_fail.status_code == 503
    assert "unavailable" in r_fail.json()["detail"].lower()


def test_integration_brute_force_api_key_throttling(integration_client, edh_storage):
    # Exhaust the 20 failed-attempt per-IP limit and verify 429 is returned
    statuses = []
    for i in range(22):
        r = integration_client.post(
            "/documents/upload",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
            headers={"X-API-Key": f"wrong-key-{i}"},
        )
        statuses.append(r.status_code)

    assert statuses[:20] == [401] * 20
    assert statuses[20] == 429
    assert statuses[21] == 429
    # Ensure zero files were written to the Data Lake during the attack
    assert len(edh_storage.edh.list_objects(zone=DataZone.RAW).items) == 0


def test_integration_salesforce_connector_from_settings_and_env(monkeypatch):
    """Verify SF_CLIENT_ID and SF_CLIENT_SECRET loaded via Settings / monkeypatch.setenv."""
    captured_oauth_form: dict[str, list[str]] = {}
    stored_versions: dict[str, bytes] = {}

    class LoopbackSalesforceHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: Any) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            path = unquote(urlsplit(self.path).path)

            if path == "/services/oauth2/token":
                captured_oauth_form.update(parse_qs(body.decode("utf-8")))
                self._send_json(200, {"access_token": "sf-oauth-tok-777", "token_type": "Bearer"})
                return

            assert self.headers.get("Authorization") == "Bearer sf-oauth-tok-777"
            if path.endswith("/sobjects/ContentVersion"):
                doc = json.loads(body.decode("utf-8"))
                stored_versions[doc["PathOnClient"]] = base64.b64decode(doc["VersionData"])
                self._send_json(201, {"id": "068SFDC0001", "success": True})
                return

        def do_GET(self) -> None:
            assert self.headers.get("Authorization") == "Bearer sf-oauth-tok-777"
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)
            if path.endswith("/limits"):
                self._send_json(200, {"DailyApiRequests": {"Max": 100000, "Remaining": 99999}})
                return
            if path.endswith("/query"):
                soql = qs.get("q", [""])[0]
                if "FROM Account" in soql:
                    self._send_json(
                        200,
                        {
                            "totalSize": 2,
                            "done": True,
                            "records": [
                                {"attributes": {"type": "Account"}, "Id": "001SF001", "Name": "Acme Corp"},
                                {"attributes": {"type": "Account"}, "Id": "001SF002", "Name": "Globex Inc"},
                            ],
                        },
                    )
                    return
                if "FROM ContentVersion" in soql and "LIKE" in soql:
                    self._send_json(
                        200,
                        {
                            "totalSize": 1,
                            "done": True,
                            "records": [
                                {
                                    "Id": "068SFDC0001",
                                    "Title": "security.pdf",
                                    "PathOnClient": "sf-rag-hub/raw/policies/security.pdf",
                                    "ContentSize": 18,
                                    "Checksum": compute_sha256(b"%PDF-1.7 sfdc test"),
                                    "Description": json.dumps({"content_type": "application/pdf"}),
                                }
                            ],
                        },
                    )
                    return
                self._send_json(200, {"totalSize": 0, "done": True, "records": []})
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackSalesforceHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Inject SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET via environment variables
        monkeypatch.setenv("SF_INSTANCE_URL", base_url)
        monkeypatch.setenv("SF_CLIENT_ID", "test-connected-app-id")
        monkeypatch.setenv("SF_CLIENT_SECRET", "test-connected-app-secret")
        monkeypatch.setenv("SF_TEST_SOBJECT", "Account")

        sf_settings = Settings(_env_file=None, gcs_document_bucket="sf-rag-hub")
        assert sf_settings.SF_CLIENT_ID == "test-connected-app-id"
        assert sf_settings.SF_CLIENT_SECRET.get_secret_value() == "test-connected-app-secret"

        with create_edh_connector(sf_settings, strategy="salesforce") as sf_edh:
            assert sf_edh.ping() is True
            meta = sf_edh.put_bytes("policies/security.pdf", b"%PDF-1.7 sfdc test", zone=DataZone.RAW)
            assert meta.ref.version == "068SFDC0001"

            # Fetch ContentVersion objects via list_objects
            files_page = sf_edh.list_objects(zone=DataZone.RAW, prefix="policies")
            assert len(files_page.items) == 1
            assert files_page.items[0].ref.key == "policies/security.pdf"

            # Fetch Salesforce SObject records (Account) via read_records
            accounts = list(sf_edh.read_records(sf_settings.SF_TEST_SOBJECT, zone=DataZone.CURATED))
            assert [a["Name"] for a in accounts] == ["Acme Corp", "Globex Inc"]

        assert captured_oauth_form["client_id"] == ["test-connected-app-id"]
        assert captured_oauth_form["client_secret"] == ["test-connected-app-secret"]
        assert stored_versions["sf-rag-hub/raw/policies/security.pdf"] == b"%PDF-1.7 sfdc test"
    finally:
        server.shutdown()
        server.server_close()


def test_live_salesforce_org_when_env_configured():
    """Live Salesforce integration test using ``backend/.env.integration`` (or env vars).

    Connects to the configured Salesforce org via OAuth2 ``client_credentials``,
    verifies `/limits`, lists `ContentVersion` objects, and fetches records from
    `settings.SF_TEST_SOBJECT` (default: `Account`).
    """
    settings = get_integration_settings()
    if not (
        settings.SF_INSTANCE_URL
        and settings.SF_CLIENT_ID
        and settings.SF_CLIENT_SECRET.get_secret_value()
    ):
        pytest.skip(
            "Live Salesforce credentials (SF_INSTANCE_URL, SF_CLIENT_ID, SF_CLIENT_SECRET) "
            "are not populated in backend/.env.integration or environment variables"
        )

    with create_edh_connector(settings, strategy="salesforce") as sf_edh:
        assert sf_edh.ping() is True
        # 1. List ContentVersion objects in the namespace
        files_page = sf_edh.list_objects(zone=DataZone.RAW, page_size=5)
        assert isinstance(files_page.items, tuple)
        # 2. Query SObject records (default: Account) from Salesforce
        records = list(sf_edh.read_records(settings.SF_TEST_SOBJECT, zone=DataZone.CURATED))
        assert isinstance(records, list)


