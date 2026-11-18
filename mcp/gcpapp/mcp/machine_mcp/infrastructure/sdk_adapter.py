from __future__ import annotations
import asyncio
import json
import uuid
from datetime import date, datetime, timezone
from typing import Optional, Any

from config.settings import Settings
from common.domain.value_objects import MachineId, SiteId
from common.infrastructure.connector import create_connector
from common.infrastructure.resilience import call_sdk_with_retry, handle_sdk_exception, retry_with_backoff
from core.exceptions import (
    AuthenticationError,
    EntityNotFoundError,
    RateLimitError,
    SdkGatewayError,
)
from core.logging import get_logger
from mcp.machine_mcp.application.ports import MachineRepositoryPort
from mcp.machine_mcp.domain.entities import (
    HardwareSpec,
    Location,
    Machine,
    MaintenanceHistory,
    MaintenanceRecord,
    OperationalStateUpdate,
)

logger = get_logger(__name__)


class SdkMachineAdapter(MachineRepositoryPort):
    """SDK adapter connecting to Enterprise Data Hub (EDH) via rag_platform_sdk for Machine operations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connector: Any = None

    def _get_connector(self) -> Any:
        if self._connector is None:
            try:
                self._connector = create_connector(
                    self._settings,
                    strategy="api_hub",
                    namespace="machine",
                )
            except (AuthenticationError, RateLimitError, SdkGatewayError):
                raise
            except Exception as e:
                logger.error("Failed to initialize EdhConnector for Machine", error=str(e))
                handle_sdk_exception(e, "initializing Machine connector")
        return self._connector

    @retry_with_backoff(max_attempts=3)
    async def get_by_id(self, machine_id: MachineId) -> Machine:
        try:
            connector = self._get_connector()
            from sdk.models import DataZone
            records = await asyncio.to_thread(
                call_sdk_with_retry,
                lambda: list(
                    connector.read_records(
                        "machines",
                        zone=DataZone.CURATED,
                        partition_filter={"machine_id": str(machine_id)},
                    )
                ),
            )
            if not records:
                raise EntityNotFoundError(f"Machine {machine_id} not found in SDK data hub")
            return self._map_record_to_machine(records[0])
        except (EntityNotFoundError, AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error getting machine details", machine_id=str(machine_id), error=str(e))
            handle_sdk_exception(e, f"getting machine {machine_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def list_fleet(
        self,
        model_type: Optional[str],
        status: Optional[str],
        site_id: Optional[SiteId],
        page: int,
        page_size: int,
    ) -> list[Machine]:
        try:
            connector = self._get_connector()
            filter_map: dict[str, str] = {}
            if status:
                filter_map["status"] = status
            if site_id:
                filter_map["site_id"] = str(site_id)

            offset = (page - 1) * page_size

            # ARCH-05: Stream iterator and slice lazily to prevent loading 10K+ records into memory
            def _stream_and_filter():
                import itertools
                from sdk.models import DataZone
                records_iter = connector.read_records(
                    "machines",
                    zone=DataZone.CURATED,
                    partition_filter=filter_map,
                )
                matching = (
                    self._map_record_to_machine(r)
                    for r in records_iter
                    if not model_type or model_type.lower() in r.get("model_type", "").lower()
                )
                return list(itertools.islice(matching, offset, offset + page_size))

            return await asyncio.to_thread(call_sdk_with_retry, _stream_and_filter)
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error listing machine fleet", error=str(e))
            handle_sdk_exception(e, "listing machine fleet")
            raise

    @retry_with_backoff(max_attempts=3)
    async def get_maintenance_history(self, machine_id: MachineId, limit: int) -> MaintenanceHistory:
        try:
            connector = self._get_connector()

            # ARCH-05: Stream iterator lazily
            def _stream_maintenance():
                import itertools
                from sdk.models import DataZone
                records_iter = connector.read_records(
                    "maintenance_logs",
                    zone=DataZone.CURATED,
                    partition_filter={"machine_id": str(machine_id)},
                )
                m_records = [
                    self._map_record_to_maintenance(r)
                    for r in itertools.islice(records_iter, limit)
                ]
                total_cost = sum(r.cost_usd for r in m_records)
                return MaintenanceHistory(
                    machine_id=machine_id,
                    records=m_records,
                    total_cost_usd=round(total_cost, 2),
                    total_records=len(m_records),
                )

            return await asyncio.to_thread(call_sdk_with_retry, _stream_maintenance)
        except (AuthenticationError, RateLimitError):
            raise
        except Exception as e:
            logger.error("SDK error fetching maintenance history", machine_id=str(machine_id), error=str(e))
            handle_sdk_exception(e, f"fetching maintenance history for {machine_id}")
            raise

    @retry_with_backoff(max_attempts=3)
    async def update_operational_state(
        self,
        machine_id: MachineId,
        target_state: str,
        reason: str,
    ) -> OperationalStateUpdate:
        try:
            connector = self._get_connector()
            from sdk.models import EdhObjectRef, DataZone

            # RELI-02: Read the actual current state before writing so the audit
            # record reflects the real previous_state rather than a hardcoded value.
            current_machine = await self.get_by_id(machine_id)
            previous_state = current_machine.status

            now = datetime.now(timezone.utc)
            audit_id = str(uuid.uuid4())
            payload = {
                "machine_id": str(machine_id),
                "previous_state": previous_state,
                "target_state": target_state,
                "reason": reason,
                "audit_id": audit_id,
                "updated_at": now.isoformat(),
            }
            if hasattr(connector, "ref"):
                ref = connector.ref(f"machines/{machine_id}/state.json", zone=DataZone.CURATED)
            else:
                ref = EdhObjectRef(namespace="machine", key=f"machines/{machine_id}/state.json", zone=DataZone.CURATED)
            await asyncio.to_thread(
                call_sdk_with_retry,
                connector.put_bytes,
                ref,
                json.dumps(payload).encode("utf-8"),
            )
            return OperationalStateUpdate(
                machine_id=machine_id,
                previous_state=previous_state,
                new_state=target_state,
                reason=reason,
                updated_at=now,
                updated_by="mcp-operator",
                audit_id=audit_id,
            )
        except (AuthenticationError, RateLimitError, EntityNotFoundError):
            raise
        except Exception as e:
            logger.error("SDK error updating operational state", machine_id=str(machine_id), error=str(e))
            handle_sdk_exception(e, f"updating state for {machine_id}")
            raise

    def _map_record_to_machine(self, rec: dict[str, Any]) -> Machine:
        loc = rec.get("location", {})
        spec = rec.get("hardware_spec", {})
        return Machine(
            machine_id=MachineId(rec["machine_id"]),
            model_type=rec.get("model_type", "GENERIC-M1"),
            model_series=rec.get("model_series", "SERIES-1"),
            firmware_version=rec.get("firmware_version", "v1.0.0"),
            commissioning_date=date.fromisoformat(rec.get("commissioning_date", "2023-01-01")),
            status=rec.get("status", "ACTIVE"),
            location=Location(
                site_id=SiteId(loc.get("site_id", "SITE-A1B2")),
                building=loc.get("building", "Main"),
                floor=int(loc.get("floor", 1)),
                rack_position=loc.get("rack_position", "R-01"),
                gps_lat=float(loc.get("gps_lat", 0.0)),
                gps_lon=float(loc.get("gps_lon", 0.0)),
            ),
            hardware_spec=HardwareSpec(
                cpu_cores=int(spec.get("cpu_cores", 8)),
                ram_gb=int(spec.get("ram_gb", 32)),
                storage_tb=float(spec.get("storage_tb", 1.0)),
                gpu_model=spec.get("gpu_model"),
                network_bandwidth_gbps=float(spec.get("network_bandwidth_gbps", 10.0)),
            ),
            owner_customer_id=rec.get("owner_customer_id", "CUST-001001"),
            serial_number=rec.get("serial_number", "SN-000000"),
            last_heartbeat=datetime.fromisoformat(rec.get("last_heartbeat", "2026-11-17T00:00:00+00:00")),
        )

    def _map_record_to_maintenance(self, rec: dict[str, Any]) -> MaintenanceRecord:
        next_sched = rec.get("next_scheduled")
        if isinstance(next_sched, str):
            next_sched = date.fromisoformat(next_sched)
        return MaintenanceRecord(
            record_id=rec.get("record_id", "REC-001"),
            machine_id=MachineId(rec["machine_id"]),
            performed_at=datetime.fromisoformat(rec.get("performed_at", "2026-01-01T00:00:00+00:00")),
            technician_name=rec.get("technician_name", "Tech"),
            technician_id=rec.get("technician_id", "TECH-01"),
            work_type=rec.get("work_type", "PREVENTIVE"),
            description=rec.get("description", ""),
            parts_replaced=list(rec.get("parts_replaced", [])),
            duration_hours=float(rec.get("duration_hours", 1.0)),
            cost_usd=float(rec.get("cost_usd", 0.0)),
            next_scheduled=next_sched,
        )
