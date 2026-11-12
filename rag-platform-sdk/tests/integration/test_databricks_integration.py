"""Live HTTP and ``.env.integration`` integration tests for ``DatabricksEdhStrategy``."""

from __future__ import annotations

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
    make_databricks_oauth_token_getter,
)
from tests.integration.conftest import INTEGRATION_ENV_FILE, load_integration_env


def test_integration_databricks_uc_volumes_and_sql_over_live_http():
    """Verify DatabricksEdhStrategy + OAuth2 M2M over a real loopback HTTP server."""
    volume_files: dict[str, tuple[bytes, dict[str, str]]] = {}
    uc_tables: dict[str, list[dict[str, Any]]] = {}
    oauth_calls = 0

    class DatabricksHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, doc: Any) -> None:
            raw = json.dumps(doc).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_PUT(self) -> None:
            assert self.headers.get("Authorization") == "Bearer dbx-live-token-001"
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            path = unquote(urlsplit(self.path).path)
            vol_path = path[len("/api/2.0/fs/files") :]
            sha = self.headers.get("X-EDH-SHA256", compute_sha256(body))
            etag = f'"{sha[:32]}"'
            volume_files[vol_path] = (
                body,
                {
                    "Content-Type": self.headers.get("Content-Type", "application/octet-stream"),
                    "Content-Length": str(len(body)),
                    "X-EDH-SHA256": sha,
                    "ETag": etag,
                },
            )
            self.send_response(204)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            nonlocal oauth_calls
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            path = unquote(urlsplit(self.path).path)

            if path == "/oidc/v1/token":
                oauth_calls += 1
                form = parse_qs(body.decode("utf-8"))
                assert form.get("grant_type") == ["client_credentials"]
                assert form.get("scope") == ["all-apis"]
                self._send_json(200, {"access_token": "dbx-live-token-001", "token_type": "Bearer"})
                return

            assert self.headers.get("Authorization") == "Bearer dbx-live-token-001"
            if path == "/api/2.0/sql/statements":
                doc = json.loads(body.decode("utf-8"))
                stmt = doc.get("statement", "")
                if stmt.startswith("INSERT INTO "):
                    m_tbl = re.match(r"INSERT INTO ([A-Za-z0-9_.]+)", stmt)
                    tbl = m_tbl.group(1) if m_tbl else "main.default.rag_chunks"
                    for r in doc.get("records", []):
                        uc_tables.setdefault(tbl, []).append(dict(r))
                    self._send_json(200, {"statement_id": "stmt-1", "status": {"state": "SUCCEEDED"}})
                    return

                if stmt.startswith("SELECT * FROM "):
                    m_tbl = re.match(r"SELECT \* FROM ([A-Za-z0-9_.]+)", stmt)
                    tbl = m_tbl.group(1) if m_tbl else ""
                    rows = list(uc_tables.get(tbl, []))
                    for col, val in re.findall(r"([A-Za-z0-9_]+) = '([^']*)'", stmt):
                        rows = [r for r in rows if str(r.get(col)) == val]
                    cols = list(rows[0].keys()) if rows else []
                    data_array = [[r.get(c) for c in cols] for r in rows]
                    self._send_json(
                        200,
                        {
                            "statement_id": "stmt-2",
                            "status": {"state": "SUCCEEDED"},
                            "manifest": {"schema": {"columns": [{"name": c} for c in cols]}},
                            "result": {"data_array": data_array},
                        },
                    )
                    return

        def do_GET(self) -> None:
            assert self.headers.get("Authorization") == "Bearer dbx-live-token-001"
            path = unquote(urlsplit(self.path).path)
            if path == "/api/2.0/clusters/spark-versions":
                self._send_json(200, {"versions": [{"key": "15.4.x-scala2.12"}]})
                return
            if path.startswith("/api/2.0/fs/files/Volumes/"):
                vol_path = path[len("/api/2.0/fs/files") :]
                raw, hdrs = volume_files[vol_path]
                self.send_response(200)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)
                return
            if path.startswith("/api/2.0/fs/directories/Volumes/"):
                dir_prefix = path[len("/api/2.0/fs/directories") :].rstrip("/")
                contents = [
                    {
                        "path": vp,
                        "name": vp.rsplit("/", 1)[-1],
                        "is_dir": False,
                        "file_size": len(raw),
                        "content_type": hdrs.get("Content-Type", "application/octet-stream"),
                        "sha256": hdrs.get("X-EDH-SHA256", ""),
                        "etag": hdrs.get("ETag", ""),
                    }
                    for vp, (raw, hdrs) in sorted(volume_files.items())
                    if vp.startswith(dir_prefix)
                ]
                self._send_json(200, {"contents": contents, "next_page_token": None})
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), DatabricksHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token_getter = make_databricks_oauth_token_getter(
            workspace_url=base_url,
            client_id_getter=lambda: "dbx-sp-client-id",
            client_secret_getter=lambda: "dbx-sp-client-secret",
        )
        cfg = EdhConnectorConfig(
            strategy="databricks",
            namespace="lakehouse-edh",
            endpoint_url=base_url,
            auth_token_getter=token_getter,
        )
        with EdhConnector(cfg) as dbx:
            assert dbx.ping() is True

            doc_bytes = b"Databricks Unity Catalog Volume document for RAG"
            meta = dbx.put_bytes(
                "manuals/architecture.md",
                doc_bytes,
                zone=DataZone.RAW,
                options=EdhWriteOptions(content_type="text/markdown"),
            )
            assert meta.size_bytes == len(doc_bytes)
            assert dbx.get_bytes("manuals/architecture.md", zone=DataZone.RAW) == doc_bytes

            page = dbx.list_objects(zone=DataZone.RAW, prefix="manuals")
            assert len(page.items) == 1
            assert page.items[0].ref.key == "manuals/architecture.md"

            batch = EdhRecordBatch(
                dataset="rag_chunks",
                records=(
                    {"chunk_id": "dbx-1", "text": "Delta Lake ACID transactions"},
                    {"chunk_id": "dbx-2", "text": "Unity Catalog governance"},
                ),
                partition_values={"tenant_id": "acme"},
                zone=DataZone.CURATED,
            )
            dbx.write_records(batch)
            rows = list(
                dbx.read_records(
                    "rag_chunks",
                    zone=DataZone.CURATED,
                    partition_filter={"tenant_id": "acme"},
                )
            )
            assert [r["chunk_id"] for r in rows] == ["dbx-1", "dbx-2"]
            assert oauth_calls == 1
    finally:
        server.shutdown()
        server.server_close()


