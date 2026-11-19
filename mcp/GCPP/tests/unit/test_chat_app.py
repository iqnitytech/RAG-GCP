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


def test_intent_router_customer_numeric_number():
    router = McpIntentRouter()
    queries = [
        "customer details for customer number 234",
        "customer details for customer no 234",
        "customer details for customer #234",
        "customer 234",
        "customer details for 234",
        "lookup customer 234",
    ]
    for q in queries:
        intent = router.route(q)
        assert intent.server_id == "customer", f"Failed server for: {q}"
        assert intent.tool_name == "customer_get_profile", f"Failed tool for: {q}"
        assert intent.arguments["customer_id"] == "234", f"Failed customer_id for: {q}"


def test_intent_router_customer_numeric_entitlements_and_machines():
    router = McpIntentRouter()
    intent_ent = router.route("What service entitlements does customer 234 have?")
    assert intent_ent.tool_name == "customer_get_entitlements"
    assert intent_ent.arguments["customer_id"] == "234"

    intent_mch = router.route("Which machines are assigned to customer 234?")
    assert intent_mch.tool_name == "customer_get_assigned_machines"
    assert intent_mch.arguments["customer_id"] == "234"


def test_intent_router_customer_search():
    router = McpIntentRouter()
    intent = router.route("Find mining customers in NA-WEST region")
    assert intent.server_id == "customer"
    assert intent.tool_name == "customer_search"
    assert intent.arguments["query"] == "Mining"
    assert intent.arguments["region"] == "NA-WEST"


def test_intent_router_customer_by_name():
    router = McpIntentRouter()
    queries = [
        "get customer details by name makbul",
        "get customer details by name: makbul",
        "customer details by name makbul",
        "find customer named makbul",
        'show me the customer details by name "Makbul"',
        "show me the customer details by name 'Makbul'",
        'customer details by name: "Makbul"',
        'find customer named "Makbul"',
        'customer details for "Makbul"',
    ]
    for q in queries:
        intent = router.route(q)
        assert intent.server_id == "customer", f"Failed server for: {q}"
        assert intent.tool_name == "customer_search", f"Failed tool for: {q}"
        assert intent.arguments["query"].lower() == "makbul", f"Failed query for: {q}"

    intent_region = router.route('search customer by name "Makbul" in NA-WEST')
    assert intent_region.server_id == "customer"
    assert intent_region.tool_name == "customer_search"
    assert intent_region.arguments["query"].lower() == "makbul"
    assert intent_region.arguments["region"] == "NA-WEST"


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

