from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from client_app.domain.entities import (
    ServerDescriptor,
    ToolDescriptor,
    ToolInvocationRequest,
    ToolInvocationResult,
)


class McpClientGatewayPort(ABC):
    """Port interface for communicating with local or networked MCP servers."""

    @abstractmethod
    async def list_servers(self) -> list[ServerDescriptor]:
        """Discover and return all configured MCP servers and their registered tools."""
        ...

    @abstractmethod
    async def get_tool(self, server_id: str, tool_name: str) -> ToolDescriptor:
        """Fetch metadata and schema for a specific tool."""
        ...

    @abstractmethod
    async def invoke_tool(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """Invoke a tool with provided arguments and measure execution telemetry."""
        ...
