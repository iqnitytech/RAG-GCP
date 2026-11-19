from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from common.domain.value_objects import MachineId
from mcp.telemetry_mcp.domain.entities import (
    Alert,
    FaultCodeDiagnosis,
    LatestMetrics,
    TimeSeriesResult,
)


class TelemetryQueryPort(ABC):
    @abstractmethod
    async def get_latest_metrics(
        self,
        machine_id: MachineId,
        metric_names: Optional[list[str]],
    ) -> LatestMetrics: ...

    @abstractmethod
    async def query_range(
        self,
        machine_id: MachineId,
        start_time: datetime,
        end_time: datetime,
        resolution: str,
        aggregation: str,
    ) -> list[TimeSeriesResult]: ...


class TelemetryAlertPort(ABC):
    @abstractmethod
    async def get_active_alerts(
        self,
        machine_id: Optional[MachineId],
        severity: Optional[str],
    ) -> list[Alert]: ...


class TelemetryDiagnosticsPort(ABC):
    @abstractmethod
    async def diagnose_fault_code(self, fault_code: str) -> FaultCodeDiagnosis: ...


class TelemetryRepositoryPort(
    TelemetryQueryPort,
    TelemetryAlertPort,
    TelemetryDiagnosticsPort,
    ABC,
):
    """Unified repository port for Telemetry MCP."""
    pass
