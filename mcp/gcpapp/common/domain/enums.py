from __future__ import annotations
from enum import Enum


class CustomerTier(str, Enum):
    """Enterprise customer service tiers."""
    ENTERPRISE = "ENTERPRISE"
    PREMIUM = "PREMIUM"
    STANDARD = "STANDARD"

    @classmethod
    def from_str(cls, val: str | None) -> "CustomerTier | None":
        if val is None:
            return None
        try:
            return cls(val.upper())
        except ValueError:
            valid = ", ".join([e.value for e in cls])
            raise ValueError(f"Invalid tier: {val!r}. Must be one of {valid}")


class BillingStatus(str, Enum):
    """Customer account billing statuses."""
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    PENDING = "PENDING"


class SlaLevel(str, Enum):
    """Contractual SLA tiers."""
    PLATINUM = "PLATINUM"
    GOLD = "GOLD"
    SILVER = "SILVER"


class MachineStatus(str, Enum):
    """Operational machine lifecycle states."""
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"

    @classmethod
    def from_str(cls, val: str | None) -> "MachineStatus | None":
        if val is None:
            return None
        try:
            return cls(val.upper())
        except ValueError:
            valid = ", ".join([e.value for e in cls])
            raise ValueError(f"Invalid machine status: {val!r}. Must be one of {valid}")


class WorkType(str, Enum):
    """Maintenance activity classifications."""
    PREVENTIVE = "PREVENTIVE"
    CORRECTIVE = "CORRECTIVE"
    EMERGENCY = "EMERGENCY"


class MetricQuality(str, Enum):
    """Sensor reading quality flags."""
    GOOD = "GOOD"
    UNCERTAIN = "UNCERTAIN"
    BAD = "BAD"


class AggregationType(str, Enum):
    """Time-series mathematical aggregations."""
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"
    SUM = "SUM"


class TimeResolution(str, Enum):
    """Time-series query bucket resolutions."""
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    ONE_HOUR = "1h"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"


class AlertSeverity(str, Enum):
    """Telemetry alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @classmethod
    def from_str(cls, val: str | None) -> "AlertSeverity | None":
        if val is None:
            return None
        try:
            return cls(val.upper())
        except ValueError:
            valid = ", ".join([e.value for e in cls])
            raise ValueError(f"Invalid alert severity: {val!r}. Must be one of {valid}")
