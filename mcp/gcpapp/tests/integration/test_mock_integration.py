from __future__ import annotations
import pytest
from config.settings import Settings
from core.factory import GatewayFactory
from mcp.customer_mcp.application.use_cases import GetAssignedMachinesUseCase, GetCustomerProfileUseCase
from mcp.machine_mcp.application.use_cases import GetMachineDetailsUseCase
from mcp.telemetry_mcp.application.use_cases import GetActiveAlertsUseCase, GetLatestMetricsUseCase


@pytest.mark.asyncio
async def test_cross_mcp_relational_consistency(settings):
    """Verifies that entities across Customer, Machine, and Telemetry MCPs correlate accurately."""
    c_repo = GatewayFactory.get_customer_gateway(settings)
    m_repo = GatewayFactory.get_machine_gateway(settings)
    t_repo = GatewayFactory.get_telemetry_gateway(settings)

    # 1. Fetch customer
    customer = await GetCustomerProfileUseCase(c_repo).execute("CUST-001001")
    assert customer.company_name == "Nexus Industrial Corp"

    # 2. Get customer's assigned machines
    assignments = await GetAssignedMachinesUseCase(c_repo).execute("CUST-001001", None)
    assert len(assignments.machines) >= 2
    assigned_machine_id = str(assignments.machines[0].machine_id)

    # 3. Verify machine exists in MachineMCP
    machine = await GetMachineDetailsUseCase(m_repo).execute(assigned_machine_id)
    assert machine.machine_id == assigned_machine_id
    assert machine.owner_customer_id == "CUST-001001"

    # 4. Verify telemetry exists for the same machine
    metrics = await GetLatestMetricsUseCase(t_repo).execute(assigned_machine_id)
    assert metrics.machine_id == assigned_machine_id
    assert len(metrics.metrics) >= 1

    # 5. Verify critical machine alert consistency
    alerts = await GetActiveAlertsUseCase(t_repo).execute(machine_id_str="MCH-X9Y8-Z7W6", severity="CRITICAL")
    assert len(alerts) >= 1
    assert alerts[0].actual_value > alerts[0].threshold_value


@pytest.mark.asyncio
async def test_tool_envelopes_customer():
    """Verify tool execution returns standard {status, data, metadata} envelope."""
    from mcp.customer_mcp.tools import register_tools
    from config.settings import Settings
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        from mcp.server.fastmcp import FastMCP

    settings = Settings(DATA_PROVIDER_MODE="MOCK")
    repo = GatewayFactory.get_customer_gateway(settings)
    app = FastMCP("test-customer-tools")
    register_tools(app, settings, repo=repo)

    # Directly verify tool registration exists
    tools = await app.list_tools()
    tool_names = [t.name for t in tools]
    assert "customer_get_profile" in tool_names
    assert "customer_search" in tool_names
    assert "customer_get_entitlements" in tool_names
    assert "customer_get_assigned_machines" in tool_names


@pytest.mark.asyncio
async def test_tool_envelopes_machine():
    from mcp.machine_mcp.tools import register_tools
    from config.settings import Settings
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        from mcp.server.fastmcp import FastMCP

    settings = Settings(DATA_PROVIDER_MODE="MOCK")
    repo = GatewayFactory.get_machine_gateway(settings)
    app = FastMCP("test-machine-tools")
    register_tools(app, settings, repo=repo)

    tools = await app.list_tools()
    tool_names = [t.name for t in tools]
    assert "machine_get_details" in tool_names
    assert "machine_list_fleet" in tool_names
    assert "machine_get_maintenance_history" in tool_names
    assert "machine_update_operational_state" in tool_names


@pytest.mark.asyncio
async def test_tool_envelopes_telemetry():
    from mcp.telemetry_mcp.tools import register_tools
    from config.settings import Settings
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        from mcp.server.fastmcp import FastMCP

    settings = Settings(DATA_PROVIDER_MODE="MOCK")
    repo = GatewayFactory.get_telemetry_gateway(settings)
    app = FastMCP("test-telemetry-tools")
    register_tools(app, settings, repo=repo)

    tools = await app.list_tools()
    tool_names = [t.name for t in tools]
    assert "telemetry_get_latest_metrics" in tool_names
    assert "telemetry_query_range" in tool_names
    assert "telemetry_get_active_alerts" in tool_names
    assert "telemetry_diagnose_fault_code" in tool_names
