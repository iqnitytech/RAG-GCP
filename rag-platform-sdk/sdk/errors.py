"""Exception hierarchy and handlers that never leak internals to clients."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sdk.logging.context import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, client-presentable errors.

    ``str(exc)`` carries internal detail and is only logged; clients receive
    ``public_message``.
    """

    status_code: int = 500
    public_message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, public_message: str | None = None):
        super().__init__(message or public_message or self.public_message)
        if public_message:
            self.public_message = public_message


class BadRequestError(AppError):
    status_code = 400
    public_message = "Bad request"


class NotFoundError(AppError):
    status_code = 404
    public_message = "Resource not found"


class ServiceUnavailableError(AppError):
    status_code = 503
    public_message = "Service is temporarily unavailable"


class EdhError(AppError):
    """Base exception for all Enterprise Data Hub connector operations."""

    status_code: int = 502
    public_message: str = "Enterprise Data Hub error"
    retryable: bool = False
    error_code: str = "EDH_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        public_message: str | None = None,
        retryable: bool | None = None,
        provider: str = "",
        operation: str = "",
        error_code: str = "",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message, public_message=public_message)
        if retryable is not None:
            self.retryable = retryable
        self.provider = provider
        self.operation = operation
        if error_code:
            self.error_code = error_code
        self.upstream_status = upstream_status


class EdhConfigError(BadRequestError, EdhError):
    """Invalid connector configuration, URI, path traversal, or unregistered strategy."""

    status_code = 400
    public_message = "Invalid data hub configuration or request"
    retryable = False



def _error_body(request: Request, detail: str) -> dict:
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    return {"detail": detail, "request_id": request_id}


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers for ``AppError`` and unexpected exceptions."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        logger.error("%s on %s %s: %s", type(exc).__name__, request.method, request.url.path, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.public_message),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "Internal server error"),
        )

