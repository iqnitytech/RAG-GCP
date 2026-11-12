"""Shared ``.env.integration`` loader and connector fixtures for real-endpoint SDK integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdk.connectors.edh import (
    AsyncEdhConnector,
    EdhConnector,
    EdhConnectorConfig,
    make_databricks_oauth_token_getter,
    make_salesforce_oauth_token_getter,
)
from tests.integration.constants import DEFAULT_NAMESPACE

INTEGRATION_ENV_FILE = Path(__file__).resolve().parent / ".env.integration"


def load_integration_env(env_path: Path = INTEGRATION_ENV_FILE) -> dict[str, str]:
    """Load key=value pairs from ``tests/integration/.env.integration`` merged with ``os.environ``."""
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip("'\"")
    for key, val in os.environ.items():
        if val:
            values[key] = val
    return values


@pytest.fixture
def integration_env() -> dict[str, str]:
    return load_integration_env()


@pytest.fixture
def salesforce_connector(integration_env: dict[str, str]) -> EdhConnector:
    sf_url = integration_env.get("SF_INSTANCE_URL", "")
    sf_client_id = integration_env.get("SF_CLIENT_ID", "")
    sf_client_secret = integration_env.get("SF_CLIENT_SECRET", "")
    namespace = integration_env.get("EDH_NAMESPACE", DEFAULT_NAMESPACE)

    if not (sf_url and sf_client_id and sf_client_secret):
        pytest.skip(
            f"Set SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET in {INTEGRATION_ENV_FILE}"
        )

    token_getter = make_salesforce_oauth_token_getter(
        instance_url=sf_url,
        client_id_getter=lambda: sf_client_id,
        client_secret_getter=lambda: sf_client_secret,
    )
    cfg = EdhConnectorConfig(
        strategy="salesforce",
        namespace=namespace,
        endpoint_url=sf_url,
        auth_token_getter=token_getter,
    )
    return EdhConnector(cfg)


@pytest.fixture
def async_salesforce_connector(salesforce_connector: EdhConnector) -> AsyncEdhConnector:
    return AsyncEdhConnector(
        salesforce_connector.config,
        strategy=salesforce_connector.strategy,
    )


@pytest.fixture
def databricks_connector(integration_env: dict[str, str]) -> EdhConnector:
    dbx_host = integration_env.get("DATABRICKS_HOST", "")
    dbx_token = integration_env.get("DATABRICKS_TOKEN", "")
    dbx_client_id = integration_env.get("DATABRICKS_CLIENT_ID", "")
    dbx_client_secret = integration_env.get("DATABRICKS_CLIENT_SECRET", "")
    dbx_catalog = integration_env.get("DATABRICKS_CATALOG", "main")
    dbx_schema = integration_env.get("DATABRICKS_SCHEMA", "default")
    dbx_warehouse = integration_env.get("DATABRICKS_WAREHOUSE_ID", "default")
    namespace = integration_env.get("EDH_NAMESPACE", DEFAULT_NAMESPACE)

    if not dbx_host or not (dbx_token or (dbx_client_id and dbx_client_secret)):
        pytest.skip(
            f"Set DATABRICKS_HOST and DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET) "
            f"in {INTEGRATION_ENV_FILE}"
        )

    token_getter = (
        (lambda: dbx_token)
        if dbx_token
        else make_databricks_oauth_token_getter(
            workspace_url=dbx_host,
            client_id_getter=lambda: dbx_client_id,
            client_secret_getter=lambda: dbx_client_secret,
        )
    )
    cfg = EdhConnectorConfig(
        strategy="databricks",
        namespace=namespace,
        endpoint_url=dbx_host,
        auth_token_getter=token_getter,
        default_headers={
            "X-Databricks-Catalog": dbx_catalog,
            "X-Databricks-Schema": dbx_schema,
            "X-Databricks-Warehouse-Id": dbx_warehouse,
        },
    )
    return EdhConnector(cfg)


@pytest.fixture
def api_hub_connector(integration_env: dict[str, str]) -> EdhConnector:
    api_url = integration_env.get("EDH_API_HUB_URL", "")
    api_token = integration_env.get("EDH_API_HUB_TOKEN", "")
    api_key = integration_env.get("EDH_API_HUB_KEY", "")
    namespace = integration_env.get("EDH_NAMESPACE", DEFAULT_NAMESPACE)

    if not api_url:
        pytest.skip(f"Set EDH_API_HUB_URL in {INTEGRATION_ENV_FILE}")

    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace=namespace,
        endpoint_url=api_url,
        auth_token_getter=(lambda: api_token) if api_token else None,
        api_key_getter=(lambda: api_key) if api_key else None,
    )
    return EdhConnector(cfg)


@pytest.fixture
def object_storage_connector(integration_env: dict[str, str]) -> EdhConnector:
    s3_url = integration_env.get("EDH_S3_ENDPOINT_URL", "")
    s3_region = integration_env.get("EDH_S3_REGION", "asia-south1")
    s3_access = integration_env.get("EDH_S3_ACCESS_KEY", "")
    s3_secret = integration_env.get("EDH_S3_SECRET_KEY", "")
    namespace = integration_env.get("EDH_NAMESPACE", DEFAULT_NAMESPACE)

    if not (s3_url and s3_access and s3_secret):
        pytest.skip(
            f"Set EDH_S3_ENDPOINT_URL, EDH_S3_ACCESS_KEY, and EDH_S3_SECRET_KEY in {INTEGRATION_ENV_FILE}"
        )

    cfg = EdhConnectorConfig(
        strategy="object_storage_hub",
        namespace=namespace,
        endpoint_url=s3_url,
        region=s3_region,
        access_key_getter=lambda: s3_access,
        secret_key_getter=lambda: s3_secret,
    )
    return EdhConnector(cfg)
