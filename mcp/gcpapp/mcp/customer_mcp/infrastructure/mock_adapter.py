from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Optional

from common.domain.value_objects import CustomerId, MachineId, SiteId
from core.exceptions import EntityNotFoundError
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

# ---------------------------------------------------------------------------
# Hardcoded mock data
# ---------------------------------------------------------------------------

_CUSTOMERS: list[Customer] = [
    # --- ENTERPRISE (1-3) ---
    Customer(
        customer_id=CustomerId("CUST-001001"),
        company_name="Nexus Industrial Corp",
        tier="ENTERPRISE",
        billing_status="ACTIVE",
        region="NA-EAST",
        industry="Manufacturing",
        address=Address(street="100 Nexus Blvd", city="Boston", state="MA", country="USA", postal_code="02101"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-001", name="Alice Marsh", email="a.marsh@gcpapp.com", phone="+1-617-555-0101", role="Strategic Account Manager"),
            AccountRepresentative(rep_id="REP-002", name="Brian Holt", email="b.holt@gcpapp.com", phone="+1-617-555-0102", role="Technical Account Manager"),
        ],
        created_at=datetime(2021, 3, 15, 8, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc),
        employee_count=5200,
        annual_revenue_usd=850_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001002"),
        company_name="Apex Manufacturing Ltd",
        tier="ENTERPRISE",
        billing_status="ACTIVE",
        region="EU-WEST",
        industry="Heavy Machinery",
        address=Address(street="22 Apex Way", city="Hamburg", state="Hamburg", country="Germany", postal_code="20095"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-003", name="Clara Schmidt", email="c.schmidt@gcpapp.com", phone="+49-40-5550103", role="Strategic Account Manager"),
            AccountRepresentative(rep_id="REP-004", name="Dieter Braun", email="d.braun@gcpapp.com", phone="+49-40-5550104", role="Solutions Architect"),
            AccountRepresentative(rep_id="REP-005", name="Eva Mueller", email="e.mueller@gcpapp.com", phone="+49-40-5550105", role="Technical Account Manager"),
        ],
        created_at=datetime(2020, 7, 1, 9, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc),
        employee_count=8400,
        annual_revenue_usd=1_200_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001003"),
        company_name="Orion Systems Inc",
        tier="ENTERPRISE",
        billing_status="ACTIVE",
        region="APAC",
        industry="Aerospace",
        address=Address(street="45 Orion Tower", city="Singapore", state="Central Region", country="Singapore", postal_code="018956"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-006", name="Frank Lim", email="f.lim@gcpapp.com", phone="+65-6555-0106", role="Strategic Account Manager"),
            AccountRepresentative(rep_id="REP-007", name="Grace Tan", email="g.tan@gcpapp.com", phone="+65-6555-0107", role="Technical Account Manager"),
        ],
        created_at=datetime(2019, 11, 20, 7, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 5, 11, 0, 0, tzinfo=timezone.utc),
        employee_count=12000,
        annual_revenue_usd=2_300_000_000,
    ),
    # --- PREMIUM (4-6) ---
    Customer(
        customer_id=CustomerId("CUST-001004"),
        company_name="Vertex Energy Solutions",
        tier="PREMIUM",
        billing_status="ACTIVE",
        region="NA-WEST",
        industry="Energy",
        address=Address(street="300 Vertex Drive", city="Houston", state="TX", country="USA", postal_code="77002"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-008", name="Henry Torres", email="h.torres@gcpapp.com", phone="+1-713-555-0108", role="Account Manager"),
            AccountRepresentative(rep_id="REP-009", name="Iris Chen", email="i.chen@gcpapp.com", phone="+1-713-555-0109", role="Technical Account Manager"),
        ],
        created_at=datetime(2022, 1, 10, 8, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 25, 9, 0, 0, tzinfo=timezone.utc),
        employee_count=1800,
        annual_revenue_usd=320_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001005"),
        company_name="Pinnacle Tech Corp",
        tier="PREMIUM",
        billing_status="ACTIVE",
        region="NA-EAST",
        industry="Technology",
        address=Address(street="150 Pinnacle Ave", city="New York", state="NY", country="USA", postal_code="10001"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-010", name="James Park", email="j.park@gcpapp.com", phone="+1-212-555-0110", role="Account Manager"),
        ],
        created_at=datetime(2022, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 14, 15, 0, 0, tzinfo=timezone.utc),
        employee_count=950,
        annual_revenue_usd=175_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001006"),
        company_name="Atlas Heavy Industries",
        tier="PREMIUM",
        billing_status="SUSPENDED",
        region="EU-WEST",
        industry="Steel & Mining",
        address=Address(street="77 Atlas Lane", city="Lyon", state="Auvergne-Rhône-Alpes", country="France", postal_code="69001"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-011", name="Katrine Dupont", email="k.dupont@gcpapp.com", phone="+33-4-5550111", role="Account Manager"),
            AccountRepresentative(rep_id="REP-012", name="Luc Bernard", email="l.bernard@gcpapp.com", phone="+33-4-5550112", role="Technical Account Manager"),
        ],
        created_at=datetime(2021, 9, 1, 8, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc),
        employee_count=2300,
        annual_revenue_usd=410_000_000,
    ),
    # --- STANDARD (7-10) ---
    Customer(
        customer_id=CustomerId("CUST-001007"),
        company_name="Meridian Robotics Group",
        tier="STANDARD",
        billing_status="ACTIVE",
        region="APAC",
        industry="Robotics",
        address=Address(street="9 Meridian Road", city="Tokyo", state="Tokyo", country="Japan", postal_code="100-0001"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-013", name="Mika Yoshida", email="m.yoshida@gcpapp.com", phone="+81-3-5550113", role="Account Manager"),
        ],
        created_at=datetime(2023, 2, 20, 9, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 18, 14, 0, 0, tzinfo=timezone.utc),
        employee_count=420,
        annual_revenue_usd=68_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001008"),
        company_name="Summit Engineering Co",
        tier="STANDARD",
        billing_status="ACTIVE",
        region="NA-WEST",
        industry="Civil Engineering",
        address=Address(street="500 Summit Peak Rd", city="Denver", state="CO", country="USA", postal_code="80202"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-014", name="Nathan Brooks", email="n.brooks@gcpapp.com", phone="+1-303-555-0114", role="Account Manager"),
        ],
        created_at=datetime(2023, 5, 10, 11, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 30, 10, 0, 0, tzinfo=timezone.utc),
        employee_count=310,
        annual_revenue_usd=42_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001009"),
        company_name="Horizon Electronics Ltd",
        tier="STANDARD",
        billing_status="PENDING",
        region="EU-WEST",
        industry="Electronics",
        address=Address(street="12 Horizon Street", city="Amsterdam", state="North Holland", country="Netherlands", postal_code="1012 AB"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-015", name="Olivia Visser", email="o.visser@gcpapp.com", phone="+31-20-5550115", role="Account Manager"),
        ],
        created_at=datetime(2024, 1, 5, 8, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 28, 16, 0, 0, tzinfo=timezone.utc),
        employee_count=185,
        annual_revenue_usd=29_000_000,
    ),
    Customer(
        customer_id=CustomerId("CUST-001010"),
        company_name="Zenith Processing Works",
        tier="STANDARD",
        billing_status="ACTIVE",
        region="NA-EAST",
        industry="Food Processing",
        address=Address(street="88 Zenith Industrial Park", city="Atlanta", state="GA", country="USA", postal_code="30301"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-016", name="Peter Walsh", email="p.walsh@gcpapp.com", phone="+1-404-555-0116", role="Account Manager"),
        ],
        created_at=datetime(2023, 8, 22, 7, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 5, 13, 0, 0, tzinfo=timezone.utc),
        employee_count=270,
        annual_revenue_usd=37_000_000,
    ),
    Customer(
        customer_id=CustomerId("234"),
        company_name="Apex Global Logistics #234",
        tier="PREMIUM",
        billing_status="ACTIVE",
        region="NA-WEST",
        industry="Industrial Automation",
        address=Address(street="234 Logistics Way", city="San Francisco", state="CA", country="USA", postal_code="94105"),
        account_representatives=[
            AccountRepresentative(rep_id="REP-234", name="Sarah Connor", email="s.connor@gcpapp.com", phone="+1-415-555-0234", role="Lead Solutions Architect"),
        ],
        created_at=datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        employee_count=1200,
        annual_revenue_usd=150_000_000,
    ),
]

