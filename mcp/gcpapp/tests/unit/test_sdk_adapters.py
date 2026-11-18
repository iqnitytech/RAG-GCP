from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
import json
from unittest.mock import MagicMock
import pytest

from config.settings import Settings
from common.domain.enums import MachineStatus
from common.domain.value_objects import CustomerId, MachineId, SiteId
from core.exceptions import (
    AuthenticationError,
    EntityNotFoundError,
    RateLimitError,
)
from mcp.customer_mcp.infrastructure.sdk_adapter import SdkCustomerAdapter
from mcp.machine_mcp.infrastructure.sdk_adapter import SdkMachineAdapter
from mcp.telemetry_mcp.infrastructure.sdk_adapter import SdkTelemetryAdapter
from sdk.connectors import EdhNotFoundError


@pytest.fixture
def sdk_settings() -> Settings:
    return Settings(
        DATA_PROVIDER_MODE="SDK",
        SDK_API_KEY="valid-test-key",
        SDK_BASE_URL="https://edh-test.internal",
    )


# ===========================================================================
# SdkCustomerAdapter Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_sdk_customer_adapter_get_by_id_success(sdk_settings: Settings):
    adapter = SdkCustomerAdapter(sdk_settings)
    mock_connector = MagicMock()
    customer_data = {
        "customer_id": "CUST-0001",
        "company_name": "Acme Mining Corp",
        "tier": "ENTERPRISE",
        "billing_status": "ACTIVE",
        "region": "US-WEST",
        "industry": "MINING",
        "address": {
            "street": "100 Mining Way",
            "city": "Denver",
            "state": "CO",
            "country": "USA",
            "postal_code": "80202",
        },
        "account_representatives": [
            {
                "rep_id": "REP-01",
                "name": "Alice Smith",
                "email": "alice@acme.com",
                "phone": "+1-555-0100",
                "role": "Lead",
            }
        ],
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "employee_count": 500,
        "annual_revenue_usd": 10000000,
    }
    mock_connector.read_records.return_value = [customer_data]
    adapter._connector = mock_connector

    customer = await adapter.get_by_id(CustomerId("CUST-0001"))
    assert customer.customer_id == CustomerId("CUST-0001")
    assert customer.company_name == "Acme Mining Corp"
    assert customer.tier == "ENTERPRISE"
    assert customer.industry == "MINING"
    assert len(customer.account_representatives) == 1
    assert customer.account_representatives[0].name == "Alice Smith"


@pytest.mark.asyncio
async def test_sdk_customer_adapter_get_by_id_not_found(sdk_settings: Settings):
    adapter = SdkCustomerAdapter(sdk_settings)
    mock_connector = MagicMock()
    mock_connector.read_records.return_value = []
    adapter._connector = mock_connector

    with pytest.raises(EntityNotFoundError):
        await adapter.get_by_id(CustomerId("CUST-9999"))


@pytest.mark.asyncio
async def test_sdk_customer_adapter_search_with_filter_and_pagination(sdk_settings: Settings):
    adapter = SdkCustomerAdapter(sdk_settings)
    mock_connector = MagicMock()
    records = [
        {"customer_id": "CUST-0001", "company_name": "Alpha Mining", "tier": "ENTERPRISE", "industry": "MINING"},
        {"customer_id": "CUST-0002", "company_name": "Beta Mining", "tier": "STANDARD", "industry": "MINING"},
        {"customer_id": "CUST-0003", "company_name": "Gamma Logistics", "tier": "STARTER", "industry": "TRANSPORT"},
    ]
    mock_connector.read_records.return_value = iter(records)
    adapter._connector = mock_connector

    # Query "Mining" should match CUST-0001 and CUST-0002, offset 0, limit 1
    results = await adapter.search(query="Mining", region=None, tier=None, limit=1, offset=0)
    assert len(results) == 1
    assert results[0].customer_id == CustomerId("CUST-0001")

    # Offset 1 returns CUST-0002
    mock_connector.read_records.return_value = iter(records)
    results_page2 = await adapter.search(query="Mining", region=None, tier=None, limit=10, offset=1)
    assert len(results_page2) == 1
    assert results_page2[0].customer_id == CustomerId("CUST-0002")


