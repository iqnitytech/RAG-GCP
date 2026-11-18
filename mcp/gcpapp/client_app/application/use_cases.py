from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any
from client_app.application.ports import McpClientGatewayPort
from client_app.domain.entities import (
    ScenarioResult,
    ScenarioStep,
    ServerDescriptor,
    ToolDescriptor,
    ToolInvocationRequest,
    ToolInvocationResult,
)
from client_app.domain.exceptions import ToolNotFoundError

PREDEFINED_SCENARIOS: dict[str, dict[str, Any]] = {
    "customer_360": {
        "scenario_id": "customer_360",
        "title": "Customer 360 & Fleet Audit",
        "description": "Fetches enterprise customer profile, queries registered machine fleet, and inspects active platinum SLAs.",
        "steps": [
            {
                "step_id": 1,
                "name": "Fetch Customer Profile",
                "server_id": "customer",
                "tool_name": "customer_get_profile",
                "arguments": {"customer_id": "CUST-001001"},
            },
            {
                "step_id": 2,
                "name": "Get Assigned Fleet Equipment",
                "server_id": "customer",
                "tool_name": "customer_get_assigned_machines",
                "arguments": {"customer_id": "CUST-001001", "site_id": "SITE-A1B2"},
            },
            {
                "step_id": 3,
                "name": "Audit Contract Entitlements & SLAs",
                "server_id": "customer",
                "tool_name": "customer_get_entitlements",
                "arguments": {"customer_id": "CUST-001001"},
            },
        ],
    },
    "machine_lifecycle": {
        "scenario_id": "machine_lifecycle",
        "title": "Machine Fleet & Maintenance Lifecycle",
        "description": "Lists deployment fleet, retrieves detailed hardware specs, checks maintenance logs, and logs an operational state change.",
        "steps": [
            {
                "step_id": 1,
                "name": "Query Active Equipment Fleet",
                "server_id": "machine",
                "tool_name": "machine_list_fleet",
                "arguments": {"status": "ACTIVE", "page": 1, "page_size": 5},
            },
            {
                "step_id": 2,
                "name": "Inspect Machine Hardware Specs",
                "server_id": "machine",
                "tool_name": "machine_get_details",
                "arguments": {"machine_id": "MCH-A1B2-C3D4"},
            },
            {
                "step_id": 3,
                "name": "Retrieve Service & Cost History",
                "server_id": "machine",
                "tool_name": "machine_get_maintenance_history",
                "arguments": {"machine_id": "MCH-A1B2-C3D4", "limit": 5},
            },
            {
                "step_id": 4,
                "name": "Trigger Operational Maintenance State",
                "server_id": "machine",
                "tool_name": "machine_update_operational_state",
                "arguments": {
                    "machine_id": "MCH-A1B2-C3D4",
                    "target_state": "MAINTENANCE",
                    "reason": "Diagnostics routine overhaul",
                },
            },
        ],
    },
    "telemetry_anomaly": {
        "scenario_id": "telemetry_anomaly",
        "title": "Telemetry Anomaly & Root Cause Diagnosis",
        "description": "Checks active plant alarms, reads live high-frequency sensor readings, and executes a fault-tree diagnosis.",
        "steps": [
            {
                "step_id": 1,
                "name": "Check Active Critical Alarms",
                "server_id": "telemetry",
                "tool_name": "telemetry_get_active_alerts",
                "arguments": {"severity": "CRITICAL"},
            },
            {
                "step_id": 2,
                "name": "Snapshot Live Operational Sensors",
                "server_id": "telemetry",
                "tool_name": "telemetry_get_latest_metrics",
                "arguments": {"machine_id": "MCH-X9Y8-Z7W6", "metric_names": "vibration,temperature,rpm"},
            },
            {
                "step_id": 3,
                "name": "Root-Cause Fault Code Diagnosis (E-001)",
                "server_id": "telemetry",
                "tool_name": "telemetry_diagnose_fault_code",
                "arguments": {"fault_code": "E-001"},
            },
        ],
    },
}


class GetServersAndToolsUseCase:
    def __init__(self, gateway: McpClientGatewayPort) -> None:
        self._gateway = gateway

    async def execute(self) -> list[ServerDescriptor]:
        return await self._gateway.list_servers()


class InvokeToolUseCase:
    def __init__(self, gateway: McpClientGatewayPort) -> None:
        self._gateway = gateway

    async def execute(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        return await self._gateway.invoke_tool(request)


class ExecuteScenarioUseCase:
    def __init__(self, gateway: McpClientGatewayPort) -> None:
        self._gateway = gateway

    async def execute(self, scenario_id: str, mode: str = "MOCK") -> ScenarioResult:
        if scenario_id not in PREDEFINED_SCENARIOS:
            raise ToolNotFoundError(f"Scenario '{scenario_id}' not found. Available: {list(PREDEFINED_SCENARIOS.keys())}")

        scenario_def = PREDEFINED_SCENARIOS[scenario_id]
        steps = scenario_def["steps"]
        step_results: list[ToolInvocationResult] = []

        start_time = time.perf_counter()
        passed_count = 0

        for step in steps:
            req = ToolInvocationRequest(
                server_id=step["server_id"],
                tool_name=step["tool_name"],
                arguments=step["arguments"],
                mode=mode,
            )
            res = await self._gateway.invoke_tool(req)
            step_results.append(res)
            if res.status == "success":
                passed_count += 1

        total_duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return ScenarioResult(
            scenario_id=scenario_id,
            title=scenario_def["title"],
            total_steps=len(steps),
            passed_steps=passed_count,
            duration_ms=total_duration_ms,
            step_results=step_results,
        )
