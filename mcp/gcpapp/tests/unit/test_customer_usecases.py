from __future__ import annotations
import pytest
from core.exceptions import DomainValidationError, EntityNotFoundError
from mcp.customer_mcp.application.use_cases import (
    GetAssignedMachinesUseCase,
    GetCustomerEntitlementsUseCase,
    GetCustomerProfileUseCase,
    SearchCustomersUseCase,
)


@pytest.mark.asyncio
async def test_get_customer_profile_success(customer_repo):
    uc = GetCustomerProfileUseCase(customer_repo)
    customer = await uc.execute("CUST-001001")
    assert customer.customer_id == "CUST-001001"
    assert customer.tier == "ENTERPRISE"
    assert customer.billing_status == "ACTIVE"


@pytest.mark.asyncio
async def test_get_customer_profile_invalid_id(customer_repo):
    uc = GetCustomerProfileUseCase(customer_repo)
    with pytest.raises(DomainValidationError):
        await uc.execute("invalid customer id")


@pytest.mark.asyncio
async def test_get_customer_profile_not_found(customer_repo):
    uc = GetCustomerProfileUseCase(customer_repo)
    with pytest.raises(EntityNotFoundError):
        await uc.execute("CUST-999999")


@pytest.mark.asyncio
async def test_search_customers_success(customer_repo):
    uc = SearchCustomersUseCase(customer_repo)
    res = await uc.execute("Nexus", "NA-EAST", "ENTERPRISE", 10, 0)
    assert len(res) == 1
    assert res[0].customer_id == "CUST-001001"


@pytest.mark.asyncio
async def test_search_customers_invalid_tier(customer_repo):
    uc = SearchCustomersUseCase(customer_repo)
    with pytest.raises(DomainValidationError, match="tier must be one of"):
        await uc.execute("", None, "INVALID_TIER", 10, 0)


@pytest.mark.asyncio
async def test_search_customers_invalid_limit(customer_repo):
    uc = SearchCustomersUseCase(customer_repo)
    with pytest.raises(DomainValidationError, match="limit must be between"):
        await uc.execute("", None, None, 150, 0)


@pytest.mark.asyncio
async def test_get_customer_entitlements_success(customer_repo):
    uc = GetCustomerEntitlementsUseCase(customer_repo)
    res = await uc.execute("CUST-001001")
    assert res.customer_id == "CUST-001001"
    assert res.total_active >= 1
    assert res.entitlements[0].sla_level == "PLATINUM"


@pytest.mark.asyncio
async def test_get_assigned_machines_with_site(customer_repo):
    uc = GetAssignedMachinesUseCase(customer_repo)
    res = await uc.execute("CUST-001001", "SITE-A1B2")
    assert res.total_count == 2
    for m in res.machines:
        assert str(m.site_id) == "SITE-A1B2"
