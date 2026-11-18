from __future__ import annotations
import pytest
from core.exceptions import DomainValidationError, EntityNotFoundError
from mcp.machine_mcp.application.use_cases import (
    GetMachineDetailsUseCase,
    GetMaintenanceHistoryUseCase,
    ListMachineFleetUseCase,
    UpdateOperationalStateUseCase,
)


@pytest.mark.asyncio
async def test_get_machine_details_success(machine_repo):
    uc = GetMachineDetailsUseCase(machine_repo)
    machine = await uc.execute("MCH-A1B2-C3D4")
    assert machine.machine_id == "MCH-A1B2-C3D4"
    assert machine.model_type == "INDUSTRIAL-X500"
    assert machine.status == "ACTIVE"


@pytest.mark.asyncio
async def test_get_machine_details_invalid_format(machine_repo):
    uc = GetMachineDetailsUseCase(machine_repo)
    with pytest.raises(DomainValidationError, match="Invalid machine_id format"):
        await uc.execute("INVALID-FORMAT")


@pytest.mark.asyncio
async def test_get_machine_details_not_found(machine_repo):
    uc = GetMachineDetailsUseCase(machine_repo)
    with pytest.raises(EntityNotFoundError):
        await uc.execute("MCH-9999-9999")


@pytest.mark.asyncio
async def test_list_machine_fleet_filters(machine_repo):
    uc = ListMachineFleetUseCase(machine_repo)
    fleet = await uc.execute(model_type="INDUSTRIAL", status="ACTIVE", page=1, page_size=10)
    assert len(fleet) >= 1
    for m in fleet:
        assert "INDUSTRIAL" in m.model_type
        assert m.status == "ACTIVE"


@pytest.mark.asyncio
async def test_list_machine_fleet_invalid_status(machine_repo):
    uc = ListMachineFleetUseCase(machine_repo)
    with pytest.raises(DomainValidationError, match="Invalid status"):
        await uc.execute(status="BROKEN")


@pytest.mark.asyncio
async def test_get_maintenance_history_success(machine_repo):
    uc = GetMaintenanceHistoryUseCase(machine_repo)
    history = await uc.execute("MCH-A1B2-C3D4", 10)
    assert history.machine_id == "MCH-A1B2-C3D4"
    assert history.total_records >= 1
    assert history.total_cost_usd > 0


@pytest.mark.asyncio
async def test_update_operational_state_valid_transition(machine_repo):
    uc = UpdateOperationalStateUseCase(machine_repo)
    res = await uc.execute(
        machine_id_str="MCH-A1B2-C3D4",
        target_state="MAINTENANCE",
        reason="Scheduled overhaul",
    )
    assert res.new_state == "MAINTENANCE"
    assert res.reason == "Scheduled overhaul"
    assert bool(res.audit_id)


@pytest.mark.asyncio
async def test_update_operational_state_invalid_transition(machine_repo):
    uc = UpdateOperationalStateUseCase(machine_repo)
    # MCH-R9S0-T1U2 is DECOMMISSIONED in mock data; cannot reactivate
    with pytest.raises(DomainValidationError):
        await uc.execute(
            machine_id_str="MCH-R9S0-T1U2",
            target_state="ACTIVE",
            reason="Attempted reactivation",
        )
