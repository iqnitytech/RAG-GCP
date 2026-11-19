from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from mcp.machine_mcp.domain.entities import Machine, MaintenanceHistory, OperationalStateUpdate
from common.domain.value_objects import MachineId, SiteId


class MachineReaderPort(ABC):
    @abstractmethod
    async def get_by_id(self, machine_id: MachineId) -> Machine: ...

    @abstractmethod
    async def list_fleet(
        self,
        model_type: Optional[str],
        status: Optional[str],
        site_id: Optional[SiteId],
        page: int,
        page_size: int,
    ) -> list[Machine]: ...


class MachineMaintenancePort(ABC):
    @abstractmethod
    async def get_maintenance_history(self, machine_id: MachineId, limit: int) -> MaintenanceHistory: ...


class MachineOperationsPort(ABC):
    @abstractmethod
    async def update_operational_state(
        self, machine_id: MachineId, target_state: str, reason: str
    ) -> OperationalStateUpdate: ...


class MachineRepositoryPort(MachineReaderPort, MachineMaintenancePort, MachineOperationsPort, ABC):
    pass
