"""One-call construction of a production-ready FastAPI app."""

from typing import Any, Awaitable, Callable, Mapping

from fastapi import FastAPI

from sdk.errors import register_exception_handlers
from sdk.health import add_health_routes
from sdk.logging import configure_logging
from sdk.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)


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
    default_max_body_bytes: int | None = None,
    route_body_limits: Mapping[str, int] | None = None,
    require_content_length_paths: tuple[str, ...] = (),
    max_body_bytes_message: str | None = None,
) -> FastAPI:
    """Create a FastAPI app with the SDK's common behaviour pre-wired.

    Includes: structured logging (JSON in production), request ids + access
    log, security headers (HSTS in production), optional body-size limits,
    generic error handlers, ``/health`` + ``/ready`` probes, and API docs
    disabled in production.
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
    if default_max_body_bytes is not None or route_body_limits or require_content_length_paths:
        app.add_middleware(
            BodySizeLimitMiddleware,
            default_max_bytes=default_max_body_bytes,
            route_limits=route_body_limits,
            require_content_length_paths=require_content_length_paths,
            max_bytes_message=max_body_bytes_message,
        )
    # Last added is outermost: request context wraps everything so every
    # response (including errors) carries a request id.
    app.add_middleware(SecurityHeadersMiddleware, hsts=is_production)
    app.add_middleware(RequestContextMiddleware, quiet_paths=("/health", "/ready"))

    add_health_routes(app, readiness_check)
    return app

