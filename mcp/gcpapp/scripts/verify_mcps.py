#!/usr/bin/env python3
"""Multi-MCP Platform Integration & Smoke Verification Script.

Adheres to Fortune 10 engineering standards:
- Supports testing individual servers or all three (--server customer|machine|telemetry|all)
- Supports hot-swapping between MOCK and SDK modes (--mode MOCK|SDK)
- Invokes registered tools, validates payloads, and prints a formatted health matrix
"""

from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Ensure workspace root is in path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

RESULTS: list[dict[str, Any]] = []


def record_check(server: str, tool: str, success: bool, details: str = "") -> None:
    badge = "✅ PASS" if success else "❌ FAIL"
    print(f"  {badge:<10} | {server:<18} | {tool:<36} | {details}")
    RESULTS.append({
        "server": server,
        "tool": tool,
        "success": success,
        "details": details,
    })


async def verify_customer_mcp(settings: Any) -> None:
    print("\n[CustomerMCP] Verifying customer-mcp-server...")
    from core.factory import GatewayFactory
    from mcp.customer_mcp.application.use_cases import (
        GetAssignedMachinesUseCase,
        GetCustomerEntitlementsUseCase,
        GetCustomerProfileUseCase,
        SearchCustomersUseCase,
    )

    try:
        repo = GatewayFactory.get_customer_gateway(settings)
    except Exception as exc:
        record_check("CustomerMCP", "gateway_initialization", False, str(exc))
        return

    # 1. customer_get_profile
    try:
        profile = await GetCustomerProfileUseCase(repo).execute("CUST-001001")
        ok = profile.customer_id == "CUST-001001" and profile.tier == "ENTERPRISE"
        record_check("CustomerMCP", "customer_get_profile", ok, f"Company: {profile.company_name}")
    except Exception as exc:
        record_check("CustomerMCP", "customer_get_profile", False, str(exc))

    # 2. customer_search
    try:
        search_res = await SearchCustomersUseCase(repo).execute("Nexus", "NA-EAST", "ENTERPRISE", 10, 0)
        ok = len(search_res) >= 1 and search_res[0].customer_id == "CUST-001001"
        record_check("CustomerMCP", "customer_search", ok, f"Found {len(search_res)} matching records")
    except Exception as exc:
        record_check("CustomerMCP", "customer_search", False, str(exc))

    # 3. customer_get_entitlements
    try:
        ent = await GetCustomerEntitlementsUseCase(repo).execute("CUST-001001")
        ok = ent.total_active >= 1 and len(ent.entitlements) >= 1
        record_check("CustomerMCP", "customer_get_entitlements", ok, f"Active SLAs: {ent.total_active}")
    except Exception as exc:
        record_check("CustomerMCP", "customer_get_entitlements", False, str(exc))

    # 4. customer_get_assigned_machines
    try:
        assign = await GetAssignedMachinesUseCase(repo).execute("CUST-001001", "SITE-A1B2")
        m_ids = [str(m.machine_id) for m in assign.machines]
        ok = "MCH-A1B2-C3D4" in m_ids
        record_check("CustomerMCP", "customer_get_assigned_machines", ok, f"Machines: {', '.join(m_ids)}")
    except Exception as exc:
        record_check("CustomerMCP", "customer_get_assigned_machines", False, str(exc))


async def verify_machine_mcp(settings: Any) -> None:
    print("\n[MachineMCP] Verifying machine-mcp-server...")
    from core.factory import GatewayFactory
    from mcp.machine_mcp.application.use_cases import (
        GetMachineDetailsUseCase,
        GetMaintenanceHistoryUseCase,
        ListMachineFleetUseCase,
        UpdateOperationalStateUseCase,
    )

    try:
        repo = GatewayFactory.get_machine_gateway(settings)
    except Exception as exc:
        record_check("MachineMCP", "gateway_initialization", False, str(exc))
        return

    # 1. machine_get_details
    try:
        details = await GetMachineDetailsUseCase(repo).execute("MCH-A1B2-C3D4")
        ok = details.machine_id == "MCH-A1B2-C3D4" and details.status in ("ACTIVE", "MAINTENANCE")
        record_check("MachineMCP", "machine_get_details", ok, f"Model: {details.model_type}, Spec: {details.hardware_spec.cpu_cores}C")
    except Exception as exc:
        record_check("MachineMCP", "machine_get_details", False, str(exc))

    # 2. machine_list_fleet
    try:
        fleet = await ListMachineFleetUseCase(repo).execute(model_type=None, status="ACTIVE", site_id_str=None, page=1, page_size=10)
        ok = len(fleet) >= 1
        record_check("MachineMCP", "machine_list_fleet", ok, f"Fleet count: {len(fleet)} machines")
    except Exception as exc:
        record_check("MachineMCP", "machine_list_fleet", False, str(exc))

    # 3. machine_get_maintenance_history
    try:
        hist = await GetMaintenanceHistoryUseCase(repo).execute("MCH-A1B2-C3D4", 5)
        ok = hist.total_records >= 1 and hist.total_cost_usd > 0
        record_check("MachineMCP", "machine_get_maintenance_history", ok, f"Records: {hist.total_records}, Cost: ${hist.total_cost_usd:,.2f}")
    except Exception as exc:
        record_check("MachineMCP", "machine_get_maintenance_history", False, str(exc))

    # 4. machine_update_operational_state
    try:
        state_res = await UpdateOperationalStateUseCase(repo).execute(
            machine_id_str="MCH-A1B2-C3D4",
            target_state="MAINTENANCE",
            reason="Enterprise Scheduled Overhaul Q4",
        )
        ok = state_res.new_state == "MAINTENANCE" and bool(state_res.audit_id)
        record_check("MachineMCP", "machine_update_operational_state", ok, f"Audit ID: {state_res.audit_id[:8]}... (New state: {state_res.new_state})")
    except Exception as exc:
        record_check("MachineMCP", "machine_update_operational_state", False, str(exc))


