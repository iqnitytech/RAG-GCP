"""Unit and contract tests for ``SalesforceEdhStrategy`` and OAuth2 / SOQL guards."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    EdhAuthError,
    EdhConfigError,
    EdhConnector,
    RawHttpResponse,
    SalesforceEdhStrategy,
    make_salesforce_oauth_token_getter,
)
from sdk.models.edh_models import EdhConnectorConfig, compute_sha256
from tests.unit.conftest import (
    assert_blob_crud_and_pagination_contract,
    assert_partitioned_records_contract,
    assert_strategy_implements_protocol,
)


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


@pytest.fixture
def salesforce_connector() -> EdhConnector:
    server = _FakeSalesforceServer()
    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace="rag-enterprise",
        endpoint_url="https://acme.my.salesforce.com",
        auth_token_getter=lambda: "00Dxx0000001gPL!AQ0AQ...",
    )
    return EdhConnector(cfg, strategy=SalesforceEdhStrategy(cfg, transport=server))


def test_salesforce_strategy_implements_protocol(salesforce_connector: EdhConnector):
    assert_strategy_implements_protocol(salesforce_connector)


def test_salesforce_strategy_blob_crud_and_pagination(salesforce_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(salesforce_connector)


def test_salesforce_strategy_partitioned_records(salesforce_connector: EdhConnector):
    assert_partitioned_records_contract(salesforce_connector)


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
    assert calls == 1

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
        strategy="sfdc",
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
