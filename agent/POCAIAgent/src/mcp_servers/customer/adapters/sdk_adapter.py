from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Sequence

from domain.errors import NotFoundError
from mcp_servers.customer.adapters.gateway import SdkDataGateway, _DEFAULT_NUMBER_FIELDS
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort
from mcp_servers.customer.resilience import (
    handle_sdk_exception,
    retry_with_backoff,
)
from settings import Settings

logger = logging.getLogger("mcp.customer.sdk_adapter")


class SdkCustomerAdapter(CustomerRepositoryPort):
    """Domain Adapter for Customer (knows 'Contact', maps to Customer).

    Implements CustomerRepositoryPort and uses SdkDataGateway for infrastructure access.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        gateway: SdkDataGateway | None = None,
        connector: Any = None,
        sf_connector: Any = None,
        sf_async_connector: Any = None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway or SdkDataGateway(
            settings,
            connector=connector,
            sf_connector=sf_connector,
            sf_async_connector=sf_async_connector,
        )
        # Expose connector attributes for backward compatibility
        self._connector = self._gateway._connector
        self._sf_connector = self._gateway._sf_connector
        self._sf_async_connector = self._gateway._sf_async_connector

    @property
    def gateway(self) -> SdkDataGateway:
        return self._gateway

    def _get_edh_connector(self) -> Any:
        return self._gateway.get_edh_connector()

    def _resolve_sf_sync_connector(self) -> Any:
        return self._gateway.resolve_sf_sync_connector()

    # ------------------------------------------------------------------
    # Gateway delegation helpers
    # ------------------------------------------------------------------

    def get_record_by_id(
        self,
        object_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self._gateway.get_record_by_id(object_name, record_id, fields=fields)

    def get_record_by_number(
        self,
        object_name: str,
        field_name: str,
        record_number: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self._gateway.get_record_by_number(
            object_name, field_name, record_number, fields=fields
        )

    def query_salesforce(
        self,
        object_name_or_soql: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._gateway.query_salesforce(
            object_name_or_soql, fields=fields, where=where, limit=limit
        )

    # ------------------------------------------------------------------
    # CustomerRepositoryPort implementation (Contact)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, customer_id: str) -> dict[str, Any] | None:
        cid_str = customer_id.strip()
        if not cid_str:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    rec = await asyncio.to_thread(self.get_record_by_id, "contact", cid_str)
                    if rec:
                        return self._map_record(rec)
                except NotFoundError:
                    pass
                except Exception as sf_err:
                    logger.debug("Salesforce get_record_by_id failed for contact, falling back to data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._get_edh_connector()

            def _read():
                return list(
                    connector.read_records(
                        "customers",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": cid_str},
                    )
                )

            records = await asyncio.to_thread(_read)
            if records:
                return self._map_record(records[0])
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting customer profile for {customer_id}")
            return None

    @retry_with_backoff(max_attempts=3)
    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        query = name.strip()
        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    records = await asyncio.to_thread(self.query_salesforce, "contact", limit=50)
                    if records:
                        matching = [
                            self._map_record(r)
                            for r in records
                            if not query
                            or query.lower()
                            in (r.get("Name") or r.get("company_name") or "").lower()
                        ]
                        return matching
                except Exception as sf_err:
                    logger.debug("Salesforce query failed for contact, falling back to data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._get_edh_connector()

            def _read_and_filter():
                records_iter = connector.read_records("customers", zone=DataZone.CURATED)
                return [
                    self._map_record(r)
                    for r in records_iter
                    if not query
                    or query.lower()
                    in (r.get("company_name") or r.get("Name") or "").lower()
                ]

            return await asyncio.to_thread(_read_and_filter)
        except Exception as exc:
            handle_sdk_exception(exc, f"searching customers matching '{name}'")
            return []

    @retry_with_backoff(max_attempts=3)
    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        clean_number = number.strip()
        if not clean_number:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    rec = await asyncio.to_thread(
                        self.get_record_by_number, "contact", "AccountNumber", clean_number
                    )
                    if rec:
                        return self._map_record(rec)
                except NotFoundError:
                    pass
                except Exception as sf_err:
                    logger.debug("Salesforce get_record_by_number failed for contact, trying data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._get_edh_connector()

            def _read():
                records = list(
                    connector.read_records(
                        "customers",
                        zone=DataZone.CURATED,
                        partition_filter={"customer_id": clean_number},
                    )
                )
                if not records:
                    all_records = connector.read_records("customers", zone=DataZone.CURATED)
                    for r in all_records:
                        if (
                            r.get("customer_id", "").lower() == clean_number.lower()
                            or r.get("AccountNumber", "").lower() == clean_number.lower()
                            or r.get("account_number", "").lower() == clean_number.lower()
                        ):
                            return [r]
                return records

            records = await asyncio.to_thread(_read)
            if records:
                return self._map_record(records[0])
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting customer by number '{number}'")
            return None

    @retry_with_backoff(max_attempts=3)
    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        clean_phone = phone.strip()
        if not clean_phone:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    records = await asyncio.to_thread(
                        self.query_salesforce, "contact", where={"Phone": clean_phone}, limit=1
                    )
                    if records:
                        return self._map_record(records[0])
                except Exception as sf_err:
                    logger.debug("Salesforce query by phone failed for contact, trying data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._get_edh_connector()
            digits = "".join(ch for ch in clean_phone if ch.isdigit())

            def _find_by_phone():
                records_iter = connector.read_records("customers", zone=DataZone.CURATED)
                for r in records_iter:
                    p = r.get("phone") or r.get("Phone") or ""
                    if p == clean_phone or (digits and "".join(ch for ch in p if ch.isdigit()) == digits):
                        return r
                    for rep in r.get("account_representatives", []):
                        rp = rep.get("phone", "")
                        if rp == clean_phone or (digits and "".join(ch for ch in rp if ch.isdigit()) == digits):
                            return r
                return None

            found = await asyncio.to_thread(_find_by_phone)
            if found:
                return self._map_record(found)
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting customer by phone '{phone}'")
            return None

    def _map_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw Contact record to unified dictionary contract."""
        addr = rec.get("address", {})
        reps = rec.get("account_representatives", [])
        phone = rec.get("phone") or rec.get("Phone") or (reps[0].get("phone", "") if reps else "")

        cid = rec.get("customer_id") or rec.get("Id") or rec.get("AccountNumber") or "UNKNOWN"
        cname = rec.get("company_name") or rec.get("Name") or "Unknown Company"
        tier = rec.get("tier") or rec.get("Tier__c") or rec.get("Tier") or rec.get("Type") or "STANDARD"
        number = rec.get("AccountNumber") or rec.get("customer_id") or rec.get("number") or cid

        return {
            "id": cid,
            "name": cname,
            "type": "Contact",
            "tier": tier,
            "phone": phone,
            "number": number,
            "billing_status": rec.get("billing_status") or rec.get("Status__c") or "ACTIVE",
            "region": rec.get("region") or rec.get("Region__c") or rec.get("BillingState") or "",
            "industry": rec.get("industry") or rec.get("Industry") or "",
            "address": addr,
            "account_representatives": reps,
        }


