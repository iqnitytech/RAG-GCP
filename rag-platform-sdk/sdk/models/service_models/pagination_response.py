"""Paginated collection response model."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Paginated collection response: ``Page[MyModel]``."""

    items: list[T]
    total: int
    page: int
    page_size: int
