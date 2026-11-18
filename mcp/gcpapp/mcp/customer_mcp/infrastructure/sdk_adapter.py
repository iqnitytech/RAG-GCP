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
    "account": "AccountNumber",
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
            Salesforce SObject API name, e.g. ``"Account"``.
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
            Salesforce SObject API name, e.g. ``"Account"``.
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
            SObject API name (e.g. ``"Account"``) or a full SOQL statement.
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
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, customer_id: CustomerId) -> Customer:
        """Fetch a Customer entity from EDH by its internal ID."""
        try:
            from sdk.models import DataZone
            connector = self._get_connector()
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "customers",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": str(customer_id)},
                    )
                ),
            )
            if not records:
                raise EntityNotFoundError(f"Customer {customer_id} not found in SDK data hub")
            return self._map_record_to_customer(records[0])
        except (EntityNotFoundError, AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error getting customer profile", customer_id=str(customer_id), error=str(e))
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
        """Search Customers in EDH with optional filters and pagination."""
        try:
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
        """Fetch service entitlements for a Customer from EDH."""
        try:
            from sdk.models import DataZone
            connector = self._get_connector()
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "customer_entitlements",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": str(customer_id)},
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
            logger.error("SDK error fetching customer entitlements", customer_id=str(customer_id), error=str(e))
            handle_sdk_exception(e, f"fetching customer entitlements for {customer_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def get_assigned_machines(
        self,
        customer_id: CustomerId,
        site_id: Optional[SiteId] = None,
    ) -> CustomerMachineAssignment:
        """Fetch machine assignments for a Customer from EDH."""
        try:
            from sdk.models import DataZone
            connector = self._get_connector()
            filter_map: dict[str, str] = {"customer_id": str(customer_id)}
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
            logger.error("SDK error fetching assigned machines", customer_id=str(customer_id), error=str(e))
            handle_sdk_exception(e, f"fetching assigned machines for {customer_id}")
            raise

    # ------------------------------------------------------------------
    # Private: mapping helpers
    # ------------------------------------------------------------------

    def _map_record_to_customer(self, rec: dict[str, Any]) -> Customer:
        """Map a raw EDH record dict to a :class:`Customer` domain entity."""
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
        return Customer(
            customer_id=CustomerId(rec["customer_id"]),
            company_name=rec.get("company_name", ""),
            tier=rec.get("tier", "STANDARD"),
            billing_status=rec.get("billing_status", "ACTIVE"),
            region=rec.get("region", ""),
            industry=rec.get("industry", ""),
            address=Address(
                street=addr.get("street", ""),
                city=addr.get("city", ""),
                state=addr.get("state", ""),
                country=addr.get("country", ""),
                postal_code=addr.get("postal_code", ""),
            ),
            account_representatives=reps,
            created_at=datetime.fromisoformat(rec.get("created_at", "2024-01-01T00:00:00+00:00")),
            updated_at=datetime.fromisoformat(rec.get("updated_at", "2024-01-01T00:00:00+00:00")),
            employee_count=int(rec.get("employee_count", 0)),
            annual_revenue_usd=int(rec.get("annual_revenue_usd", 0)),
        )

    def _map_record_to_entitlement(self, rec: dict[str, Any]) -> ServiceEntitlement:
        """Map a raw EDH record dict to a :class:`ServiceEntitlement` domain entity."""
        exp_date = rec.get("expiration_date", "2026-12-31")
        if isinstance(exp_date, str):
            exp_date = date.fromisoformat(exp_date)
        return ServiceEntitlement(
            entitlement_id=rec.get("entitlement_id", ""),
            service_name=rec.get("service_name", ""),
            sla_level=rec.get("sla_level", "SILVER"),
            features=list(rec.get("features", [])),
            expiration_date=exp_date,
            is_active=bool(rec.get("is_active", True)),
            max_machines=int(rec.get("max_machines", 10)),
        )
