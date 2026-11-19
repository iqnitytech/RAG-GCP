from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.domain.value_objects import MachineId
from core.exceptions import EntityNotFoundError
from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort
from mcp.telemetry_mcp.domain.entities import (
    AggregatedDataPoint,
    Alert,
    FaultCodeDiagnosis,
    LatestMetrics,
    MetricPoint,
    TimeSeriesResult,
)

# ---------------------------------------------------------------------------
# Pre-seeded Diagnostic Fault Code Knowledge Base (15 entries)
# ---------------------------------------------------------------------------

FAULT_CODE_DB: dict[str, FaultCodeDiagnosis] = {
    "E-001": FaultCodeDiagnosis(
        fault_code="E-001",
        name="Motor Overheat",
        description="Motor core temperature has exceeded safety threshold (>90°C).",
        root_causes=[
            "Inadequate cooling airflow or clogged fan filter",
            "High duty-cycle operation above rated capacity",
            "Internal bearing friction overload",
        ],
        diagnostic_steps=[
            "Verify ambient temperature and ventilation intake",
            "Inspect physical fan assembly for obstruction or blade damage",
            "Measure resistance across motor windings",
            "Check current draw on all phases for unbalance",
            "Review thermal cutout sensor calibration",
        ],
        recommended_parts=["Cooling Fan Assembly CF-200", "Thermal Cutout Switch TC-50", "Motor Bearing Set MB-100"],
        severity="CRITICAL",
        estimated_repair_hours=4.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/E-001",
    ),
    "E-002": FaultCodeDiagnosis(
        fault_code="E-002",
        name="Bearing Wear Detected",
        description="High-frequency acoustic and vibration signatures indicate advanced race/cage degradation.",
        root_causes=[
            "Lubricant breakdown or contamination",
            "Shaft misalignment or excessive radial load",
            "Normal fatigue life (L10h) reached",
        ],
        diagnostic_steps=[
            "Perform spectral vibration analysis (HFE envelope)",
            "Take grease sample to test for metallic particulate",
            "Check shaft runout using dial indicator",
            "Inspect coupling alignment with laser tool",
        ],
        recommended_parts=["SKF Explorer Bearing 6210-2Z", "High-Temp Synthetic Grease Mobilith SHC 100"],
        severity="WARNING",
        estimated_repair_hours=6.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/E-002",
    ),
    "E-003": FaultCodeDiagnosis(
        fault_code="E-003",
        name="Vibration Threshold Exceeded",
        description="Overall RMS vibration velocity exceeds ISO 10816-3 Class II Limit (7.1 mm/s).",
        root_causes=[
            "Rotor unbalance or mass loss",
            "Loose foundation anchor bolts",
            "Resonance at current operating frequency",
        ],
        diagnostic_steps=[
            "Inspect foundation hold-down bolts with torque wrench",
            "Perform single-plane dynamic balance run",
            "Sweep variable frequency drive (VFD) to test resonance",
        ],
        recommended_parts=["Anchor Bolt Kit M16-Grade 8.8", "Elastomeric Vibration Isolator Mounts"],
        severity="CRITICAL",
        estimated_repair_hours=3.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/E-003",
    ),
    "V-001": FaultCodeDiagnosis(
        fault_code="V-001",
        name="Voltage Spike",
        description="Transient overvoltage detected on 3-phase bus (>480V for >20ms).",
        root_causes=[
            "Power grid transient or lightning strike nearby",
            "Capacitor bank switching on local substation",
            "Regenerative braking energy dumped without dynamic braking resistor",
        ],
        diagnostic_steps=[
            "Check power quality analyzer logs on primary feeder",
            "Inspect surge protection device (SPD) status indicator",
            "Test dynamic braking chopper resistor bank",
        ],
        recommended_parts=["Type 2 Surge Protection Device SPD-480V", "Dynamic Braking Resistor 50 Ohm 5kW"],
        severity="WARNING",
        estimated_repair_hours=2.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/V-001",
    ),
    "V-002": FaultCodeDiagnosis(
        fault_code="V-002",
        name="Undervoltage Fault",
        description="Supply voltage dropped below 85% nominal voltage under load.",
        root_causes=[
            "Heavy feeder line loading during peak plant hours",
            "Loose terminal lugs on main incoming contactor",
            "Transformer tap setting incorrect",
        ],
        diagnostic_steps=[
            "Measure line-to-line voltages at disconnect switch",
            "Thermally image main distribution lug connections",
            "Check uninterruptible power supply (UPS) bypass status",
        ],
        recommended_parts=["Main Contactor 3P 100A", "Copper Terminal Lugs 4/0 AWG"],
        severity="WARNING",
        estimated_repair_hours=1.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/V-002",
    ),
    "P-001": FaultCodeDiagnosis(
        fault_code="P-001",
        name="Pressure Relief Valve Open",
        description="Hydraulic/pneumatic pressure exceeded maximum safety limit (8.5 bar).",
        root_causes=[
            "Proportional control valve stuck closed",
            "Accumulator pre-charge bladder rupture",
            "Pressure sensor reading drift or calibration offset",
        ],
        diagnostic_steps=[
            "Check manual mechanical pressure gauge reading",
            "Inspect pilot line filters for debris",
            "Verify relief valve cracking pressure",
        ],
        recommended_parts=["Pressure Relief Valve PRV-100", "Hydraulic Filter Element 10 Micron"],
        severity="CRITICAL",
        estimated_repair_hours=3.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/P-001",
    ),
    "P-002": FaultCodeDiagnosis(
        fault_code="P-002",
        name="Pressure Sensor Failure",
        description="Analog 4-20mA pressure transmitter output out-of-range (<3.6mA or >21mA).",
        root_causes=[
            "Broken signal wire or corroded M12 connector",
            "Diaphragm rupture due to water hammer effect",
            "Power supply voltage dropout to loop circuit",
        ],
        diagnostic_steps=[
            "Measure loop current with calibrated multimeter",
            "Inspect transducer body and electrical cable gland",
            "Bench test transducer with hand calibrator pump",
        ],
        recommended_parts=["WIKA P-30 Pressure Transmitter 0-10 Bar", "Shielded M12 Cable 5m"],
        severity="WARNING",
        estimated_repair_hours=1.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/P-002",
    ),
    "C-001": FaultCodeDiagnosis(
        fault_code="C-001",
        name="Communication Timeout",
        description="Fieldbus (Profinet/EtherCAT) connection lost to remote I/O rack for >500ms.",
        root_causes=[
            "Damaged industrial Ethernet patch cable",
            "Managed switch port disabled by spanning-tree loop detection",
            "EMI interference from unshielded servo drive cables",
        ],
        diagnostic_steps=[
            "Inspect LED link status on remote I/O bus coupler",
            "Test cable continuity with cable certifier",
            "Review managed switch port error counters (CRC errors)",
        ],
        recommended_parts=["Industrial Cat6A Shielded Patch Cable 10m", "Harting RJ45 IP67 Connector"],
        severity="CRITICAL",
        estimated_repair_hours=2.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/C-001",
    ),
    "C-002": FaultCodeDiagnosis(
        fault_code="C-002",
        name="Network Packet Loss",
        description="Telemetry stream dropping >5% of UDP packets over 60 second window.",
        root_causes=[
            "Bandwidth saturation on plant IoT gateway uplink",
            "Duplex mismatch on switch port",
            "Buffer overflow on edge computing module",
        ],
        diagnostic_steps=[
            "Check ping latency and packet drop to telemetry broker",
            "Verify switch port duplex is configured to 1000Mbps Full",
            "Restart edge gateway MQTT forwarding service",
        ],
        recommended_parts=["Gigabit Industrial Switch Module 4-Port", "Edge Gateway Flash Card"],
        severity="INFO",
        estimated_repair_hours=1.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/C-002",
    ),
    "F-001": FaultCodeDiagnosis(
        fault_code="F-001",
        name="Fan Motor Failure",
        description="Auxiliary enclosure cooling fan tachometer signal missing (0 RPM feedback).",
        root_causes=[
            "Fan motor burnt out",
            "Foreign object jammed in fan impeller",
            "Tachometer signal pull-up resistor missing or disconnected",
        ],
        diagnostic_steps=[
            "Power down machine and check fan rotation freely by hand",
            "Measure 24VDC supply voltage at fan connector terminals",
            "Check PLC digital input status for fan OK signal",
        ],
        recommended_parts=["Rittal Enclosure Cooling Fan 24VDC 120mm", "Finger Guard Grille"],
        severity="WARNING",
        estimated_repair_hours=1.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/F-001",
    ),
    "L-001": FaultCodeDiagnosis(
        fault_code="L-001",
        name="Lubrication System Low",
        description="Centralized grease/oil reservoir level sensor tripped low limit switch.",
        root_causes=[
            "Scheduled lubricant top-off missed",
            "Leak in primary distributor metering block",
            "Float switch stuck in low position",
        ],
        diagnostic_steps=[
            "Visually inspect clear oil/grease reservoir level",
            "Inspect lines for visible pooling or cracked tubing",
            "Test level sensor continuity across float travel",
        ],
        recommended_parts=["ISO VG 220 Synthetic Gear Oil 5L", "Distribution Tubing 6mm Nylon 20m"],
        severity="WARNING",
        estimated_repair_hours=1.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/L-001",
    ),
    "L-002": FaultCodeDiagnosis(
        fault_code="L-002",
        name="Lubrication Pump Fault",
        description="Auto-lube pump motor thermal overload tripped or pressure build failure.",
        root_causes=[
            "Metering piston jammed with solidified grease",
            "Clogged inlet suction screen",
            "Air pocket trapped in pump manifold",
        ],
        diagnostic_steps=[
            "Bleed lubrication manifold air release valve",
            "Clean pump inlet suction strainer",
            "Measure pump motor winding resistance",
        ],
        recommended_parts=["Lincoln Auto-Lube Pump Unit 24V", "Pressure Relief Bleed Valve"],
        severity="WARNING",
        estimated_repair_hours=2.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/L-002",
    ),
    "S-001": FaultCodeDiagnosis(
        fault_code="S-001",
        name="Safety Interlock Triggered",
        description="Safety circuit channel A/B discrepancy detected on perimeter light curtain or door switch.",
        root_causes=[
            "Guard door opened during automatic cycle",
            "Optical sensor misalignment on light curtain receiver",
            "Safety relay contact weld or dual-channel timing fault",
        ],
        diagnostic_steps=[
            "Inspect safety door mechanical tongue switch alignment",
            "Clean light curtain optical lenses with lint-free cloth",
            "Check LED indicators on Pilz safety relay",
        ],
        recommended_parts=["RFID Safety Interlock Switch Euchner CTA", "Pilz PNOZ Dual Channel Relay"],
        severity="CRITICAL",
        estimated_repair_hours=1.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/S-001",
    ),
    "S-002": FaultCodeDiagnosis(
        fault_code="S-002",
        name="Emergency Stop Activated",
        description="Hardware E-stop push button depressed on operator console.",
        root_causes=[
            "Operator initiated emergency stop",
            "Broken wire in normally-closed E-stop loop",
            "Contact block detached from push button operator head",
        ],
        diagnostic_steps=[
            "Verify all E-stop buttons around perimeter are released and reset",
            "Measure 24VDC loop integrity back to safety controller",
            "Inspect rear contact block latch mechanism",
        ],
        recommended_parts=["Emergency Stop Pushbutton Twist-to-Reset 40mm", "Dual NC Contact Block"],
        severity="CRITICAL",
        estimated_repair_hours=0.5,
        documentation_url="https://docs.enterprise.internal/diagnostics/S-002",
    ),
    "M-001": FaultCodeDiagnosis(
        fault_code="M-001",
        name="Memory Overflow in Controller",
        description="Industrial PC / PLC volatile heap utilization exceeded 95%.",
        root_causes=[
            "Local diagnostic log file rotation failure",
            "Recipe management system memory leak",
            "Circular buffer pointer corruption",
        ],
        diagnostic_steps=[
            "Inspect controller task monitor and memory heap allocation",
            "Purge archived local trace files from flash storage",
            "Update PLC firmware to latest stable patch release",
        ],
        recommended_parts=["Industrial CompactFlash / SD Card 16GB High-Endurance"],
        severity="WARNING",
        estimated_repair_hours=2.0,
        documentation_url="https://docs.enterprise.internal/diagnostics/M-001",
    ),
}

