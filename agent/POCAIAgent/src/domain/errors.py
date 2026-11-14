class DomainError(Exception):
    """Base class for domain/application errors."""


class AuthenticationError(DomainError):
    pass


class AuthorizationError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class UpstreamError(DomainError):
    """A downstream MCP/API/LLM call failed."""