def test_live_databricks_workspace_from_integration_env():
    """Connect to a live Databricks workspace using ``tests/integration/.env.integration``."""
    env = load_integration_env()
    dbx_host = env.get("DATABRICKS_HOST", "")
    dbx_token = env.get("DATABRICKS_TOKEN", "")
    dbx_client_id = env.get("DATABRICKS_CLIENT_ID", "")
    dbx_client_secret = env.get("DATABRICKS_CLIENT_SECRET", "")
    dbx_catalog = env.get("DATABRICKS_CATALOG", "main")
    dbx_schema = env.get("DATABRICKS_SCHEMA", "default")
    dbx_warehouse = env.get("DATABRICKS_WAREHOUSE_ID", "default")
    dbx_table = env.get("DATABRICKS_TEST_TABLE", "rag_chunks")
    namespace = env.get("EDH_NAMESPACE", "crm-edh")

    if not dbx_host or not (dbx_token or (dbx_client_id and dbx_client_secret)):
        pytest.skip(
            f"Populate DATABRICKS_HOST and DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET) "
            f"in {INTEGRATION_ENV_FILE} to run live Databricks workspace integration tests"
        )

    token_getter = (
        (lambda: dbx_token)
        if dbx_token
        else make_databricks_oauth_token_getter(
            workspace_url=dbx_host,
            client_id_getter=lambda: dbx_client_id,
            client_secret_getter=lambda: dbx_client_secret,
        )
    )
    cfg = EdhConnectorConfig(
        strategy="databricks",
        namespace=namespace,
        endpoint_url=dbx_host,
        auth_token_getter=token_getter,
        default_headers={
            "X-Databricks-Catalog": dbx_catalog,
            "X-Databricks-Schema": dbx_schema,
            "X-Databricks-Warehouse-Id": dbx_warehouse,
        },
    )
    with EdhConnector(cfg) as dbx:
        assert dbx.ping() is True
        records = list(dbx.read_records(dbx_table, zone=DataZone.CURATED))
        assert isinstance(records, list)
