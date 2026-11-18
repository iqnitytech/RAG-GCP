from __future__ import annotations
import pytest
from datetime import datetime, timezone
from common.domain.value_objects import CustomerId, MachineId, SiteId
from mcp.customer_mcp.domain.entities import (
    Address,
    AccountRepresentative,
    Customer,
    CustomerEntitlements,
    ServiceEntitlement,
)


def test_customer_id_validation_success():
    cid = CustomerId("CUST-001001")
    assert str(cid) == "CUST-001001"
    cid_numeric = CustomerId("234")
    assert str(cid_numeric) == "234"


def test_customer_id_validation_failure():
    with pytest.raises(ValueError, match="Invalid CustomerId"):
        CustomerId("bad id with spaces!")


def test_machine_id_validation_success():
    mid = MachineId("MCH-A1B2-C3D4")
    assert str(mid) == "MCH-A1B2-C3D4"


def test_machine_id_validation_failure():
    with pytest.raises(ValueError, match="Invalid MachineId"):
        MachineId("INVALID-MACHINE-ID")


def test_site_id_validation_success():
    sid = SiteId("SITE-A1B2")
    assert str(sid) == "SITE-A1B2"


def test_site_id_validation_failure():
    with pytest.raises(ValueError, match="Invalid SiteId"):
        SiteId("BADSITE")


def test_customer_entity_instantiation():
    now = datetime.now(timezone.utc)
    c = Customer(
        customer_id=CustomerId("CUST-001001"),
        company_name="Nexus Industrial Corp",
        tier="ENTERPRISE",
        billing_status="ACTIVE",
        region="NA-EAST",
        industry="Manufacturing",
        address=Address("100 Nexus Blvd", "Boston", "MA", "USA", "02101"),
        account_representatives=[
            AccountRepresentative("REP-01", "Alice Marsh", "a.marsh@corp.com", "+1-555-0101", "TAM")
        ],
        created_at=now,
        updated_at=now,
        employee_count=5000,
        annual_revenue_usd=800_000_000,
    )
    assert c.company_name == "Nexus Industrial Corp"
    assert c.tier == "ENTERPRISE"
    assert len(c.account_representatives) == 1


def test_customer_entitlements_active_count():
    ent = CustomerEntitlements(
        customer_id=CustomerId("CUST-001001"),
        entitlements=[
            ServiceEntitlement("E1", "Service A", "PLATINUM", ["F1"], datetime(2027, 1, 1).date(), True, 50),
            ServiceEntitlement("E2", "Service B", "SILVER", ["F2"], datetime(2025, 1, 1).date(), False, 10),
        ],
        total_active=1,
    )
    assert ent.total_active == 1
    assert len(ent.entitlements) == 2
