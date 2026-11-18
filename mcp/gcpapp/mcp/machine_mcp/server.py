from __future__ import annotations
from typing import Optional
from core.mcp_server import FastMCP

from config.settings import Settings
from core.factory import GatewayFactory
from core.logging import configure_logging, get_logger
from mcp.machine_mcp.tools import register_tools

logger = get_logger(__name__)


def create_server(settings: Optional[Settings] = None) -> FastMCP:
    """Initialize FastMCP Machine server with loaded settings and registered tools."""
    if settings is None:
        settings = Settings()
    configure_logging(level=settings.LOG_LEVEL, format=settings.LOG_FORMAT)
    logger.info("Initializing MachineMCP Server", mode=settings.DATA_PROVIDER_MODE)

    # ARCH-01: Create a single repo instance at server startup and share it across
    # all tool invocations — avoids per-request adapter construction and enables
    # connection reuse within the SDK connector.
    repo = GatewayFactory.get_machine_gateway(settings)

    app = FastMCP(
        "machine-mcp-server",
        instructions="Enterprise Machine MCP Server providing equipment hardware specifications, fleet management, maintenance records, and operational state control.",
    )
    register_tools(app, settings, repo=repo)
    return app


if __name__ == "__main__":
    server = create_server()
    server.run(transport="stdio")
