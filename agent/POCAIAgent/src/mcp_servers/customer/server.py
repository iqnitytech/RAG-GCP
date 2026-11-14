from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from domain.errors import NotFoundError
from domain.ports import CustomerRepository
from mcp_servers.common import require_role

ROLE = "Customer.Read"


def build_server(repo: CustomerRepository) -> FastMCP:
    mcp = FastMCP("customer-info", stateless_http=True, json_response=True)

    @mcp.tool()
    async def get_customer(customer_id: str) -> dict:
        """Get a customer (name, tier) by id, e.g. C-1."""
        require_role(ROLE)
        customer = await repo.get(customer_id)
        if customer is None:
            raise NotFoundError(f"Customer {customer_id} not found")
        return asdict(customer)

    @mcp.tool()
    async def search_customers(name: str) -> list[dict]:
        """Search customers by (partial) name."""
        require_role(ROLE)
        return [asdict(c) for c in await repo.search(name)]

    return mcp
