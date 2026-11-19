from __future__ import annotations
import threading
import time
from typing import Any


class MetricLabelKey:
    @staticmethod
    def serialize(labels: dict[str, str]) -> str:
        if not labels:
            return ""
        items = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(items) + "}"


class Counter:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._values: dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = MetricLabelKey.serialize(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            for key, val in sorted(self._values.items()):
                lines.append(f"{self.name}{key} {val}")
        return lines


class Gauge:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._values: dict[str, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        key = MetricLabelKey.serialize(labels)
        with self._lock:
            self._values[key] = float(value)

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            for key, val in sorted(self._values.items()):
                lines.append(f"{self.name}{key} {val}")
        return lines


class Histogram:
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self, name: str, description: str, buckets: tuple[float, ...] | None = None) -> None:
        self.name = name
        self.description = description
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: dict[str, dict[float, int]] = {}
        self._sums: dict[str, float] = {}
        self._totals: dict[str, int] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = MetricLabelKey.serialize(labels)
        with self._lock:
            if key not in self._counts:
                self._counts[key] = {b: 0 for b in self.buckets}
                self._sums[key] = 0.0
                self._totals[key] = 0

            self._sums[key] += value
            self._totals[key] += 1
            for b in self.buckets:
                if value <= b:
                    self._counts[key][b] += 1

    def collect(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            for key in sorted(self._counts.keys()):
                label_base = key[1:-1] if key else ""
                prefix = f"{label_base}," if label_base else ""
                cumulative = 0
                for b in self.buckets:
                    cumulative = self._counts[key][b]
                    lines.append(f'{self.name}_bucket{{{prefix}le="{b}"}} {cumulative}')
                lines.append(f'{self.name}_bucket{{{prefix}le="+Inf"}} {self._totals[key]}')
                lines.append(f"{self.name}_sum{key} {self._sums[key]:.6f}")
                lines.append(f"{self.name}_count{key} {self._totals[key]}")
        return lines


class PrometheusRegistry:
    """In-memory Prometheus metrics registry (OBS-03)."""
    def __init__(self) -> None:
        self.tool_invocations = Counter(
            "mcp_tool_invocations_total",
            "Total number of MCP tool invocations across all servers",
        )
        self.tool_duration = Histogram(
            "mcp_tool_duration_seconds",
            "Latency of MCP tool invocations in seconds",
        )
        self.circuit_breaker_state = Gauge(
            "mcp_circuit_breaker_state",
            "State of circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
        )
        self.circuit_breaker_trips = Counter(
            "mcp_circuit_breaker_trips_total",
            "Total number of times circuit breaker has tripped to OPEN",
        )

    def generate_metrics_text(self) -> str:
        """Render metrics in Prometheus text exposition format (version 0.0.4)."""
        lines: list[str] = []
        lines.extend(self.tool_invocations.collect())
        lines.extend(self.tool_duration.collect())
        lines.extend(self.circuit_breaker_state.collect())
        lines.extend(self.circuit_breaker_trips.collect())
        return "\n".join(lines) + "\n"


_registry = PrometheusRegistry()


def get_metrics_registry() -> PrometheusRegistry:
    return _registry


def record_tool_metrics(server: str, tool: str, duration_seconds: float, status: str) -> None:
    _registry.tool_invocations.inc(server=server, tool=tool, status=status)
    _registry.tool_duration.observe(duration_seconds, server=server, tool=tool)


def update_circuit_breaker_metric(breaker_name: str, state_str: str) -> None:
    state_val = 0.0
    if state_str == "HALF_OPEN":
        state_val = 1.0
    elif state_str == "OPEN":
        state_val = 2.0
    _registry.circuit_breaker_state.set(state_val, breaker=breaker_name)
    if state_str == "OPEN":
        _registry.circuit_breaker_trips.inc(breaker=breaker_name)
