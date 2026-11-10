"""Health and liveness probe response model."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response returned by the ``GET /health`` liveness endpoint."""

    status: str
