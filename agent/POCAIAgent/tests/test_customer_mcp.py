from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

from domain.errors import AuthenticationError, AuthorizationError, DomainError, NotFoundError, UpstreamError
from domain.ports import UserContext
from infrastructure.auth import user_context
from infrastructure.mcp.clients import InProcessMcpClient
from mcp_servers.common import bind_user, create_http_app
from mcp_servers.customer.adapters.factory import CustomerDataProviderFactory
from mcp_servers.customer.adapters.gateway import SdkDataGateway
from mcp_servers.customer.adapters.mock_adapter import MockAccountAdapter, MockCustomerAdapter
from mcp_servers.customer.adapters.sdk_adapter import SdkAccountAdapter, SdkCustomerAdapter
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort
from mcp_servers.customer.resilience import sanitize_error_message
from mcp_servers.customer.server import build_server
from mcp_servers.customer.tools import (
    get_account_handler,
    get_customer_by_number_handler,
    get_customer_by_phone_handler,
    get_customer_handler,
    search_customers_handler,
)
from settings import Settings

pytestmark = pytest.mark.anyio

CUSTOMER_READ_CTX = UserContext("test-user", "tenant-1", frozenset({"Customer.Read"}))
OTHER_ROLE_CTX = UserContext("test-user", "tenant-1", frozenset({"Machine.Read"}))


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ==============================================================================
# 1. Mode Resolution & Factory Tests
# ==============================================================================

def test_factory_resolves_mock_mode():
    s = Settings(data_provider_mode="mock")
    provider = CustomerDataProviderFactory.get_provider(s)
    assert isinstance(provider, MockCustomerAdapter)


def test_factory_resolves_sdk_modes(monkeypatch):
    for mode in ("sdk", "SDK", "live", "LIVE", "real", "REAL"):
        s = Settings(data_provider_mode=mode)
        provider = CustomerDataProviderFactory.get_provider(s)
        assert isinstance(provider, SdkCustomerAdapter)

    # Test via environment variable override
    monkeypatch.setenv("DATA_PROVIDER_MODE", "SDK")
    provider_env = CustomerDataProviderFactory.get_provider(Settings(data_provider_mode="mock"))
    assert isinstance(provider_env, SdkCustomerAdapter)


def test_factory_uses_injected_provider():
    custom = MagicMock(spec=CustomerRepositoryPort)
    assert CustomerDataProviderFactory.get_provider(repo=custom) is custom


def test_build_salesforce_sdk_mode():
    from infrastructure.salesforce.factory import build_salesforce
    from infrastructure.salesforce.sdk import SalesforceSdkClient

    s = Settings(
        sf_mode="sdk",
        sf_instance_url="https://instance.salesforce.com",
        sf_client_id="test_client_id",
        sf_client_secret="test_client_secret",
    )
    client = build_salesforce(s)
    assert isinstance(client, SalesforceSdkClient)


# ==============================================================================
# 2. DATA_PROVIDER_MODE=MOCK Tests
# ==============================================================================

async def test_mock_get_customer_by_id():
    adapter = MockCustomerAdapter()
    cust1 = await adapter.get_by_id("C-1")
    assert cust1 is not None
    assert cust1["id"] == "C-1"
    assert cust1["name"] == "Acme Corp"
    assert cust1["tier"] == "Gold"

    cust2 = await adapter.get_by_id("CUST-001001")
    assert cust2 is not None
    assert cust2["name"] == "Nexus Industrial Corp"

    # Non-existent customer
    assert await adapter.get_by_id("UNKNOWN-999") is None


async def test_mock_search_customers_by_name():
    adapter = MockCustomerAdapter()
    results = await adapter.search_by_name("Acme")
    assert len(results) >= 1
    assert any(c["id"] == "C-1" for c in results)

    # Case insensitive & partial match
    results_lower = await adapter.search_by_name("globex")
    assert len(results_lower) >= 1
    assert any(c["id"] == "C-2" for c in results_lower)

    # No match
    assert await adapter.search_by_name("NonExistentCompanyXYZ") == []


async def test_mock_get_customer_by_number():
    adapter = MockCustomerAdapter()
    # By account number
    c1 = await adapter.get_by_number("C-1")
    assert c1 is not None and c1["name"] == "Acme Corp"

    # By customer ID as number
    c2 = await adapter.get_by_number("CUST-001001")
    assert c2 is not None and c2["name"] == "Nexus Industrial Corp"

    assert await adapter.get_by_number("NON-EXISTENT") is None


async def test_mock_get_customer_by_phone():
    adapter = MockCustomerAdapter()
    c1 = await adapter.get_by_phone("+1-555-0101")
    assert c1 is not None and c1["id"] == "C-1"

    # Normalized digit matching
    c2 = await adapter.get_by_phone("16175550101")
    assert c2 is not None and c2["id"] == "CUST-001001"

    assert await adapter.get_by_phone("+1-000-0000") is None


