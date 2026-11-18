from __future__ import annotations
import pytest
from starlette.testclient import TestClient

from client_app.application.use_cases import (
    ExecuteScenarioUseCase,
    GetServersAndToolsUseCase,
    InvokeToolUseCase,
)
from client_app.domain.entities import ToolInvocationRequest
from client_app.infrastructure.mcp_gateway import LocalMcpClientGateway
from client_app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def gateway():
    return LocalMcpClientGateway()


@pytest.mark.asyncio
async def test_gateway_list_servers(gateway):
    servers = await GetServersAndToolsUseCase(gateway).execute()
    assert len(servers) == 3
    server_ids = {s.server_id for s in servers}
    assert server_ids == {"customer", "machine", "telemetry"}

    # Total tools across all three servers must be 12
    total_tools = sum(len(s.tools) for s in servers)
    assert total_tools == 12

    # Verify tool parameters are correctly extracted from MCP schema
    tools_by_name = {t.name: t for s in servers for t in s.tools}
    assert "customer_get_profile" in tools_by_name
    cust_tool = tools_by_name["customer_get_profile"]
    assert len(cust_tool.parameters) == 1
    assert cust_tool.parameters[0].name == "customer_id"
    assert cust_tool.parameters[0].required is True
    assert cust_tool.parameters[0].example == "CUST-001001"


@pytest.mark.asyncio
async def test_gateway_invoke_tool_success(gateway):
    use_case = InvokeToolUseCase(gateway)
    req = ToolInvocationRequest(
        server_id="customer",
        tool_name="customer_get_profile",
        arguments={"customer_id": "CUST-001001"},
        mode="MOCK",
    )
    res = await use_case.execute(req)
    assert res.status == "success"
    assert res.payload["data"]["customer_id"] == "CUST-001001"
    assert res.duration_ms > 0


@pytest.mark.asyncio
async def test_gateway_execute_scenario(gateway):
    use_case = ExecuteScenarioUseCase(gateway)
    res = await use_case.execute("customer_360", mode="MOCK")
    assert res.passed_steps == res.total_steps
    assert res.total_steps == 3
    assert len(res.step_results) == 3


def test_api_list_servers(client):
    response = client.get("/api/servers")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


def test_api_invoke_tool(client):
    payload = {
        "server_id": "machine",
        "tool_name": "machine_get_details",
        "arguments": {"machine_id": "MCH-A1B2-C3D4"},
        "mode": "MOCK",
    }
    response = client.post("/api/invoke", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["status"] == "success"
    assert res["payload"]["data"]["machine_id"] == "MCH-A1B2-C3D4"


def test_api_scenarios(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3


def test_api_run_scenario(client):
    response = client.post("/api/scenarios/telemetry_anomaly/run", json={"mode": "MOCK"})
    assert response.status_code == 200
    res = response.json()
    assert res["passed_steps"] == res["total_steps"]
    assert res["total_steps"] == 3
