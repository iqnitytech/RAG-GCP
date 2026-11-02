"""One-call construction of a production-ready FastAPI app."""

from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from sdk.errors import register_exception_handlers
from sdk.health import add_health_routes
from sdk.logging import configure_logging
from sdk.middleware import RequestContextMiddleware, SecurityHeadersMiddleware


def create_base_app(
    *,
    title: str,
    description: str = "",
    version: str = "1.0.0",
    is_production: bool = False,
    debug: bool = False,
    service_name: str | None = None,
    lifespan: Any = None,
    readiness_check: Callable[[], Awaitable[bool]] | None = None,
) -> FastAPI:
    """Create a FastAPI app with the SDK's common behaviour pre-wired.

    Includes: structured logging (JSON in production), request ids + access
    log, security headers (HSTS in production), generic error handlers,
    ``/health`` + ``/ready`` probes, and API docs disabled in production.
    """
    configure_logging(
        level="DEBUG" if debug else "INFO",
        json_logs=is_production,
        service=service_name or title,
    )

    app = FastAPI(
        title=title,
        description=description,
        version=version,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    register_exception_handlers(app)
    # Last added is outermost: request context wraps everything so every
    # response (including errors) carries a request id.
    app.add_middleware(SecurityHeadersMiddleware, hsts=is_production)
    app.add_middleware(RequestContextMiddleware, quiet_paths=("/health", "/ready"))

    add_health_routes(app, readiness_check)
    return app

