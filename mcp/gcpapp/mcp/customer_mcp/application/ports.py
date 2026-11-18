from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from mcp.customer_mcp.domain.entities import Customer, CustomerEntitlements, CustomerMachineAssignment
from common.domain.value_objects import CustomerId, SiteId

class CustomerReaderPort(ABC):
    @abstractmethod
    async def get_by_id(self, customer_id: CustomerId) -> Customer: ...
    @abstractmethod
    async def search(self, query: str, region: Optional[str], tier: Optional[str], limit: int, offset: int) -> list[Customer]: ...

class CustomerEntitlementPort(ABC):
    @abstractmethod
    async def get_entitlements(self, customer_id: CustomerId) -> CustomerEntitlements: ...

class CustomerMachinePort(ABC):
    @abstractmethod
    async def get_assigned_machines(self, customer_id: CustomerId, site_id: Optional[SiteId] = None) -> CustomerMachineAssignment: ...

class CustomerRepositoryPort(CustomerReaderPort, CustomerEntitlementPort, CustomerMachinePort, ABC):
    pass
