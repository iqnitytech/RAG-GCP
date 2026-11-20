from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from domain.errors import AuthenticationError, NotFoundError, UpstreamError
from settings import Settings

logger = logging.getLogger("mcp.customer.gateway")

_DEFAULT_NUMBER_FIELDS: dict[str, str] = {
    "contact": "AccountNumber",
    "account": "AccountNumber",
    "case": "CaseNumber",
    "contract": "ContractNumber",
    "order": "OrderNumber",
    "user": "Username",
}


class SdkDataGateway:
    """Infrastructure Gateway managing low-level connectivity to Salesforce and EDH."""

    def __init__(
        self,
        settings: Settings,
        *,
        connector: Any = None,
        sf_connector: Any = None,
        sf_async_connector: Any = None,
    ) -> None:
        self._settings = settings
        self._connector: Any = connector
        self._sf_connector: Any = sf_connector
        self._sf_async_connector: Any = sf_async_connector

    # ------------------------------------------------------------------
    # Connector lifecycle
    # ------------------------------------------------------------------

    def get_edh_connector(self) -> Any:
        """Lazily initialize EDH API Hub connector from settings."""
        if self._connector is not None:
            return self._connector

        if not self._settings.sdk_api_key:
            raise AuthenticationError("SDK_API_KEY is not configured or empty")
        if not self._settings.sdk_base_url:
            raise UpstreamError("SDK_BASE_URL is not configured or empty")

        from sdk.connectors.edh import EdhConnector
        from sdk.models.edh_models import EdhConnectorConfig

        config = EdhConnectorConfig(
            strategy="api_hub",
            namespace="customer",
            endpoint_url=self._settings.sdk_base_url,
            api_key_getter=lambda: self._settings.sdk_api_key,
            timeout_seconds=float(self._settings.sdk_timeout_seconds),
        )
        self._connector = EdhConnector(config)
        logger.info("Initialized EdhConnector for API Hub customer namespace")
        return self._connector

    def resolve_sf_sync_connector(self) -> Any:
        """Return synchronous Salesforce EdhConnector, building it if needed."""
        if self._sf_connector is not None:
            return self._sf_connector

        if not self._settings.is_salesforce_configured:
            raise UpstreamError(
                "Salesforce is not fully configured. Set SF_INSTANCE_URL and either "
                "SF_ACCESS_TOKEN or SF_CLIENT_ID + SF_CLIENT_SECRET."
            )

        from sdk.connectors.edh import EdhConnector, make_salesforce_oauth_token_getter
        from sdk.models.edh_models import EdhConnectorConfig

        token = self._settings.sf_access_token.strip()
        if token and not token.lower().startswith("your-"):
            token_getter = lambda: self._settings.sf_access_token
        else:
            token_getter = make_salesforce_oauth_token_getter(
                instance_url=self._settings.sf_instance_url,
                client_id_getter=lambda: self._settings.sf_client_id,
                client_secret_getter=lambda: self._settings.sf_client_secret,
            )

        config = EdhConnectorConfig(
            strategy="salesforce",
            namespace="customer",
            endpoint_url=self._settings.sf_instance_url,
            auth_token_getter=token_getter,
        )
        self._sf_connector = EdhConnector(config)
        logger.info("Initialized EdhConnector for Salesforce customer namespace")
        return self._sf_connector

    # ------------------------------------------------------------------
    # Generic Salesforce record retrieval
    # ------------------------------------------------------------------

    def get_record_by_id(
        self,
        object_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a record by SObject name and ID."""
        connector = self.resolve_sf_sync_connector()
        with connector as sf:
            if hasattr(sf, "getRecordById"):
                return sf.getRecordById(object_name, record_id, fields=fields)
            if hasattr(sf, "get_record_by_id"):
                return sf.get_record_by_id(object_name, record_id, fields=fields)
            records = sf.query(object_name, fields=fields, where={"Id": record_id}, limit=1)
            if not records:
                raise NotFoundError(f"No {object_name} found with Id={record_id}")
            return records[0]

    def get_record_by_number(
        self,
        object_name: str,
        field_name: str,
        record_number: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a record by unique number field."""
        clean = record_number.strip()
        if not clean:
            raise ValueError("record_number cannot be empty")
        resolved_field = (
            field_name.strip()
            if field_name and field_name.strip()
            else _DEFAULT_NUMBER_FIELDS.get(object_name.lower(), f"{object_name}Number")
        )

        connector = self.resolve_sf_sync_connector()
        with connector as sf:
            records = sf.query(object_name, fields=fields, where={resolved_field: clean}, limit=1)
        if not records:
            raise NotFoundError(f"No {object_name} found with {resolved_field}='{clean}'")
        return records[0]

    def query_salesforce(
        self,
        object_name_or_soql: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run an SObject or SOQL query against Salesforce."""
        connector = self.resolve_sf_sync_connector()
        with connector as sf:
            return sf.query(object_name_or_soql, fields=fields, where=where, limit=limit)

    # ------------------------------------------------------------------
    # Generic EDH dataset retrieval
    # ------------------------------------------------------------------

    def read_edh_records(
        self,
        dataset: str,
        *,
        zone: Any = None,
        partition_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Read records from EDH for a given dataset and zone."""
        from sdk.models import DataZone

        resolved_zone = zone or DataZone.CURATED
        connector = self.get_edh_connector()
        kwargs: dict[str, Any] = {"zone": resolved_zone}
        if partition_filter:
            kwargs["partition_filter"] = partition_filter
        return list(connector.read_records(dataset, **kwargs))
