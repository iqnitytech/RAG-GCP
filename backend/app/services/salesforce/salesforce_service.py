"""Salesforce service wrapping the RAG Platform SDK ``SalesforceEdhStrategy``."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Sequence

from sdk.connectors.edh import (
    AsyncEdhConnector,
    DataZone,
    EdhConfigError,
    EdhConnector,
    EdhObjectMetadata,
    EdhObjectRef,
    SalesforceErrorConstants,
    log_and_build_edh_error,
)

from app.connectors.edh_connector import (
    create_async_edh_connector,
    create_edh_connector,
)
from app.core.config import Settings, get_settings

logger = logging.getLogger("app.services.salesforce")


class SalesforceService:
    """Service layer connecting to Salesforce via the SDK EDH Connector."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        connector: EdhConnector | None = None,
        async_connector: AsyncEdhConnector | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._connector = connector
        self._async_connector = async_connector

    def _ensure_configured(self) -> None:
        if self._connector is not None or self._async_connector is not None:
            return
        if not self._settings.is_salesforce_configured:
            raise log_and_build_edh_error(
                logger,
                EdhConfigError,
                SalesforceErrorConstants.MSG_MISSING_ENDPOINT,
                provider=SalesforceErrorConstants.PROVIDER,
                operation="getRecordById",
                error_code=SalesforceErrorConstants.ERR_MISSING_ENDPOINT,
                public_message=SalesforceErrorConstants.PUBLIC_CONFIG_ERROR,
            )

    def _resolve_sync_connector(self) -> EdhConnector:
        self._ensure_configured()
        if self._connector is not None:
            return self._connector
        return create_edh_connector(self._settings, strategy="salesforce")

    def _resolve_async_connector(self) -> AsyncEdhConnector:
        self._ensure_configured()
        if self._async_connector is not None:
            return self._async_connector
        if self._connector is not None:
            return AsyncEdhConnector(
                self._connector.config,
                strategy=self._connector.strategy,
            )
        return create_async_edh_connector(self._settings, strategy="salesforce")

    def get_record_by_id_sync(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by ID using the synchronous SDK connector."""
        connector = self._resolve_sync_connector()
        with connector as sf:
            record = sf.getRecordById(sobject_name, record_id, fields=fields)
            logger.info(
                "Fetched Salesforce record -- sobject=%s record_id=%s",
                sobject_name,
                record_id,
            )
            return record

    async def get_record_by_id(
        self,
        sobject_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by ID using the async SDK connector."""
        async_connector = self._resolve_async_connector()
        async with async_connector as sf:
            record = await sf.getRecordById(sobject_name, record_id, fields=fields)
            logger.info(
                "Fetched Salesforce record (async) -- sobject=%s record_id=%s",
                sobject_name,
                record_id,
            )
            return record

    async def query(
        self,
        sobject_name: str,
        *,
        fields: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run a SOQL query for ``sobject_name`` using the async SDK connector."""
        async_connector = self._resolve_async_connector()
        async with async_connector as sf:
            return await sf.query(sobject_name, fields=fields, where=where, limit=limit)

    async def list_shared_files(
        self,
        path_prefix: str = "",
        *,
        zone: DataZone = DataZone.RAW,
        max_files: int = 500,
    ) -> list[EdhObjectMetadata]:
        """List files under a shared Salesforce path prefix (ContentVersion objects)."""
        async_connector = self._resolve_async_connector()
        items: list[EdhObjectMetadata] = []
        token: str | None = None
        async with async_connector as sf:
            while len(items) < max_files:
                page_size = min(100, max_files - len(items))
                page = await sf.list_objects(
                    zone=zone,
                    prefix=path_prefix,
                    page_size=page_size,
                    page_token=token,
                )
                items.extend(page.items)
                if not page.next_page_token:
                    break
                token = page.next_page_token
        return items

    async def download_shared_file(
        self,
        key: str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bytes:
        """Download binary file content for a shared Salesforce file key."""
        async_connector = self._resolve_async_connector()
        async with async_connector as sf:
            ref = EdhObjectRef(
                namespace=sf.config.namespace,
                key=key,
                zone=zone,
            )
            return await sf.get_bytes(ref)

    # CamelCase alias matching SDK naming
    getRecordById = get_record_by_id


@lru_cache(maxsize=1)
def get_salesforce_service() -> SalesforceService:
    """Return a cached :class:`SalesforceService` instance."""
    return SalesforceService(get_settings())
