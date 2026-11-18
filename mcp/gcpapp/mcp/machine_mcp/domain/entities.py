from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from common.domain.enums import MachineStatus, WorkType
from common.domain.value_objects import MachineId, SiteId


@dataclass(frozen=True)
class HardwareSpec:
    cpu_cores: int
    ram_gb: int
    storage_tb: float
    gpu_model: Optional[str]
    network_bandwidth_gbps: float


@dataclass(frozen=True)
class Location:
    site_id: SiteId
    building: str
    floor: int
    rack_position: str
    gps_lat: float
    gps_lon: float


@dataclass(frozen=True)
class Machine:
    machine_id: MachineId
    model_type: str
    model_series: str
    firmware_version: str
    commissioning_date: date
    status: MachineStatus | str
    location: Location
    hardware_spec: HardwareSpec
    owner_customer_id: str
    serial_number: str
    last_heartbeat: datetime


@dataclass(frozen=True)
class MaintenanceRecord:
    record_id: str
    machine_id: MachineId
    performed_at: datetime
    technician_name: str
    technician_id: str
    work_type: WorkType | str
    description: str
    parts_replaced: list[str]
    duration_hours: float
    cost_usd: float
    next_scheduled: Optional[date]


@dataclass(frozen=True)
class MaintenanceHistory:
    machine_id: MachineId
    records: list[MaintenanceRecord]
    total_cost_usd: float
    total_records: int


@dataclass(frozen=True)
class OperationalStateUpdate:
    machine_id: MachineId
    previous_state: MachineStatus | str
    new_state: MachineStatus | str
    reason: str
    updated_at: datetime
    updated_by: str
    audit_id: str
