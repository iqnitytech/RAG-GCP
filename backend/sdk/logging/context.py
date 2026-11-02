"""Request-scoped context shared by logging, middleware and error handlers."""

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the current request's correlation id (None outside a request)."""
    return _request_id.get()


def set_request_id(value: str | None):
    """Set the correlation id; returns a token for ``reset_request_id``."""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)

