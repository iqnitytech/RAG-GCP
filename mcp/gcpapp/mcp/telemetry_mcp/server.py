from __future__ import annotations
from typing import Optional
from core.mcp_server import FastMCP

from config.settings import Settings
from core.factory import GatewayFactory
from core.logging import configure_logging, get_logger
from mcp.telemetry_mcp.tools import register_tools

logger = get_logger(__name__)


def create_server(settings: Optional[Settings] = None) -> FastMCP:
    """Initialize FastMCP Telemetry server with loaded settings and registered tools."""
    if settings is None:
        settings = Settings()
    configure_logging(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)
    logger.info("Initializing TelemetryMCP Server", mode=settings.DATA_PROVIDER_MODE)

    # ARCH-01: Create a single repo instance at server startup and share it across
    # all tool invocations — avoids per-request adapter construction and enables
    # connection reuse within the SDK connector.
    repo = GatewayFactory.get_telemetry_gateway(settings)

    app = FastMCP(
        "telemetry-mcp-server",
        instructions="Enterprise Telemetry MCP Server providing live high-frequency sensor readings, time-series aggregation, real-time threshold alert monitoring, and root-cause fault code diagnostics.",
    )
    register_tools(app, settings, repo=repo)
    return app


if __name__ == "__main__":
    server = create_server()
    server.run(transport="stdio")
