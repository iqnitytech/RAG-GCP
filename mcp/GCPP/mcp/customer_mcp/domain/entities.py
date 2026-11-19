from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from common.domain.enums import BillingStatus, CustomerTier, SlaLevel
from common.domain.value_objects import CustomerId, MachineId, SiteId

@dataclass(frozen=True)
class Address:
    street: str
    city: str
    state: str
    country: str
    postal_code: str

@dataclass(frozen=True)
class AccountRepresentative:
    rep_id: str
    name: str
    email: str
    phone: str
    role: str

@dataclass(frozen=True)
class Customer:
    customer_id: CustomerId
    company_name: str
    tier: CustomerTier | str
    billing_status: BillingStatus | str
    region: str
    industry: str
    address: Address
    account_representatives: list[AccountRepresentative]
    created_at: datetime
    updated_at: datetime
    employee_count: int
    annual_revenue_usd: int

@dataclass(frozen=True)
class ServiceEntitlement:
    entitlement_id: str
    service_name: str
    sla_level: SlaLevel | str
    features: list[str]
    expiration_date: date
    is_active: bool
    max_machines: int

@dataclass(frozen=True)
class CustomerEntitlements:
    customer_id: CustomerId
    entitlements: list[ServiceEntitlement]
    total_active: int

@dataclass(frozen=True)
class AssignedMachine:
    machine_id: MachineId
    site_id: SiteId
    model_type: str
    assigned_date: date

@dataclass(frozen=True)
class CustomerMachineAssignment:
    customer_id: CustomerId
    machines: list[AssignedMachine]
    total_count: int
