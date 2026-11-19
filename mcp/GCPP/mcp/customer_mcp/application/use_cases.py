from __future__ import annotations
from typing import Optional
from mcp.customer_mcp.application.ports import CustomerRepositoryPort
from mcp.customer_mcp.domain.entities import Customer, CustomerEntitlements, CustomerMachineAssignment
from common.domain.enums import CustomerTier
from common.domain.value_objects import CustomerId, PaginationParams, SiteId
from core.exceptions import EntityNotFoundError, DomainValidationError

MAX_SEARCH_QUERY_LENGTH = 100
MAX_REGION_LENGTH = 50


class GetCustomerProfileUseCase:
    def __init__(self, repo: CustomerRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, customer_id: str) -> Customer:
        try:
            cid = CustomerId(customer_id)
        except ValueError as e:
            raise DomainValidationError(str(e)) from e
        return await self._repo.get_by_id(cid)


class SearchCustomersUseCase:
    def __init__(self, repo: CustomerRepositoryPort) -> None:
        self._repo = repo

    async def execute(
        self,
        query: str = "",
        region: Optional[str] = None,
        tier: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        pagination: Optional[PaginationParams] = None,
    ) -> list[Customer]:
        # SEC-04: Enforce query length limits to prevent DoS / buffer issues
        if query and len(query) > MAX_SEARCH_QUERY_LENGTH:
            raise DomainValidationError(
                f"Query length cannot exceed {MAX_SEARCH_QUERY_LENGTH} characters (received {len(query)})"
            )
        if region and len(region) > MAX_REGION_LENGTH:
            raise DomainValidationError(
                f"Region length cannot exceed {MAX_REGION_LENGTH} characters (received {len(region)})"
            )

        # ARCH-04: Wire PaginationParams
        params = pagination or PaginationParams.from_offset_limit(offset=offset, limit=limit)

        # ARCH-03: Validate tier against CustomerTier enum
        tier_enum: Optional[CustomerTier] = None
        if tier:
            try:
                tier_enum = CustomerTier(tier.upper())
            except ValueError:
                valid_tiers = ", ".join([e.value for e in CustomerTier])
                raise DomainValidationError(f"tier must be one of {valid_tiers}")

        tier_str = tier_enum.value if tier_enum else None
        return await self._repo.search(query, region, tier_str, params.limit, params.offset)


class GetCustomerEntitlementsUseCase:
    def __init__(self, repo: CustomerRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, customer_id: str) -> CustomerEntitlements:
        try:
            cid = CustomerId(customer_id)
        except ValueError as e:
            raise DomainValidationError(str(e)) from e
        return await self._repo.get_entitlements(cid)


class GetAssignedMachinesUseCase:
    def __init__(self, repo: CustomerRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, customer_id: str, site_id: Optional[str]) -> CustomerMachineAssignment:
        try:
            cid = CustomerId(customer_id)
        except ValueError as e:
            raise DomainValidationError(str(e)) from e
        sid = None
        if site_id:
            try:
                sid = SiteId(site_id)
            except ValueError as e:
                raise DomainValidationError(str(e)) from e
        return await self._repo.get_assigned_machines(cid, sid)
