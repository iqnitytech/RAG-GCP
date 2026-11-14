from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

from domain.ports import McpToolClient, ToolSpec, UserContext

audit_log = logging.getLogger("audit")


class AuditingMcpClient:
    """Decorator: audit every tool call (who, what, args hash — never raw args/PII)."""

    def __init__(self, inner: McpToolClient, target: str) -> None:
        self._inner, self._target = inner, target

    async def list_tools(self, ctx: UserContext) -> list[ToolSpec]:
        return await self._inner.list_tools(ctx)

    async def call_tool(self, name: str, args: Mapping[str, Any], ctx: UserContext) -> Any:
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]
        outcome = "ok"
        try:
            return await self._inner.call_tool(name, args, ctx)
        except Exception as exc:
            outcome = type(exc).__name__
            raise
        finally:
            audit_log.info(
                json.dumps({"event": "tool_call", "user": ctx.user_id, "mcp": self._target, "tool": name,
                            "args_sha256": digest, "outcome": outcome})
            )