_CUSTOMER_INDEX: dict[str, Customer] = {str(c.customer_id): c for c in _CUSTOMERS}

# Machine assignments: customer_id -> list of (machine_id, site_id, model_type, assigned_date)
_MACHINE_DATA: dict[str, list[tuple[str, str, str, date]]] = {
    "CUST-001001": [
        ("MCH-A1B2-C3D4", "SITE-A1B2", "NX-3000", date(2022, 4, 10)),
        ("MCH-E5F6-G7H8", "SITE-A1B2", "NX-3000", date(2022, 7, 15)),
    ],
    "CUST-001002": [
        ("MCH-X9Y8-Z7W6", "SITE-C3D4", "AP-5000", date(2021, 6, 20)),
    ],
    "CUST-001003": [
        ("MCH-P1Q2-R3S4", "SITE-E5F6", "OR-7200", date(2020, 3, 5)),
        ("MCH-T5U6-V7W8", "SITE-E5F6", "OR-7200", date(2021, 9, 12)),
    ],
    "CUST-001004": [
        ("MCH-B2C3-D4E5", "SITE-G7H8", "VX-1500", date(2022, 11, 1)),
    ],
    "CUST-001005": [
        ("MCH-F6G7-H8I9", "SITE-A1B2", "PT-2200", date(2023, 1, 18)),
        ("MCH-J1K2-L3M4", "SITE-A1B2", "PT-2200", date(2023, 4, 25)),
    ],
    "CUST-001006": [
        ("MCH-N5O6-P7Q8", "SITE-C3D4", "AT-4100", date(2022, 8, 30)),
    ],
    "CUST-001007": [
        ("MCH-R9S0-T1U2", "SITE-E5F6", "MR-800", date(2023, 7, 14)),
    ],
    "CUST-001008": [],
    "CUST-001009": [],
    "CUST-001010": [],
    "234": [
        ("MCH-A1B2-C3D4", "SITE-A1B2", "NX-3000", date(2023, 2, 1)),
    ],
}

