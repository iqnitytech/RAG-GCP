from __future__ import annotations
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from common.domain.value_objects import MachineId, SiteId
from core.exceptions import EntityNotFoundError, DomainValidationError
from mcp.machine_mcp.application.ports import MachineRepositoryPort
from mcp.machine_mcp.domain.entities import (
    HardwareSpec,
    Location,
    Machine,
    MaintenanceHistory,
    MaintenanceRecord,
    OperationalStateUpdate,
)

# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _d(year: int, month: int, day: int) -> date:
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

_MACHINES_LIST: list[Machine] = [
    Machine(
        machine_id=MachineId("MCH-A1B2-C3D4"),
        model_type="INDUSTRIAL-X500",
        model_series="X500-Gen3",
        firmware_version="3.14.2",
        commissioning_date=_d(2022, 3, 15),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-A1B2"),
            building="BuildingA",
            floor=1,
            rack_position="A1-R03",
            gps_lat=37.7749,
            gps_lon=-122.4194,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=32,
            ram_gb=256,
            storage_tb=4.0,
            gpu_model="NVIDIA A100",
            network_bandwidth_gbps=25.0,
        ),
        owner_customer_id="CUST-001001",
        serial_number="SN-X500-001001",
        last_heartbeat=_dt(2026, 11, 17, 10, 0),
    ),
    Machine(
        machine_id=MachineId("MCH-E5F6-G7H8"),
        model_type="INDUSTRIAL-X500",
        model_series="X500-Gen2",
        firmware_version="3.10.1",
        commissioning_date=_d(2021, 6, 20),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-A1B2"),
            building="BuildingB",
            floor=2,
            rack_position="B2-R07",
            gps_lat=37.7750,
            gps_lon=-122.4195,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=16,
            ram_gb=128,
            storage_tb=2.0,
            gpu_model=None,
            network_bandwidth_gbps=10.0,
        ),
        owner_customer_id="CUST-001001",
        serial_number="SN-X500-001002",
        last_heartbeat=_dt(2026, 11, 17, 9, 55),
    ),
    Machine(
        machine_id=MachineId("MCH-X9Y8-Z7W6"),
        model_type="SERVO-S800",
        model_series="S800-Pro",
        firmware_version="2.5.0",
        commissioning_date=_d(2020, 11, 1),
        status="MAINTENANCE",
        location=Location(
            site_id=SiteId("SITE-C3D4"),
            building="BuildingC",
            floor=1,
            rack_position="C1-R12",
            gps_lat=40.7128,
            gps_lon=-74.0060,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=8,
            ram_gb=64,
            storage_tb=1.0,
            gpu_model=None,
            network_bandwidth_gbps=1.0,
        ),
        owner_customer_id="CUST-001002",
        serial_number="SN-S800-002001",
        last_heartbeat=_dt(2026, 11, 17, 8, 30),
    ),
    Machine(
        machine_id=MachineId("MCH-P1Q2-R3S4"),
        model_type="PRESS-P400",
        model_series="P400-HeavyDuty",
        firmware_version="1.8.3",
        commissioning_date=_d(2023, 1, 10),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-C3D4"),
            building="BuildingD",
            floor=3,
            rack_position="D3-R02",
            gps_lat=40.7130,
            gps_lon=-74.0062,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=4,
            ram_gb=32,
            storage_tb=0.5,
            gpu_model=None,
            network_bandwidth_gbps=1.0,
        ),
        owner_customer_id="CUST-001003",
        serial_number="SN-P400-003001",
        last_heartbeat=_dt(2026, 11, 17, 10, 5),
    ),
    Machine(
        machine_id=MachineId("MCH-T5U6-V7W8"),
        model_type="PRESS-P400",
        model_series="P400-Standard",
        firmware_version="1.7.1",
        commissioning_date=_d(2022, 7, 22),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-E5F6"),
            building="BuildingE",
            floor=1,
            rack_position="E1-R05",
            gps_lat=51.5074,
            gps_lon=-0.1278,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=4,
            ram_gb=32,
            storage_tb=0.5,
            gpu_model=None,
            network_bandwidth_gbps=1.0,
        ),
        owner_customer_id="CUST-001003",
        serial_number="SN-P400-003002",
        last_heartbeat=_dt(2026, 11, 17, 9, 45),
    ),
    Machine(
        machine_id=MachineId("MCH-B2C3-D4E5"),
        model_type="COMPUTE-C200",
        model_series="C200-Edge",
        firmware_version="4.2.1",
        commissioning_date=_d(2023, 5, 1),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-E5F6"),
            building="BuildingF",
            floor=2,
            rack_position="F2-R09",
            gps_lat=51.5075,
            gps_lon=-0.1279,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=64,
            ram_gb=512,
            storage_tb=8.0,
            gpu_model="NVIDIA H100",
            network_bandwidth_gbps=100.0,
        ),
        owner_customer_id="CUST-001004",
        serial_number="SN-C200-004001",
        last_heartbeat=_dt(2026, 11, 17, 10, 10),
    ),
    Machine(
        machine_id=MachineId("MCH-F6G7-H8I9"),
        model_type="COMPUTE-C200",
        model_series="C200-Core",
        firmware_version="4.1.0",
        commissioning_date=_d(2022, 9, 15),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-G7H8"),
            building="BuildingG",
            floor=1,
            rack_position="G1-R01",
            gps_lat=48.8566,
            gps_lon=2.3522,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=32,
            ram_gb=256,
            storage_tb=4.0,
            gpu_model="NVIDIA RTX 4090",
            network_bandwidth_gbps=25.0,
        ),
        owner_customer_id="CUST-001005",
        serial_number="SN-C200-005001",
        last_heartbeat=_dt(2026, 11, 17, 9, 50),
    ),
    Machine(
        machine_id=MachineId("MCH-J1K2-L3M4"),
        model_type="INDUSTRIAL-X500",
        model_series="X500-Gen4",
        firmware_version="3.20.0",
        commissioning_date=_d(2024, 2, 1),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-G7H8"),
            building="BuildingH",
            floor=4,
            rack_position="H4-R15",
            gps_lat=48.8567,
            gps_lon=2.3523,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=48,
            ram_gb=384,
            storage_tb=6.0,
            gpu_model="NVIDIA A100",
            network_bandwidth_gbps=50.0,
        ),
        owner_customer_id="CUST-001005",
        serial_number="SN-X500-005002",
        last_heartbeat=_dt(2026, 11, 17, 10, 15),
    ),
    Machine(
        machine_id=MachineId("MCH-N5O6-P7Q8"),
        model_type="SERVO-S800",
        model_series="S800-Compact",
        firmware_version="2.3.5",
        commissioning_date=_d(2021, 4, 12),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-A1B2"),
            building="BuildingA",
            floor=3,
            rack_position="A3-R06",
            gps_lat=37.7751,
            gps_lon=-122.4196,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=8,
            ram_gb=64,
            storage_tb=1.0,
            gpu_model=None,
            network_bandwidth_gbps=1.0,
        ),
        owner_customer_id="CUST-001006",
        serial_number="SN-S800-006001",
        last_heartbeat=_dt(2026, 11, 17, 9, 40),
    ),
    Machine(
        machine_id=MachineId("MCH-R9S0-T1U2"),
        model_type="PRESS-P400",
        model_series="P400-Legacy",
        firmware_version="1.2.0",
        commissioning_date=_d(2018, 8, 30),
        status="DECOMMISSIONED",
        location=Location(
            site_id=SiteId("SITE-C3D4"),
            building="BuildingC",
            floor=2,
            rack_position="C2-R20",
            gps_lat=40.7129,
            gps_lon=-74.0061,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=4,
            ram_gb=16,
            storage_tb=0.25,
            gpu_model=None,
            network_bandwidth_gbps=0.1,
        ),
        owner_customer_id="CUST-001007",
        serial_number="SN-P400-007001",
        last_heartbeat=_dt(2024, 1, 1, 0, 0),
    ),
    Machine(
        machine_id=MachineId("MCH-W5X6-Y7Z8"),
        model_type="COMPUTE-C200",
        model_series="C200-HPC",
        firmware_version="4.3.2",
        commissioning_date=_d(2024, 6, 1),
        status="ACTIVE",
        location=Location(
            site_id=SiteId("SITE-E5F6"),
            building="BuildingE",
            floor=3,
            rack_position="E3-R10",
            gps_lat=51.5076,
            gps_lon=-0.1280,
        ),
        hardware_spec=HardwareSpec(
            cpu_cores=96,
            ram_gb=768,
            storage_tb=12.0,
            gpu_model="NVIDIA H100",
            network_bandwidth_gbps=200.0,
        ),
        owner_customer_id="CUST-001003",
        serial_number="SN-C200-003003",
        last_heartbeat=_dt(2026, 11, 17, 10, 20),
    ),
]