# ==============================================================================
# 3. Tool Handlers & RBAC Validation Tests
# ==============================================================================

async def test_tool_enforces_role():
    adapter = MockCustomerAdapter()
    # Missing context
    with pytest.raises(AuthenticationError):
        await get_customer_handler(adapter, customer_id="C-1")

    # Unauthorized role
    with bind_user(OTHER_ROLE_CTX):
        with pytest.raises(AuthorizationError):
            await get_customer_handler(adapter, customer_id="C-1")

    # Authorized role
    with bind_user(CUSTOMER_READ_CTX):
        c = await get_customer_handler(adapter, customer_id="C-1")
        assert c["name"] == "Acme Corp"


async def test_tool_lookup_by_number_and_phone():
    adapter = MockCustomerAdapter()
    with bind_user(CUSTOMER_READ_CTX):
        # By number parameter
        c_num = await get_customer_handler(adapter, number="C-1")
        assert c_num["id"] == "C-1"

        # By dedicated number handler
        c_num_h = await get_customer_by_number_handler(adapter, number="CUST-001001")
        assert c_num_h["name"] == "Nexus Industrial Corp"

        # By phone parameter
        c_phone = await get_customer_handler(adapter, phone="+1-555-0101")
        assert c_phone["id"] == "C-1"

        # By dedicated phone handler
        c_phone_h = await get_customer_by_phone_handler(adapter, phone="+1-617-555-0101")
        assert c_phone_h["id"] == "CUST-001001"

        # Fallback resolution when customer_id is a phone or number
        c_fall = await get_customer_handler(adapter, customer_id="+1-555-0102")
        assert c_fall["id"] == "C-2"


async def test_tool_validation_and_not_found():
    adapter = MockCustomerAdapter()
    with bind_user(CUSTOMER_READ_CTX):
        # Empty input
        with pytest.raises(DomainError):
            await get_customer_handler(adapter, customer_id="", number="", phone="")

        # Length limits
        with pytest.raises(DomainError):
            await get_customer_handler(adapter, customer_id="A" * 101)

        with pytest.raises(DomainError):
            await search_customers_handler(adapter, name="A" * 101)

        with pytest.raises(DomainError):
            await get_customer_handler(adapter, phone="1" * 51)

        # Not found
        with pytest.raises(NotFoundError):
            await get_customer_handler(adapter, customer_id="NON-EXISTENT")

        with pytest.raises(NotFoundError):
            await get_customer_by_number_handler(adapter, number="NON-EXISTENT")

        with pytest.raises(NotFoundError):
            await get_customer_by_phone_handler(adapter, phone="+1-999-999-9999")


# ==============================================================================
# 4. SdkCustomerAdapter Tests (rag-platform-sdk integration)
# ==============================================================================

@pytest.fixture
def sdk_settings() -> Settings:
    return Settings(
        data_provider_mode="SDK",
        sdk_api_key="test-api-key",
        sdk_base_url="https://edh-test.internal",
        sdk_timeout_seconds=10.0,
    )


async def test_sdk_customer_adapter_edh_path(sdk_settings: Settings):
    mock_connector = MagicMock()
    customer_record = {
        "customer_id": "CUST-EDH-01",
        "company_name": "EDH Industrial Systems",
        "tier": "ENTERPRISE",
        "billing_status": "ACTIVE",
        "region": "NA-EAST",
        "industry": "AEROSPACE",
        "phone": "+1-555-4321",
        "account_representatives": [
            {"rep_id": "REP-1", "name": "Jane Doe", "phone": "+1-555-4321", "role": "TAM"}
        ],
    }
    mock_connector.read_records.return_value = [customer_record]
    adapter = SdkCustomerAdapter(sdk_settings, connector=mock_connector)

    # 1. Lookup by ID
    c_id = await adapter.get_by_id("CUST-EDH-01")
    assert c_id is not None
    assert c_id["id"] == "CUST-EDH-01"
    assert c_id["name"] == "EDH Industrial Systems"
    assert c_id["tier"] == "ENTERPRISE"
    assert c_id["phone"] == "+1-555-4321"

    # 2. Search by Name
    mock_connector.read_records.return_value = iter([customer_record])
    c_search = await adapter.search_by_name("Industrial")
    assert len(c_search) == 1
    assert c_search[0]["id"] == "CUST-EDH-01"

    # 3. Lookup by Number
    mock_connector.read_records.return_value = [customer_record]
    c_num = await adapter.get_by_number("CUST-EDH-01")
    assert c_num is not None
    assert c_num["id"] == "CUST-EDH-01"

    # 4. Lookup by Phone
    mock_connector.read_records.return_value = iter([customer_record])
    c_phone = await adapter.get_by_phone("+1-555-4321")
    assert c_phone is not None
    assert c_phone["id"] == "CUST-EDH-01"


