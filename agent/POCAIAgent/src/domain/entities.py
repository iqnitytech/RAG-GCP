from __future__ import annotations

from dataclasses import dataclass

TEMP_LIMIT_C = 80.0
VIBRATION_LIMIT = 0.8


@dataclass(frozen=True)
class Machine:
    id: str
    name: str
    status: str
    customer_id: str


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    tier: str


@dataclass(frozen=True)
class TelemetryReading:
    machine_id: str
    temperature_c: float
    vibration: float
    timestamp: str

    @property
    def is_anomaly(self) -> bool:
        return self.temperature_c > TEMP_LIMIT_C or self.vibration > VIBRATION_LIMIT
