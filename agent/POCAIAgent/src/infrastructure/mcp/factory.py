from __future__ import annotations

from application.manifest import AgentManifest
from domain.ports import McpToolClient, SalesforcePort
from infrastructure.auth.gcp_tokens import google_id_token
from infrastructure.mcp.clients import HttpMcpClient, InProcessMcpClient
from infrastructure.observability import AuditingMcpClient
from settings import Settings


def build_mcp_client(manifest: AgentManifest, settings: Settings, sf: SalesforcePort) -> McpToolClient:
    """Factory keyed on transport. New transport = new branch here (or a registry of builders)."""
    if manifest.transport == "inprocess":
        from mcp_servers.catalog import CATALOG

        client: McpToolClient = InProcessMcpClient(CATALOG[manifest.endpoint](sf))
    elif manifest.transport == "streamable_http":
        client = HttpMcpClient(
            manifest.endpoint,
            settings.user_context_secret,
            id_token_provider=google_id_token if settings.mcp_auth == "google" else None,
            timeout_s=settings.mcp_timeout_s,
        )
    else:
        raise ValueError(f"Unknown transport: {manifest.transport}")
    return AuditingMcpClient(client, manifest.name)