# Build lookup dict: machine_id string -> Machine
_MACHINES: dict[str, Machine] = {str(m.machine_id): m for m in _MACHINES_LIST}

# Mutable state dict for status updates
_MACHINE_STATUS: dict[str, str] = {str(m.machine_id): m.status for m in _MACHINES_LIST}

# ---------------------------------------------------------------------------
# Maintenance records (3 per machine)
# ---------------------------------------------------------------------------

def _build_records(machine_id_str: str) -> list[MaintenanceRecord]:
    mid = MachineId(machine_id_str)
    return [
        MaintenanceRecord(
            record_id=f"MR-{machine_id_str}-001",
            machine_id=mid,
            performed_at=_dt(2026, 1, 15, 8, 0),
            technician_name="Alice Johnson",
            technician_id="TECH-001",
            work_type="PREVENTIVE",
            description="Scheduled quarterly inspection and lubrication",
            parts_replaced=["Lubrication Filter LF-100", "O-Ring Set OR-50"],
            duration_hours=2.5,
            cost_usd=450.00,
            next_scheduled=_d(2026, 4, 15),
        ),
        MaintenanceRecord(
            record_id=f"MR-{machine_id_str}-002",
            machine_id=mid,
            performed_at=_dt(2026, 5, 20, 14, 0),
            technician_name="Bob Martinez",
            technician_id="TECH-002",
            work_type="CORRECTIVE",
            description="Replaced worn drive belt after vibration alert",
            parts_replaced=["Drive Belt DB-200", "Tensioner Pulley TP-75"],
            duration_hours=4.0,
            cost_usd=1200.00,
            next_scheduled=_d(2026, 11, 20),
        ),
        MaintenanceRecord(
            record_id=f"MR-{machine_id_str}-003",
            machine_id=mid,
            performed_at=_dt(2026, 9, 10, 9, 0),
            technician_name="Carol Smith",
            technician_id="TECH-003",
            work_type="PREVENTIVE",
            description="Annual firmware update and hardware diagnostics",
            parts_replaced=[],
            duration_hours=1.5,
            cost_usd=300.00,
            next_scheduled=_d(2027, 9, 10),
        ),
    ]