async def test_sdk_customer_adapter_salesforce_path():
    sf_settings = Settings(
        data_provider_mode="SDK",
        sf_instance_url="https://test.salesforce.com",
        sf_access_token="test-access-token",
    )
    mock_sf_connector = MagicMock()
    sf_record = {
        "Id": "001xx000003DHPwAAO",
        "Name": "Apex Global SF",
        "AccountNumber": "ACC-777",
        "Tier__c": "PREMIUM",
        "Phone": "+1-415-555-7777",
        "BillingState": "CA",
    }
    # Configure context manager on sf_connector
    mock_sf = MagicMock()
    mock_sf_connector.__enter__.return_value = mock_sf
    mock_sf.getRecordById.return_value = sf_record
    mock_sf.query.return_value = [sf_record]

    adapter = SdkCustomerAdapter(sf_settings, sf_connector=mock_sf_connector)

    # 1. By ID
    res_id = await adapter.get_by_id("001xx000003DHPwAAO")
    assert res_id is not None
    assert res_id["name"] == "Apex Global SF"
    assert res_id["tier"] == "PREMIUM"

    # 2. By Number
    res_num = await adapter.get_by_number("ACC-777")
    assert res_num is not None
    assert res_num["number"] == "ACC-777"

    # 3. By Phone
    res_phone = await adapter.get_by_phone("+1-415-555-7777")
    assert res_phone is not None
    assert res_phone["phone"] == "+1-415-555-7777"

    # 4. Search by Name
    res_search = await adapter.search_by_name("Apex")
    assert len(res_search) == 1
    assert res_search[0]["id"] == "001xx000003DHPwAAO"


async def test_sdk_customer_adapter_error_translation(sdk_settings: Settings):
    from sdk.connectors import EdhAuthError, EdhNotFoundError, EdhRateLimitError

    mock_conn = MagicMock()
    adapter = SdkCustomerAdapter(sdk_settings, connector=mock_conn)

    # Auth error
    mock_conn.read_records.side_effect = EdhAuthError("Invalid API key")
    with pytest.raises(AuthenticationError):
        await adapter.get_by_id("C-1")

    # Rate limit error
    mock_conn.read_records.side_effect = EdhRateLimitError("Quota exceeded")
    with pytest.raises(UpstreamError):
        await adapter.get_by_id("C-1")

    # Generic SDK error
    mock_conn.read_records.side_effect = RuntimeError("Internal server explosion")
    with pytest.raises(UpstreamError):
        await adapter.get_by_id("C-1")


def test_error_sanitization_owasp_a05():
    # Domain validation messages are safe to return
    msg, err_type = sanitize_error_message(DomainError("Invalid ID format"), "corr-1")
    assert "Invalid ID format" in msg
    assert err_type == "DomainError"

    # Internal database or stacktrace details are sanitized
    internal_exc = RuntimeError("FATAL: password authentication failed for user 'postgres' at 10.0.0.1")
    msg_sanitized, err_type_sanitized = sanitize_error_message(internal_exc, "corr-secret-123")
    assert "password" not in msg_sanitized
    assert "postgres" not in msg_sanitized
    assert "10.0.0.1" not in msg_sanitized
    assert "corr-secret-123" in msg_sanitized
    assert err_type_sanitized == "UpstreamError"


# ==============================================================================
# 5. FastMCP End-to-End Server & InProcessMcpClient Tests
# ==============================================================================

async def test_fastmcp_inprocess_client_mock_mode():
    server = build_server(settings=Settings(data_provider_mode="mock"))
    client = InProcessMcpClient(server)

    # 1. Lookup by ID via FastMCP
    c1 = await client.call_tool("get_customer", {"customer_id": "C-1"}, CUSTOMER_READ_CTX)
    assert c1["name"] == "Acme Corp"
    assert c1["tier"] == "Gold"

    # 2. Search by Name via FastMCP
    search_res = await client.call_tool("search_customers", {"name": "Globex"}, CUSTOMER_READ_CTX)
    assert len(search_res) >= 1
    assert search_res[0]["id"] == "C-2"

    # 3. Lookup by Number via FastMCP get_customer
    c_num = await client.call_tool("get_customer", {"customer_id": "", "number": "C-2"}, CUSTOMER_READ_CTX)
    assert c_num["id"] == "C-2"

    # 4. Lookup by Phone via FastMCP get_customer
    c_phone = await client.call_tool("get_customer", {"customer_id": "", "phone": "+1-555-0101"}, CUSTOMER_READ_CTX)
    assert c_phone["id"] == "C-1"


