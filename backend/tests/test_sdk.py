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
    dep = make_api_key_dependency(lambda: "k")
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(dep)])
    def x():
        return {}

    c = TestClient(app)
    assert c.get("/x").status_code == 401
    assert c.get("/x", headers={"X-API-Key": "k"}).status_code == 200


def test_rate_limiter_factory():
    limiter = RateLimiter(lambda: 1)
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(limiter)])
    def x():
        return {}

    c = TestClient(app)
    assert c.get("/x").status_code == 200
    assert c.get("/x").status_code == 429