_MAINTENANCE: dict[str, list[MaintenanceRecord]] = {
    mid: _build_records(mid) for mid in _MACHINES
}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class MockMachineAdapter(MachineRepositoryPort):
    async def get_by_id(self, machine_id: MachineId) -> Machine:
        key = str(machine_id)
        if key not in _MACHINES:
            raise EntityNotFoundError(f"Machine not found: {key}")
        m = _MACHINES[key]
        # Return machine with current (possibly updated) status
        current_status = _MACHINE_STATUS[key]
        if m.status != current_status:
            m = Machine(
                machine_id=m.machine_id,
                model_type=m.model_type,
                model_series=m.model_series,
                firmware_version=m.firmware_version,
                commissioning_date=m.commissioning_date,
                status=current_status,
                location=m.location,
                hardware_spec=m.hardware_spec,
                owner_customer_id=m.owner_customer_id,
                serial_number=m.serial_number,
                last_heartbeat=m.last_heartbeat,
            )
        return m

    async def list_fleet(
        self,
        model_type: Optional[str],
        status: Optional[str],
        site_id: Optional[SiteId],
        page: int,
        page_size: int,
    ) -> list[Machine]:
        results: list[Machine] = []
        for m in _MACHINES_LIST:
            current_status = _MACHINE_STATUS[str(m.machine_id)]
            # Rebuild machine with current status if needed
            machine = m if m.status == current_status else Machine(
                machine_id=m.machine_id,
                model_type=m.model_type,
                model_series=m.model_series,
                firmware_version=m.firmware_version,
                commissioning_date=m.commissioning_date,
                status=current_status,
                location=m.location,
                hardware_spec=m.hardware_spec,
                owner_customer_id=m.owner_customer_id,
                serial_number=m.serial_number,
                last_heartbeat=m.last_heartbeat,
            )
            if model_type and model_type.upper() not in machine.model_type.upper():
                continue
            if status and machine.status != status:
                continue
            if site_id and str(machine.location.site_id) != str(site_id):
                continue
            results.append(machine)

        # Pagination
        start = (page - 1) * page_size
        return results[start : start + page_size]

    async def get_maintenance_history(self, machine_id: MachineId, limit: int) -> MaintenanceHistory:
        key = str(machine_id)
        if key not in _MACHINES:
            raise EntityNotFoundError(f"Machine not found: {key}")
        records = _MAINTENANCE.get(key, [])[:limit]
        return MaintenanceHistory(
            machine_id=machine_id,
            records=records,
            total_cost_usd=sum(r.cost_usd for r in records),
            total_records=len(records),
        )

    async def update_operational_state(
        self, machine_id: MachineId, target_state: str, reason: str
    ) -> OperationalStateUpdate:
        key = str(machine_id)
        if key not in _MACHINES:
            raise EntityNotFoundError(f"Machine not found: {key}")
        previous_state = _MACHINE_STATUS[key]
        _MACHINE_STATUS[key] = target_state
        return OperationalStateUpdate(
            machine_id=machine_id,
            previous_state=previous_state,
            new_state=target_state,
            reason=reason,
            updated_at=datetime.now(tz=timezone.utc),
            updated_by="system",
            audit_id=str(uuid.uuid4()),
        )
