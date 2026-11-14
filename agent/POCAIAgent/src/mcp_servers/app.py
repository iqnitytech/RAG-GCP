"""ASGI entrypoint for a single MCP service: MCP_SERVICE=machine|customer|telemetry

    uvicorn mcp_servers.app:app --port 8101
"""
from __future__ import annotations

import os

from infrastructure.salesforce.factory import build_salesforce
from mcp_servers.catalog import CATALOG
from mcp_servers.common import create_http_app
from settings import Settings

_settings = Settings.from_env()
_service = os.environ["MCP_SERVICE"]
app = create_http_app(
    CATALOG[_service](build_salesforce(_settings)),
    _settings,
    audience=os.environ.get("MCP_AUDIENCE", ""),
)