# ---------------------------------------------------------------------------
# Pre-seeded Relational Active Alerts
# ---------------------------------------------------------------------------

_MOCK_ALERTS: list[Alert] = [
    Alert(
        alert_id="ALT-001",
        machine_id=MachineId("MCH-X9Y8-Z7W6"),
        severity="CRITICAL",
        metric_name="vibration",
        threshold_value=7.1,
        actual_value=8.3,
        triggered_at=datetime(2026, 11, 17, 10, 30, 0, tzinfo=timezone.utc),
        description="Vibration velocity exceeds ISO 10816 CRITICAL limit (8.3 mm/s > 7.1 mm/s) on servo spindle.",
        remediation="Immediate inspection required. Run fault code E-003 and E-002 diagnostics. Halt machine if vibration persists.",
        is_acknowledged=False,
    ),
    Alert(
        alert_id="ALT-002",
        machine_id=MachineId("MCH-A1B2-C3D4"),
        severity="WARNING",
        metric_name="temperature",
        threshold_value=80.0,
        actual_value=83.5,
        triggered_at=datetime(2026, 11, 17, 11, 15, 0, tzinfo=timezone.utc),
        description="Main motor stator temperature elevated (83.5°C vs 80.0°C warning threshold).",
        remediation="Check cooling fan airflow and air intake filters. Inspect with thermal camera.",
        is_acknowledged=True,
    ),
    Alert(
        alert_id="ALT-003",
        machine_id=MachineId("MCH-P1Q2-R3S4"),
        severity="WARNING",
        metric_name="pressure",
        threshold_value=7.5,
        actual_value=7.8,
        triggered_at=datetime(2026, 11, 17, 9, 45, 0, tzinfo=timezone.utc),
        description="Hydraulic system pressure nearing relief pressure threshold (7.8 bar vs 7.5 bar).",
        remediation="Check hydraulic fluid viscosity and proportional valve calibration.",
        is_acknowledged=False,
    ),
    Alert(
        alert_id="ALT-004",
        machine_id=MachineId("MCH-B2C3-D4E5"),
        severity="INFO",
        metric_name="rpm",
        threshold_value=3200.0,
        actual_value=3350.0,
        triggered_at=datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc),
        description="Spindle rotational speed reached upper operational band during high-throughput cycle.",
        remediation="No immediate action required. Verify cutting tool wear index on next shift change.",
        is_acknowledged=True,
    ),
    Alert(
        alert_id="ALT-005",
        machine_id=MachineId("MCH-F6G7-H8I9"),
        severity="INFO",
        metric_name="humidity",
        threshold_value=70.0,
        actual_value=72.0,
        triggered_at=datetime(2026, 11, 17, 7, 30, 0, tzinfo=timezone.utc),
        description="Cabinet interior relative humidity slightly elevated (72% vs 70% threshold).",
        remediation="Inspect cabinet door gasket seal and check desiccant cartridge indicator.",
        is_acknowledged=False,
    ),
]


