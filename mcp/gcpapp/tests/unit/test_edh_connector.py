"""Unit tests for common/infrastructure/edh_connector.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from config.settings import Settings
from core.exceptions import AuthenticationError, SdkGatewayError
from common.infrastructure.connector import (
    build_salesforce_token_getter,
    create_async_edh_connector,
    create_connector,
)


@pytest.fixture
def sf_oauth_settings() -> Settings:
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SF_INSTANCE_URL="https://acme.my.salesforce.com",
        SF_CLIENT_ID="sf-client-id-123",
        SF_CLIENT_SECRET="sf-client-secret-xyz",
    )


@pytest.fixture
def sf_token_settings() -> Settings:
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SF_INSTANCE_URL="https://acme.my.salesforce.com",
        SF_ACCESS_TOKEN="live-static-token",
    )


@pytest.fixture
def unconfigured_settings() -> Settings:
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SF_INSTANCE_URL="",
        SDK_API_KEY="",
        SDK_BASE_URL="",
    )


class TestBuildSalesforceTokenGetter:
    def test_prefers_static_access_token(self, sf_token_settings: Settings):
        getter = build_salesforce_token_getter(sf_token_settings)
        assert getter() == "live-static-token"

    def test_falls_back_to_oauth_token_getter(self, sf_oauth_settings: Settings):
        with patch("sdk.connectors.edh.make_salesforce_oauth_token_getter") as mock_make:
            mock_make.return_value = lambda: "oauth-token-abc"
            getter = build_salesforce_token_getter(sf_oauth_settings)

            assert getter() == "oauth-token-abc"
            mock_make.assert_called_once()
            _, kwargs = mock_make.call_args
            assert kwargs["instance_url"] == "https://acme.my.salesforce.com"
            assert kwargs["client_id_getter"]() == "sf-client-id-123"
            assert kwargs["client_secret_getter"]() == "sf-client-secret-xyz"

    def test_unconfigured_raises_sdk_gateway_error(self, unconfigured_settings: Settings):
        with pytest.raises(SdkGatewayError):
            build_salesforce_token_getter(unconfigured_settings)


class TestCreateEdhConnector:
    def test_salesforce_connector_creation(self, sf_token_settings: Settings):
        with patch("sdk.connectors.edh.EdhConnector") as mock_conn_cls, \
             patch("sdk.models.edh_models.EdhConnectorConfig") as mock_cfg_cls:
            conn = create_connector(sf_token_settings, strategy="salesforce", namespace="customer")
            assert conn == mock_conn_cls.return_value
            mock_cfg_cls.assert_called_once()
            _, kwargs = mock_cfg_cls.call_args
            assert kwargs["strategy"] == "salesforce"
            assert kwargs["namespace"] == "customer"
            assert kwargs["endpoint_url"] == "https://acme.my.salesforce.com"

    def test_salesforce_unconfigured_raises(self, unconfigured_settings: Settings):
        with pytest.raises(SdkGatewayError):
            create_connector(unconfigured_settings, strategy="salesforce")

    def test_api_hub_connector_creation(self):
        settings = Settings(
            DATA_PROVIDER_MODE="SDK",
            SDK_API_KEY="valid-key",
            SDK_BASE_URL="https://edh.example.com",
            SDK_TIMEOUT_SECONDS=15,
        )
        with patch("sdk.connectors.edh.EdhConnector") as mock_conn_cls, \
             patch("sdk.models.edh_models.EdhConnectorConfig") as mock_cfg_cls:
            conn = create_connector(settings, strategy="api_hub", namespace="customer")
            assert conn == mock_conn_cls.return_value
            mock_cfg_cls.assert_called_once()
            _, kwargs = mock_cfg_cls.call_args
            assert kwargs["strategy"] == "api_hub"
            assert kwargs["namespace"] == "customer"
            assert kwargs["endpoint_url"] == "https://edh.example.com"
            assert kwargs["api_key_getter"]() == "valid-key"
            assert kwargs["timeout_seconds"] == 15.0

    def test_api_hub_missing_api_key_raises(self):
        settings = Settings(
            DATA_PROVIDER_MODE="SDK",
            SDK_API_KEY="",
            SDK_BASE_URL="https://edh.example.com",
        )
        with pytest.raises(AuthenticationError):
            create_connector(settings, strategy="api_hub")

    def test_api_hub_missing_base_url_raises(self):
        settings = Settings(
            DATA_PROVIDER_MODE="SDK",
            SDK_API_KEY="valid-key",
            SDK_BASE_URL="",
        )
        with pytest.raises(SdkGatewayError):
            create_connector(settings, strategy="api_hub")

    def test_unsupported_strategy_raises(self, sf_token_settings: Settings):
        with pytest.raises(ValueError, match="Unsupported connector strategy"):
            create_connector(sf_token_settings, strategy="unknown_strategy")


class TestCreateAsyncEdhConnector:
    def test_async_connector_creation(self, sf_token_settings: Settings):
        with patch("common.infrastructure.edh_connector.create_connector") as mock_sync_factory, \
             patch("sdk.connectors.edh.AsyncEdhConnector") as mock_async_cls:
            sync_mock = MagicMock()
            sync_mock.config = MagicMock()
            sync_mock.strategy = "salesforce"
            mock_sync_factory.return_value = sync_mock

            async_conn = create_async_edh_connector(sf_token_settings, strategy="salesforce")
            assert async_conn == mock_async_cls.return_value
            mock_async_cls.assert_called_once_with(sync_mock.config, strategy="salesforce")