class SdkAccountAdapter(AccountRepositoryPort):
    """Domain Adapter for Account (knows 'Account', maps to Account).

    Implements AccountRepositoryPort and uses SdkDataGateway for infrastructure access.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        gateway: SdkDataGateway | None = None,
        connector: Any = None,
        sf_connector: Any = None,
        sf_async_connector: Any = None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway or SdkDataGateway(
            settings,
            connector=connector,
            sf_connector=sf_connector,
            sf_async_connector=sf_async_connector,
        )
        self._connector = self._gateway._connector
        self._sf_connector = self._gateway._sf_connector
        self._sf_async_connector = self._gateway._sf_async_connector

    @property
    def gateway(self) -> SdkDataGateway:
        return self._gateway

    def get_record_by_id(
        self,
        object_name: str,
        record_id: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self._gateway.get_record_by_id(object_name, record_id, fields=fields)

    def get_record_by_number(
        self,
        object_name: str,
        field_name: str,
        record_number: str,
        *,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self._gateway.get_record_by_number(
            object_name, field_name, record_number, fields=fields
        )

    def query_salesforce(
        self,
        object_name_or_soql: str,
        *,
        fields: Sequence[str] | None = None,
        where: Mapping[str, Any] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._gateway.query_salesforce(
            object_name_or_soql, fields=fields, where=where, limit=limit
        )

    # ------------------------------------------------------------------
    # AccountRepositoryPort implementation (Account)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, account_id: str) -> dict[str, Any] | None:
        aid_str = account_id.strip()
        if not aid_str:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    rec = await asyncio.to_thread(self.get_record_by_id, "account", aid_str)
                    if rec:
                        return self._map_record(rec)
                except NotFoundError:
                    pass
                except Exception as sf_err:
                    logger.debug("Salesforce get_record_by_id failed for account, falling back to data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._gateway.get_edh_connector()

            def _read():
                # Check accounts dataset, fallback to customers
                for dataset in ("accounts", "customers"):
                    try:
                        records = list(
                            connector.read_records(
                                dataset,
                                zone=DataZone.CURATED,
                                partition_filter={"customer_id": aid_str},
                            )
                        )
                        if records:
                            return records
                    except Exception:
                        pass
                return []

            records = await asyncio.to_thread(_read)
            if records:
                return self._map_record(records[0])
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting account profile for {account_id}")
            return None

    @retry_with_backoff(max_attempts=3)
    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        query = name.strip()
        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    records = await asyncio.to_thread(self.query_salesforce, "account", limit=50)
                    if records:
                        matching = [
                            self._map_record(r)
                            for r in records
                            if not query
                            or query.lower()
                            in (r.get("Name") or r.get("company_name") or "").lower()
                        ]
                        return matching
                except Exception as sf_err:
                    logger.debug("Salesforce query failed for account, falling back to data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._gateway.get_edh_connector()

            def _read_and_filter():
                for dataset in ("accounts", "customers"):
                    try:
                        records_iter = connector.read_records(dataset, zone=DataZone.CURATED)
                        results = [
                            self._map_record(r)
                            for r in records_iter
                            if not query
                            or query.lower()
                            in (r.get("company_name") or r.get("Name") or "").lower()
                        ]
                        if results:
                            return results
                    except Exception:
                        pass
                return []

            return await asyncio.to_thread(_read_and_filter)
        except Exception as exc:
            handle_sdk_exception(exc, f"searching accounts matching '{name}'")
            return []

    @retry_with_backoff(max_attempts=3)
    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        clean_number = number.strip()
        if not clean_number:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    rec = await asyncio.to_thread(
                        self.get_record_by_number, "account", "AccountNumber", clean_number
                    )
                    if rec:
                        return self._map_record(rec)
                except NotFoundError:
                    pass
                except Exception as sf_err:
                    logger.debug("Salesforce get_record_by_number failed for account, trying data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._gateway.get_edh_connector()

            def _read():
                for dataset in ("accounts", "customers"):
                    try:
                        records = list(
                            connector.read_records(
                                dataset,
                                zone=DataZone.CURATED,
                                partition_filter={"customer_id": clean_number},
                            )
                        )
                        if records:
                            return records
                        all_records = connector.read_records(dataset, zone=DataZone.CURATED)
                        for r in all_records:
                            if (
                                r.get("customer_id", "").lower() == clean_number.lower()
                                or r.get("AccountNumber", "").lower() == clean_number.lower()
                                or r.get("account_number", "").lower() == clean_number.lower()
                            ):
                                return [r]
                    except Exception:
                        pass
                return []

            records = await asyncio.to_thread(_read)
            if records:
                return self._map_record(records[0])
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting account by number '{number}'")
            return None

    @retry_with_backoff(max_attempts=3)
    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        clean_phone = phone.strip()
        if not clean_phone:
            return None

        try:
            # 1. Salesforce path when configured
            if self._sf_connector is not None or self._settings.is_salesforce_configured:
                try:
                    records = await asyncio.to_thread(
                        self.query_salesforce, "account", where={"Phone": clean_phone}, limit=1
                    )
                    if records:
                        return self._map_record(records[0])
                except Exception as sf_err:
                    logger.debug("Salesforce query by phone failed for account, trying data hub: %s", sf_err)

            # 2. EDH Data Hub fallback
            from sdk.models import DataZone
            connector = self._gateway.get_edh_connector()
            digits = "".join(ch for ch in clean_phone if ch.isdigit())

            def _find_by_phone():
                for dataset in ("accounts", "customers"):
                    try:
                        records_iter = connector.read_records(dataset, zone=DataZone.CURATED)
                        for r in records_iter:
                            p = r.get("phone") or r.get("Phone") or ""
                            if p == clean_phone or (digits and "".join(ch for ch in p if ch.isdigit()) == digits):
                                return r
                    except Exception:
                        pass
                return None

            found = await asyncio.to_thread(_find_by_phone)
            if found:
                return self._map_record(found)
            return None
        except Exception as exc:
            handle_sdk_exception(exc, f"getting account by phone '{phone}'")
            return None

    def _map_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw Account record to unified dictionary contract."""
        billing_addr = rec.get("BillingAddress") or rec.get("address") or {}
        addr = {
            "street": billing_addr.get("street") or rec.get("BillingStreet") or "",
            "city": billing_addr.get("city") or rec.get("BillingCity") or "",
            "state": billing_addr.get("state") or rec.get("BillingState") or "",
            "postal_code": billing_addr.get("postalCode") or rec.get("BillingPostalCode") or "",
            "country": billing_addr.get("country") or rec.get("BillingCountry") or "",
        }

        reps = rec.get("account_representatives", [])
        phone = rec.get("phone") or rec.get("Phone") or ""

        aid = rec.get("Id") or rec.get("customer_id") or rec.get("AccountNumber") or "UNKNOWN"
        aname = rec.get("Name") or rec.get("company_name") or "Unknown Account"
        tier = rec.get("Tier__c") or rec.get("Tier") or rec.get("Type") or rec.get("tier") or "STANDARD"
        number = rec.get("AccountNumber") or rec.get("customer_id") or rec.get("number") or aid

        return {
            "id": aid,
            "name": aname,
            "type": "Account",
            "tier": tier,
            "phone": phone,
            "number": number,
            "billing_status": rec.get("billing_status") or rec.get("Status__c") or "ACTIVE",
            "region": rec.get("region") or rec.get("Region__c") or rec.get("BillingState") or "",
            "industry": rec.get("industry") or rec.get("Industry") or "",
            "address": addr,
            "account_representatives": reps,
        }
