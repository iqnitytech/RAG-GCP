from __future__ import annotations
from typing import Any


class ClientAppError(Exception):
    """Base exception for client application domain."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ServerNotFoundError(ClientAppError):
    pass


class ToolNotFoundError(ClientAppError):
    pass


class InvocationError(ClientAppError):
    pass
