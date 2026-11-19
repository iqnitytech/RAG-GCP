from __future__ import annotations
from typing import Optional
from mcp.machine_mcp.application.ports import MachineRepositoryPort
from mcp.machine_mcp.domain.entities import Machine, MaintenanceHistory, OperationalStateUpdate
from common.domain.enums import MachineStatus
from common.domain.value_objects import MachineId, PaginationParams, SiteId
from core.exceptions import DomainValidationError, EntityNotFoundError

VALID_STATUSES = {e.value for e in MachineStatus}
MAX_MODEL_TYPE_LENGTH = 100
MAX_REASON_LENGTH = 500

# Allowed transitions: from_state -> set of allowed to_states
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    MachineStatus.ACTIVE.value: {MachineStatus.MAINTENANCE.value, MachineStatus.DECOMMISSIONED.value},
    MachineStatus.MAINTENANCE.value: {MachineStatus.ACTIVE.value, MachineStatus.DECOMMISSIONED.value},
    MachineStatus.DECOMMISSIONED.value: set(),  # Cannot transition out
}


class GetMachineDetailsUseCase:
    def __init__(self, repo: MachineRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, machine_id_str: str) -> Machine:
        try:
            machine_id = MachineId(machine_id_str)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}. Expected MCH-XXXX-XXXX") from exc
        return await self._repo.get_by_id(machine_id)


class ListMachineFleetUseCase:
    def __init__(self, repo: MachineRepositoryPort) -> None:
        self._repo = repo

    async def execute(
        self,
        model_type: Optional[str] = None,
        status: Optional[str] = None,
        site_id_str: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        pagination: Optional[PaginationParams] = None,
    ) -> list[Machine]:
        # SEC-04: Enforce query length limits
        if model_type and len(model_type) > MAX_MODEL_TYPE_LENGTH:
            raise DomainValidationError(
                f"model_type length cannot exceed {MAX_MODEL_TYPE_LENGTH} characters"
            )

        # ARCH-03: Enforce MachineStatus Enum
        if status is not None and status not in VALID_STATUSES:
            raise DomainValidationError(
                f"Invalid status {status!r}. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )

        # ARCH-04: Wire PaginationParams
        params = pagination or PaginationParams.from_page(page=page, page_size=page_size)

        site_id: Optional[SiteId] = None
        if site_id_str:
            try:
                site_id = SiteId(site_id_str)
            except ValueError as exc:
                raise DomainValidationError(f"Invalid site_id format: {site_id_str!r}. Expected SITE-XXXX") from exc

        return await self._repo.list_fleet(
            model_type=model_type or None,
            status=status,
            site_id=site_id,
            page=params.page,
            page_size=params.page_size,
        )


class GetMaintenanceHistoryUseCase:
    def __init__(self, repo: MachineRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, machine_id_str: str, limit: int = 20) -> MaintenanceHistory:
        try:
            machine_id = MachineId(machine_id_str)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}") from exc
        if not (1 <= limit <= 100):
            raise DomainValidationError("limit must be between 1 and 100")
        return await self._repo.get_maintenance_history(machine_id, limit)


class UpdateOperationalStateUseCase:
    def __init__(self, repo: MachineRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, machine_id_str: str, target_state: str, reason: str) -> OperationalStateUpdate:
        try:
            machine_id = MachineId(machine_id_str)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}") from exc

        # SEC-04: Enforce string length limits
        if len(reason) > MAX_REASON_LENGTH:
            raise DomainValidationError(f"reason length cannot exceed {MAX_REASON_LENGTH} characters")

        # ARCH-03: Enforce MachineStatus Enum
        if target_state not in VALID_STATUSES:
            raise DomainValidationError(
                f"Invalid target_state {target_state!r}. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        if not reason or not reason.strip():
            raise DomainValidationError("reason must be non-empty")

        # Fetch current machine to validate transition
        machine = await self._repo.get_by_id(machine_id)
        current_state = machine.status

        if current_state == MachineStatus.DECOMMISSIONED.value:
            raise DomainValidationError("Cannot reactivate a decommissioned machine")

        allowed = ALLOWED_TRANSITIONS.get(current_state, set())
        if target_state not in allowed:
            raise DomainValidationError(
                f"Transition from {current_state} to {target_state} is not allowed"
            )

        return await self._repo.update_operational_state(machine_id, target_state, reason)
