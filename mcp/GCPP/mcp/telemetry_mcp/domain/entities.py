from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from common.domain.enums import AggregationType, AlertSeverity, MetricQuality, TimeResolution
from common.domain.value_objects import MachineId


@dataclass(frozen=True)
class MetricPoint:
    metric_name: str
    value: float
    unit: str  # e.g. "RPM", "°C", "bar", "mm/s", "kW", "%", "A", "V"
    timestamp: datetime
    quality: MetricQuality | str  # "GOOD", "UNCERTAIN", "BAD"


@dataclass(frozen=True)
class LatestMetrics:
    machine_id: MachineId
    metrics: list[MetricPoint]
    snapshot_time: datetime
    source: str


@dataclass(frozen=True)
class AggregatedDataPoint:
    timestamp: datetime
    value: float
    metric_name: str
    aggregation: AggregationType | str  # "AVG", "MAX", "MIN"


@dataclass(frozen=True)
class TimeSeriesResult:
    machine_id: MachineId
    metric_name: str
    start_time: datetime
    end_time: datetime
    resolution: TimeResolution | str  # "1m", "5m", "1h", "6h", "1d"
    aggregation: AggregationType | str  # "AVG", "MAX", "MIN"
    data_points: list[AggregatedDataPoint]
    total_points: int


@dataclass(frozen=True)
class Alert:
    alert_id: str
    machine_id: MachineId
    severity: AlertSeverity | str  # "INFO", "WARNING", "CRITICAL"
    metric_name: str
    threshold_value: float
    actual_value: float
    triggered_at: datetime
    description: str
    remediation: str
    is_acknowledged: bool


@dataclass(frozen=True)
class FaultCodeDiagnosis:
    fault_code: str
    name: str
    description: str
    root_causes: list[str]
    diagnostic_steps: list[str]
    recommended_parts: list[str]
    severity: AlertSeverity | str  # "INFO", "WARNING", "CRITICAL"
    estimated_repair_hours: float
    documentation_url: str