async def verify_telemetry_mcp(settings: Any) -> None:
    print("\n[TelemetryMCP] Verifying telemetry-mcp-server...")
    from core.factory import GatewayFactory
    from mcp.telemetry_mcp.application.use_cases import (
        DiagnoseFaultCodeUseCase,
        GetActiveAlertsUseCase,
        GetLatestMetricsUseCase,
        QueryRangeUseCase,
    )

    try:
        repo = GatewayFactory.get_telemetry_gateway(settings)
    except Exception as exc:
        record_check("TelemetryMCP", "gateway_initialization", False, str(exc))
        return

    # 1. telemetry_get_latest_metrics
    try:
        metrics = await GetLatestMetricsUseCase(repo).execute("MCH-A1B2-C3D4", ["vibration", "temperature", "rpm"])
        m_map = {m.metric_name: m.value for m in metrics.metrics}
        ok = "temperature" in m_map and "vibration" in m_map
        record_check("TelemetryMCP", "telemetry_get_latest_metrics", ok, f"Sensors: {m_map}")
    except Exception as exc:
        record_check("TelemetryMCP", "telemetry_get_latest_metrics", False, str(exc))

    # 2. telemetry_query_range
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        series = await QueryRangeUseCase(repo).execute("MCH-A1B2-C3D4", start, end, "5m", "AVG")
        ok = len(series) >= 1 and series[0].total_points >= 2
        record_check("TelemetryMCP", "telemetry_query_range", ok, f"Series: {len(series)} metrics, {series[0].total_points} points each")
    except Exception as exc:
        record_check("TelemetryMCP", "telemetry_query_range", False, str(exc))

    # 3. telemetry_get_active_alerts
    try:
        alerts = await GetActiveAlertsUseCase(repo).execute(machine_id_str="MCH-X9Y8-Z7W6", severity="CRITICAL")
        ok = len(alerts) >= 1 and alerts[0].actual_value > alerts[0].threshold_value
        record_check("TelemetryMCP", "telemetry_get_active_alerts", ok, f"Critical alert: {alerts[0].description[:45]}...")
    except Exception as exc:
        record_check("TelemetryMCP", "telemetry_get_active_alerts", False, str(exc))

    # 4. telemetry_diagnose_fault_code
    try:
        diag = await DiagnoseFaultCodeUseCase(repo).execute("E-001")
        ok = diag.fault_code == "E-001" and len(diag.root_causes) >= 3 and len(diag.recommended_parts) >= 2
        record_check("TelemetryMCP", "telemetry_diagnose_fault_code", ok, f"Fault: {diag.name} (Repair: {diag.estimated_repair_hours}h)")
    except Exception as exc:
        record_check("TelemetryMCP", "telemetry_diagnose_fault_code", False, str(exc))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-MCP Platform Verification")
    parser.add_argument(
        "--mode",
        choices=["MOCK", "SDK"],
        default=os.getenv("DATA_PROVIDER_MODE", "MOCK").upper(),
        help="Data provider mode: MOCK (default) or SDK",
    )
    parser.add_argument(
        "--server",
        choices=["customer", "machine", "telemetry", "all"],
        default="all",
        help="Which MCP server to test (default: all)",
    )
    args = parser.parse_args()

    os.environ["DATA_PROVIDER_MODE"] = args.mode

    from config.settings import Settings
    from core.logging import configure_logging

    configure_logging(level="WARNING", format="TEXT")
    settings = Settings(DATA_PROVIDER_MODE=args.mode)

    print("=" * 95)
    print("  ENTERPRISE MULTI-MCP PLATFORM: INTEGRATION & HEALTH VERIFICATION")
    print(f"  Target Server: {args.server.upper():<10} | Mode: DATA_PROVIDER_MODE={args.mode}")
    print("=" * 95)
    print(f"  {'STATUS':<10} | {'SERVER':<18} | {'TOOL NAME':<36} | {'OUTPUT / EVIDENCE'}")
    print("-" * 95)

    if args.server in ("customer", "all"):
        await verify_customer_mcp(settings)
    if args.server in ("machine", "all"):
        await verify_machine_mcp(settings)
    if args.server in ("telemetry", "all"):
        await verify_telemetry_mcp(settings)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["success"])
    failed = total - passed

    print("\n" + "=" * 95)
    print(f"  EXECUTION SUMMARY: {passed}/{total} Checks Passed | {failed} Failed")
    if failed == 0 and total > 0:
        print(f"  RESULT: {args.server.upper()} MCP CAPABILITIES VERIFIED (100% HEALTH CHECK PASSED)")
    else:
        print(f"  RESULT: VERIFICATION COMPLETED WITH {failed} DEFECTS (Check SDK credentials if in SDK mode)")
    print("=" * 95)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
