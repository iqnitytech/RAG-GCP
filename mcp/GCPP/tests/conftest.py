from __future__ import annotations
import os
import pytest

os.environ["DATA_PROVIDER_MODE"] = "MOCK"

from config.settings import Settings
from core.factory import GatewayFactory
from mcp.customer_mcp.application.ports import CustomerRepositoryPort
from mcp.machine_mcp.application.ports import MachineRepositoryPort
from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(DATA_PROVIDER_MODE="MOCK")


@pytest.fixture
def customer_repo(settings: Settings) -> CustomerRepositoryPort:
    return GatewayFactory.get_customer_gateway(settings)


@pytest.fixture
def machine_repo(settings: Settings) -> MachineRepositoryPort:
    return GatewayFactory.get_machine_gateway(settings)


@pytest.fixture
def telemetry_repo(settings: Settings) -> TelemetryRepositoryPort:
    return GatewayFactory.get_telemetry_gateway(settings)


@pytest.fixture
def client():
    from starlette.testclient import TestClient
    from client_app.main import app
    return TestClient(app)
