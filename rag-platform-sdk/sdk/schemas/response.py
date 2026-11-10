"""Generic response envelopes."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard success envelope: ``ApiResponse[MyModel]``."""

    success: bool = True
    data: T | None = None
    message: str | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Shape of every error body produced by the SDK exception handlers."""

    detail: str
    request_id: str | None = None


class Page(BaseModel, Generic[T]):
    """Paginated collection: ``Page[MyModel]``."""

    items: list[T]
    total: int
    page: int
    page_size: int


class HealthResponse(BaseModel):
    status: str

