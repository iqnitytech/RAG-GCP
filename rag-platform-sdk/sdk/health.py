"""Liveness and readiness probe routes."""

import logging
from typing import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sdk.models.service_models.health_response import HealthResponse

logger = logging.getLogger(__name__)


def add_health_routes(
    app: FastAPI,
    readiness_check: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Register ``GET /health`` (liveness) and ``GET /ready`` (readiness).

    ``/health`` never touches external systems. ``/ready`` returns 503 when
    ``readiness_check`` returns False or raises.
    """

    @app.get("/health", response_model=HealthResponse, include_in_schema=False)
    async def health() -> HealthResponse:
        return HealthResponse(status="healthy")

    @app.get("/ready", include_in_schema=False)
    async def ready():
        ok = True
        if readiness_check is not None:
            try:
                ok = await readiness_check()
            except Exception:
                logger.exception("Readiness check failed")
                ok = False
        if not ok:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

