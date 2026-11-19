from __future__ import annotations
import re
from datetime import datetime
from typing import Optional

from common.domain.enums import AggregationType, AlertSeverity, TimeResolution
from common.domain.value_objects import MachineId
from core.exceptions import DomainValidationError
from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort
from mcp.telemetry_mcp.domain.entities import (
    Alert,
    FaultCodeDiagnosis,
    LatestMetrics,
    TimeSeriesResult,
)

ALLOWED_METRICS = {
    "vibration",
    "temperature",
    "rpm",
    "pressure",
    "power_kw",
    "humidity",
    "current_amp",
    "voltage",
}

VALID_RESOLUTIONS = {e.value for e in TimeResolution}
VALID_AGGREGATIONS = {e.value for e in AggregationType}
VALID_SEVERITIES = {e.value for e in AlertSeverity}
FAULT_CODE_RE = re.compile(r"^[A-Z0-9]{1,4}-[0-9]{3,6}$")
MAX_FAULT_CODE_LENGTH = 50


class GetLatestMetricsUseCase:
    def __init__(self, repo: TelemetryRepositoryPort) -> None:
        self._repo = repo

    async def execute(
        self,
        machine_id_str: str,
        metric_names: Optional[list[str]] = None,
    ) -> LatestMetrics:
        try:
            machine_id = MachineId(machine_id_str)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}. Expected MCH-XXXX-XXXX") from exc

        if metric_names:
            invalid = [m for m in metric_names if m.lower() not in ALLOWED_METRICS]
            if invalid:
                raise DomainValidationError(
                    f"Invalid metric names: {invalid}. Supported metrics: {sorted(ALLOWED_METRICS)}"
                )
            cleaned_metrics = [m.lower() for m in metric_names]
        else:
            cleaned_metrics = None

        return await self._repo.get_latest_metrics(machine_id, cleaned_metrics)


class QueryRangeUseCase:
    def __init__(self, repo: TelemetryRepositoryPort) -> None:
        self._repo = repo

    async def execute(
        self,
        machine_id_str: str,
        start_time: datetime,
        end_time: datetime,
        resolution: str = "5m",
        aggregation: str = "AVG",
    ) -> list[TimeSeriesResult]:
        try:
            machine_id = MachineId(machine_id_str)
        except ValueError as exc:
            raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}. Expected MCH-XXXX-XXXX") from exc

        if start_time >= end_time:
            raise DomainValidationError(f"start_time ({start_time.isoformat()}) must be before end_time ({end_time.isoformat()})")

        res_lower = resolution.lower()
        if res_lower not in VALID_RESOLUTIONS:
            raise DomainValidationError(
                f"Invalid resolution: {resolution!r}. Allowed resolutions: {sorted(VALID_RESOLUTIONS)}"
            )

        agg_upper = aggregation.upper()
        if agg_upper not in VALID_AGGREGATIONS:
            raise DomainValidationError(
                f"Invalid aggregation: {aggregation!r}. Allowed aggregations: {sorted(VALID_AGGREGATIONS)}"
            )

        return await self._repo.query_range(machine_id, start_time, end_time, res_lower, agg_upper)


class GetActiveAlertsUseCase:
    def __init__(self, repo: TelemetryRepositoryPort) -> None:
        self._repo = repo

    async def execute(
        self,
        machine_id_str: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> list[Alert]:
        machine_id: Optional[MachineId] = None
        if machine_id_str:
            try:
                machine_id = MachineId(machine_id_str)
            except ValueError as exc:
                raise DomainValidationError(f"Invalid machine_id format: {machine_id_str!r}. Expected MCH-XXXX-XXXX") from exc

        sev_upper: Optional[str] = None
        if severity:
            sev_upper = severity.upper()
            if sev_upper not in VALID_SEVERITIES:
                raise DomainValidationError(
                    f"Invalid severity: {severity!r}. Allowed severities: {sorted(VALID_SEVERITIES)}"
                )

        return await self._repo.get_active_alerts(machine_id, sev_upper)


class DiagnoseFaultCodeUseCase:
    def __init__(self, repo: TelemetryRepositoryPort) -> None:
        self._repo = repo

    async def execute(self, fault_code: str) -> FaultCodeDiagnosis:
        # SEC-04: Length limit
        if len(fault_code) > MAX_FAULT_CODE_LENGTH:
            raise DomainValidationError(f"fault_code cannot exceed {MAX_FAULT_CODE_LENGTH} characters")

        code_upper = fault_code.strip().upper()
        if not FAULT_CODE_RE.match(code_upper):
            raise DomainValidationError(
                f"Invalid fault_code format: {fault_code!r}. Expected format like E-001, V-002, or FLT-1001"
            )
        return await self._repo.diagnose_fault_code(code_upper)
