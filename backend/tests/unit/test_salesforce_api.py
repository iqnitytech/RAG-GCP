"""Unit tests for ``GET /salesforce/{sobject_name}/records/{record_id}`` using the SDK."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.security import reset_rate_limiter
from app.dependencies import get_salesforce_service_dependency
from app.main import app
from app.services.salesforce.salesforce_service import SalesforceService
from sdk.connectors.edh import (
    EdhConnector,
    EdhConnectorConfig,
    RawHttpResponse,
    SalesforceEdhStrategy,
    SalesforceErrorConstants,
)


def _fake_sf_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> RawHttpResponse:
    if "/sobjects/Account/001000000000001" in url and method == "GET":
        payload = {
            "attributes": {"type": "Account"},
            "Id": "001000000000001",
            "Name": "Acme Enterprise",
            "Industry": "AI",
        }
        return RawHttpResponse(
            200,
            {"content-type": "application/json"},
            json.dumps(payload).encode("utf-8"),
        )
    return RawHttpResponse(
        404,
        {"content-type": "application/json"},
        b'[{"errorCode": "NOT_FOUND", "message": "The requested resource does not exist"}]',
    )


@pytest.fixture
def sf_connector() -> EdhConnector:
    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace="rag-enterprise",
        endpoint_url="https://acme.my.salesforce.com",
        auth_token_getter=lambda: "00Dxx0000001gPL!AQ0AQ...",
    )
    strategy = SalesforceEdhStrategy(cfg, transport=_fake_sf_transport)
    return EdhConnector(cfg, strategy=strategy)


@pytest.fixture
def client(sf_connector: EdhConnector, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_key", SecretStr(""))
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    reset_rate_limiter()
    service = SalesforceService(settings=settings, connector=sf_connector)
    app.dependency_overrides[get_salesforce_service_dependency] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_salesforce_record_by_id_success_and_closes_connection(
    client: TestClient,
    sf_connector: EdhConnector,
):
    assert sf_connector.is_open is False

    resp = client.get(
        "/salesforce/Account/records/001000000000001",
        params={"fields": "Id,Name,Industry"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sobject"] == "Account"
    assert data["record_id"] == "001000000000001"
    assert data["record"]["Name"] == "Acme Enterprise"
    assert data["record"]["Industry"] == "AI"
    assert sf_connector.is_open is False

    # Alias route /salesforce/records/{sobject_name}/{record_id}
    resp_alias = client.get("/salesforce/records/Account/001000000000001")
    assert resp_alias.status_code == 200
    assert resp_alias.json()["record"]["Id"] == "001000000000001"


def test_get_salesforce_record_by_id_not_found_logs_and_returns_error_code(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    resp = client.get("/salesforce/Account/records/001000000000999")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == SalesforceErrorConstants.PUBLIC_NOT_FOUND
    assert body["error_code"] == SalesforceErrorConstants.ERR_RECORD_NOT_FOUND
    assert body["request_id"]
    assert SalesforceErrorConstants.ERR_RECORD_NOT_FOUND in caplog.text


def test_get_salesforce_record_by_id_placeholder_env_returns_400_config_error(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = Settings(
        _env_file=None,
        gcp_project_id="p",
        gcs_document_bucket="b",
        sf_instance_url="https://your-org.my.salesforce.com",
        sf_client_id="your-salesforce-client-id",
        sf_client_secret=SecretStr("your-salesforce-client-secret"),
    )
    assert settings.is_salesforce_configured is False
    unconfigured_service = SalesforceService(settings=settings)

    monkeypatch.setattr(get_settings(), "api_key", SecretStr(""))
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 0)
    reset_rate_limiter()
    app.dependency_overrides[get_salesforce_service_dependency] = lambda: unconfigured_service
    try:
        c = TestClient(app)
        resp = c.get("/salesforce/Account/records/001000000000001")
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"] == SalesforceErrorConstants.PUBLIC_CONFIG_ERROR
        assert body["error_code"] == SalesforceErrorConstants.ERR_MISSING_ENDPOINT
    finally:
        app.dependency_overrides.clear()
