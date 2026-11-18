from __future__ import annotations
from typing import Any
from config.settings import Settings
from core.factory import GatewayFactory
from core.logging import get_logger

logger = get_logger(__name__)


def check_readiness(settings: Settings | None = None) -> dict[str, Any]:
    """Check readiness of all MCP subsystems, adapters, and providers (OBS-02).
    Returns 200/ready status dictionary.
    """
    s = settings or Settings()
    statuses: dict[str, str] = {}
    ready = True

    try:
        c_gateway = GatewayFactory.get_customer_gateway(s)
        statuses["customer_mcp"] = "ready"
    except Exception as exc:
        statuses["customer_mcp"] = f"unready: {exc}"
        ready = False

    try:
        m_gateway = GatewayFactory.get_machine_gateway(s)
        statuses["machine_mcp"] = "ready"
    except Exception as exc:
        statuses["machine_mcp"] = f"unready: {exc}"
        ready = False

    try:
        t_gateway = GatewayFactory.get_telemetry_gateway(s)
        statuses["telemetry_mcp"] = "ready"
    except Exception as exc:
        statuses["telemetry_mcp"] = f"unready: {exc}"
        ready = False

    return {
        "status": "ready" if ready else "unready",
        "mode": s.DATA_PROVIDER_MODE,
        "components": statuses,
    }


def check_liveness() -> dict[str, str]:
    """Simple process liveness probe."""
    return {"status": "healthy", "service": "mcp-platform"}
