"""Pagination request schema and FastAPI dependency (delegating to ``sdk.models.service_models``)."""

from sdk.models.service_models.pagination_request import PageParams, get_page_params

__all__ = ["PageParams", "get_page_params"]
