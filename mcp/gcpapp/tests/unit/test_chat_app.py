from __future__ import annotations
import pytest
from starlette.testclient import TestClient

from chat_app.agent.router import McpIntentRouter
from chat_app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_intent_router_customer_profile():
    router = McpIntentRouter()
    intent = router.route("Show me the profile for customer CUST-001001")
    assert intent.server_id == "customer"
    assert intent.tool_name == "customer_get_profile"
    assert intent.arguments["customer_id"] == "CUST-001001"


def test_intent_router_customer_search():
    router = McpIntentRouter()
    intent = router.route("Find mining customers in NA-WEST region")
    assert intent.server_id == "customer"
    assert intent.tool_name == "customer_search"
    assert intent.arguments["query"] == "Mining"
    assert intent.arguments["region"] == "NA-WEST"


def test_intent_router_machine_details():
    router = McpIntentRouter()
    intent = router.route("Get specs and hardware for machine MCH-A1B2-C3D4")
    assert intent.server_id == "machine"
    assert intent.tool_name == "machine_get_details"
    assert intent.arguments["machine_id"] == "MCH-A1B2-C3D4"


def test_intent_router_machine_state_update():
    router = McpIntentRouter()
    intent = router.route("Update machine MCH-A1B2-C3D4 state to MAINTENANCE for urgent overhaul")
    assert intent.server_id == "machine"
    assert intent.tool_name == "machine_update_operational_state"
    assert intent.arguments["machine_id"] == "MCH-A1B2-C3D4"
    assert intent.arguments["new_state"] == "MAINTENANCE"
    assert "overhaul" in intent.arguments["reason"]


def test_intent_router_telemetry_live():
    router = McpIntentRouter()
    intent = router.route("What are the latest sensor readings for machine MCH-A1B2-C3D4?")
    assert intent.server_id == "telemetry"
    assert intent.tool_name == "telemetry_get_latest_metrics"
    assert intent.arguments["machine_id"] == "MCH-A1B2-C3D4"


def test_intent_router_telemetry_diagnose():
    router = McpIntentRouter()
    intent = router.route("Diagnose fault code E-001 and recommend fixes")
    assert intent.server_id == "telemetry"
    assert intent.tool_name == "telemetry_diagnose_fault_code"
    assert intent.arguments["fault_code"] == "E-001"


def test_chat_api_health(client: TestClient):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_chat_api_sample_questions(client: TestClient):
    res = client.get("/api/sample-questions")
    assert res.status_code == 200
    questions = res.json()
    assert len(questions) >= 12
    categories = {q["category"] for q in questions}
    assert {"Customer", "Machine", "Telemetry"} <= categories


def test_chat_api_process_chat_mock_mode(client: TestClient):
    res = client.post(
        "/api/chat",
        json={
            "message": "Show me the profile for customer CUST-001001",
            "mode": "MOCK",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["tool_used"] == "customer_get_profile"
    assert data["server_id"] == "customer"
    assert "Nexus Industrial Corp" in data["reply"]
    assert data["mode"] == "MOCK"


def test_chat_api_process_chat_sdk_mode(client: TestClient):
    # In SDK mode without external credentials, it cleanly reports Authentication failure
    res = client.post(
        "/api/chat",
        json={
            "message": "Show me the profile for customer CUST-001001",
            "mode": "SDK",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "SDK"
    assert data["tool_used"] == "customer_get_profile"
    assert "Authentication" in data["reply"]
