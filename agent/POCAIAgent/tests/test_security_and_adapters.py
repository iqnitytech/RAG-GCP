import time

import pytest
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from domain.errors import AuthenticationError
from domain.ports import UserContext
from infrastructure.auth import user_context
from infrastructure.auth.validators import EntraTokenValidator
from infrastructure.mcp.clients import InProcessMcpClient
from infrastructure.salesforce.mock import MockSalesforce
from infrastructure.salesforce.repositories import SalesforceMachineRepository
from infrastructure.salesforce.sdk import SalesforceSdkClient, _ident
from mcp_servers.common import create_http_app
from mcp_servers.machine.server import build_server
from settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_user_context_roundtrip_and_tamper():
    ctx = UserContext("u", "t", frozenset({"A"}))
    assert user_context.verify(user_context.sign(ctx, "s"), "s") == ctx
    with pytest.raises(AuthenticationError):
        user_context.verify(user_context.sign(ctx, "s"), "other")
    with pytest.raises(AuthenticationError):
        user_context.verify(user_context.sign(ctx, "s", ttl_s=-1), "s")


class _Key:
    def __init__(self, key):
        self.key = key


class _Jwks:
    def __init__(self, public):
        self.public = public

    def get_signing_key_from_jwt(self, token):
        return _Key(self.public)


async def test_entra_validator():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = EntraTokenValidator("tid", "api://x", jwks_client=_Jwks(priv.public_key()))
    base = {"iss": "https://login.microsoftonline.com/tid/v2.0", "aud": "api://x", "oid": "o1", "tid": "tid",
            "roles": ["Machine.Read"], "exp": int(time.time()) + 60}
    ok = await v.validate(pyjwt.encode(base, priv, algorithm="RS256"))
    assert ok.user_id == "o1" and "Machine.Read" in ok.roles
    for bad in ({**base, "aud": "other"}, {**base, "exp": int(time.time()) - 5}, {**base, "tid": "evil"}):
        with pytest.raises(AuthenticationError):
            await v.validate(pyjwt.encode(bad, priv, algorithm="RS256"))


def test_soql_identifier_guard():
    with pytest.raises(ValueError):
        _ident("Id; DROP")


async def test_sdk_adapter_escapes_literals():
    captured = []

    class FakeSf:
        def query_all(self, soql):
            captured.append(soql)
            return {"records": [{"attributes": {}, "Id": "M-1"}]}

    rows = await SalesforceSdkClient(FakeSf()).query("Machine", ["Id"], {"Id": "x' OR Name != '"})
    assert rows == [{"Id": "M-1"}]
    assert "\\'" in captured[0] and "x' OR" not in captured[0]


async def test_mcp_tool_enforces_role():
    client = InProcessMcpClient(build_server(SalesforceMachineRepository(MockSalesforce())))
    ok = await client.call_tool("get_machine", {"machine_id": "M-1"}, UserContext("u", "t", frozenset({"Machine.Read"})))
    assert ok["name"] == "CNC-100"
    from domain.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        await client.call_tool("get_machine", {"machine_id": "M-1"}, UserContext("u", "t", frozenset({"Customer.Read"})))


def test_mcp_http_middleware_requires_signed_context():
    settings = Settings(user_context_secret="s")
    app = create_http_app(build_server(SalesforceMachineRepository(MockSalesforce())), settings)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/mcp").status_code == 401
        assert c.post("/mcp", headers={user_context.HEADER: "x.y"}).status_code == 401
        signed = user_context.sign(UserContext("u", "t", frozenset({"Machine.Read"})), "s")
        r = c.post(
            "/mcp",
            headers={user_context.HEADER: signed, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code not in (401, 403)
