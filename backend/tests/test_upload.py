import io

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.security import reset_rate_limiter
from app.dependencies import get_storage_service_dependency
from app.main import app
from app.services.storage.storage_service import StorageServiceError, UploadResult


class FakeStorage:
    def __init__(self):
        self.fail = False
        self.calls = []

    def upload_file(self, file_object, destination_name, content_type, metadata=None):
        if self.fail:
            raise StorageServiceError("secret internal detail")
        self.calls.append((destination_name, content_type, metadata, len(file_object.read())))
        return UploadResult("bucket", destination_name, f"gs://bucket/{destination_name}")

    def ping(self):
        return not self.fail


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def client(storage, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_key", SecretStr(""))
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    reset_rate_limiter()
    app.dependency_overrides[get_storage_service_dependency] = lambda: storage
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, name, data, headers=None):
    return client.post(
        "/documents/upload",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        headers=headers,
    )


def test_valid_pdf_is_uploaded_with_sanitised_name(client, storage):
    r = upload(client, "My Report (v1).pdf", b"%PDF-1.4 hello")
    assert r.status_code == 201
    name, content_type, metadata, size = storage.calls[0]
    assert name.endswith("_My_Report_v1.pdf") and name.startswith("documents/")
    assert content_type == "application/pdf"
    assert metadata == {"original_filename": "My Report (v1).pdf"}
    assert size == 14


def test_content_must_match_extension(client):
    assert upload(client, "x.pdf", b"not a pdf").status_code == 400


def test_unsupported_extension(client):
    assert upload(client, "x.exe", b"MZ").status_code == 400


def test_empty_file(client):
    assert upload(client, "x.txt", b"").status_code == 400


def test_file_too_large(client):
    assert upload(client, "x.txt", b"a" * (10 * 1024 * 1024 + 1)).status_code == 413


def test_path_traversal_is_neutralised(client, storage):
    assert upload(client, "../../etc/x.txt", b"hello").status_code == 201
    assert ".." not in storage.calls[0][0]


def test_storage_failure_is_generic_503(client, storage):
    storage.fail = True
    r = upload(client, "x.txt", b"hello")
    assert r.status_code == 503
    assert "secret" not in r.text


def test_api_key_required_when_configured(client, storage, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", SecretStr("k1"))
    assert upload(client, "x.txt", b"hi").status_code == 401
    assert upload(client, "x.txt", b"hi", {"X-API-Key": "bad"}).status_code == 401
    assert len(storage.calls) == 0
    assert upload(client, "x.txt", b"hi", {"X-API-Key": "k1"}).status_code == 201
    assert len(storage.calls) == 1


def test_unauthenticated_upload_rejected_before_parsing_body(client, storage, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", SecretStr("k1"))
    form_called = False

    async def fail_if_form_parsed(self, *args, **kwargs):
        nonlocal form_called
        form_called = True
        raise AssertionError("Multipart body should not be parsed for unauthenticated requests")

    monkeypatch.setattr("starlette.requests.Request.form", fail_if_form_parsed)
    assert upload(client, "x.txt", b"hi").status_code == 401
    assert upload(client, "x.txt", b"hi", {"X-API-Key": "wrong"}).status_code == 401
    assert not form_called
    assert len(storage.calls) == 0


def test_oversized_and_chunked_upload_rejected_before_spooling(client, storage):
    # Oversized upload rejected by BodySizeLimitMiddleware before form parsing
    r = upload(client, "x.txt", b"a" * (12 * 1024 * 1024))
    assert r.status_code == 413
    assert len(storage.calls) == 0

    # Chunked request without Content-Length rejected with 411 before reading stream
    def stream_chunks():
        yield b"chunk-1"
        yield b"chunk-2"

    r_chunked = client.post(
        "/documents/upload",
        content=stream_chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=xyz"},
    )
    assert r_chunked.status_code == 411
    assert len(storage.calls) == 0



def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    assert upload(client, "x.txt", b"hi").status_code == 201
    assert upload(client, "x.txt", b"hi").status_code == 201
    r = upload(client, "x.txt", b"hi")
    assert r.status_code == 429 and "retry-after" in r.headers


def test_health_and_ready(client, storage, monkeypatch):
    monkeypatch.setattr("app.main.get_storage_service", lambda: storage)
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/ready").status_code == 200
    storage.fail = True
    assert client.get("/ready").status_code == 503