class MockTelemetryAdapter(TelemetryRepositoryPort):
    """High-fidelity synthetic Telemetry adapter generating deterministic relational metrics."""

    def __init__(self) -> None:
        self._alerts = list(_MOCK_ALERTS)

    def _hash_machine(self, machine_id: MachineId) -> int:
        """Deterministic integer hash based on machine_id string."""
        return sum(ord(c) * (i + 1) for i, c in enumerate(str(machine_id)))

    async def get_latest_metrics(
        self,
        machine_id: MachineId,
        metric_names: Optional[list[str]],
    ) -> LatestMetrics:
        h = self._hash_machine(machine_id)
        now = datetime.now(timezone.utc)

        # MCH-X9Y8-Z7W6 has critical vibration for relational fidelity
        is_critical_machine = (str(machine_id) == "MCH-X9Y8-Z7W6")
        vibration_val = 8.3 if is_critical_machine else round(0.5 + (h % 35) * 0.1, 2)

        all_metrics: dict[str, MetricPoint] = {
            "vibration": MetricPoint(
                metric_name="vibration",
                value=vibration_val,
                unit="mm/s",
                timestamp=now,
                quality="UNCERTAIN" if is_critical_machine else "GOOD",
            ),
            "temperature": MetricPoint(
                metric_name="temperature",
                value=round(45.0 + (h % 38) * 1.0, 1),
                unit="°C",
                timestamp=now,
                quality="GOOD",
            ),
            "rpm": MetricPoint(
                metric_name="rpm",
                value=float(1200 + (h % 2200)),
                unit="RPM",
                timestamp=now,
                quality="GOOD",
            ),
            "pressure": MetricPoint(
                metric_name="pressure",
                value=round(2.0 + (h % 55) * 0.1, 2),
                unit="bar",
                timestamp=now,
                quality="GOOD",
            ),
            "power_kw": MetricPoint(
                metric_name="power_kw",
                value=round(15.0 + (h % 120) * 1.0, 1),
                unit="kW",
                timestamp=now,
                quality="GOOD",
            ),
            "humidity": MetricPoint(
                metric_name="humidity",
                value=round(30.0 + (h % 45) * 1.0, 1),
                unit="%",
                timestamp=now,
                quality="GOOD",
            ),
            "current_amp": MetricPoint(
                metric_name="current_amp",
                value=round(25.0 + (h % 150) * 1.0, 1),
                unit="A",
                timestamp=now,
                quality="GOOD",
            ),
            "voltage": MetricPoint(
                metric_name="voltage",
                value=round(395.0 + (h % 25) * 1.0, 1),
                unit="V",
                timestamp=now,
                quality="GOOD",
            ),
        }

        if metric_names:
            selected = [all_metrics[m] for m in metric_names if m in all_metrics]
        else:
            selected = list(all_metrics.values())

        return LatestMetrics(
            machine_id=machine_id,
            metrics=selected,
            snapshot_time=now,
            source="mock-telemetry-engine",
        )

    async def query_range(
        self,
        machine_id: MachineId,
        start_time: datetime,
        end_time: datetime,
        resolution: str,
        aggregation: str,
    ) -> list[TimeSeriesResult]:
        h = self._hash_machine(machine_id)
        # Resolution step in seconds
        step_map = {
            "1m": 60,
            "5m": 300,
            "1h": 3600,
            "6h": 21600,
            "1d": 86400,
        }
        step_seconds = step_map.get(resolution.lower(), 300)

        total_duration = (end_time - start_time).total_seconds()
        # Cap data points to at most 100 per metric for performance
        num_points = min(100, max(2, int(total_duration // step_seconds)))
        actual_interval = total_duration / num_points

        metrics_to_query = ["temperature", "vibration", "rpm", "pressure"]
        results: list[TimeSeriesResult] = []

        for metric in metrics_to_query:
            base = 50.0 if metric == "temperature" else (2.0 if metric == "vibration" else (1800.0 if metric == "rpm" else 5.0))
            if str(machine_id) == "MCH-X9Y8-Z7W6" and metric == "vibration":
                base = 8.0

            data_points: list[AggregatedDataPoint] = []
            for i in range(num_points):
                pt_time = start_time + timedelta(seconds=i * actual_interval)
                # Deterministic sinusoidal oscillation
                wave = math.sin((i / 5.0) + (h % 10)) * (base * 0.1)
                val = round(base + wave, 2)
                if aggregation == "MAX":
                    val = round(val * 1.05, 2)
                elif aggregation == "MIN":
                    val = round(val * 0.95, 2)

                data_points.append(
                    AggregatedDataPoint(
                        timestamp=pt_time,
                        value=val,
                        metric_name=metric,
                        aggregation=aggregation,
                    )
                )

            results.append(
                TimeSeriesResult(
                    machine_id=machine_id,
                    metric_name=metric,
                    start_time=start_time,
                    end_time=end_time,
                    resolution=resolution,
                    aggregation=aggregation,
                    data_points=data_points,
                    total_points=len(data_points),
                )
            )

        return results

    async def get_active_alerts(
        self,
        machine_id: Optional[MachineId],
        severity: Optional[str],
    ) -> list[Alert]:
        alerts = list(self._alerts)
        if machine_id:
            alerts = [a for a in alerts if str(a.machine_id) == str(machine_id)]
        if severity:
            alerts = [a for a in alerts if a.severity.upper() == severity.upper()]
        return alerts

    async def diagnose_fault_code(self, fault_code: str) -> FaultCodeDiagnosis:
        code_upper = fault_code.upper()
        if code_upper not in FAULT_CODE_DB:
            raise EntityNotFoundError(f"Fault code {fault_code!r} not found in enterprise diagnostic dictionary")
        return FAULT_CODE_DB[code_upper]
