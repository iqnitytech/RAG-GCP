from __future__ import annotations
import builtins
from typing import Any

class McpPlatformError(Exception):
    def __init__(self, message: str, details: dict[str, Any] | None = None, correlation_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.correlation_id = correlation_id

class DomainError(McpPlatformError): pass
class EntityNotFoundError(DomainError): pass
class DomainValidationError(DomainError): pass
class InfrastructureError(McpPlatformError): pass
class GatewayError(InfrastructureError): pass
class SdkGatewayError(GatewayError): pass
class MockGatewayError(GatewayError): pass

# CODE-02: Inherit from builtins and use Mcp prefix to avoid shadowing Python builtins
class McpConnectionError(InfrastructureError, builtins.ConnectionError):
    """MCP platform connection failure."""
    pass

class McpTimeoutError(InfrastructureError, builtins.TimeoutError):
    """MCP platform timeout failure."""
    pass

# Aliases for backwards compatibility without unmitigated shadowing
ConnectionError = McpConnectionError
TimeoutError = McpTimeoutError

class AuthenticationError(InfrastructureError): pass
class RateLimitError(InfrastructureError): pass
class CircuitBreakerOpenError(InfrastructureError): pass
class ToolExecutionError(McpPlatformError): pass