async def test_fastmcp_inprocess_client_sdk_mode(sdk_settings: Settings):
    mock_connector = MagicMock()
    sdk_cust = {
        "customer_id": "CUST-9001",
        "company_name": "Cyberdyne Systems",
        "tier": "ENTERPRISE",
        "phone": "+1-408-555-9001",
    }
    mock_connector.read_records.return_value = [sdk_cust]
    sdk_adapter = SdkCustomerAdapter(sdk_settings, connector=mock_connector)

    server = build_server(repo=sdk_adapter, settings=sdk_settings)
    client = InProcessMcpClient(server)

    # Call get_customer on SDK-backed server
    res = await client.call_tool("get_customer", {"customer_id": "CUST-9001"}, CUSTOMER_READ_CTX)
    assert res["id"] == "CUST-9001"
    assert res["name"] == "Cyberdyne Systems"
    assert res["phone"] == "+1-408-555-9001"


def test_fastmcp_http_app_integration():
    settings = Settings(user_context_secret="test-secret", data_provider_mode="mock")
    app = create_http_app(build_server(settings=settings), settings)

    with TestClient(app) as c:
        # /health endpoint
        assert c.get("/health").status_code == 200

        # Unauthenticated request
        assert c.post("/mcp").status_code == 401

        # Valid signed context header
        signed = user_context.sign(CUSTOMER_READ_CTX, "test-secret")
        r = c.post(
            "/mcp",
            headers={
                user_context.HEADER: signed,
                "Accept": "application/json, text/event-stream",
                "Host": "localhost",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code not in (401, 403)


# ==============================================================================
# 6. Two-Tier Architecture (Gateway + Account Support) Tests
# ==============================================================================

async def test_sdk_data_gateway_direct():
    settings = Settings(
        data_provider_mode="SDK",
        sf_instance_url="https://test.salesforce.com",
        sf_access_token="test-token",
    )
    mock_sf_connector = MagicMock()
    mock_sf = MagicMock()
    mock_sf_connector.__enter__.return_value = mock_sf
    account_record = {"Id": "001999", "Name": "Wayne Enterprises", "AccountNumber": "ACC-999"}
    mock_sf.getRecordById.return_value = account_record
    mock_sf.query.return_value = [account_record]

    gateway = SdkDataGateway(settings, sf_connector=mock_sf_connector)

    # 1. get_record_by_id
    rec = gateway.get_record_by_id("Account", "001999")
    assert rec["Name"] == "Wayne Enterprises"
    mock_sf.getRecordById.assert_called_with("Account", "001999", fields=None)

    # 2. get_record_by_number
    rec_num = gateway.get_record_by_number("Account", "AccountNumber", "ACC-999")
    assert rec_num["Id"] == "001999"

    # 3. query_salesforce
    results = gateway.query_salesforce("Account", limit=5)
    assert len(results) == 1


async def test_sdk_account_adapter(sdk_settings: Settings):
    mock_sf_connector = MagicMock()
    mock_sf = MagicMock()
    mock_sf_connector.__enter__.return_value = mock_sf
    account_record = {
        "Id": "001xx000003DHPwAAO",
        "Name": "Wayne Enterprises",
        "AccountNumber": "ACC-100",
        "Type": "ENTERPRISE",
        "Phone": "+1-212-555-0199",
        "BillingState": "NY",
    }
    mock_sf.getRecordById.return_value = account_record
    mock_sf.query.return_value = [account_record]

    adapter = SdkAccountAdapter(sdk_settings, sf_connector=mock_sf_connector)
    assert isinstance(adapter, AccountRepositoryPort)

    # Lookup account
    acc = await adapter.get_by_id("001xx000003DHPwAAO")
    assert acc is not None
    assert acc["name"] == "Wayne Enterprises"
    assert acc["type"] == "Account"
    assert acc["tier"] == "ENTERPRISE"
    mock_sf.getRecordById.assert_called_with("account", "001xx000003DHPwAAO", fields=None)


async def test_get_account_tool_inprocess():
    server = build_server(settings=Settings(data_provider_mode="mock"))
    client = InProcessMcpClient(server)

    # 1. Calling dedicated get_account tool
    acc = await client.call_tool("get_account", {"account_id": "ACC-1"}, CUSTOMER_READ_CTX)
    assert acc["id"] == "ACC-1"
    assert acc["name"] == "Acme Corp"
    assert acc["type"] == "Account"

    # 2. get_account_handler directly with MockAccountAdapter
    adapter = MockAccountAdapter()
    with bind_user(CUSTOMER_READ_CTX):
        acc2 = await get_account_handler(adapter, account_id="ACC-1")
        assert acc2["id"] == "ACC-1"
        assert acc2["type"] == "Account"

