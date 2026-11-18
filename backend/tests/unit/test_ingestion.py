"""Unit tests for the Phase 1 ingestion pipeline and API."""

from __future__ import annotations

import io
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.security import reset_rate_limiter
from app.dependencies import (
    get_ingestion_service_dependency,
    get_salesforce_service_dependency,
    get_storage_service_dependency,
)
from app.main import app
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.embedder import HashEmbedder, VertexEmbedder
from app.services.ingestion.parsers import (
    UnsupportedDocumentError,
    parse_document,
    supported_extensions,
)
from app.services.ingestion.service import IngestionService
from app.services.ingestion.vector_store import InMemoryVectorStore, QdrantVectorStore


# ---------------------------------------------------------------- parsers / chunker
def test_parse_text_html_json_docx():
    assert parse_document(b"hello\r\n\r\n\r\n\r\nworld", "a.txt") == "hello\n\nworld"
    html = b"<html><style>x{}</style><body><h1>Title</h1><p>Body</p><script>bad()</script></body></html>"
    out = parse_document(html, "a.html")
    assert "Title" in out and "Body" in out and "bad()" not in out
    assert '"k": 1' in parse_document(b'{"k":1}', "a.json")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Docx text</w:t></w:r></w:p></w:body></w:document>",
        )
    assert parse_document(buf.getvalue(), "a.docx") == "Docx text"


def test_parse_rejects_unsupported_and_empty():
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"MZ", "x.exe")
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"   \n ", "x.txt")


class FakeOcr:
    def __init__(self, text="Scanned invoice total 42"):
        self.text = text
        self.calls = []

    def extract_text(self, data, mime_type):
        self.calls.append(mime_type)
        return self.text


def test_parse_csv_tsv_rows_keep_headers():
    out = parse_document(b"name,city\nAda,London\nLin,Paris\n", "p.csv")
    assert out == "name: Ada | city: London\nname: Lin | city: Paris"
    assert "city: Rome" in parse_document(b"name\tcity\nAmy\tRome\n", "p.tsv")
    assert "a: 1" in parse_document(b"a;b\n1;2\n", "semi.csv")


def test_parse_xlsx_pptx_eml_xml_jsonl_rtf():
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>Product</t></si><si><t>Widget</t></si></sst>')
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"><sheets><sheet name="Sales"/></sheets></workbook>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData><row><c t="s"><v>0</v></c><c><v>10</v></c></row>'
            f'<row><c t="s"><v>1</v></c><c><v>20</v></c></row></sheetData></worksheet>',
        )
    out = parse_document(buf.getvalue(), "s.xlsx")
    assert "Sheet: Sales" in out and "Product | 10" in out and "Widget | 20" in out

    pbuf = io.BytesIO()
    with zipfile.ZipFile(pbuf, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Quarterly goals</a:t></p:sld>',
        )
    assert "Slide 1:\nQuarterly goals" == parse_document(pbuf.getvalue(), "d.pptx")

    eml = b"From: a@x.com\nTo: b@x.com\nSubject: Hello\nContent-Type: text/plain\n\nBody text here"
    out = parse_document(eml, "m.eml")
    assert "Subject: Hello" in out and "Body text here" in out

    assert "Leaf" in parse_document(b"<r><a>Leaf</a></r>", "d.xml")
    assert parse_document(b'{"a":1}\n{"b":2}\n', "d.jsonl") == '{"a": 1}\n{"b": 2}'
    assert parse_document(rb"{\rtf1\ansi Hello\par World}", "d.rtf") == "Hello\nWorld"


def test_images_and_scanned_pdfs_use_ocr():
    ocr = FakeOcr()
    assert parse_document(b"\x89PNG...", "scan.PNG", ocr) == "Scanned invoice total 42"
    assert parse_document(b"\xff\xd8...", "p.jpeg", ocr) == "Scanned invoice total 42"
    assert ocr.calls == ["image/png", "image/jpeg"]
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"\x89PNG", "scan.png")  # OCR not configured
    assert ".pdf" in supported_extensions() and ".png" in supported_extensions()


def test_pdf_text_extracted_and_scanned_falls_back_to_ocr():
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    blank = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(blank)
    ocr = FakeOcr("OCR of scanned pdf")
    assert parse_document(blank.getvalue(), "scan.pdf", ocr) == "OCR of scanned pdf"
    assert ocr.calls == ["application/pdf"]
    with pytest.raises(UnsupportedDocumentError):
        parse_document(blank.getvalue(), "scan.pdf")  # no text, no OCR


