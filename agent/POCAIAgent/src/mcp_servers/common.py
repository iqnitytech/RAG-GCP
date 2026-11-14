"""Shared plumbing for MCP servers: user-context propagation, RBAC guard, ASGI auth middleware."""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from starlette.types import ASGIApp, Receive, Scope, Send

from domain.errors import AuthenticationError, AuthorizationError
from domain.ports import UserContext
from infrastructure.auth import user_context
from infrastructure.auth.gcp_tokens import verify_google_id_token
from settings import Settings

_current_user: ContextVar[UserContext | None] = ContextVar("mcp_current_user", default=None)


@contextmanager
def bind_user(ctx: UserContext) -> Iterator[None]:
    token = _current_user.set(ctx)
    try:
        yield
    finally:
        _current_user.reset(token)


def require_role(*roles: str) -> UserContext:
    """Call at the top of every tool. Platform.Admin always passes."""
    ctx = _current_user.get()
    if ctx is None:
        raise AuthenticationError("Missing user context")
    if not ctx.has_any(*roles):
        raise AuthorizationError(f"Requires one of: {', '.join(roles)}")
    return ctx


class AuthMiddleware:
    """Pure ASGI middleware (safe for streaming responses)."""

    def __init__(self, app: ASGIApp, settings: Settings, audience: str = "") -> None:
        self.app, self.settings, self.audience = app, settings, audience

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        try:
            if self.settings.mcp_auth == "google":
                auth = headers.get("authorization", "")
                if not auth.lower().startswith("bearer "):
                    raise AuthenticationError("Missing bearer token")
                claims = verify_google_id_token(auth[7:], self.audience)
                allowed = self.settings.mcp_allowed_caller_sa
                if allowed and claims.get("email") != allowed:
                    raise AuthorizationError("Caller not allowed")
            raw = headers.get(user_context.HEADER.lower())
            if not raw:
                raise AuthenticationError("Missing user context")
            ctx = user_context.verify(raw, self.settings.user_context_secret)
        except (AuthenticationError, AuthorizationError, ValueError) as exc:
            status = 403 if isinstance(exc, AuthorizationError) else 401
            body = json.dumps({"error": str(exc)}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        with bind_user(ctx):
            await self.app(scope, receive, send)


def create_http_app(mcp, settings: Settings, audience: str = ""):
    """Wrap a FastMCP server as an ASGI app with auth + /health."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health", lambda r: JSONResponse({"status": "ok"})))
    app.add_middleware(AuthMiddleware, settings=settings, audience=audience)
    return app
