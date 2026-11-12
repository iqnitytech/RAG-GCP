"""Enterprise Data Hub (EDH) connector factory bound to backend settings."""

from functools import lru_cache
from typing import Callable

from sdk.connectors.edh import (
    EdhConnector,
    make_databricks_oauth_token_getter,
    make_salesforce_oauth_token_getter,
)
from sdk.models.edh_models import EdhConnectorConfig

from app.core.config import Settings, get_settings

_SALESFORCE_STRATEGIES = {"salesforce", "sfdc", "salesforce_crm"}
_DATABRICKS_STRATEGIES = {"databricks", "databricks_uc", "unity_catalog", "dbx"}


def create_edh_connector(
    settings: Settings | None = None,
    *,
    strategy: str = "data_lake",
    root_path: str = "./.data_lake",
    endpoint_url: str = "",
    auth_token_getter: Callable[[], str] | None = None,
) -> EdhConnector:
    """Build an :class:`EdhConnector` configured from application settings.

    - When ``strategy`` is ``"salesforce"`` (or ``"sfdc"`` / ``"salesforce_crm"``),
      automatically resolves ``endpoint_url`` from ``settings.SF_INSTANCE_URL`` and
      wires :func:`make_salesforce_oauth_token_getter` from ``settings.SF_CLIENT_ID``
      and ``settings.SF_CLIENT_SECRET``.
    - When ``strategy`` is ``"databricks"`` (or ``"databricks_uc"`` / ``"unity_catalog"``),
      automatically resolves ``endpoint_url`` from ``settings.DATABRICKS_HOST``,
      propagates Unity Catalog / SQL Warehouse headers, and wires either
      ``settings.DATABRICKS_TOKEN`` or :func:`make_databricks_oauth_token_getter`
      from ``settings.DATABRICKS_CLIENT_ID`` and ``settings.DATABRICKS_CLIENT_SECRET``.
    """
    resolved = settings or get_settings()
    namespace = resolved.gcs_document_bucket or resolved.gcp_project_id or "rag-default"
    norm_strategy = strategy.strip().lower()

    effective_endpoint = endpoint_url
    if not effective_endpoint:
        if norm_strategy in _SALESFORCE_STRATEGIES:
            effective_endpoint = resolved.SF_INSTANCE_URL
        elif norm_strategy in _DATABRICKS_STRATEGIES:
            effective_endpoint = resolved.DATABRICKS_HOST

    effective_token_getter = auth_token_getter
    default_headers: dict[str, str] = {}

    if (
        effective_token_getter is None
        and norm_strategy in _SALESFORCE_STRATEGIES
        and effective_endpoint
        and resolved.SF_CLIENT_ID
        and resolved.SF_CLIENT_SECRET.get_secret_value()
    ):
        effective_token_getter = make_salesforce_oauth_token_getter(
            instance_url=effective_endpoint,
            client_id_getter=lambda: resolved.SF_CLIENT_ID,
            client_secret_getter=lambda: resolved.SF_CLIENT_SECRET.get_secret_value(),
        )
    elif norm_strategy in _DATABRICKS_STRATEGIES:
        default_headers = {
            "X-Databricks-Catalog": resolved.DATABRICKS_CATALOG,
            "X-Databricks-Schema": resolved.DATABRICKS_SCHEMA,
            "X-Databricks-Warehouse-Id": resolved.DATABRICKS_WAREHOUSE_ID,
        }
        if effective_token_getter is None:
            if resolved.DATABRICKS_TOKEN.get_secret_value():
                effective_token_getter = lambda: resolved.DATABRICKS_TOKEN.get_secret_value()
            elif (
                effective_endpoint
                and resolved.DATABRICKS_CLIENT_ID
                and resolved.DATABRICKS_CLIENT_SECRET.get_secret_value()
            ):
                effective_token_getter = make_databricks_oauth_token_getter(
                    workspace_url=effective_endpoint,
                    client_id_getter=lambda: resolved.DATABRICKS_CLIENT_ID,
                    client_secret_getter=lambda: resolved.DATABRICKS_CLIENT_SECRET.get_secret_value(),
                )

    config = EdhConnectorConfig(
        strategy=strategy,
        namespace=namespace,
        root_path=root_path,
        endpoint_url=effective_endpoint,
        region=resolved.gcp_region,
        auth_token_getter=effective_token_getter,
        default_headers=default_headers,
    )
    return EdhConnector(config)


@lru_cache(maxsize=1)
def get_edh_connector() -> EdhConnector:
    """Return a cached default :class:`EdhConnector` instance."""
    return create_edh_connector()