@pytest.mark.asyncio
async def test_sdk_customer_adapter_get_entitlements(sdk_settings: Settings):
    adapter = Scp_adapter = SdkCustomerAdapter(sdk_settings)
    mock_connector = MagicMock()
    entitlements_data = [
        {
            "entitlement_id": "ENT-001",
            "service_name": "Telematics",
            "sla_level": "GOLD",
            "features": ["ADVANCED_ANALYTICS", "PREDICTIVE_MAINTENANCE"],
            "expiration_date": "2027-01-01",
            "is_active": True,
            "max_machines": 100,
        }
    ]
    mock_connector.read_records.return_value = entitlements_data
    adapter._connector = mock_connector

    ent = await adapter.get_entitlements(CustomerId("CUST-0001"))
    assert ent.customer_id == CustomerId("CUST-0001")
    assert len(ent.entitlements) == 1
    assert ent.entitlements[0].sla_level == "GOLD"
    assert "ADVANCED_ANALYTICS" in ent.entitlements[0].features


@pytest.mark.asyncio
async def test_sdk_customer_adapter_get_assigned_machines(sdk_settings: Settings):
    adapter = SdkCustomerAdapter(sdk_settings)
    mock_connector = MagicMock()
    records = [
        {
            "customer_id": "CUST-0001",
            "machine_id": "MCH-0001-0001",
            "model_type": "CAT-797F",
            "serial_number": "SN-001",
            "site_id": "SITE-A1B2",
            "status": "ACTIVE",
            "assigned_date": "2024-01-01",
        }
    ]
    mock_connector.read_records.return_value = iter(records)
    adapter._connector = mock_connector

    assignment = await adapter.get_assigned_machines(CustomerId("CUST-0001"))
    assert assignment.customer_id == CustomerId("CUST-0001")
    assert len(assignment.machines) == 1
    assert assignment.machines[0].machine_id == MachineId("MCH-0001-0001")
    assert assignment.machines[0].model_type == "CAT-797F"


# ===========================================================================
# SdkMachineAdapter Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_sdk_machine_adapter_get_by_id_success(sdk_settings: Settings):
    adapter = SdkMachineAdapter(sdk_settings)
    mock_connector = MagicMock()
    machine_data = {
        "machine_id": "MCH-0001-0001",
        "model_type": "CAT-797F",
        "model_series": "SERIES-F",
        "serial_number": "SN-0001",
        "owner_customer_id": "CUST-0001",
        "status": "ACTIVE",
        "firmware_version": "v2.1.0",
        "commissioning_date": "2024-01-01",
        "location": {"site_id": "SITE-A1B2", "building": "HQ", "floor": 1, "rack_position": "R-1", "gps_lat": 37.77, "gps_lon": -122.41},
        "hardware_spec": {"cpu_cores": 16, "ram_gb": 64, "storage_tb": 2.0, "gpu_model": "NVIDIA-T4", "network_bandwidth_gbps": 10.0},
        "last_heartbeat": "2026-11-17T00:00:00+00:00",
    }
    mock_connector.read_records.return_value = [machine_data]
    adapter._connector = mock_connector

    machine = await adapter.get_by_id(MachineId("MCH-0001-0001"))
    assert machine.machine_id == MachineId("MCH-0001-0001")
    assert machine.model_type == "CAT-797F"
    assert machine.status == "ACTIVE"


@pytest.mark.asyncio
async def test_sdk_machine_adapter_list_fleet_streaming_and_filtering(sdk_settings: Settings):
    adapter = SdkMachineAdapter(sdk_settings)
    mock_connector = MagicMock()
    records = [
        {"machine_id": "MCH-0001-0001", "model_type": "CAT-797F", "status": "ACTIVE"},
        {"machine_id": "MCH-0001-0002", "model_type": "KOM-930E", "status": "MAINTENANCE"},
        {"machine_id": "MCH-0001-0003", "model_type": "CAT-797F", "status": "ACTIVE"},
    ]
    mock_connector.read_records.return_value = iter(records)
    adapter._connector = mock_connector

    # Filter by model_type="CAT-797F"
    fleet = await adapter.list_fleet(model_type="CAT-797F", status=None, site_id=None, page=1, page_size=10)
    assert len(fleet) == 2
    assert {m.machine_id for m in fleet} == {MachineId("MCH-0001-0001"), MachineId("MCH-0001-0003")}


