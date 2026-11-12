"""API-Driven Data Hub Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class ApiHubErrorConstants:
    """Centralized error codes and message templates for ``ApiDrivenEdhStrategy``."""

    PROVIDER = "api_hub"

    ERR_MISSING_ENDPOINT = "API_HUB_CONFIG_MISSING_ENDPOINT"
    ERR_NAMESPACE_MISMATCH = "API_HUB_CONFIG_NAMESPACE_MISMATCH"
    ERR_INVALID_CHUNK_SIZE = "API_HUB_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "API_HUB_CONFIG_INVALID_PAGE_SIZE"
    ERR_AUTH_FAILED = "API_HUB_AUTH_UNAUTHORIZED"
    ERR_NOT_FOUND = "API_HUB_NOT_FOUND"
    ERR_CONFLICT = "API_HUB_CONFLICT"
    ERR_SHA256_MISMATCH_WRITE = "API_HUB_INTEGRITY_SHA256_MISMATCH_WRITE"
    ERR_SHA256_MISMATCH_READ = "API_HUB_INTEGRITY_SHA256_MISMATCH_READ"
    ERR_RATE_LIMIT_EXCEEDED = "API_HUB_RATE_LIMIT_EXCEEDED"
    ERR_TRANSIENT_UPSTREAM = "API_HUB_TRANSIENT_UPSTREAM_ERROR"
    ERR_UNEXPECTED_RESPONSE = "API_HUB_UNEXPECTED_ERROR"

    PUBLIC_CONFIG_ERROR = "Invalid API Hub connector configuration or request parameters"
    PUBLIC_AUTH_ERROR = "API Data Hub authentication failed"
    PUBLIC_NOT_FOUND = "API Data Hub resource not found"
    PUBLIC_CONFLICT = "API Data Hub resource conflict"
    PUBLIC_INTEGRITY_ERROR = "API Data Hub payload checksum verification failed"
    PUBLIC_RATE_LIMIT = "API Data Hub rate limit exceeded"
    PUBLIC_TRANSIENT_ERROR = "API Data Hub is temporarily unavailable"
    PUBLIC_UNEXPECTED_ERROR = "API Data Hub operation failed"

    MSG_MISSING_ENDPOINT = "ApiDrivenEdhStrategy requires EdhConnectorConfig.endpoint_url"
    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in API Hub connector: bound to {expected!r}, got {actual!r}"
    )
    MSG_INVALID_CHUNK_SIZE = "API Hub stream_chunks chunk_size must be > 0 (got {chunk_size})"
    MSG_INVALID_PAGE_SIZE = "API Hub list_objects page_size must be > 0 (got {page_size})"
    MSG_SHA256_MISMATCH_WRITE = (
        "SHA-256 mismatch on API Hub put_bytes for {uri}: expected {expected}, got {actual}"
    )
    MSG_SHA256_MISMATCH_READ = "API Hub payload SHA-256 mismatch for {uri}"
    MSG_HTTP_ERROR = (
        "API Hub {operation} failed with HTTP {status_code} [{hub_code}]: {detail}"
    )


__all__ = ["ApiHubErrorConstants"]
