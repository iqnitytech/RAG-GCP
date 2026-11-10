"""Standard API success response envelope model."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard success envelope: ``ApiResponse[MyModel]``."""

    success: bool = True
    data: T | None = None
    message: str | None = None
    request_id: str | None = None