@pytest.mark.asyncio
async def test_sdk_machine_adapter_update_operational_state_preserves_previous_state(sdk_settings: Settings):
    adapter = SdkMachineAdapter(sdk_settings)
    mock_connector = MagicMock()
    existing_machine = {
        "machine_id": "MCH-0001-0001",
        "model_type": "CAT-797F",
        "status": "STANDBY",  # NOT ACTIVE!
    }
    mock_connector.read_records.return_value = [existing_machine]
    adapter._connector = mock_connector

    res = await adapter.update_operational_state(
        machine_id=MachineId("MCH-0001-0001"),
        target_state="ACTIVE",
        reason="Scheduled startup",
    )
    # Verifies RELI-02 fix: previous_state must be STANDBY (the actual state), not hardcoded ACTIVE
    assert res.previous_state == "STANDBY"
    assert res.new_state == "ACTIVE"
    assert mock_connector.put_bytes.called


# ===========================================================================
# SdkTelemetryAdapter Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_sdk_telemetry_adapter_get_latest_metrics(sdk_settings: Settings):
    adapter = SdkTelemetryAdapter(sdk_settings)
    mock_connector = MagicMock()
    now_iso = datetime.now(timezone.utc).isoformat()
    records = [
        {"machine_id": "MCH-0001-0001", "metric_name": "oil_pressure", "value": 45.2, "unit": "PSI", "timestamp": now_iso},
        {"machine_id": "MCH-0001-0001", "metric_name": "coolant_temp", "value": 85.0, "unit": "C", "timestamp": now_iso},
    ]
    mock_connector.read_records.return_value = iter(records)
    adapter._connector = mock_connector

    metrics = await adapter.get_latest_metrics(MachineId("MCH-0001-0001"), metric_names=None)
    assert metrics.machine_id == MachineId("MCH-0001-0001")
    names = [p.metric_name for p in metrics.metrics]
    assert "oil_pressure" in names
    assert "coolant_temp" in names


@pytest.mark.asyncio
async def test_sdk_telemetry_adapter_get_active_alerts(sdk_settings: Settings):
    adapter = SdkTelemetryAdapter(sdk_settings)
    mock_connector = MagicMock()
    records = [
        {
            "alert_id": "ALT-101",
            "machine_id": "MCH-0001-0001",
            "severity": "CRITICAL",
            "metric_name": "engine_temp",
            "threshold_value": 105.0,
            "actual_value": 112.5,
            "triggered_at": "2026-11-17T10:00:00+00:00",
            "description": "High temperature alert",
            "remediation": "Check coolant level",
            "is_acknowledged": False,
        }
    ]
    mock_connector.read_records.return_value = iter(records)
    adapter._connector = mock_connector

    alerts = await adapter.get_active_alerts(machine_id=MachineId("MCH-0001-0001"), severity="CRITICAL")
    assert len(alerts) == 1
    assert alerts[0].alert_id == "ALT-101"
    assert alerts[0].severity == "CRITICAL"
    assert alerts[0].actual_value == 112.5


@pytest.mark.asyncio
async def test_sdk_telemetry_adapter_diagnose_fault_code(sdk_settings: Settings):
    adapter = SdkTelemetryAdapter(sdk_settings)
    mock_connector = MagicMock()
    diagnosis_data = {
        "fault_code": "FC-ENG-001",
        "name": "Engine Overheat",
        "description": "Engine coolant temperature exceeding critical threshold",
        "root_causes": ["Low coolant level", "Blocked radiator", "Faulty water pump"],
        "diagnostic_steps": ["Inspect coolant level", "Check radiator fins"],
        "recommended_parts": ["WP-990 Water Pump", "RAD-200 Radiator"],
        "severity": "CRITICAL",
        "estimated_repair_hours": 2.5,
        "documentation_url": "https://manuals.internal/FC-ENG-001.pdf",
    }
    mock_connector.get_bytes.return_value = json.dumps(diagnosis_data).encode("utf-8")
    adapter._connector = mock_connector

    diag = await adapter.diagnose_fault_code("FC-ENG-001")
    assert diag.fault_code == "FC-ENG-001"
    assert diag.severity == "CRITICAL"
    assert diag.estimated_repair_hours == 2.5
    assert len(diag.root_causes) == 3
