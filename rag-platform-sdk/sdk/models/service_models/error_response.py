"""Standard error response envelope model."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Shape of every error body produced by the SDK exception handlers."""

    detail: str
    request_id: str | None = None
