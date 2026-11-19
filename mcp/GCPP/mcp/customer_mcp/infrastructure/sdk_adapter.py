"""SDK adapter: connects to Enterprise Data Hub (EDH) + Salesforce via rag_platform_sdk.

Architecture notes
------------------
* EDH connector  – used for internal customer data (unchanged behaviour).
* Salesforce connectors – thin wrappers around the same factory helpers used by
  ``salesforce_service.py`` so the two services share identical auth/retry logic.
* Record-retrieval helpers (``get_record_by_id`` / ``get_record_by_number`` and
  their async twins) delegate to the shared Salesforce connectors and mirror the
  SOQL/query patterns from ``SalesforceService``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from config.settings import Settings
from common.domain.value_objects import CustomerId, MachineId, SiteId
from common.infrastructure.connector import (
    build_salesforce_token_getter,
    create_async_edh_connector,
    create_connector,
)
from common.infrastructure.resilience import (
    call_sdk_with_retry,
    handle_sdk_exception,
    retry_with_backoff,
)
from core.exceptions import (
    AuthenticationError,
    EntityNotFoundError,
    RateLimitError,
    SdkGatewayError,
)
from core.logging import get_logger
from mcp.customer_mcp.application.ports import CustomerRepositoryPort
from mcp.customer_mcp.domain.entities import (
    AccountRepresentative,
    Address,
    AssignedMachine,
    Customer,
    CustomerEntitlements,
    CustomerMachineAssignment,
    ServiceEntitlement,
)

logger = get_logger(__name__)

# Default number field names per SObject, mirrored from salesforce_service.py so
# both services agree on field conventions without duplicating the mapping.
_DEFAULT_NUMBER_FIELDS: dict[str, str] = {
    "contact": "AccountNumber",
    "case": "CaseNumber",
    "contract": "ContractNumber",
    "order": "OrderNumber",
    "solution": "SolutionNumber",
    "asset": "SerialNumber",
}


class SdkCustomerAdapter(CustomerRepositoryPort):
    """SDK adapter connecting to Enterprise Data Hub (EDH) via rag_platform_sdk.

    Additionally exposes Salesforce record-retrieval helpers that reuse the same
    connector factory and auth logic as ``SalesforceService``.

    Parameters
    ----------
    settings:
        Application settings injected at construction time (dependency injection).
    sf_connector:
        Optional pre-built synchronous Salesforce connector (useful for testing).
    sf_async_connector:
        Optional pre-built asynchronous Salesforce connector (useful for testing).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        sf_connector: Any = None,
        sf_async_connector: Any = None,
    ) -> None:
        self._settings = settings
        # EDH (internal data hub) connector – lazy-initialised on first use.
        self._connector: Any = None
        # Salesforce connectors – accept pre-built instances for DI / testing.
        self._sf_connector: Any = sf_connector
        self._sf_async_connector: Any = sf_async_connector

    # ------------------------------------------------------------------
    # Private: EDH connector
    # ------------------------------------------------------------------

    def _get_connector(self) -> Any:
        """Return (and lazily initialise) the EDH connector.

        Raises
        ------
        AuthenticationError
            When ``SDK_API_KEY`` is absent or empty.
        SdkGatewayError
            When ``SDK_BASE_URL`` is absent or any other initialisation error occurs.
        """
        if self._connector is None:
            try:
                self._connector = create_connector(
                    self._settings,
                    strategy="salesforce",
                    namespace="customer",
                )
            except (AuthenticationError, RateLimitError, SdkGatewayError):
                raise
            except Exception as e:
                logger.error("Failed to initialise EdhConnector for Customer", error=str(e))
                handle_sdk_exception(e, "initialising Customer connector")
        return self._connector

    # ------------------------------------------------------------------
    # Private: Salesforce connectors
    # ------------------------------------------------------------------

    def _ensure_sf_configured(self) -> None:
        """Raise ``SdkGatewayError`` when Salesforce settings are missing."""
        if self._sf_connector is not None or self._sf_async_connector is not None:
            return  # injected connectors override settings check
        if not self._settings.is_salesforce_configured:
            raise SdkGatewayError(
                "Salesforce is not fully configured. "
                "Set SF_INSTANCE_URL and either SF_ACCESS_TOKEN or "
                "SF_CLIENT_ID + SF_CLIENT_SECRET in your environment."
            )

    def _resolve_sf_sync_connector(self) -> Any:
        """Return the synchronous Salesforce connector, building it if needed."""
        self._ensure_sf_configured()
        if self._sf_connector is not None:
            return self._sf_connector
        return create_connector(
            self._settings,
            strategy="salesforce",
            namespace="customer",
        )

    def _resolve_sf_async_connector(self) -> Any:
        """Return the asynchronous Salesforce connector, building it if needed."""
        self._ensure_sf_configured()
        if self._sf_async_connector is not None:
            return self._sf_async_connector
        if self._sf_connector is not None:
            from sdk.connectors.edh import AsyncEdhConnector
            return AsyncEdhConnector(
                self._sf_connector.config,
                strategy=self._sf_connector.strategy,
            )
        return create_async_edh_connector(
            self._settings,
            strategy="salesforce",
            namespace="customer",
        )

    def _build_sf_token_getter(self) -> Any:
        """Return a callable that yields a Salesforce auth token.

        Prefers a static access-token; falls back to OAuth client-credentials.
        Delegates to ``build_salesforce_token_getter``.
        """
        return build_salesforce_token_getter(self._settings)

    # ------------------------------------------------------------------
    # Private: shared Salesforce query helpers
    # ------------------------------------------------------------------

    def _resolve_number_field(self, object_name: str, field_name: str | None) -> str:
        """Return the resolved number field name for *object_name*.

        Uses *field_name* when provided; otherwise looks up the default from
        ``_DEFAULT_NUMBER_FIELDS`` and falls back to ``{object_name}Number``.
        """
        if field_name and field_name.strip():
            return field_name.strip()
        return _DEFAULT_NUMBER_FIELDS.get(object_name.lower(), f"{object_name}Number")

    def _validate_record_number(self, record_number: str, operation: str) -> str:
        """Strip and validate that *record_number* is non-empty.

        Returns the cleaned value or raises ``ValueError``.
        """
        clean = record_number.strip()
        if not clean:
            raise ValueError(
                f"record_number cannot be empty (operation={operation})"
            )
        return clean

    # ------------------------------------------------------------------
    # Public: Salesforce – get_record_by_id (sync + async)
    # ------------------------------------------------------------------

    def get_record_by_id(
        self,
        object_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by its Salesforce ID (synchronous).

        Parameters
        ----------
        object_name:
            Salesforce SObject API name, e.g. ``"contact"``.
        record_id:
            18-character Salesforce record ID.
        fields:
            Optional list of field API names to return.  ``None`` returns all.

        Returns
        -------
        dict[str, Any]
            Salesforce record payload.

        Raises
        ------
        EntityNotFoundError
            When no record with *record_id* exists.
        SdkGatewayError
            For connection / query errors.
        """
        connector = self._resolve_sf_sync_connector()
        logger.info(
            "Fetching Salesforce record by ID (sync)",
            object_name=object_name,
            record_id=record_id,
        )
        with connector as sf:
            record = sf.getRecordById(object_name, record_id, fields=fields)
        logger.info(
            "Fetched Salesforce record by ID (sync)",
            object_name=object_name,
            record_id=record_id,
        )
        return record

    async def aget_record_by_id(
        self,
        object_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by its Salesforce ID (async).

        Delegates to :meth:`get_record_by_id` via :func:`asyncio.to_thread` so
        that the synchronous SDK connector is called without blocking the event
        loop — the same pattern used by the EDH-backed repository methods.

        Parameters
        ----------
        object_name:
            Salesforce SObject API name, e.g. ``"contact"``.
        record_id:
            18-character Salesforce record ID.
        fields:
            Optional list of field API names to return.  ``None`` returns all.

        Returns
        -------
        dict[str, Any]
            Salesforce record payload.

        Raises
        ------
        EntityNotFoundError
            When no record with *record_id* exists.
        SdkGatewayError
            For connection / query errors.
        """
        return await asyncio.to_thread(
            self.get_record_by_id, object_name, record_id, fields=fields
        )

    # ------------------------------------------------------------------
    # Public: Salesforce – get_record_by_number (sync + async)
    # ------------------------------------------------------------------

    def get_record_by_number(
        self,
        object_name: str,
        field_name: str,
        record_number: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by its human-readable number field (synchronous).

        Parameters
        ----------
        object_name:
            Salesforce SObject API name, e.g. ``"Case"``.
        field_name:
            The API name of the number field to query on (e.g. ``"CaseNumber"``).
            Falls back to ``_DEFAULT_NUMBER_FIELDS`` when empty.
        record_number:
            The number value to look up (e.g. ``"00001234"``).
        fields:
            Optional list of field API names to return.  ``None`` returns all.

        Returns
        -------
        dict[str, Any]
            First matching Salesforce record.

        Raises
        ------
        ValueError
            When *record_number* is empty.
        EntityNotFoundError
            When no record matches.
        SdkGatewayError
            For connection / query errors.
        """
        clean_number = self._validate_record_number(record_number, "get_record_by_number")
        resolved_field = self._resolve_number_field(object_name, field_name)

        logger.info(
            "Fetching Salesforce record by number (sync)",
            object_name=object_name,
            field_name=resolved_field,
            record_number=clean_number,
        )
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            records = sf.query(
                object_name,
                fields=fields,
                where={resolved_field: clean_number},
                limit=1,
            )

        if not records:
            raise EntityNotFoundError(
                f"No {object_name} record found with {resolved_field}='{clean_number}'"
            )

        logger.info(
            "Fetched Salesforce record by number (sync)",
            object_name=object_name,
            field_name=resolved_field,
            record_number=clean_number,
        )
        return records[0]

    async def aget_record_by_number(
        self,
        object_name: str,
        field_name: str,
        record_number: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a Salesforce SObject record by its human-readable number field (async).

        Delegates to :meth:`get_record_by_number` via :func:`asyncio.to_thread`
        so the synchronous SDK connector is called without blocking the event loop.

        Parameters
        ----------
        object_name:
            Salesforce SObject API name, e.g. ``"Case"``.
        field_name:
            The API name of the number field to query on (e.g. ``"CaseNumber"``).
            Falls back to ``_DEFAULT_NUMBER_FIELDS`` when empty.
        record_number:
            The number value to look up (e.g. ``"00001234"``).
        fields:
            Optional list of field API names to return.  ``None`` returns all.

        Returns
        -------
        dict[str, Any]
            First matching Salesforce record.

        Raises
        ------
        ValueError
            When *record_number* is empty.
        EntityNotFoundError
            When no record matches.
        SdkGatewayError
            For connection / query errors.
        """
        return await asyncio.to_thread(
            self.get_record_by_number, object_name, field_name, record_number, fields=fields
        )

    # ------------------------------------------------------------------
    # Public: Salesforce – SOQL / generic query (sync + async)
    # ------------------------------------------------------------------

    def query_salesforce(
        self,
        object_name_or_soql: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SOQL or filtered SObject query against Salesforce (synchronous).

        Parameters
        ----------
        object_name_or_soql:
            SObject API name (e.g. ``"contact"``) or a full SOQL statement.
        fields:
            Optional sequence of field API names to return.
        where:
            Optional filter mapping (e.g. ``{"Type": "Customer"}``) or WHERE clause string.
        limit:
            Optional maximum number of records to return.

        Returns
        -------
        list[dict[str, Any]]
            Matching Salesforce records.
        """
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            records = sf.query(object_name_or_soql, fields=fields, where=where, limit=limit)
            logger.info(
                "Executed Salesforce query (sync)",
                target=object_name_or_soql,
                count=len(records),
            )
            return records

    async def aquery_salesforce(
        self,
        object_name_or_soql: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a query against Salesforce (async).

        Delegates to :meth:`query_salesforce` via :func:`asyncio.to_thread`
        so the SDK connector is called without blocking the event loop.
        """
        return await asyncio.to_thread(
            self.query_salesforce,
            object_name_or_soql,
            fields=fields,
            where=where,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # CustomerRepositoryPort – EDH-backed implementations (unchanged)
    # Public: Salesforce – create / update / delete / describe_schema
    # (Mirrors SalesforceService from SDK backend)
    # ------------------------------------------------------------------

    def create_record(
        self,
        object_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a new Salesforce SObject record (synchronous)."""
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            if hasattr(sf, "create_record"):
                result = sf.create_record(object_name, data)
            elif hasattr(sf, "createRecord"):
                result = sf.createRecord(object_name, data)
            else:
                result = sf.Create(object_name, data)
            logger.info("Created Salesforce record (sync)", object_name=object_name, id=result.get("id"))
            return result

    async def acreate_record(
        self,
        object_name: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a new Salesforce SObject record (async)."""
        return await asyncio.to_thread(self.create_record, object_name, data)

    createRecord = acreate_record

    def update_record(
        self,
        object_name: str,
        record_id: str,
        data: Mapping[str, Any],
    ) -> bool:
        """Update an existing Salesforce SObject record by ID (synchronous)."""
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            if hasattr(sf, "update"):
                result = sf.update(object_name, record_id, data)
            elif hasattr(sf, "Update"):
                result = sf.Update(object_name, record_id, data)
            else:
                result = sf.update_record(object_name, record_id, data)
            logger.info("Updated Salesforce record (sync)", object_name=object_name, record_id=record_id)
            return bool(result)

    async def aupdate_record(
        self,
        object_name: str,
        record_id: str,
        data: Mapping[str, Any],
    ) -> bool:
        """Update an existing Salesforce SObject record by ID (async)."""
        return await asyncio.to_thread(self.update_record, object_name, record_id, data)

    updateRecord = aupdate_record

    def delete_record(
        self,
        object_name: str,
        record_id: str,
    ) -> bool:
        """Delete an existing Salesforce SObject record by ID (synchronous)."""
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            if hasattr(sf, "delete_record"):
                result = sf.delete_record(object_name, record_id)
            elif hasattr(sf, "deleteRecord"):
                result = sf.deleteRecord(object_name, record_id)
            else:
                result = sf.Delete(object_name, record_id)
            logger.info("Deleted Salesforce record (sync)", object_name=object_name, record_id=record_id)
            return bool(result)

    async def adelete_record(
        self,
        object_name: str,
        record_id: str,
    ) -> bool:
        """Delete an existing Salesforce SObject record by ID (async)."""
        return await asyncio.to_thread(self.delete_record, object_name, record_id)

    deleteRecord = adelete_record

    def describe_schema(
        self,
        sobject_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe Salesforce SObject schema metadata (synchronous)."""
        connector = self._resolve_sf_sync_connector()
        with connector as sf:
            if hasattr(sf, "describe_schema"):
                result = sf.describe_schema(sobject_name)
            elif hasattr(sf, "DescribeSchema"):
                result = sf.DescribeSchema(sobject_name)
            else:
                result = sf.describeSchema(sobject_name)
            logger.info("Described Salesforce schema (sync)", sobject_name=sobject_name)
            return result

    async def adescribe_schema(
        self,
        sobject_name: str | None = None,
    ) -> dict[str, Any]:
        """Describe Salesforce SObject schema metadata (async)."""
        return await asyncio.to_thread(self.describe_schema, sobject_name)

    describeSchema = adescribe_schema

    # CamelCase and convenience aliases matching SalesforceService
    getRecordById = aget_record_by_id
    getRecordByNumber = aget_record_by_number
    get_record_by_id_sync = get_record_by_id
    get_record_by_number_sync = get_record_by_number
    query = aquery_salesforce
    query_sync = query_salesforce

    # ------------------------------------------------------------------
    # CustomerRepositoryPort – Salesforce-first with EDH fallback
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, customer_id: CustomerId) -> Customer:
        """Fetch a Customer entity using specific Salesforce methods with EDH fallback."""
        cid_str = str(customer_id)
        try:
            # 1. Prefer specific Salesforce retrieval methods when configured
            if self._settings.is_salesforce_configured:
                try:
                    # Check if 15 or 18-character Salesforce SObject ID (e.g. 001...)
                    # if cid_str.startswith("001") or len(cid_str) in (15, 18):
                    rec = await self.aget_record_by_id("contact", cid_str)
                    if rec:
                        return self._map_record_to_customer(rec)
                    # Otherwise look up by AccountNumber via specific get_record_by_number
                    # rec = await self.aget_record_by_number("contact", "AccountNumber", cid_str)
                    # if rec:
                    #     return self._map_record_to_customer(rec)
                except EntityNotFoundError:
                    raise
                except Exception as sf_err:
                    logger.debug("Salesforce-specific get_record failed, falling back to data hub", error=str(sf_err))

            # 2. Data hub / EDH partition fallback
            from sdk.models import DataZone
            connector = self._get_connector()
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "customers",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": cid_str},
                    )
                ),
            )
            if not records:
                raise EntityNotFoundError(f"Customer {customer_id} not found in SDK data hub")
            return self._map_record_to_customer(records[0])
        except (EntityNotFoundError, AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error getting customer profile", customer_id=cid_str, error=str(e))
            handle_sdk_exception(e, f"getting customer profile for {customer_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def search(
        self,
        query: str,
        region: Optional[str],
        tier: Optional[str],
        limit: int,
        offset: int,
    ) -> list[Customer]:
        """Search Customers using specific Salesforce query methods with EDH fallback."""
        try:
            # 1. Prefer specific Salesforce query pushing filters to SOQL
            if self._settings.is_salesforce_configured:
                try:
                    where_clauses: dict[str, Any] = {}
                    if region:
                        where_clauses["BillingState"] = region
                    if tier:
                        where_clauses["Type"] = tier
                    sf_records = await self.aquery_salesforce("contact", where=where_clauses or None, limit=limit + offset)
                    if sf_records:
                        matching = [
                            self._map_record_to_customer(r)
                            for r in sf_records
                            if not query or query.lower() in (r.get("Name") or r.get("company_name") or "").lower()
                        ]
                        return matching[offset: offset + limit]
                except Exception as sf_err:
                    logger.debug("Salesforce-specific query failed, falling back to data hub", error=str(sf_err))

            # 2. Data hub / EDH partition fallback
            from sdk.models import DataZone
            connector = self._get_connector()
            filter_map: dict[str, str] = {}
            if region:
                filter_map["region"] = region
            if tier:
                filter_map["tier"] = tier

            # ARCH-05: Stream iterator and slice lazily to prevent loading 10K+ records into memory.
            def _stream_and_filter():
                import itertools
                records_iter = connector.read_records(
                    "customers",
                    zone=DataZone.CURATED,
                    partition_filter=filter_map,
                )
                matching = (
                    self._map_record_to_customer(r)
                    for r in records_iter
                    if not query or query.lower() in r.get("company_name", "").lower()
                )
                return list(itertools.islice(matching, offset, offset + limit))

            return await asyncio.to_thread(call_sdk_with_retry, _stream_and_filter)
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error searching customers", query=query, error=str(e))
            handle_sdk_exception(e, "searching customers")
            raise

    @retry_with_backoff(max_attempts=3)
    async def get_entitlements(self, customer_id: CustomerId) -> CustomerEntitlements:
        """Fetch service entitlements using specific Salesforce Contract query with EDH fallback."""
        cid_str = str(customer_id)
        try:
            # 1. Prefer specific Salesforce Contract query when configured
            if self._settings.is_salesforce_configured:
                try:
                    where_filter = {"AccountId": cid_str} if cid_str.startswith("001") else {"AccountNumber": cid_str}
                    contracts = await self.aquery_salesforce("Contract", where=where_filter)
                    if contracts:
                        entitlements = [self._map_record_to_entitlement(c) for c in contracts]
                        return CustomerEntitlements(
                            customer_id=customer_id,
                            entitlements=entitlements,
                            total_active=sum(1 for e in entitlements if e.is_active),
                        )
                except Exception as sf_err:
                    logger.debug("Salesforce contract query failed, falling back to data hub", error=str(sf_err))

            # 2. Data hub / EDH partition fallback
            from sdk.models import DataZone
            connector = self._get_connector()
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "customer_entitlements",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": cid_str},
                    )
                ),
            )
            entitlements = [self._map_record_to_entitlement(r) for r in records]
            return CustomerEntitlements(
                customer_id=customer_id,
                entitlements=entitlements,
                total_active=sum(1 for e in entitlements if e.is_active),
            )
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error fetching customer entitlements", customer_id=cid_str, error=str(e))
            handle_sdk_exception(e, f"fetching customer entitlements for {customer_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def get_assigned_machines(
        self,
        customer_id: CustomerId,
        site_id: Optional[SiteId] = None,
    ) -> CustomerMachineAssignment:
        """Fetch machine assignments using specific Salesforce Asset query with EDH fallback."""
        cid_str = str(customer_id)
        try:
            # 1. Prefer specific Salesforce Asset query when configured
            if self._settings.is_salesforce_configured:
                try:
                    where_cond: dict[str, Any] = {"AccountId": cid_str} if cid_str.startswith("001") else {"AccountNumber": cid_str}
                    if site_id:
                        where_cond["SiteId__c"] = str(site_id)
                    assets = await self.aquery_salesforce("Asset", where=where_cond)
                    if assets:
                        machines = [
                            AssignedMachine(
                                machine_id=MachineId(a.get("SerialNumber") or a.get("machine_id") or a.get("Id")),
                                site_id=SiteId(a.get("SiteId__c") or a.get("site_id") or "SITE-A1B2"),
                                model_type=a.get("Model__c") or a.get("model_type", "UNKNOWN"),
                                assigned_date=date.fromisoformat(a.get("InstallDate") or a.get("assigned_date", "2024-01-01")),
                            )
                            for a in assets
                        ]
                        return CustomerMachineAssignment(
                            customer_id=customer_id,
                            machines=machines,
                            total_count=len(machines),
                        )
                except Exception as sf_err:
                    logger.debug("Salesforce asset query failed, falling back to data hub", error=str(sf_err))

            # 2. Data hub / EDH partition fallback
            from sdk.models import DataZone
            connector = self._get_connector()
            filter_map: dict[str, str] = {"customer_id": cid_str}
            if site_id:
                filter_map["site_id"] = str(site_id)

            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "customer_machines",
                        zone=DataZone.CURATED,
                        partition_filter=filter_map,
                    )
                ),
            )
            machines = [
                AssignedMachine(
                    machine_id=MachineId(r["machine_id"]),
                    site_id=SiteId(r["site_id"]),
                    model_type=r.get("model_type", "UNKNOWN"),
                    assigned_date=date.fromisoformat(r.get("assigned_date", "2024-01-01")),
                )
                for r in records
            ]
            return CustomerMachineAssignment(
                customer_id=customer_id,
                machines=machines,
                total_count=len(machines),
            )
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error fetching assigned machines", customer_id=cid_str, error=str(e))
            handle_sdk_exception(e, f"fetching assigned machines for {customer_id}")
            raise

    # ------------------------------------------------------------------
    # Private: mapping helpers
    # ------------------------------------------------------------------

    def _map_record_to_customer(self, rec: dict[str, Any]) -> Customer:
        """Map a raw EDH or Salesforce record dict to a :class:`Customer` domain entity."""
        addr = rec.get("address", {})
        reps = [
            AccountRepresentative(
                rep_id=r.get("rep_id", ""),
                name=r.get("name", ""),
                email=r.get("email", ""),
                phone=r.get("phone", ""),
                role=r.get("role", ""),
            )
            for r in rec.get("account_representatives", [])
        ]
        cid = rec.get("customer_id") or rec.get("AccountNumber") or rec.get("Id") or "UNKNOWN"
        cname = rec.get("company_name") or rec.get("Name") or "Unknown Company"
        tier = rec.get("tier") or rec.get("Tier__c") or rec.get("Type") or "STANDARD"
        billing_status = rec.get("billing_status") or rec.get("Status__c") or ("ACTIVE" if rec.get("Active__c") == "Yes" else "ACTIVE")
        region = rec.get("region") or rec.get("Region__c") or rec.get("BillingState") or ""
        industry = rec.get("industry") or rec.get("Industry") or ""

        street = addr.get("street") or rec.get("BillingStreet") or ""
        city = addr.get("city") or rec.get("BillingCity") or ""
        state = addr.get("state") or rec.get("BillingState") or ""
        country = addr.get("country") or rec.get("BillingCountry") or ""
        postal_code = addr.get("postal_code") or rec.get("BillingPostalCode") or ""

        created_val = rec.get("created_at") or rec.get("CreatedDate") or "2024-01-01T00:00:00+00:00"
        updated_val = rec.get("updated_at") or rec.get("LastModifiedDate") or "2024-01-01T00:00:00+00:00"

        return Customer(
            customer_id=CustomerId(cid),
            company_name=cname,
            tier=tier,
            billing_status=billing_status,
            region=region,
            industry=industry,
            address=Address(
                street=street,
                city=city,
                state=state,
                country=country,
                postal_code=postal_code,
            ),
            account_representatives=reps,
            created_at=datetime.fromisoformat(created_val) if isinstance(created_val, str) else created_val,
            updated_at=datetime.fromisoformat(updated_val) if isinstance(updated_val, str) else updated_val,
            employee_count=int(rec.get("employee_count") or rec.get("NumberOfEmployees") or 0),
            annual_revenue_usd=int(rec.get("annual_revenue_usd") or rec.get("AnnualRevenue") or 0),
        )

    def _map_record_to_entitlement(self, rec: dict[str, Any]) -> ServiceEntitlement:
        """Map a raw EDH or Salesforce record dict to a :class:`ServiceEntitlement` domain entity."""
        ent_id = rec.get("entitlement_id") or rec.get("ContractNumber") or rec.get("Id") or ""
        service_name = rec.get("service_name") or rec.get("Name") or rec.get("SpecialTerms") or "Standard Service"
        sla = rec.get("sla_level") or rec.get("SlaLevel__c") or "SILVER"
        features_raw = rec.get("features", [])
        features = list(features_raw) if isinstance(features_raw, (list, tuple)) else [str(features_raw)]
        exp_date = rec.get("expiration_date") or rec.get("EndDate") or "2026-12-31"
        if isinstance(exp_date, str):
            exp_date = date.fromisoformat(exp_date)
        is_active = bool(rec.get("is_active", rec.get("Status") == "Activated"))
        max_machines = int(rec.get("max_machines") or rec.get("MaxMachines__c") or 10)

        return ServiceEntitlement(
            entitlement_id=ent_id,
            service_name=service_name,
            sla_level=sla,
            features=features,
            expiration_date=exp_date,
            is_active=is_active,
            max_machines=max_machines,
        )
