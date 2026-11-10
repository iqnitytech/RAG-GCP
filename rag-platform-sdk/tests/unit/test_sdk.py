import io
import json
import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from sdk import AppError, BadRequestError, create_base_app
from sdk.auth.api_key import make_api_key_dependency
from sdk.logging import JsonFormatter, RequestIdFilter, set_request_id, reset_request_id
from sdk.ratelimit import RateLimiter
from sdk.schemas import ApiResponse, Page, get_page_params, PageParams


def build_app(**kwargs) -> FastAPI:
    app = create_base_app(title="t", **kwargs)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/bad")
    async def bad():
        raise BadRequestError("internal", public_message="Nope")

    @app.get("/page", response_model=ApiResponse[Page[int]])
    async def page(p: PageParams = Depends(get_page_params)):
        return ApiResponse(data=Page(items=[1, 2], total=2, page=p.page, page_size=p.page_size))

    return app


def test_request_id_generated_and_propagated():
    c = TestClient(build_app())
    assert len(c.get("/health").headers["x-request-id"]) == 32
    assert c.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    # Unsafe ids are replaced
    assert c.get("/health", headers={"X-Request-ID": "bad id!"}).headers["x-request-id"] != "bad id!"


def test_security_headers():
    r = TestClient(build_app(is_production=True)).get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in r.headers
    assert "strict-transport-security" not in TestClient(build_app()).get("/health").headers


def test_unhandled_error_is_generic_with_request_id():
    c = TestClient(build_app(), raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    assert "secret" not in r.text
    assert r.json()["detail"] == "Internal server error"


def test_app_error_uses_public_message():
    r = TestClient(build_app()).get("/bad")
    assert r.status_code == 400
    assert r.json()["detail"] == "Nope" and r.json()["request_id"]


def test_docs_disabled_in_production():
    assert TestClient(build_app(is_production=True)).get("/docs").status_code == 404
    assert TestClient(build_app()).get("/docs").status_code == 200


def test_ready_probe():
    async def down():
        return False

    assert TestClient(build_app()).get("/ready").status_code == 200
    assert TestClient(build_app(readiness_check=down)).get("/ready").status_code == 503


def test_generic_schemas_and_pagination():
    r = TestClient(build_app()).get("/page?page=2&page_size=5")
    assert r.json()["data"] == {"items": [1, 2], "total": 2, "page": 2, "page_size": 5}
    assert TestClient(build_app()).get("/page?page_size=1000").status_code == 422


def test_json_log_formatter_includes_request_id():
    handler_filter = RequestIdFilter()
    record = logging.LogRecord("n", logging.INFO, "f", 1, "hello %s", ("x",), None)
    token = set_request_id("rid-1")
    try:
        handler_filter.filter(record)
    finally:
        reset_request_id(token)
    out = json.loads(JsonFormatter("svc").format(record))
    assert out["message"] == "hello x" and out["request_id"] == "rid-1"
    assert out["severity"] == "INFO" and out["service"] == "svc"


def test_api_key_dependency_factory():
    dep = make_api_key_dependency(lambda: "k", failure_limit_getter=lambda: 2)
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(dep)])
    def x():
        return {}

    c = TestClient(app)
    assert c.get("/x").status_code == 401
    assert c.get("/x", headers={"X-API-Key": "k"}).status_code == 200
    assert c.get("/x", headers={"X-API-Key": "bad"}).status_code == 401
    # 3rd failed attempt from the same client IP is rate-limited with 429
    r = c.get("/x", headers={"X-API-Key": "bad"})
    assert r.status_code == 429
    assert "retry-after" in r.headers
    # Even a valid key is rejected before comparison while the IP is throttled
    assert c.get("/x", headers={"X-API-Key": "k"}).status_code == 429


def test_rate_limiter_factory():
    limiter = RateLimiter(lambda: 1)
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(limiter)])
    def x():
        return {}

    c = TestClient(app)
    assert c.get("/x").status_code == 200
    assert c.get("/x").status_code == 429


def test_rate_limiter_bounds_memory_and_evicts_idle(monkeypatch):
    now = 1000.0
    monkeypatch.setattr("sdk.ratelimit.time.monotonic", lambda: now)
    limiter = RateLimiter(lambda: 5, max_identities=3)

    for i in range(10):
        limiter.check(f"id-{i}")
    assert limiter.tracked_identities <= 3

    # Advance past the window and trigger eviction
    now += RateLimiter.WINDOW_SECONDS + 1.0
    limiter.check("fresh-id")
    assert limiter.tracked_identities == 1


def test_body_size_limit_middleware_rejects_oversized_and_chunked():
    from fastapi import File, Request, UploadFile

    parsed = False
    app = create_base_app(
        title="t",
        route_body_limits={"/upload": 64, "/stream": 64},
        require_content_length_paths=("/strict",),
    )

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        nonlocal parsed
        parsed = True
        return {"size": len(await file.read())}

    @app.post("/stream")
    async def stream_ep(request: Request):
        nonlocal parsed
        body = await request.body()
        parsed = True
        return {"size": len(body)}

    @app.post("/strict")
    async def strict():
        return {"ok": True}

    c = TestClient(app)
    # Oversized declared Content-Length is rejected with 413 before multipart parsing
    r = c.post("/upload", files={"file": ("a.txt", io.BytesIO(b"x" * 200), "text/plain")})
    assert r.status_code == 413
    assert not parsed

    # Streamed/chunked upload without Content-Length exceeding limit is aborted mid-stream
    def chunk_gen():
        for _ in range(10):
            yield b"0123456789"

    r_chunked = c.post("/stream", content=chunk_gen(), headers={"Content-Type": "application/octet-stream"})
    assert r_chunked.status_code == 413
    assert not parsed

    # Missing Content-Length on a strict path returns 411
    r_strict = c.post("/strict", content=chunk_gen())
    assert r_strict.status_code == 411


