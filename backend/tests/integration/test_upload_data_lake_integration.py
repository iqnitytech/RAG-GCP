"""Backend integration tests combining FastAPI upload routes, security middleware, and the EDH Data Lake connector.

Uses object names and payloads from ``backend/tests/integration/constants.py`` and
settings from ``backend/tests/integration/.env.integration``.
"""

from __future__ import annotations

import io
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient

from app.connectors import create_edh_connector
from app.core.config import get_integration_settings
from app.core.security import reset_rate_limiter
from app.dependencies import get_storage_service_dependency
from app.main import app
from app.models.edh_models import DataZone, EdhWriteOptions, compute_sha256
from app.models.service_models import UploadResult
from app.services.storage.storage_service import StorageServiceError
from tests.integration.constants import (
    UPLOAD_LIST_PREFIX,
    UPLOAD_REQUEST_ID,
    UPLOAD_TEST_CONTENT_TYPE,
    UPLOAD_TEST_FILENAME,
    UPLOAD_TEST_PDF_BYTES,
)


class EdhBackedStorageService:
    """Integration adapter bridging ``StorageService`` to a disk-backed ``EdhConnector``."""

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
def edh_storage(tmp_path):
    settings = get_integration_settings()
    connector = create_edh_connector(
        settings,
        strategy="data_lake",
        root_path=str(tmp_path / "lake"),
    )
    return EdhBackedStorageService(connector)


@pytest.fixture
def integration_client(edh_storage, monkeypatch):
    int_settings = get_integration_settings()
    monkeypatch.setattr("app.core.config.get_settings", lambda: int_settings)
    monkeypatch.setattr("app.core.security.get_settings", lambda: int_settings)
    reset_rate_limiter()
    app.dependency_overrides[get_storage_service_dependency] = lambda: edh_storage
    monkeypatch.setattr("app.main.get_storage_service", lambda: edh_storage)
    yield TestClient(app)
    app.dependency_overrides.clear()
    reset_rate_limiter()


def test_end_to_end_upload_persists_into_edh_data_lake(integration_client, edh_storage):
    int_settings = get_integration_settings()
    api_key = int_settings.api_key.get_secret_value()
    resp = integration_client.post(
        "/documents/upload",
        files={"file": (UPLOAD_TEST_FILENAME, io.BytesIO(UPLOAD_TEST_PDF_BYTES), UPLOAD_TEST_CONTENT_TYPE)},
        headers={"X-API-Key": api_key, "X-Request-ID": UPLOAD_REQUEST_ID},
    )
    assert resp.status_code == 201, resp.text
    assert resp.headers["x-request-id"] == UPLOAD_REQUEST_ID

    body = resp.json()
    assert body["filename"] == UPLOAD_TEST_FILENAME
    assert body["size_bytes"] == len(UPLOAD_TEST_PDF_BYTES)
    assert body["storage"]["gs_url"].startswith("edh://")

    page = edh_storage.edh.list_objects(zone=DataZone.RAW, prefix=UPLOAD_LIST_PREFIX)
    assert len(page.items) == 1
    stored_meta = page.items[0]
    assert stored_meta.size_bytes == len(UPLOAD_TEST_PDF_BYTES)
    assert stored_meta.sha256 == compute_sha256(UPLOAD_TEST_PDF_BYTES)
    assert stored_meta.custom_metadata.get("original_filename") == UPLOAD_TEST_FILENAME
    assert edh_storage.edh.get_bytes(stored_meta.ref) == UPLOAD_TEST_PDF_BYTES


def test_integration_ready_probe_and_failure_handling(integration_client, edh_storage):
    int_settings = get_integration_settings()
    api_key = int_settings.api_key.get_secret_value()

    assert integration_client.get("/health").status_code == 200
    assert integration_client.get("/ready").status_code == 200

    edh_storage.fail = True
    assert integration_client.get("/ready").status_code == 503

    r_fail = integration_client.post(
        "/documents/upload",
        files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")},
        headers={"X-API-Key": api_key},
    )
    assert r_fail.status_code == 503
    assert "unavailable" in r_fail.json()["detail"].lower()


def test_integration_brute_force_api_key_throttling(integration_client, edh_storage):
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
    assert len(edh_storage.edh.list_objects(zone=DataZone.RAW).items) == 0