def test_gemini_ocr_adapter_parses_response():
    from app.services.ingestion.ocr import VertexGeminiOcr

    def handler(request: httpx.Request) -> httpx.Response:
        assert b"inline_data" in request.read()
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "line1"}, {"text": "line2"}]}}]}
        )

    class Creds:
        valid = True
        token = "t"

    ocr = VertexGeminiOcr(
        project_id="p",
        region="r",
        credentials_getter=lambda: Creds(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert ocr.extract_text(b"img", "image/png") == "line1\nline2"


def test_chunker_respects_size_and_overlap():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(10))
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert len(chunks) > 3
    assert all(len(c) <= 300 for c in chunks)
    assert chunk_text("   ") == []
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=10)


# ---------------------------------------------------------------- service
def make_service():
    store = InMemoryVectorStore()
    return IngestionService(
        embedder=HashEmbedder(64), vector_store=store, chunk_size=200, chunk_overlap=40
    ), store


def test_ingest_is_idempotent_and_searchable():
    svc, store = make_service()
    text = "Salesforce opportunities close in Q4. " * 20 + "\n\nQdrant stores embeddings. " * 20
    r1 = svc.ingest_text(text, source="gcs://a.txt", metadata={"tenant": "t1"})
    assert not r1.skipped and r1.chunks == store.count() > 1
    r2 = svc.ingest_text(text, source="gcs://a.txt")
    assert r2.skipped and store.count() == r1.chunks

    top = store.search(svc._embedder.embed_query("Qdrant embeddings"), limit=1)[0][1]
    assert top.payload["tenant"] == "t1" and top.payload["source"] == "gcs://a.txt"
    assert "Qdrant" in top.payload["text"]


# ---------------------------------------------------------------- adapters
def test_qdrant_adapter_creates_collection_and_upserts():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.url.path.endswith("/points/count"):
            return httpx.Response(200, json={"result": {"count": 3}})
        return httpx.Response(200, json={"result": True})

    client = httpx.Client(base_url="http://qdrant", transport=httpx.MockTransport(handler))
    store = QdrantVectorStore(url="http://qdrant", collection="c", http_client=client)
    svc = IngestionService(
        embedder=HashEmbedder(16), vector_store=store, chunk_size=200, chunk_overlap=40
    )
    svc.ingest_text("hello world " * 30, source="s")
    assert ("PUT", "/collections/c") in calls
    assert ("PUT", "/collections/c/points") in calls
    assert store.count() == 3


