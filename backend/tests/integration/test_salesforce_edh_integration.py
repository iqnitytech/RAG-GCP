"""Backend integration tests for ``SalesforceEdhStrategy`` via ``Settings`` and ``.env.integration``."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.connectors import create_edh_connector
from app.core.config import Settings, get_integration_settings
from app.models.edh_models import DataZone, compute_sha256


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

            files_page = sf_edh.list_objects(zone=DataZone.RAW, prefix="policies")
            assert len(files_page.items) == 1
            assert files_page.items[0].ref.key == "policies/security.pdf"

            accounts = list(sf_edh.read_records(sf_settings.SF_TEST_SOBJECT, zone=DataZone.CURATED))
            assert [a["Name"] for a in accounts] == ["Acme Corp", "Globex Inc"]

        assert captured_oauth_form["client_id"] == ["test-connected-app-id"]
        assert captured_oauth_form["client_secret"] == ["test-connected-app-secret"]
        assert stored_versions["sf-rag-hub/raw/policies/security.pdf"] == b"%PDF-1.7 sfdc test"
    finally:
        server.shutdown()
        server.server_close()


def test_live_salesforce_org_when_env_configured():
    """Live Salesforce integration test using ``backend/tests/integration/.env.integration``."""
    settings = get_integration_settings()
    if not (
        settings.SF_INSTANCE_URL
        and settings.SF_CLIENT_ID
        and settings.SF_CLIENT_SECRET.get_secret_value()
    ):
        pytest.skip(
            "Live Salesforce credentials (SF_INSTANCE_URL, SF_CLIENT_ID, SF_CLIENT_SECRET) "
            "are not populated in backend/tests/integration/.env.integration"
        )

    with create_edh_connector(settings, strategy="salesforce") as sf_edh:
        assert sf_edh.ping() is True
        files_page = sf_edh.list_objects(zone=DataZone.RAW, page_size=5)
        assert isinstance(files_page.items, tuple)
        records = list(sf_edh.read_records(settings.SF_TEST_SOBJECT, zone=DataZone.CURATED))
        assert isinstance(records, list)
