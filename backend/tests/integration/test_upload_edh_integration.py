"""Backend integration tests combining FastAPI upload routes, security middleware, and the EDH Data Lake connector.

Verifies the full end-to-end flow:
1. Authenticated ``POST /documents/upload`` -> request validation -> storage adapter backed by ``EdhConnector(DataLakeStrategy)`` on disk.
2. Verification that uploaded documents are persisted in the Data Lake ``RAW`` zone with intact SHA-256 digests and custom metadata.
3. Brute-force failed API key attempts across multiple requests trigger HTTP 429 throttling before multipart body parsing.
4. Readiness probe (``GET /ready``) reflects the underlying storage/EDH health state.
"""

from __future__ import annotations

import io
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.connectors import create_edh_connector
from app.core.config import get_settings
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
