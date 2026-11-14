from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping

import anyio

from domain.errors import AuthorizationError, UpstreamError
from domain.ports import ToolSpec, UserContext
from infrastructure.auth import user_context
from mcp_servers.common import bind_user


def _normalize(result: Any) -> Any:
    """Convert MCP results (SDK-version tolerant) into plain JSON-like data."""
    if isinstance(result, tuple) and len(result) == 2:  # (content, structured)
        content, structured = result
        return _normalize(structured if structured is not None else content)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    if isinstance(result, dict):
        return result.get("result", result)
    content = getattr(result, "content", result)
    if isinstance(content, list):
        texts = [getattr(c, "text", None) for c in content]
        texts = [t for t in texts if t is not None]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except ValueError:
                return texts[0]
        return texts
    return content


class InProcessMcpClient:
    """Calls a FastMCP server object directly. For local dev and tests; same contract as HTTP."""

    def __init__(self, server) -> None:
        self._server = server

    async def list_tools(self, ctx: UserContext) -> list[ToolSpec]:
        return [ToolSpec(t.name, t.description or "", t.inputSchema) for t in await self._server.list_tools()]

    async def call_tool(self, name: str, args: Mapping[str, Any], ctx: UserContext) -> Any:
        with bind_user(ctx):
            try:
                return _normalize(await self._server.call_tool(name, dict(args)))
            except Exception as exc:
                raise _map_error(exc) from exc


class HttpMcpClient:
    """Streamable-HTTP MCP client. Sends a Google ID token (Cloud Run IAM) + signed user context."""

    def __init__(
        self,
        url: str,
        user_context_secret: str,
        id_token_provider: Callable[[str], Awaitable[str]] | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._url = url
        self._secret = user_context_secret
        self._id_token = id_token_provider
        self._timeout = timeout_s

    async def _headers(self, ctx: UserContext) -> dict[str, str]:
        headers = {user_context.HEADER: user_context.sign(ctx, self._secret)}
        if self._id_token:
            audience = self._url.split("/mcp")[0]
            headers["Authorization"] = f"Bearer {await self._id_token(audience)}"
        return headers

    async def _session(self, ctx: UserContext, fn):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = await self._headers(ctx)
        try:
            with anyio.fail_after(self._timeout):
                async with streamablehttp_client(self._url, headers=headers) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        return await fn(session)
        except TimeoutError as exc:
            raise UpstreamError(f"MCP timeout: {self._url}") from exc
        except BaseExceptionGroup as exc:
            raise UpstreamError(f"MCP call failed: {self._url}: {exc.exceptions[0]!r}") from exc

    async def list_tools(self, ctx: UserContext) -> list[ToolSpec]:
        res = await self._session(ctx, lambda s: s.list_tools())
        return [ToolSpec(t.name, t.description or "", t.inputSchema) for t in res.tools]

    async def call_tool(self, name: str, args: Mapping[str, Any], ctx: UserContext) -> Any:
        res = await self._session(ctx, lambda s: s.call_tool(name, dict(args)))
        if res.isError:
            text = " ".join(getattr(c, "text", "") for c in res.content)
            if "Requires one of" in text:
                raise AuthorizationError(text)
            raise UpstreamError(text)
        return _normalize(res)


def _map_error(exc: Exception) -> Exception:
    text = str(exc)
    if "Requires one of" in text:
        return AuthorizationError(text)
    return UpstreamError(text)
