"""Unit and contract tests for ``DatabricksEdhStrategy`` and OAuth2 / Unity Catalog SQL guards."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    DatabricksEdhStrategy,
    EdhConfigError,
    EdhConnector,
    RawHttpResponse,
    make_databricks_oauth_token_getter,
)
from sdk.models.edh_models import EdhConnectorConfig, compute_sha256
from tests.unit.conftest import (
    assert_blob_crud_and_pagination_contract,
    assert_partitioned_records_contract,
    assert_strategy_implements_protocol,
)


class _FakeDatabricksServer:
    """In-memory Databricks Unity Catalog Volumes & SQL Statements simulator for contract tests."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawHttpResponse:
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/api/2.0/clusters/spark-versions" and method == "GET":
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                b'{"versions":[{"key":"15.4.x-scala2.12"}]}',
            )

        if path.startswith("/api/2.0/fs/files/Volumes/"):
            vol_path = path[len("/api/2.0/fs/files") :]
            if method == "PUT":
                overwrite = qs.get("overwrite", ["true"])[0] == "true"
                if not overwrite and vol_path in self.files:
                    return RawHttpResponse(409, {}, b"File already exists")
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
                self.files[vol_path] = (data, hdrs)
                return RawHttpResponse(204, {"etag": etag}, b"")

            if method == "GET":
                if vol_path not in self.files:
                    return RawHttpResponse(404, {}, b"Not Found")
                data, hdrs = self.files[vol_path]
                return RawHttpResponse(200, hdrs, data)

            if method == "HEAD":
                if vol_path not in self.files:
                    return RawHttpResponse(404, {}, b"")
                _, hdrs = self.files[vol_path]
                return RawHttpResponse(200, hdrs, b"")

            if method == "DELETE":
                if vol_path not in self.files:
                    return RawHttpResponse(404, {}, b"")
                del self.files[vol_path]
                return RawHttpResponse(204, {}, b"")

        if path.startswith("/api/2.0/fs/directories/Volumes/") and method == "GET":
            dir_prefix = path[len("/api/2.0/fs/directories") :].rstrip("/")
            page_size = int(qs.get("page_size", ["100"])[0])
            page_token = qs.get("page_token", [None])[0]

            matched = [
                (vp, payload, hdrs)
                for vp, (payload, hdrs) in sorted(self.files.items())
                if vp.startswith(dir_prefix)
            ]
            start = int(page_token) if page_token and page_token.isdigit() else 0
            sliced = matched[start : start + page_size]
            next_tok = str(start + page_size) if (start + page_size) < len(matched) else None
            contents = [
                {
                    "path": vp,
                    "name": vp.rsplit("/", 1)[-1],
                    "is_dir": False,
                    "file_size": len(p),
                    "content_type": h.get("content-type", "application/octet-stream"),
                    "sha256": h.get("x-edh-sha256", ""),
                    "etag": h.get("etag", ""),
                }
                for vp, p, h in sliced
            ]
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"contents": contents, "next_page_token": next_tok}).encode("utf-8"),
            )

        if path == "/api/2.0/sql/statements" and method == "POST":
            doc = json.loads((body or b"{}").decode("utf-8"))
            stmt = doc.get("statement", "")
            if stmt.startswith("INSERT INTO "):
                m_tbl = re.match(r"INSERT INTO ([A-Za-z0-9_.]+)", stmt)
                tbl = m_tbl.group(1) if m_tbl else "main.default.unknown"
                for r in doc.get("records", []):
                    self.tables.setdefault(tbl, []).append(dict(r))
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps({"statement_id": "stmt-insert-1", "status": {"state": "SUCCEEDED"}}).encode("utf-8"),
                )

            if stmt.startswith("SELECT * FROM "):
                m_tbl = re.match(r"SELECT \* FROM ([A-Za-z0-9_.]+)", stmt)
                tbl = m_tbl.group(1) if m_tbl else ""
                rows = list(self.tables.get(tbl, []))
                for col, val in re.findall(r"([A-Za-z0-9_]+) = '([^']*)'", stmt):
                    rows = [r for r in rows if str(r.get(col)) == val]
                cols = list(rows[0].keys()) if rows else []
                data_array = [[r.get(c) for c in cols] for r in rows]
                resp_doc = {
                    "statement_id": "stmt-select-1",
                    "status": {"state": "SUCCEEDED"},
                    "manifest": {"schema": {"columns": [{"name": c} for c in cols]}},
                    "result": {"data_array": data_array},
                }
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps(resp_doc).encode("utf-8"),
                )

        return RawHttpResponse(400, {}, b"Unsupported Databricks endpoint")


