from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SampleQuestion:
    category: str
    question: str
    target_tool: str
    server_id: str
    description: str
    expected_args: dict[str, Any]


SAMPLE_QUESTIONS: list[SampleQuestion] = [
    # Customer Domain
    SampleQuestion(
        category="Customer",
        question="Show me the full profile and contact for customer CUST-001001",
        target_tool="customer_get_profile",
        server_id="customer",
        description="Retrieves full 360-degree customer profile, company metadata, and account reps.",
        expected_args={"customer_id": "CUST-001001"},
    ),
    SampleQuestion(
        category="Customer",
        question="Search for customers in the MINING industry in NA-WEST region",
        target_tool="customer_search",
        server_id="customer",
        description="Filters customer directory by industry, query keyword, or region.",
        expected_args={"query": "Mining", "region": "NA-WEST", "limit": 5},
    ),
    SampleQuestion(
        category="Customer",
        question="What service entitlements and SLA tier does CUST-001001 have?",
        target_tool="customer_get_entitlements",
        server_id="customer",
        description="Checks SLA tier, active support hours, and enabled feature entitlements.",
        expected_args={"customer_id": "CUST-001001"},
    ),
    SampleQuestion(
        category="Customer",
        question="Which machines are currently assigned to customer CUST-001001?",
        target_tool="customer_get_assigned_machines",
        server_id="customer",
        description="Lists all industrial machines associated with this customer contract.",
        expected_args={"customer_id": "CUST-001001"},
    ),

    # Machine Domain
    SampleQuestion(
        category="Machine",
        question="Get hardware specifications and GPS location for machine MCH-A1B2-C3D4",
        target_tool="machine_get_details",
        server_id="machine",
        description="Retrieves machine model, CPU, RAM, firmware version, and location.",
        expected_args={"machine_id": "MCH-A1B2-C3D4"},
    ),
    SampleQuestion(
        category="Machine",
        question="List all machines currently in MAINTENANCE state",
        target_tool="machine_list_fleet",
        server_id="machine",
        description="Queries fleet registry filtered by operational status.",
        expected_args={"status": "MAINTENANCE", "page": 1, "page_size": 10},
    ),
    SampleQuestion(
        category="Machine",
        question="Show the maintenance and repair history for machine MCH-A1B2-C3D4",
        target_tool="machine_get_maintenance_history",
        server_id="machine",
        description="Returns logged technician work orders, replaced parts, and costs.",
        expected_args={"machine_id": "MCH-A1B2-C3D4", "limit": 5},
    ),
    SampleQuestion(
        category="Machine",
        question="Update machine MCH-A1B2-C3D4 state to MAINTENANCE for scheduled bearing replacement",
        target_tool="machine_update_operational_state",
        server_id="machine",
        description="Executes audited operational state change with transition validation.",
        expected_args={"machine_id": "MCH-A1B2-C3D4", "new_state": "MAINTENANCE", "reason": "Scheduled bearing replacement"},
    ),

    # Telemetry Domain
    SampleQuestion(
        category="Telemetry",
        question="What are the latest sensor readings for machine MCH-A1B2-C3D4?",
        target_tool="telemetry_get_latest_metrics",
        server_id="telemetry",
        description="Fetches live telemetry sensor snapshot including temperature and vibration.",
        expected_args={"machine_id": "MCH-A1B2-C3D4"},
    ),
    SampleQuestion(
        category="Telemetry",
        question="Show hourly average telemetry time-series for machine MCH-A1B2-C3D4",
        target_tool="telemetry_query_range",
        server_id="telemetry",
        description="Aggregates sensor metrics across a time window with resolution buckets.",
        expected_args={
            "machine_id": "MCH-A1B2-C3D4",
            "start_time": "2026-11-17T08:00:00Z",
            "end_time": "2026-11-17T12:00:00Z",
            "resolution": "5m",
            "aggregation": "AVG",
        },
    ),
    SampleQuestion(
        category="Telemetry",
        question="Are there any CRITICAL active telemetry alerts across the fleet?",
        target_tool="telemetry_get_active_alerts",
        server_id="telemetry",
        description="Queries real-time threshold alert breaches by severity.",
        expected_args={"severity": "CRITICAL"},
    ),
    SampleQuestion(
        category="Telemetry",
        question="Diagnose fault code E-001 and recommend repair steps",
        target_tool="telemetry_diagnose_fault_code",
        server_id="telemetry",
        description="Looks up diagnostic tree, root causes, repair hours, and parts for a fault.",
        expected_args={"fault_code": "E-001"},
    ),
]
