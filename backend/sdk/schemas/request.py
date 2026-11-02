"""Generic request schemas and dependencies."""

from fastapi import Query
from pydantic import BaseModel


class PageParams(BaseModel):
    """Standard pagination parameters (1-based pages)."""

    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_page_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PageParams:
    """FastAPI dependency: ``params: PageParams = Depends(get_page_params)``."""
    return PageParams(page=page, page_size=page_size)

