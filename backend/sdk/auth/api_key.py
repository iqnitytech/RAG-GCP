"""Shared-secret API key authentication via the ``X-API-Key`` header."""

from secrets import compare_digest
from typing import Callable

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader


def make_api_key_dependency(
    key_getter: Callable[[], str],
    header_name: str = "X-API-Key",
) -> Callable[..., None]:
    """Build a FastAPI dependency that enforces an API key.

    Args:
        key_getter: Returns the expected key at request time. An empty value
            disables the check (development only; production configuration
            should refuse to start without a key).
        header_name: Header carrying the key.
    """
    header = APIKeyHeader(name=header_name, auto_error=False)

    def require_api_key(provided: str | None = Security(header)) -> None:
        expected = key_getter()
        if not expected:
            return
        if not provided or not compare_digest(provided.encode(), expected.encode()):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    return require_api_key