# SLA / entitlement data by tier
_SLA_BY_TIER = {
    "ENTERPRISE": ("PLATINUM", ["24/7 Remote Monitoring", "Predictive Maintenance AI", "On-Site Engineer Dispatch"]),
    "PREMIUM": ("GOLD", ["Business-Hours Monitoring", "Predictive Maintenance AI"]),
    "STANDARD": ("SILVER", ["Email & Portal Support"]),
}
_SERVICE_NAMES = {
    "ENTERPRISE": "Enterprise Industrial Platform",
    "PREMIUM": "Premium Industrial Suite",
    "STANDARD": "Standard Industrial Package",
}
_MAX_MACHINES = {"ENTERPRISE": 50, "PREMIUM": 20, "STANDARD": 10}
_EXPIRY_BY_TIER = {
    "ENTERPRISE": date(2027, 12, 31),
    "PREMIUM": date(2027, 6, 30),
    "STANDARD": date(2026, 12, 31),
}


def _build_entitlements(customer: Customer) -> CustomerEntitlements:
    sla, features = _SLA_BY_TIER[customer.tier]
    entitlement = ServiceEntitlement(
        entitlement_id=f"ENT-{str(customer.customer_id).replace('CUST-', '')}",
        service_name=_SERVICE_NAMES[customer.tier],
        sla_level=sla,
        features=list(features),
        expiration_date=_EXPIRY_BY_TIER[customer.tier],
        is_active=customer.billing_status == "ACTIVE",
        max_machines=_MAX_MACHINES[customer.tier],
    )
    return CustomerEntitlements(
        customer_id=customer.customer_id,
        entitlements=[entitlement],
        total_active=1 if entitlement.is_active else 0,
    )


class MockCustomerAdapter(CustomerRepositoryPort):
    async def get_by_id(self, customer_id: CustomerId) -> Customer:
        key = str(customer_id)
        if key not in _CUSTOMER_INDEX:
            raise EntityNotFoundError(f"Customer {key} not found")
        return _CUSTOMER_INDEX[key]

    async def search(
        self,
        query: str,
        region: Optional[str],
        tier: Optional[str],
        limit: int,
        offset: int,
    ) -> list[Customer]:
        results = list(_CUSTOMERS)
        if query:
            q = query.lower()
            results = [c for c in results if q in c.company_name.lower()]
        if region:
            results = [c for c in results if c.region == region]
        if tier:
            results = [c for c in results if c.tier == tier.upper()]
        return results[offset: offset + limit]

    async def get_entitlements(self, customer_id: CustomerId) -> CustomerEntitlements:
        key = str(customer_id)
        if key not in _CUSTOMER_INDEX:
            raise EntityNotFoundError(f"Customer {key} not found")
        return _build_entitlements(_CUSTOMER_INDEX[key])

    async def get_assigned_machines(
        self, customer_id: CustomerId, site_id: Optional[SiteId]
    ) -> CustomerMachineAssignment:
        key = str(customer_id)
        if key not in _CUSTOMER_INDEX:
            raise EntityNotFoundError(f"Customer {key} not found")
        raw = _MACHINE_DATA.get(key, [])
        machines = [
            AssignedMachine(
                machine_id=MachineId(mid),
                site_id=SiteId(sid),
                model_type=mtype,
                assigned_date=adate,
            )
            for mid, sid, mtype, adate in raw
        ]
        if site_id:
            sid_str = str(site_id)
            machines = [m for m in machines if str(m.site_id) == sid_str]
        return CustomerMachineAssignment(
            customer_id=customer_id,
            machines=machines,
            total_count=len(machines),
        )
