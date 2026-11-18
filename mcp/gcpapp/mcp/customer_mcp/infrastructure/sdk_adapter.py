from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
from typing import Optional, Any

from config.settings import Settings
from common.domain.value_objects import CustomerId, MachineId, SiteId
from common.infrastructure.resilience import call_sdk_with_retry, handle_sdk_exception, retry_with_backoff
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


class SdkCustomerAdapter(CustomerRepositoryPort):
    """SDK adapter connecting to Enterprise Data Hub (EDH) via rag_platform_sdk."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connector: Any = None

    def _get_connector(self) -> Any:
        if self._connector is None:
            if not self._settings.SDK_API_KEY:
                raise AuthenticationError("SDK_API_KEY is not configured or empty. Please set SDK_API_KEY in your environment.")
            if not self._settings.SDK_BASE_URL:
                raise SdkGatewayError("SDK_BASE_URL is not configured or empty. Please set SDK_BASE_URL in your environment.")
            try:
                from sdk.connectors.edh import EdhConnector
                from sdk.models import EdhConnectorConfig

                def get_api_key() -> str:
                    key = self._settings.SDK_API_KEY
                    if not key:
                        raise AuthenticationError("SDK_API_KEY is not configured or empty")
                    return key

                # SEC-01: No fallback to placeholder credentials (e.g. 'dev-key').
                # Settings already validated at startup and key getter explicitly validates key.
                config = EdhConnectorConfig(
                    strategy="api_hub",
                    namespace="customer",
                    endpoint_url=self._settings.SDK_BASE_URL,
                    api_key_getter=get_api_key,
                    timeout_seconds=float(self._settings.SDK_TIMEOUT_SECONDS),
                )
                self._connector = EdhConnector(config)
            except (AuthenticationError, RateLimitError):
                raise
            except Exception as e:
                logger.error("Failed to initialize EdhConnector for Customer", error=str(e))
                handle_sdk_exception(e, "initializing Customer connector")
        return self._connector

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, customer_id: CustomerId) -> Customer:
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
        try:
            from sdk.models import DataZone
            connector = self._get_connector()
            filter_map: dict[str, str] = {}
            if region:
                filter_map["region"] = region
            if tier:
                filter_map["tier"] = tier

            # ARCH-05: Stream iterator and slice lazily to prevent loading 10K+ records into memory
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

    def _map_record_to_customer(self, rec: dict[str, Any]) -> Customer:
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