@pytest.fixture
def databricks_connector() -> EdhConnector:
    server = _FakeDatabricksServer()
    cfg = EdhConnectorConfig(
        strategy="databricks",
        namespace="rag-enterprise",
        endpoint_url="https://adb-1234567890.12.azuredatabricks.net",
        auth_token_getter=lambda: "dapi0123456789abcdef",
    )
    return EdhConnector(cfg, strategy=DatabricksEdhStrategy(cfg, transport=server))


def test_databricks_strategy_implements_protocol(databricks_connector: EdhConnector):
    assert_strategy_implements_protocol(databricks_connector)


def test_databricks_strategy_blob_crud_and_pagination(databricks_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(databricks_connector)


def test_databricks_strategy_partitioned_records(databricks_connector: EdhConnector):
    assert_partitioned_records_contract(databricks_connector)


def test_databricks_oauth_token_getter_and_sql_chunk_pagination_and_injection_guard():
    oauth_calls = 0

    def dbx_oauth_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        nonlocal oauth_calls
        oauth_calls += 1
        assert method == "POST"
        assert url == "https://adb-123.azuredatabricks.net/oidc/v1/token"
        form = parse_qs((body or b"").decode("utf-8"))
        assert form.get("scope") == ["all-apis"]
        if form.get("client_secret") == ["bad"]:
            return RawHttpResponse(401, {}, b'{"error":"invalid_client"}')
        return RawHttpResponse(200, {"content-type": "application/json"}, b'{"access_token":"dbx-m2m-tok"}')

    getter = make_databricks_oauth_token_getter(
        workspace_url="https://adb-123.azuredatabricks.net",
        client_id_getter=lambda: "sp-id",
        client_secret_getter=lambda: "sp-secret",
        transport=dbx_oauth_transport,
    )
    assert getter() == "dbx-m2m-tok"
    assert getter() == "dbx-m2m-tok"
    assert oauth_calls == 1

    def paged_sql_transport(method, url, headers, body, timeout) -> RawHttpResponse:
        if url.endswith("/api/2.0/sql/statements") and method == "POST":
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "statement_id": "stmt-99",
                        "status": {"state": "SUCCEEDED"},
                        "manifest": {"schema": {"columns": [{"name": "chunk_id"}, {"name": "text"}]}},
                        "result": {
                            "data_array": [["c-1", "First chunk"]],
                            "next_chunk_internal_link": "/api/2.0/sql/statements/stmt-99/result/chunks/1",
                        },
                    }
                ).encode("utf-8"),
            )
        if url.endswith("/api/2.0/sql/statements/stmt-99/result/chunks/1") and method == "GET":
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"data_array": [["c-2", "Second chunk"]]}).encode("utf-8"),
            )
        return RawHttpResponse(404, {}, b"")

    cfg = EdhConnectorConfig(
        strategy="unity_catalog",
        namespace="dbx-vol",
        endpoint_url="https://adb-123.azuredatabricks.net",
        auth_token_getter=getter,
    )
    conn = EdhConnector(cfg, strategy=DatabricksEdhStrategy(cfg, transport=paged_sql_transport))
    rows = list(conn.read_records("rag_chunks", partition_filter={"tenant_id": "acme"}))
    assert rows == [
        {"chunk_id": "c-1", "text": "First chunk"},
        {"chunk_id": "c-2", "text": "Second chunk"},
    ]

    with pytest.raises(EdhConfigError):
        list(conn.read_records("rag_chunks; DROP TABLE"))

    with pytest.raises(EdhConfigError):
        list(conn.read_records("rag_chunks", partition_filter={"tenant_id OR 1=1": "acme"}))
