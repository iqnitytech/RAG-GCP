"""Unit and contract tests for ``SalesforceEdhStrategy`` and OAuth2 / SOQL guards."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    ApiDrivenEdhStrategy,
    ApiHubErrorConstants,
    AsyncEdhConnector,
    DataLakeErrorConstants,
    DataLakeStrategy,
    DatabricksEdhStrategy,
    DatabricksErrorConstants,
    EdhAuthError,
    EdhConfigError,
    EdhConflictError,
    EdhConnector,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhTransientError,
    InMemoryEdhStrategy,
    MemoryErrorConstants,
    ObjectStorageErrorConstants,
    ObjectStorageHubStrategy,
    RawHttpResponse,
    SalesforceEdhStrategy,
    SalesforceErrorConstants,
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
        self.bulk_query_jobs: dict[str, dict[str, Any]] = {}
        self.bulk_ingest_jobs: dict[str, dict[str, Any]] = {}
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

            batch_hdr = headers.get("Sforce-Query-Options", "")
            m_batch = re.search(r"batchSize=(\d+)", batch_hdr)
            batch_size = int(m_batch.group(1)) if m_batch else len(rows) or 1
            if len(rows) > batch_size:
                page = rows[:batch_size]
                rest = rows[batch_size:]
                self._seq += 1
                cursor_id = f"cursor-{self._seq}"
                self.sobject_rows[f"__cursor_{cursor_id}"] = rest
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps(
                        {
                            "totalSize": len(rows),
                            "done": False,
                            "nextRecordsUrl": f"/services/data/v60.0/query/{cursor_id}",
                            "records": page,
                        }
                    ).encode("utf-8"),
                )
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"totalSize": len(rows), "done": True, "records": rows}).encode("utf-8"),
            )

        if "/query/cursor-" in path and method == "GET":
            cursor_id = path.rsplit("/", 1)[1]
            rest = self.sobject_rows.pop(f"__cursor_{cursor_id}", [])
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"totalSize": len(rest), "done": True, "records": rest}).encode("utf-8"),
            )

        # 1. Describe Schema (global /sobjects or /sobjects/<SObject>/describe)
        if path.endswith("/sobjects") and method == "GET":
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "encoding": "UTF-8",
                        "maxBatchSize": 200,
                        "sobjects": [
                            {"name": "Account", "label": "Account", "queryable": True},
                            {"name": "Knowledge__kav", "label": "Knowledge", "queryable": True},
                        ],
                    }
                ).encode("utf-8"),
            )

        m_desc = re.search(r"/sobjects/([A-Za-z0-9_]+)/describe$", path)
        if m_desc and method == "GET":
            sobj = m_desc.group(1)
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "name": sobj,
                        "label": sobj,
                        "queryable": True,
                        "fields": [
                            {"name": "Id", "type": "id"},
                            {"name": "Name", "type": "string"},
                            {"name": "External_Id__c", "type": "string", "externalId": True},
                        ],
                    }
                ).encode("utf-8"),
            )

        # 4, 5, 6, 7, 8. SObject CRUD + Upsert
        m_sobj_create = re.search(r"/sobjects/([A-Za-z0-9_]+)$", path)
        if m_sobj_create and method == "POST":
            sobj = m_sobj_create.group(1)
            doc = json.loads((body or b"{}").decode("utf-8"))
            self._seq += 1
            rec_id = f"001000000000{self._seq:03d}"
            stored = {"attributes": {"type": sobj}, "Id": rec_id, **doc}
            self.sobject_rows.setdefault(sobj, []).append(stored)
            return RawHttpResponse(
                201,
                {"content-type": "application/json"},
                json.dumps({"id": rec_id, "success": True, "errors": []}).encode("utf-8"),
            )

        m_sobj_upsert = re.search(r"/sobjects/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)/([^/]+)$", path)
        if m_sobj_upsert and method == "PATCH":
            sobj, ext_field, ext_val = m_sobj_upsert.group(1), m_sobj_upsert.group(2), m_sobj_upsert.group(3)
            doc = json.loads((body or b"{}").decode("utf-8"))
            rows = self.sobject_rows.setdefault(sobj, [])
            for existing in rows:
                if str(existing.get(ext_field)) == ext_val:
                    existing.update(doc)
                    return RawHttpResponse(
                        200,
                        {"content-type": "application/json"},
                        json.dumps({"id": existing["Id"], "success": True, "created": False, "errors": []}).encode("utf-8"),
                    )
            self._seq += 1
            rec_id = f"001000000000{self._seq:03d}"
            stored = {"attributes": {"type": sobj}, "Id": rec_id, ext_field: ext_val, **doc}
            rows.append(stored)
            return RawHttpResponse(
                201,
                {"content-type": "application/json"},
                json.dumps({"id": rec_id, "success": True, "created": True, "errors": []}).encode("utf-8"),
            )

        m_sobj_id = re.search(r"/sobjects/([A-Za-z0-9_]+)/([^/]+)$", path)
        if m_sobj_id:
            sobj, rec_id = m_sobj_id.group(1), m_sobj_id.group(2)
            rows = self.sobject_rows.setdefault(sobj, [])
            matched = next((r for r in rows if r.get("Id") == rec_id), None)
            if method == "GET":
                if not matched:
                    return RawHttpResponse(404, {}, b"Record Not Found")
                fields_param = qs.get("fields", [""])[0]
                if fields_param:
                    wanted = {"Id", "attributes", *[f.strip() for f in fields_param.split(",") if f.strip()]}
                    filtered = {k: v for k, v in matched.items() if k in wanted}
                    return RawHttpResponse(200, {"content-type": "application/json"}, json.dumps(filtered).encode("utf-8"))
                return RawHttpResponse(200, {"content-type": "application/json"}, json.dumps(matched).encode("utf-8"))
            if method == "PATCH":
                if not matched:
                    return RawHttpResponse(404, {}, b"Record Not Found")
                doc = json.loads((body or b"{}").decode("utf-8"))
                matched.update(doc)
                return RawHttpResponse(204, {}, b"")
            if method == "DELETE":
                if not matched:
                    return RawHttpResponse(404, {}, b"Record Not Found")
                rows.remove(matched)
                return RawHttpResponse(204, {}, b"")

        # 9. BulkRead (Bulk API 2.0 Query)
        if path.endswith("/jobs/query") and method == "POST":
            doc = json.loads((body or b"{}").decode("utf-8"))
            self._seq += 1
            job_id = f"750xx000000{self._seq:03d}"
            self.bulk_query_jobs[job_id] = {
                "id": job_id,
                "operation": doc.get("operation", "query"),
                "query": doc.get("query", ""),
                "state": "UploadComplete",
                "polls": 0,
            }
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(self.bulk_query_jobs[job_id]).encode("utf-8"),
            )

        m_bquery_res = re.search(r"/jobs/query/([^/]+)/results$", path)
        if m_bquery_res and method == "GET":
            job_id = m_bquery_res.group(1)
            job = self.bulk_query_jobs.get(job_id)
            if not job:
                return RawHttpResponse(404, {}, b"Job Not Found")
            soql = job["query"]
            m_from = re.search(r"FROM ([A-Za-z0-9_]+)", soql)
            sobj_type = m_from.group(1) if m_from else ""
            rows = list(self.sobject_rows.get(sobj_type, []))
            for col, val in re.findall(r"([A-Za-z0-9_]+) = '([^']*)'", soql):
                rows = [r for r in rows if str(r.get(col)) == val]

            locator = qs.get("locator", [""])[0]
            if not locator and len(rows) > 1:
                sliced = rows[:1]
                next_loc = "loc-page-2"
            elif locator == "loc-page-2":
                sliced = rows[1:]
                next_loc = "null"
            else:
                sliced = rows
                next_loc = "null"

            buf = io.StringIO()
            fieldnames = ["Id", "Name"]
            for r in sliced:
                for k in r.keys():
                    if k != "attributes" and k not in fieldnames:
                        fieldnames.append(k)
            writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for r in sliced:
                writer.writerow({k: r.get(k, "") for k in fieldnames})
            return RawHttpResponse(
                200,
                {"content-type": "text/csv", "Sforce-Locator": next_loc},
                buf.getvalue().encode("utf-8"),
            )

        m_bquery_job = re.search(r"/jobs/query/([^/]+)$", path)
        if m_bquery_job and method == "GET":
            job_id = m_bquery_job.group(1)
            job = self.bulk_query_jobs.get(job_id)
            if not job:
                return RawHttpResponse(404, {}, b"Job Not Found")
            job["polls"] += 1
            job["state"] = "JobComplete"
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(job).encode("utf-8"),
            )

        # 10. BulkWrite (Bulk API 2.0 Ingest)
        if path.endswith("/jobs/ingest") and method == "POST":
            doc = json.loads((body or b"{}").decode("utf-8"))
            self._seq += 1
            job_id = f"751xx000000{self._seq:03d}"
            self.bulk_ingest_jobs[job_id] = {
                "id": job_id,
                "object": doc.get("object", "Account"),
                "operation": doc.get("operation", "insert"),
                "externalIdFieldName": doc.get("externalIdFieldName"),
                "state": "Open",
                "csv_data": "",
                "successful_rows": [],
            }
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(self.bulk_ingest_jobs[job_id]).encode("utf-8"),
            )

        m_bingest_batch = re.search(r"/jobs/ingest/([^/]+)/batches$", path)
        if m_bingest_batch and method == "PUT":
            job_id = m_bingest_batch.group(1)
            job = self.bulk_ingest_jobs.get(job_id)
            if not job:
                return RawHttpResponse(404, {}, b"Job Not Found")
            job["csv_data"] = (body or b"").decode("utf-8")
            return RawHttpResponse(201, {}, b"")

        m_bingest_succ = re.search(r"/jobs/ingest/([^/]+)/successfulResults$", path)
        if m_bingest_succ and method == "GET":
            job_id = m_bingest_succ.group(1)
            job = self.bulk_ingest_jobs.get(job_id)
            if not job:
                return RawHttpResponse(404, {}, b"Job Not Found")
            buf = io.StringIO()
            succ = job["successful_rows"]
            if succ:
                writer = csv.DictWriter(buf, fieldnames=list(succ[0].keys()), lineterminator="\n")
                writer.writeheader()
                for r in succ:
                    writer.writerow(r)
            return RawHttpResponse(200, {"content-type": "text/csv"}, buf.getvalue().encode("utf-8"))

        m_bingest_fail = re.search(r"/jobs/ingest/([^/]+)/failedResults$", path)
        if m_bingest_fail and method == "GET":
            return RawHttpResponse(200, {"content-type": "text/csv"}, b"sf__Id,sf__Error\n")

        m_bingest_job = re.search(r"/jobs/ingest/([^/]+)$", path)
        if m_bingest_job:
            job_id = m_bingest_job.group(1)
            job = self.bulk_ingest_jobs.get(job_id)
            if not job:
                return RawHttpResponse(404, {}, b"Job Not Found")
            if method == "PATCH":
                doc = json.loads((body or b"{}").decode("utf-8"))
                job["state"] = doc.get("state", "UploadComplete")
                # Process CSV rows into sobject_rows
                reader = csv.DictReader(io.StringIO(job["csv_data"]))
                sobj = job["object"]
                for row in reader:
                    self._seq += 1
                    rec_id = row.get("Id") or f"001bulk0000{self._seq:03d}"
                    stored = {"attributes": {"type": sobj}, "Id": rec_id, **dict(row)}
                    self.sobject_rows.setdefault(sobj, []).append(stored)
                    job["successful_rows"].append(
                        {"sf__Id": rec_id, "sf__Created": "true", **dict(row)}
                    )
                job["numberRecordsProcessed"] = len(job["successful_rows"])
                job["numberRecordsFailed"] = 0
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps(job).encode("utf-8"),
                )
            if method == "GET":
                job["state"] = "JobComplete"
                return RawHttpResponse(
                    200,
                    {"content-type": "application/json"},
                    json.dumps(job).encode("utf-8"),
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


def test_salesforce_1_describe_schema(salesforce_connector: EdhConnector):
    global_schema = salesforce_connector.describe_schema()
    assert "sobjects" in global_schema
    assert any(obj["name"] == "Account" for obj in global_schema["sobjects"])

    account_schema = salesforce_connector.DescribeSchema("Account")
    assert account_schema["name"] == "Account"
    assert any(f["name"] == "External_Id__c" for f in account_schema["fields"])

    with pytest.raises(EdhConfigError):
        salesforce_connector.describe_schema("Account; DROP")


def test_salesforce_2_query_and_3_paginated_query(salesforce_connector: EdhConnector):
    salesforce_connector.Create("Account", {"Name": "Acme Alpha", "Industry": "Tech"})
    salesforce_connector.Create("Account", {"Name": "Acme Beta", "Industry": "Tech"})
    salesforce_connector.Create("Account", {"Name": "Other Corp", "Industry": "Finance"})

    # 2. Query (structured parameters + raw SOQL + PascalCase alias)
    tech_rows = salesforce_connector.query(
        "Account",
        fields=["Id", "Name", "Industry"],
        where={"Industry": "Tech"},
        limit=10,
    )
    assert len(tech_rows) == 2
    assert {r["Name"] for r in tech_rows} == {"Acme Alpha", "Acme Beta"}
    assert "attributes" not in tech_rows[0]

    raw_rows = salesforce_connector.Query(
        "SELECT Id, Name FROM Account WHERE Industry = 'Finance'"
    )
    assert len(raw_rows) == 1
    assert raw_rows[0]["Name"] == "Other Corp"

    # 3. PaginatedQuery across nextRecordsUrl pages
    pages = list(
        salesforce_connector.PaginatedQuery(
            "Account",
            fields=["Id", "Name"],
            where={"Industry": "Tech"},
            batch_size=1,
        )
    )
    assert len(pages) == 2
    assert len(pages[0]) == 1
    assert len(pages[1]) == 1
    assert {pages[0][0]["Name"], pages[1][0]["Name"]} == {"Acme Alpha", "Acme Beta"}


def test_salesforce_4_get_5_create_6_update_7_upsert_8_delete(salesforce_connector: EdhConnector):
    # 5. Create
    created = salesforce_connector.Create("Account", {"Name": "Initial Corp", "Industry": "AI"})
    assert created["success"] is True
    rec_id = created["id"]

    # 4. GetRecordById
    rec = salesforce_connector.GetRecordById("Account", rec_id, fields=["Name", "Industry"])
    assert rec["Id"] == rec_id
    assert rec["Name"] == "Initial Corp"
    assert rec["Industry"] == "AI"

    # 6. Update
    updated = salesforce_connector.Update("Account", rec_id, {"Name": "Updated Corp"})
    assert updated is True
    rec_after = salesforce_connector.get_record_by_id("Account", rec_id)
    assert rec_after["Name"] == "Updated Corp"

    # 7. Upsert (insert new + update existing by external ID)
    upserted_new = salesforce_connector.Upsert(
        "Account",
        "External_Id__c",
        "EXT-100",
        {"Name": "Upserted New", "Industry": "Cloud"},
    )
    assert upserted_new["success"] is True
    assert upserted_new["created"] is True
    ext_rec_id = upserted_new["id"]

    upserted_existing = salesforce_connector.upsert(
        "Account",
        "External_Id__c",
        "EXT-100",
        {"Name": "Upserted Modified"},
    )
    assert upserted_existing["id"] == ext_rec_id
    assert upserted_existing["created"] is False
    assert salesforce_connector.get_record_by_id("Account", ext_rec_id)["Name"] == "Upserted Modified"

    # 8. Delete (both Delete / delete_record and delete(sobject_name, record_id))
    assert salesforce_connector.Delete("Account", rec_id) is True
    assert salesforce_connector.Delete("Account", rec_id) is False
    assert salesforce_connector.delete("Account", ext_rec_id) is True


def test_salesforce_9_bulk_read_and_10_bulk_write(salesforce_connector: EdhConnector):
    # 10. BulkWrite (Bulk API 2.0 Ingest)
    write_res = salesforce_connector.BulkWrite(
        "Account",
        [
            {"Name": "Bulk Corp 1", "Industry": "Manufacturing"},
            {"Name": "Bulk Corp 2", "Industry": "Manufacturing"},
        ],
        operation="insert",
        poll_interval_seconds=0.0,
    )
    assert write_res["state"] == "JobComplete"
    assert write_res["number_records_processed"] == 2
    assert write_res["number_records_failed"] == 0
    assert len(write_res["successful_results"]) == 2

    # 9. BulkRead (Bulk API 2.0 Query with Sforce-Locator multi-chunk pagination)
    read_rows = salesforce_connector.BulkRead(
        "Account",
        fields=["Id", "Name", "Industry"],
        where={"Industry": "Manufacturing"},
        poll_interval_seconds=0.0,
    )
    assert len(read_rows) == 2
    assert {r["Name"] for r in read_rows} == {"Bulk Corp 1", "Bulk Corp 2"}


def test_salesforce_create_record_and_delete_record_and_connection_lifecycle(
    salesforce_connector: EdhConnector,
):
    strategy: SalesforceEdhStrategy = salesforce_connector.strategy  # type: ignore[assignment]
    pool = strategy.connection_pool
    assert salesforce_connector.is_open is False

    # Calling outside a 'with' block automatically opens and closes the connection when done
    created = salesforce_connector.createRecord(
        "Account",
        {"Name": "Lifecycle Account", "Industry": "Energy"},
    )
    assert created["success"] is True
    rec_id = created["id"]
    assert salesforce_connector.is_open is False
    assert pool.open_count >= 1
    assert pool.close_count >= 1

    # Explicit 'with' block keeps connection open across multiple calls and closes on exit
    with salesforce_connector as sf:
        assert sf.is_open is True
        fetched = sf.getRecordById("Account", rec_id)
        assert fetched["Name"] == "Lifecycle Account"
        assert sf.deleteRecord("Account", rec_id) is True
        assert sf.DeleteRecord("Account", rec_id) is False
        assert sf.is_open is True

    assert salesforce_connector.is_open is False


def test_salesforce_async_all_functionalities_and_connection_lifecycle():
    server = _FakeSalesforceServer()
    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace="rag-enterprise",
        endpoint_url="https://acme.my.salesforce.com",
        auth_token_getter=lambda: "00Dxx0000001gPL!AQ0AQ...",
    )
    strategy = SalesforceEdhStrategy(cfg, transport=server)
    async_sf = AsyncEdhConnector(cfg, strategy=strategy)

    async def _run_async_suite() -> None:
        assert async_sf.is_open is False

        # One-off async call outside 'async with' opens and closes automatically
        assert await async_sf.ping() is True
        assert async_sf.is_open is False

        async with async_sf as sf:
            assert sf.is_open is True

            # 1. DescribeSchema
            schema = await sf.describeSchema("Account")
            assert schema["name"] == "Account"

            # 5. createRecord / CreateRecord
            c1 = await sf.createRecord("Account", {"Name": "Async Corp 1", "Industry": "AI"})
            c2 = await sf.CreateRecord("Account", {"Name": "Async Corp 2", "Industry": "AI"})
            rec_id_1 = c1["id"]
            rec_id_2 = c2["id"]

            # 4. getRecordById
            fetched = await sf.getRecordById("Account", rec_id_1, fields=["Name", "Industry"])
            assert fetched["Name"] == "Async Corp 1"

            # 6. updateRecord
            assert await sf.updateRecord("Account", rec_id_1, {"Name": "Async Corp 1 Updated"}) is True

            # 7. upsertRecord
            upserted = await sf.upsertRecord(
                "Account",
                "External_Id__c",
                "ASYNC-EXT-1",
                {"Name": "Async Upserted", "Industry": "AI"},
            )
            assert upserted["success"] is True

            # 2. Query
            queried = await sf.query("Account", where={"Industry": "AI"})
            assert len(queried) == 3

            # 3. PaginatedQuery
            pages = [
                page
                async for page in sf.paginatedQuery(
                    "Account",
                    where={"Industry": "AI"},
                    batch_size=2,
                )
            ]
            assert len(pages) == 2

            # 10. BulkWrite & 9. BulkRead
            bw = await sf.bulkWrite(
                "Account",
                [{"Name": "Async Bulk 1", "Industry": "Robotics"}],
                operation="insert",
                poll_interval_seconds=0.0,
            )
            assert bw["state"] == "JobComplete"

            br = await sf.bulkRead(
                "Account",
                where={"Industry": "Robotics"},
                poll_interval_seconds=0.0,
            )
            assert len(br) == 1
            assert br[0]["Name"] == "Async Bulk 1"

            # 8. deleteRecord / DeleteRecord
            assert await sf.deleteRecord("Account", rec_id_1) is True
            assert await sf.DeleteRecord("Account", rec_id_2) is True
            assert await sf.deleteRecord("Account", upserted["id"]) is True

        # Connection must be closed after exiting 'async with'
        assert async_sf.is_open is False

    asyncio.run(_run_async_suite())


def test_salesforce_error_constants_and_structured_logging(caplog: pytest.LogCaptureFixture):
    """Verify Salesforce REST errors and validation errors use SalesforceErrorConstants and log properly."""
    import logging

    caplog.set_level(logging.WARNING)

    def error_transport(method: str, url: str, headers: Any, body: Any, timeout: float) -> RawHttpResponse:
        if "MALFORMED" in url:
            return RawHttpResponse(
                400,
                {"content-type": "application/json"},
                b'[{"errorCode": "MALFORMED_QUERY", "message": "unexpected token: FROM"}]',
            )
        if "DUPLICATE" in url:
            return RawHttpResponse(
                400,
                {"content-type": "application/json"},
                b'[{"errorCode": "DUPLICATE_VALUE", "message": "duplicate value found: External_Id__c"}]',
            )
        if "LIMIT_EXCEEDED" in url:
            return RawHttpResponse(
                403,
                {"content-type": "application/json"},
                b'[{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "TotalRequests Limit exceeded."}]',
            )
        if "LOCK_ROW" in url:
            return RawHttpResponse(
                500,
                {"content-type": "application/json"},
                b'[{"errorCode": "UNABLE_TO_LOCK_ROW", "message": "unable to obtain exclusive access"}]',
            )
        return RawHttpResponse(
            404,
            {"content-type": "application/json"},
            b'[{"errorCode": "NOT_FOUND", "message": "Provided external ID field does not exist"}]',
        )

    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace="rag-enterprise",
        endpoint_url="https://acme.my.salesforce.com",
    )
    strategy = SalesforceEdhStrategy(cfg, transport=error_transport)

    # 1. Validation error (empty fields on createRecord) -> SF_VALIDATION_EMPTY_RECORD_FIELDS
    with pytest.raises(EdhConfigError) as exc_empty:
        strategy.createRecord("Account", {})
    assert exc_empty.value.error_code == SalesforceErrorConstants.ERR_EMPTY_RECORD_FIELDS
    assert exc_empty.value.public_message == SalesforceErrorConstants.PUBLIC_VALIDATION_ERROR

    # 2. Validation error (empty record_id on deleteRecord) -> SF_VALIDATION_INVALID_RECORD_ID
    with pytest.raises(EdhConfigError) as exc_id:
        strategy.deleteRecord("Account", "   ")
    assert exc_id.value.error_code == SalesforceErrorConstants.ERR_INVALID_RECORD_ID

    # 3. Salesforce 400 MALFORMED_QUERY -> EdhConfigError with SF_SOQL_MALFORMED_QUERY
    with pytest.raises(EdhConfigError) as exc_soql:
        strategy.query("Account", where={"Name": "MALFORMED"})
    assert exc_soql.value.error_code == SalesforceErrorConstants.ERR_MALFORMED_QUERY
    assert exc_soql.value.upstream_status == 400
    assert "unexpected token: FROM" in str(exc_soql.value)

    # 4. Salesforce 400 DUPLICATE_VALUE -> EdhConflictError with SF_CONFLICT_DUPLICATE_VALUE
    with pytest.raises(EdhConflictError) as exc_dup:
        strategy.getRecordById("Account", "DUPLICATE")
    assert exc_dup.value.error_code == SalesforceErrorConstants.ERR_DUPLICATE_VALUE
    assert exc_dup.value.public_message == SalesforceErrorConstants.PUBLIC_CONFLICT

    # 5. Salesforce 403 REQUEST_LIMIT_EXCEEDED -> EdhRateLimitError with SF_RATE_LIMIT_EXCEEDED
    with pytest.raises(EdhRateLimitError) as exc_rate:
        strategy.getRecordById("Account", "LIMIT_EXCEEDED")
    assert exc_rate.value.error_code == SalesforceErrorConstants.ERR_RATE_LIMIT_EXCEEDED
    assert exc_rate.value.public_message == SalesforceErrorConstants.PUBLIC_RATE_LIMIT

    # 6. Salesforce 500 UNABLE_TO_LOCK_ROW -> EdhTransientError with SF_TRANSIENT_ROW_LOCK
    with pytest.raises(EdhTransientError) as exc_lock:
        strategy.getRecordById("Account", "LOCK_ROW")
    assert exc_lock.value.error_code == SalesforceErrorConstants.ERR_UNABLE_TO_LOCK_ROW

    # 7. Salesforce 404 NOT_FOUND -> EdhNotFoundError with SF_NOT_FOUND_RECORD
    with pytest.raises(EdhNotFoundError) as exc_nf:
        strategy.getRecordById("Account", "001000000000999")
    assert exc_nf.value.error_code == SalesforceErrorConstants.ERR_RECORD_NOT_FOUND

    # Verify structured log messages were emitted with error codes
    logged_text = "\n".join(r.getMessage() for r in caplog.records)
    assert SalesforceErrorConstants.ERR_EMPTY_RECORD_FIELDS in logged_text
    assert SalesforceErrorConstants.ERR_MALFORMED_QUERY in logged_text
    assert SalesforceErrorConstants.ERR_DUPLICATE_VALUE in logged_text
    assert SalesforceErrorConstants.ERR_RATE_LIMIT_EXCEEDED in logged_text
    assert SalesforceErrorConstants.ERR_UNABLE_TO_LOCK_ROW in logged_text
    assert SalesforceErrorConstants.ERR_RECORD_NOT_FOUND in logged_text


def test_other_strategies_error_constants_and_logging(caplog: pytest.LogCaptureFixture, tmp_path: Any):
    """Verify Databricks, API Hub, Object Storage, Data Lake, and Memory strategies log error constants."""
    import logging
    from sdk.models.edh_models import EdhObjectRef

    caplog.set_level(logging.WARNING)

    # Databricks missing endpoint -> DBX_CONFIG_MISSING_ENDPOINT
    with pytest.raises(EdhConfigError) as dbx_exc:
        DatabricksEdhStrategy(EdhConnectorConfig(strategy="databricks", namespace="main"))
    assert dbx_exc.value.error_code == DatabricksErrorConstants.ERR_MISSING_ENDPOINT

    # API Hub missing endpoint -> API_HUB_CONFIG_MISSING_ENDPOINT
    with pytest.raises(EdhConfigError) as api_exc:
        ApiDrivenEdhStrategy(EdhConnectorConfig(strategy="api_hub", namespace="main"))
    assert api_exc.value.error_code == ApiHubErrorConstants.ERR_MISSING_ENDPOINT

    # Object Storage missing endpoint -> OBJ_STORAGE_CONFIG_MISSING_ENDPOINT
    with pytest.raises(EdhConfigError) as obj_exc:
        ObjectStorageHubStrategy(EdhConnectorConfig(strategy="object_storage_hub", namespace="main"))
    assert obj_exc.value.error_code == ObjectStorageErrorConstants.ERR_MISSING_ENDPOINT

    # Data Lake missing root_path -> DATA_LAKE_CONFIG_MISSING_ROOT_PATH
    with pytest.raises(EdhConfigError) as dl_exc:
        DataLakeStrategy(EdhConnectorConfig(strategy="data_lake", namespace="main"))
    assert dl_exc.value.error_code == DataLakeErrorConstants.ERR_MISSING_ROOT_PATH

    # Memory not found -> MEMORY_NOT_FOUND
    mem = InMemoryEdhStrategy(EdhConnectorConfig(strategy="memory", namespace="main"))
    with pytest.raises(EdhNotFoundError) as mem_exc:
        mem.get_bytes(EdhObjectRef(namespace="main", key="missing.txt"))
    assert mem_exc.value.error_code == MemoryErrorConstants.ERR_NOT_FOUND

    logged_text = "\n".join(r.getMessage() for r in caplog.records)
    assert DatabricksErrorConstants.ERR_MISSING_ENDPOINT in logged_text
    assert ApiHubErrorConstants.ERR_MISSING_ENDPOINT in logged_text
    assert ObjectStorageErrorConstants.ERR_MISSING_ENDPOINT in logged_text
    assert DataLakeErrorConstants.ERR_MISSING_ROOT_PATH in logged_text
    assert MemoryErrorConstants.ERR_NOT_FOUND in logged_text

