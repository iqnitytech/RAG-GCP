from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from domain.ports import TelemetryRepository
from mcp_servers.common import require_role

ROLE = "Telemetry.Read"


def build_server(repo: TelemetryRepository) -> FastMCP:
    mcp = FastMCP("telemetry-info", stateless_http=True, json_response=True)

    @mcp.tool()
    async def latest_readings(machine_id: str, limit: int = 5) -> list[dict]:
        """Latest telemetry readings (temperature, vibration) for a machine id."""
        require_role(ROLE)
        return [asdict(r) | {"is_anomaly": r.is_anomaly} for r in await repo.latest(machine_id, limit)]

    @mcp.tool()
    async def anomalies(machine_id: str) -> list[dict]:
        """Readings of a machine that exceed temperature or vibration limits (anomaly, alert)."""
        require_role(ROLE)
        return [asdict(r) | {"is_anomaly": True} for r in await repo.latest(machine_id, 100) if r.is_anomaly]

    return mcp
