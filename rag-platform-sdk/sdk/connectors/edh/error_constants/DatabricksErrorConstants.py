"""Databricks EDH Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class DatabricksErrorConstants:
    """Centralized error codes and message templates for ``DatabricksEdhStrategy``."""

    PROVIDER = "databricks"

    ERR_MISSING_ENDPOINT = "DBX_CONFIG_MISSING_ENDPOINT"
    ERR_NAMESPACE_MISMATCH = "DBX_CONFIG_NAMESPACE_MISMATCH"
    ERR_UNSAFE_IDENTIFIER = "DBX_CONFIG_UNSAFE_UC_IDENTIFIER"
    ERR_INVALID_CHUNK_SIZE = "DBX_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "DBX_CONFIG_INVALID_PAGE_SIZE"

    ERR_OAUTH_TOKEN_EXCHANGE = "DBX_AUTH_OAUTH_EXCHANGE_FAILED"
    ERR_OAUTH_MISSING_TOKEN = "DBX_AUTH_MISSING_ACCESS_TOKEN"
    ERR_AUTH_FAILED = "DBX_AUTH_UNAUTHORIZED"

    ERR_RESOURCE_NOT_FOUND = "DBX_NOT_FOUND_RESOURCE"
    ERR_RESOURCE_CONFLICT = "DBX_CONFLICT_RESOURCE_EXISTS"
    ERR_SHA256_MISMATCH_WRITE = "DBX_INTEGRITY_SHA256_MISMATCH_WRITE"
    ERR_SHA256_MISMATCH_READ = "DBX_INTEGRITY_SHA256_MISMATCH_READ"
    ERR_RATE_LIMIT_EXCEEDED = "DBX_RATE_LIMIT_EXCEEDED"
    ERR_SQL_STATEMENT_FAILED = "DBX_SQL_STATEMENT_FAILED"
    ERR_TRANSIENT_UPSTREAM = "DBX_TRANSIENT_UPSTREAM_ERROR"
    ERR_UNEXPECTED_RESPONSE = "DBX_UNEXPECTED_ERROR"

    PUBLIC_CONFIG_ERROR = "Invalid Databricks connector configuration or Unity Catalog identifier"
    PUBLIC_AUTH_ERROR = "Databricks authentication or permission check failed"
    PUBLIC_NOT_FOUND = "Requested Databricks Unity Catalog volume file or table was not found"
    PUBLIC_CONFLICT = "Databricks Unity Catalog resource already exists"
    PUBLIC_INTEGRITY_ERROR = "Databricks Unity Catalog file checksum verification failed"
    PUBLIC_RATE_LIMIT = "Databricks API rate limit exceeded; please retry later"
    PUBLIC_TRANSIENT_ERROR = "Databricks workspace or SQL warehouse is temporarily unavailable"
    PUBLIC_UNEXPECTED_ERROR = "Databricks operation failed"

    MSG_MISSING_ENDPOINT = (
        "DatabricksEdhStrategy requires EdhConnectorConfig.endpoint_url (Databricks workspace URL)"
    )
    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in Databricks connector: bound to {expected!r}, got {actual!r}"
    )
    MSG_UNSAFE_IDENTIFIER = "Unsafe Databricks Unity Catalog {kind}: {name!r}"
    MSG_INVALID_CHUNK_SIZE = "Databricks stream_chunks chunk_size must be > 0 (got {chunk_size})"
    MSG_INVALID_PAGE_SIZE = "Databricks list_objects page_size must be > 0 (got {page_size})"
    MSG_OAUTH_FAILED = (
        "Databricks OAuth2 token exchange failed with HTTP {status_code}: {detail}"
    )
    MSG_OAUTH_MISSING_TOKEN = "Databricks OAuth2 response missing access_token"
    MSG_SHA256_MISMATCH_WRITE = (
        "SHA-256 mismatch on Databricks put_bytes for {uri}: expected {expected}, got {actual}"
    )
    MSG_SHA256_MISMATCH_READ = "Databricks Unity Catalog file SHA-256 mismatch for {uri}"
    MSG_SQL_STATEMENT_FAILED = (
        "Databricks SQL statement ended in state {state}: {detail}"
    )
    MSG_HTTP_ERROR = (
        "Databricks {operation} failed with HTTP {status_code} [{dbx_code}]: {detail}"
    )


__all__ = ["DatabricksErrorConstants"]
