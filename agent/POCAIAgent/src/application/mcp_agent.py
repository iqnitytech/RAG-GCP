from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Sequence

from domain.ports import LLMProvider, McpToolClient, UserContext


@dataclass
class McpAgent:
    """Generic domain agent backed by one MCP server. Machine/Customer/Telemetry are all instances of this."""

    name: str
    description: str
    required_roles: frozenset[str]
    mcp: McpToolClient
    llm: LLMProvider
    keywords: tuple[str, ...] = field(default_factory=tuple)
    order: int = 0                                   # execution stage; lower runs first
    consumes: tuple[str, ...] = field(default_factory=tuple)   # id kinds this agent needs
    produces: tuple[str, ...] = field(default_factory=tuple)   # id kinds its results expose
    version: str = "1.0.0"
    owner: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    async def handle(self, message: str, ctx: UserContext, only_tools: Sequence[str] | None = None) -> str:
        tools = await self.mcp.list_tools(ctx)
        if only_tools:  # the selector already chose the skills; fall back to all tools if none match
            tools = [t for t in tools if t.name in only_tools] or tools
        call = await self.llm.pick_tool(message, tools)
        if call is None:
            return "I could not find a suitable tool, or required details (such as an id) are missing."
        result = await self.mcp.call_tool(call.name, call.args, ctx)
        return json.dumps(result, default=str)
