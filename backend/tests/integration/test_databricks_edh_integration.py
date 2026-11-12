"""Backend integration tests for ``DatabricksEdhStrategy`` via ``Settings`` and ``.env.integration``."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.connectors import create_edh_connector
from app.core.config import Settings, get_integration_settings
from app.models.edh_models import DataZone


def test_integration_databricks_connector_from_settings_and_env(monkeypatch):
    """Verify DATABRICKS_HOST, DATABRICKS_CLIENT_ID, and DATABRICKS_CLIENT_SECRET via Settings."""
    captured_oauth_form: dict[str, list[str]] = {}
    stored_files: dict[str, bytes] = {}

    class LoopbackDatabricksHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: Any) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_PUT(self) -> None:
            assert self.headers.get("Authorization") == "Bearer dbx-oauth-tok-888"
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            path = unquote(urlsplit(self.path).path)
            vol_path = path[len("/api/2.0/fs/files") :]
            stored_files[vol_path] = body
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            path = unquote(urlsplit(self.path).path)
            if path == "/oidc/v1/token":
                captured_oauth_form.update(parse_qs(body.decode("utf-8")))
                self._send_json(200, {"access_token": "dbx-oauth-tok-888", "token_type": "Bearer"})
                return

            assert self.headers.get("Authorization") == "Bearer dbx-oauth-tok-888"
            if path == "/api/2.0/sql/statements":
                self._send_json(
                    200,
                    {
                        "statement_id": "stmt-dbx-1",
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "chunk_id"}, {"name": "title"}]}},
                        "result": {"data_array": [["c-101", "Unity Catalog Guide"]]},
                    },
                )
                return

        def do_GET(self) -> None:
            assert self.headers.get("Authorization") == "Bearer dbx-oauth-tok-888"
            path = unquote(urlsplit(self.path).path)
            if path == "/api/2.0/clusters/spark-versions":
                self._send_json(200, {"versions": [{"key": "15.4.x-scala2.12"}]})
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackDatabricksHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("DATABRICKS_HOST", base_url)
        monkeypatch.setenv("DATABRICKS_CLIENT_ID", "test-dbx-sp-id")
        monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "test-dbx-sp-secret")
        monkeypatch.setenv("DATABRICKS_CATALOG", "enterprise_cat")
        monkeypatch.setenv("DATABRICKS_SCHEMA", "rag_schema")
        monkeypatch.setenv("DATABRICKS_TEST_TABLE", "rag_chunks")

        dbx_settings = Settings(_env_file=None, gcs_document_bucket="dbx_vol")
        with create_edh_connector(dbx_settings, strategy="databricks") as dbx_edh:
            assert dbx_edh.ping() is True
            dbx_edh.put_bytes("docs/guide.pdf", b"%PDF-1.7 dbx test", zone=DataZone.RAW)
            rows = list(dbx_edh.read_records(dbx_settings.DATABRICKS_TEST_TABLE, zone=DataZone.CURATED))
            assert rows == [{"chunk_id": "c-101", "title": "Unity Catalog Guide"}]

        assert captured_oauth_form["client_id"] == ["test-dbx-sp-id"]
        assert captured_oauth_form["client_secret"] == ["test-dbx-sp-secret"]
        assert stored_files["/Volumes/enterprise_cat/rag_schema/dbx_vol/raw/docs/guide.pdf"] == b"%PDF-1.7 dbx test"
    finally:
        server.shutdown()
        server.server_close()


def test_live_databricks_workspace_when_env_configured():
    """Live Databricks integration test using ``backend/tests/integration/.env.integration``."""
    settings = get_integration_settings()
    has_auth = bool(
        settings.DATABRICKS_TOKEN.get_secret_value()
        or (settings.DATABRICKS_CLIENT_ID and settings.DATABRICKS_CLIENT_SECRET.get_secret_value())
    )
    if not (settings.DATABRICKS_HOST and has_auth):
        pytest.skip(
            "Live Databricks credentials (DATABRICKS_HOST + DATABRICKS_TOKEN or "
            "DATABRICKS_CLIENT_ID/SECRET) are not populated in .env.integration"
        )

    with create_edh_connector(settings, strategy="databricks") as dbx_edh:
        assert dbx_edh.ping() is True
        records = list(dbx_edh.read_records(settings.DATABRICKS_TEST_TABLE, zone=DataZone.CURATED))
        assert isinstance(records, list)
