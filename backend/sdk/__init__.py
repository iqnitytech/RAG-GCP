"""Reusable platform SDK: auth, schemas, logging, middleware, errors, probes.

The SDK is framework-level and must never import from ``app``. Applications
inject their own settings (as plain callables or duck-typed objects).
"""

from sdk.app_factory import create_base_app
from sdk.errors import (
    AppError,
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
    register_exception_handlers,
)
from sdk.logging import configure_logging, get_request_id

__all__ = [
    "AppError",
    "BadRequestError",
    "NotFoundError",
    "ServiceUnavailableError",
    "configure_logging",
    "create_base_app",
    "get_request_id",
    "register_exception_handlers",
]

