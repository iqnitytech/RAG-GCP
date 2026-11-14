from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from domain.errors import NotFoundError
from domain.ports import MachineRepository
from mcp_servers.common import require_role

ROLE = "Machine.Read"


def build_server(repo: MachineRepository) -> FastMCP:
    mcp = FastMCP("machine-info", stateless_http=True, json_response=True)

    @mcp.tool()
    async def get_machine(machine_id: str) -> dict:
        """Get a machine (status, owning customer) by its id, e.g. M-1."""
        require_role(ROLE)
        machine = await repo.get(machine_id)
        if machine is None:
            raise NotFoundError(f"Machine {machine_id} not found")
        return asdict(machine)

    @mcp.tool()
    async def list_machines_by_customer(customer_id: str) -> list[dict]:
        """List all machines owned by a customer id, e.g. C-1."""
        require_role(ROLE)
        return [asdict(m) for m in await repo.list_by_customer(customer_id)]

    return mcp