def test_vertex_embedder_parses_predictions():
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.read().decode().split('"content"')) - 1
        return httpx.Response(
            200, json={"predictions": [{"embeddings": {"values": [0.1, 0.2]}} for _ in range(n)]}
        )

    class Creds:
        valid = True
        token = "tok"

    emb = VertexEmbedder(
        project_id="p",
        region="r",
        dimensions=2,
        credentials_getter=lambda: Creds(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert emb.embed_documents(["a", "b", "c"]) == [[0.1, 0.2]] * 3
    assert emb.embed_query("q") == [0.1, 0.2]


# ---------------------------------------------------------------- API
class FakeStorage:
    def __init__(self):
        self.files = {
            "docs/a.txt": b"Enterprise RAG ingestion works. " * 30,
            "docs/b.txt": b"Master Services Agreement terms and obligations. " * 25,
            "shared/finance/q1.csv": b"metric,value\nrevenue,1200\nmargin,35\n",
        }

    def download_bytes(self, name):
        if name not in self.files:
            raise RuntimeError("missing")
        return self.files[name]

    def list_objects(self, prefix="", max_results=500):
        return [k for k in self.files if k.startswith(prefix)][:max_results]


class FakeSalesforce:
    def __init__(self):
        self.shared_files = {
            "shared/policies/hr_policy.txt": b"Annual leave and remote work policy details. " * 20,
            "shared/policies/security.md": b"# Security Standard\nZero trust architecture required. " * 15,
            "shared/contracts/corrupt.docx": b"not-a-valid-zip",
        }

    async def query(self, sobject, *, fields=None, where=None, limit=None):
        return [
            {"Id": "001A", "Name": "Acme", "Description": "Acme builds rockets " * 10},
            {"Id": "001B", "Name": "Beta", "Description": ""},
        ]

    async def list_shared_files(self, path_prefix="", *, zone=None, max_files=500):
        from sdk.connectors.edh import DataZone, EdhObjectMetadata, EdhObjectRef

        z = zone or DataZone.RAW
        return [
            EdhObjectMetadata(
                ref=EdhObjectRef(namespace="rag-default", key=k, zone=z),
                size_bytes=len(v),
            )
            for k, v in self.shared_files.items()
            if k.startswith(path_prefix)
        ][:max_files]

    async def download_shared_file(self, key, *, zone=None):
        if key not in self.shared_files:
            raise RuntimeError("missing shared file")
        return self.shared_files[key]


@pytest.fixture
def api(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_key", SecretStr(""))
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    reset_rate_limiter()
    svc, store = make_service()
    app.dependency_overrides[get_ingestion_service_dependency] = lambda: svc
    app.dependency_overrides[get_storage_service_dependency] = lambda: FakeStorage()
    app.dependency_overrides[get_salesforce_service_dependency] = lambda: FakeSalesforce()
    yield TestClient(app), store
    app.dependency_overrides.clear()


def wait_done(client, job_id):
    for _ in range(50):
        body = client.get(f"/ingestion/jobs/{job_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_gcs_job_indexes_and_isolates_failures(api):
    client, store = api
    r = client.post(
        "/ingestion/jobs",
        json={"source_type": "gcs", "object_names": ["docs/a.txt", "docs/missing.txt"]},
    )
    assert r.status_code == 202
    body = wait_done(client, r.json()["job_id"])
    assert body["status"] == "completed"
    assert body["documents_indexed"] == 1 and len(body["errors"]) == 1
    assert body["chunks_indexed"] == store.count() > 0


def test_salesforce_job_indexes_records_with_text(api):
    client, store = api
    r = client.post(
        "/ingestion/jobs",
        json={"source_type": "salesforce", "sobject": "Account", "text_fields": ["Name", "Description"]},
    )
    body = wait_done(client, r.json()["job_id"])
    assert body["documents_indexed"] == 2 and body["status"] == "completed"
    sources = {p.payload["record_id"] for p in store._points.values()}
    assert sources == {"001A", "001B"}


def test_request_validation_and_unknown_job(api):
    client, _ = api
    assert client.post("/ingestion/jobs", json={"source_type": "gcs"}).status_code == 422
    assert (
        client.post("/ingestion/jobs", json={"source_type": "salesforce", "sobject": "Account"}).status_code
        == 422
    )
    assert client.post("/ingestion/jobs", json={"source_type": "salesforce_path"}).status_code == 422
    r = client.get("/ingestion/jobs/nope")
    assert r.status_code == 404 and r.json()["detail"] == "Ingestion job not found"


def test_salesforce_shared_paths_background_ingestion_and_file_tracking(api):
    client, store = api

    # Trigger background ingestion across two shared Salesforce paths (2 valid files + 1 corrupt file)
    r = client.post(
        "/ingestion/salesforce/shared-paths",
        json={"shared_paths": ["shared/policies", "shared/contracts"], "zone": "raw"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    body = wait_done(client, job_id)
    assert body["status"] == "completed"
    assert body["source_type"] == "salesforce_path"
    assert body["files_discovered"] == 3
    assert body["files_processed"] == 3
    assert body["files_succeeded"] == 2
    assert body["files_failed"] == 1
    assert body["documents_indexed"] == 2
    assert set(body["processed_files"]) == {
        "shared/policies/hr_policy.txt",
        "shared/policies/security.md",
    }
    assert len(body["errors"]) == 1
    assert body["chunks_indexed"] == store.count() > 0

    # Verify GET /ingestion/jobs returns the job with file tracking counts
    jobs_list = client.get("/ingestion/jobs").json()
    assert any(j["job_id"] == job_id and j["files_processed"] == 3 for j in jobs_list)


def test_generic_shared_paths_filesystem_and_gcs_background_ingestion(api, tmp_path, monkeypatch):
    client, store = api

    # 1. Any local/mounted shared folder (provider="filesystem")
    hr_dir = tmp_path / "hr" / "2026"
    hr_dir.mkdir(parents=True)
    (hr_dir / "handbook.txt").write_text("Employee handbook and benefits policy. " * 15, encoding="utf-8")
    (hr_dir / "roster.csv").write_text("team,headcount\nAI,24\nPlatform,18\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "shared_folders_root", str(tmp_path))

    r_fs = client.post(
        "/ingestion/shared-paths",
        json={"provider": "filesystem", "shared_paths": ["hr"]},
    )
    assert r_fs.status_code == 202
    fs_job = wait_done(client, r_fs.json()["job_id"])
    assert fs_job["status"] == "completed"
    assert fs_job["files_discovered"] == 2
    assert fs_job["files_processed"] == 2
    assert fs_job["files_succeeded"] == 2
    assert fs_job["files_failed"] == 0
    assert set(fs_job["processed_files"]) == {"hr/2026/handbook.txt", "hr/2026/roster.csv"}

    # 2. Any GCS bucket folder prefix (provider="gcs")
    r_gcs = client.post(
        "/ingestion/shared-paths",
        json={"provider": "gcs", "shared_paths": ["shared/finance"]},
    )
    assert r_gcs.status_code == 202
    gcs_job = wait_done(client, r_gcs.json()["job_id"])
    assert gcs_job["status"] == "completed"
    assert gcs_job["files_discovered"] == 1
    assert gcs_job["files_processed"] == 1
    assert gcs_job["processed_files"] == ["shared/finance/q1.csv"]


def test_multi_application_id_scoping_cpq_and_customer_experience(api):
    client, store = api

    # 1. Default applications include CPQ and CustomerExperience
    apps = client.get("/ingestion/applications").json()
    app_ids = {a["application_id"] for a in apps}
    assert "CPQ" in app_ids and "CustomerExperience" in app_ids

    # 2. Register a third application
    reg = client.post(
        "/ingestion/applications",
        json={"application_id": "PartnerPortal", "name": "Partner Portal"},
    )
    assert reg.status_code == 201
    assert reg.json()["application_id"] == "PartnerPortal"

    # 3. Ingest docs/a.txt under CPQ and docs/b.txt under CustomerExperience
    r_cpq = client.post(
        "/ingestion/jobs",
        json={"application_id": "CPQ", "source_type": "gcs", "object_names": ["docs/a.txt"]},
    )
    r_cx = client.post(
        "/ingestion/jobs",
        json={
            "application_id": "CustomerExperience",
            "source_type": "gcs",
            "object_names": ["docs/b.txt"],
        },
    )
    wait_done(client, r_cpq.json()["job_id"])
    wait_done(client, r_cx.json()["job_id"])

    # 4. Filter jobs and documents by application_id
    cpq_docs = client.get("/ingestion/documents", params={"application_id": "CPQ"}).json()
    cx_docs = client.get(
        "/ingestion/documents", params={"application_id": "CustomerExperience"}
    ).json()
    assert len(cpq_docs) == 1 and cpq_docs[0]["application_id"] == "CPQ"
    assert len(cx_docs) == 1 and cx_docs[0]["application_id"] == "CustomerExperience"

    cpq_jobs = client.get("/ingestion/jobs", params={"application_id": "CPQ"}).json()
    assert all(j["application_id"] == "CPQ" for j in cpq_jobs)

    # 5. Cross-application linking (CPQ -> CustomerExperience) is blocked with HTTP 400
    cross_link = client.post(
        f"/ingestion/documents/{cpq_docs[0]['document_id']}/links",
        json={"target_document_id": cx_docs[0]["document_id"], "relation_type": "depends_on"},
    )
    assert cross_link.status_code == 400
    assert "different applications" in cross_link.json()["detail"].lower()


def test_event_listener_webhook_and_folder_watcher_subscriptions(api, tmp_path, monkeypatch):
    client, store = api
    monkeypatch.setattr(get_settings(), "shared_folders_root", str(tmp_path))

    # 1. Push Webhook Event Listener (POST /ingestion/events/webhook)
    wh = client.post(
        "/ingestion/events/webhook",
        json={
            "event_type": "file.created",
            "application_id": "CPQ",
            "provider": "gcs",
            "paths": ["docs/a.txt"],
        },
    )
    assert wh.status_code == 202
    wh_data = wh.json()
    assert wh_data["event_type"] == "file.created"
    assert wh_data["application_id"] == "CPQ"
    wh_job = wait_done(client, wh_data["job_id"])
    assert wh_job["files_processed"] == 1 and wh_job["files_succeeded"] == 1

    # 2. Folder Watch Subscription + Delta Polling (POST /ingestion/events/subscriptions & /poll)
    watch_dir = tmp_path / "cx_inbox"
    watch_dir.mkdir(parents=True)
    (watch_dir / "ticket_1.txt").write_text(
        "Customer onboarding guide and SLA escalation matrix. " * 15,
        encoding="utf-8",
    )

    sub_resp = client.post(
        "/ingestion/events/subscriptions",
        json={
            "application_id": "CustomerExperience",
            "provider": "filesystem",
            "shared_path": "cx_inbox",
        },
    )
    assert sub_resp.status_code == 201
    sub_id = sub_resp.json()["subscription_id"]

    # First poll detects ticket_1.txt and triggers 1 background job
    poll_1 = client.post("/ingestion/events/poll", params={"application_id": "CustomerExperience"})
    assert poll_1.status_code == 200
    events_1 = poll_1.json()
    assert len(events_1) == 1
    assert events_1[0]["paths"] == ["cx_inbox/ticket_1.txt"]
    wait_done(client, events_1[0]["job_id"])

    # Second poll with no new/changed files triggers 0 events
    poll_2 = client.post("/ingestion/events/poll", params={"application_id": "CustomerExperience"})
    assert poll_2.json() == []

    # Drop a new file into the watched folder -> third poll detects only the new file
    (watch_dir / "ticket_2.txt").write_text(
        "VIP customer support playbook and response time targets. " * 15,
        encoding="utf-8",
    )
    poll_3 = client.post("/ingestion/events/poll", params={"application_id": "CustomerExperience"})
    events_3 = poll_3.json()
    assert len(events_3) == 1
    assert events_3[0]["paths"] == ["cx_inbox/ticket_2.txt"]
    wait_done(client, events_3[0]["job_id"])

    # Verify subscription counters and event history
    subs = client.get(
        "/ingestion/events/subscriptions", params={"application_id": "CustomerExperience"}
    ).json()
    assert subs[0]["events_triggered"] == 2
    assert subs[0]["files_detected_total"] == 2

    recent_events = client.get(
        "/ingestion/events", params={"application_id": "CustomerExperience"}
    ).json()
    assert len(recent_events) == 2

    # Unsubscribe watched folder
    assert client.delete(f"/ingestion/events/subscriptions/{sub_id}").status_code == 200


def test_google_cloud_pubsub_publish_pull_push_and_gcs_object_finalize(api):
    import base64
    import json
    from app.services.ingestion.pubsub import GcpPubSubBroker

    client, store = api

    # 1. Publish an ingestion event to Pub/Sub (POST /ingestion/events/pubsub/publish)
    pub = client.post(
        "/ingestion/events/pubsub/publish",
        json={
            "event_type": "file.created",
            "application_id": "CPQ",
            "provider": "gcs",
            "paths": ["docs/a.txt"],
        },
    )
    assert pub.status_code == 202
    pub_body = pub.json()
    assert pub_body["message_id"].startswith("pubsub-")
    assert pub_body["application_id"] == "CPQ"

    # 2. Pull from Pub/Sub subscription (POST /ingestion/events/pubsub/pull) -> queues job & acks
    pulled = client.post("/ingestion/events/pubsub/pull")
    assert pulled.status_code == 200
    pulled_events = pulled.json()
    assert len(pulled_events) == 1
    assert pulled_events[0]["pubsub_message_id"] == pub_body["message_id"]
    pulled_job = wait_done(client, pulled_events[0]["job_id"])
    assert pulled_job["files_processed"] == 1 and pulled_job["files_succeeded"] == 1

    # Second pull returns empty because the message was acknowledged
    assert client.post("/ingestion/events/pubsub/pull").json() == []

    # 3. Native GCS Bucket Notification (OBJECT_FINALIZE) via Pub/Sub Push Subscription
    gcs_notification = {
        "bucket": "rag-enterprise-bucket",
        "name": "docs/b.txt",
        "metadata": {"application_id": "CustomerExperience"},
    }
    b64_data = base64.b64encode(json.dumps(gcs_notification).encode("utf-8")).decode("ascii")
    push_resp = client.post(
        "/ingestion/events/pubsub/push",
        json={
            "message": {
                "messageId": "gcp-msg-991",
                "data": b64_data,
                "attributes": {
                    "eventType": "OBJECT_FINALIZE",
                    "bucketId": "rag-enterprise-bucket",
                },
            },
            "subscription": "projects/p/subscriptions/rag-ingestion-sub",
        },
    )
    assert push_resp.status_code == 202
    push_data = push_resp.json()
    assert push_data["application_id"] == "CustomerExperience"
    assert push_data["provider"] == "gcs"
    assert push_data["paths"] == ["docs/b.txt"]
    assert push_data["pubsub_message_id"] == "gcp-msg-991"
    push_job = wait_done(client, push_data["job_id"])
    assert push_job["files_processed"] == 1 and push_job["files_succeeded"] == 1

    # 4. GcpPubSubBroker REST v1 (:publish, :pull, :acknowledge)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith(":publish"):
            return httpx.Response(200, json={"messageIds": ["1001"]})
        if request.url.path.endswith(":pull"):
            return httpx.Response(
                200,
                json={
                    "receivedMessages": [
                        {
                            "ackId": "ack-1001",
                            "message": {
                                "messageId": "1001",
                                "data": b64_data,
                                "attributes": {"eventType": "OBJECT_FINALIZE"},
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json={})

    class Creds:
        valid = True
        token = "gcp-token"

    broker = GcpPubSubBroker(
        project_id="my-proj",
        topic="rag-topic",
        subscription="rag-sub",
        credentials_getter=lambda: Creds(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    msgs = broker.pull(max_messages=5)
    assert len(msgs) == 1 and msgs[0].ack_id == "ack-1001"
    msg_id = broker.publish(msgs[0].event)
    assert msg_id == "1001"
    broker.ack([msgs[0].ack_id])
    assert any(p.endswith(":publish") for _, p in calls)
    assert any(p.endswith(":pull") for _, p in calls)
    assert any(p.endswith(":acknowledge") for _, p in calls)


def test_admin_portal_document_linking_and_vector_payload_sync(api):
    client, store = api

    # 1. User uploads / ingests two documents
    r = client.post(
        "/ingestion/jobs",
        json={"source_type": "gcs", "object_names": ["docs/a.txt", "docs/b.txt"]},
    )
    wait_done(client, r.json()["job_id"])

    # 2. Admin Portal lists indexed documents
    docs = client.get("/ingestion/documents").json()
    assert len(docs) == 2
    by_name = {d["filename"]: d["document_id"] for d in docs}
    doc_a = by_name["docs/a.txt"]
    doc_b = by_name["docs/b.txt"]

    # 3. Admin links doc_a -> doc_b ("depends_on")
    link_resp = client.post(
        f"/ingestion/documents/{doc_a}/links",
        json={
            "target_document_id": doc_b,
            "relation_type": "depends_on",
            "created_by": "admin@company.com",
        },
    )
    assert link_resp.status_code == 201
    link_id = link_resp.json()["link_id"]

    # 4. Verify vector store chunks for both documents now carry linked_document_ids
    for point in store._points.values():
        if point.payload["document_id"] == doc_a:
            assert point.payload["linked_document_ids"] == [doc_b]
        elif point.payload["document_id"] == doc_b:
            assert point.payload["linked_document_ids"] == [doc_a]

    # 5. Inspect outgoing/incoming links
    overview = client.get(f"/ingestion/documents/{doc_a}/links").json()
    assert len(overview["outgoing"]) == 1
    assert overview["linked_document_ids"] == [doc_b]

    # 6. Cycle detection: doc_b -> doc_a with "depends_on" must be rejected with 400
    cycle_resp = client.post(
        f"/ingestion/documents/{doc_b}/links",
        json={"target_document_id": doc_a, "relation_type": "depends_on"},
    )
    assert cycle_resp.status_code == 400
    assert "circular" in cycle_resp.json()["detail"].lower()

    # 7. Self-link and duplicate link rejected with 400
    assert (
        client.post(
            f"/ingestion/documents/{doc_a}/links",
            json={"target_document_id": doc_a, "relation_type": "related"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/ingestion/documents/{doc_a}/links",
            json={"target_document_id": doc_b, "relation_type": "depends_on"},
        ).status_code
        == 400
    )

    # 8. Admin removes the link and vector payloads are cleared
    del_resp = client.delete(f"/ingestion/documents/{doc_a}/links/{link_id}")
    assert del_resp.status_code == 200
    for point in store._points.values():
        assert point.payload["linked_document_ids"] == []

