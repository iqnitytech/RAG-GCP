"""Object Storage Hub Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class ObjectStorageErrorConstants:
    """Centralized error codes and message templates for ``ObjectStorageHubStrategy``."""

    PROVIDER = "object_storage_hub"

    ERR_MISSING_ENDPOINT = "OBJ_STORAGE_CONFIG_MISSING_ENDPOINT"
    ERR_NAMESPACE_MISMATCH = "OBJ_STORAGE_CONFIG_NAMESPACE_MISMATCH"
    ERR_INVALID_CHUNK_SIZE = "OBJ_STORAGE_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "OBJ_STORAGE_CONFIG_INVALID_PAGE_SIZE"
    ERR_AUTH_FAILED = "OBJ_STORAGE_AUTH_UNAUTHORIZED"
    ERR_NOT_FOUND = "OBJ_STORAGE_NOT_FOUND"
    ERR_CONFLICT = "OBJ_STORAGE_CONFLICT"
    ERR_SHA256_MISMATCH_WRITE = "OBJ_STORAGE_INTEGRITY_SHA256_MISMATCH_WRITE"
    ERR_SHA256_MISMATCH_READ = "OBJ_STORAGE_INTEGRITY_SHA256_MISMATCH_READ"
    ERR_RATE_LIMIT_EXCEEDED = "OBJ_STORAGE_RATE_LIMIT_EXCEEDED"
    ERR_TRANSIENT_UPSTREAM = "OBJ_STORAGE_TRANSIENT_UPSTREAM_ERROR"
    ERR_UNEXPECTED_RESPONSE = "OBJ_STORAGE_UNEXPECTED_ERROR"

    PUBLIC_CONFIG_ERROR = "Invalid Object Storage Hub configuration or request parameters"
    PUBLIC_AUTH_ERROR = "Object Storage Hub authentication or signature verification failed"
    PUBLIC_NOT_FOUND = "Requested object or bucket was not found in Object Storage Hub"
    PUBLIC_CONFLICT = "Object already exists in Object Storage Hub and overwrite is disabled"
    PUBLIC_INTEGRITY_ERROR = "Object Storage Hub payload checksum verification failed"
    PUBLIC_RATE_LIMIT = "Object Storage Hub rate limit (SlowDown) exceeded"
    PUBLIC_TRANSIENT_ERROR = "Object Storage Hub is temporarily unavailable"
    PUBLIC_UNEXPECTED_ERROR = "Object Storage Hub operation failed"

    MSG_MISSING_ENDPOINT = (
        "ObjectStorageHubStrategy requires EdhConnectorConfig.endpoint_url"
    )
    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in Object Storage Hub: bound to {expected!r}, got {actual!r}"
    )
    MSG_INVALID_CHUNK_SIZE = (
        "Object Storage Hub stream_chunks chunk_size must be > 0 (got {chunk_size})"
    )
    MSG_INVALID_PAGE_SIZE = (
        "Object Storage Hub list_objects page_size must be > 0 (got {page_size})"
    )
    MSG_SHA256_MISMATCH_WRITE = (
        "SHA-256 mismatch on Object Storage put_bytes for {uri}: expected {expected}, got {actual}"
    )
    MSG_SHA256_MISMATCH_READ = "Object Storage Hub payload SHA-256 mismatch for {uri}"
    MSG_HTTP_ERROR = (
        "Object Storage Hub {operation} failed with HTTP {status_code} [{s3_code}]: {detail}"
    )


__all__ = ["ObjectStorageErrorConstants"]
