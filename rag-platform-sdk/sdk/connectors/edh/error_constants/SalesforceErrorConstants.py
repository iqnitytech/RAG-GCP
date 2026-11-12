"""Salesforce EDH Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class SalesforceErrorConstants:
    """Centralized error codes and message templates for ``SalesforceEdhStrategy``."""

    PROVIDER = "salesforce"

    # Error Codes
    ERR_MISSING_ENDPOINT = "SF_CONFIG_MISSING_ENDPOINT"
    ERR_NAMESPACE_MISMATCH = "SF_CONFIG_NAMESPACE_MISMATCH"
    ERR_UNSAFE_IDENTIFIER = "SF_CONFIG_UNSAFE_SOQL_IDENTIFIER"
    ERR_EMPTY_SOQL_OR_SOBJECT = "SF_CONFIG_EMPTY_SOQL_OR_SOBJECT"
    ERR_INVALID_BATCH_SIZE = "SF_CONFIG_INVALID_BATCH_SIZE"
    ERR_INVALID_CHUNK_SIZE = "SF_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "SF_CONFIG_INVALID_PAGE_SIZE"
    ERR_EMPTY_RECORD_ID = "SF_CONFIG_EMPTY_RECORD_ID"
    ERR_MISSING_DELETE_RECORD_ID = "SF_CONFIG_MISSING_DELETE_RECORD_ID"
    ERR_EMPTY_EXTERNAL_ID_VALUE = "SF_CONFIG_EMPTY_EXTERNAL_ID_VALUE"
    ERR_MISSING_EXTERNAL_ID_FIELD = "SF_CONFIG_MISSING_EXTERNAL_ID_FIELD"
    ERR_INVALID_BULK_OPERATION = "SF_CONFIG_INVALID_BULK_OPERATION"
    ERR_EMPTY_BULK_RECORDS = "SF_CONFIG_EMPTY_BULK_RECORDS"

    ERR_OAUTH_TOKEN_EXCHANGE = "SF_AUTH_OAUTH_EXCHANGE_FAILED"
    ERR_OAUTH_MISSING_TOKEN = "SF_AUTH_MISSING_ACCESS_TOKEN"
    ERR_AUTH_FAILED = "SF_AUTH_UNAUTHORIZED"
    ERR_INSUFFICIENT_ACCESS = "SF_AUTH_INSUFFICIENT_ACCESS"

    ERR_CONTENT_VERSION_NOT_FOUND = "SF_NOT_FOUND_CONTENT_VERSION"
    ERR_RECORD_NOT_FOUND = "SF_NOT_FOUND_RECORD"
    ERR_SOBJECT_NOT_FOUND = "SF_NOT_FOUND_SOBJECT"

    ERR_CONTENT_VERSION_CONFLICT = "SF_CONFLICT_CONTENT_VERSION_EXISTS"
    ERR_DUPLICATE_RECORD = "SF_CONFLICT_DUPLICATE_VALUE"

    ERR_SHA256_MISMATCH_WRITE = "SF_INTEGRITY_SHA256_MISMATCH_WRITE"
    ERR_SHA256_MISMATCH_READ = "SF_INTEGRITY_SHA256_MISMATCH_READ"

    ERR_RATE_LIMIT_EXCEEDED = "SF_RATE_LIMIT_EXCEEDED"
    ERR_BULK_QUERY_MISSING_JOB_ID = "SF_BULK_QUERY_MISSING_JOB_ID"
    ERR_BULK_QUERY_TIMEOUT = "SF_BULK_QUERY_TIMEOUT"
    ERR_BULK_QUERY_FAILED = "SF_BULK_QUERY_JOB_FAILED"
    ERR_BULK_INGEST_MISSING_JOB_ID = "SF_BULK_INGEST_MISSING_JOB_ID"
    ERR_BULK_INGEST_TIMEOUT = "SF_BULK_INGEST_TIMEOUT"
    ERR_BULK_INGEST_FAILED = "SF_BULK_INGEST_JOB_FAILED"
    ERR_TRANSIENT_UPSTREAM = "SF_TRANSIENT_UPSTREAM_ERROR"
    ERR_MALFORMED_REQUEST = "SF_BAD_REQUEST"
    ERR_UNEXPECTED_RESPONSE = "SF_UNEXPECTED_ERROR"

    # Public client-safe messages
    PUBLIC_CONFIG_ERROR = "Invalid Salesforce connector configuration or request parameters"
    PUBLIC_AUTH_ERROR = "Salesforce authentication or permission check failed"
    PUBLIC_NOT_FOUND = "Requested Salesforce record or document was not found"
    PUBLIC_CONFLICT = "Salesforce record or document already exists or conflicts with existing data"
    PUBLIC_INTEGRITY_ERROR = "Salesforce document checksum verification failed"
    PUBLIC_RATE_LIMIT = "Salesforce API rate limit exceeded; please retry later"
    PUBLIC_TRANSIENT_ERROR = "Salesforce service is temporarily unavailable"
    PUBLIC_UNEXPECTED_ERROR = "Salesforce operation failed"

    # Detailed log / exception message templates
    MSG_MISSING_ENDPOINT = (
        "SalesforceEdhStrategy requires EdhConnectorConfig.endpoint_url (Salesforce instance URL)"
    )
    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in Salesforce connector: bound to {expected!r}, got {actual!r}"
    )
    MSG_UNSAFE_IDENTIFIER = "Unsafe Salesforce SOQL {kind}: {name!r}"
    MSG_EMPTY_SOQL_OR_SOBJECT = "Salesforce soql_or_sobject parameter must not be empty"
    MSG_INVALID_BATCH_SIZE = "Salesforce paginated_query batch_size must be > 0 (got {batch_size})"
    MSG_INVALID_CHUNK_SIZE = "Salesforce stream_chunks chunk_size must be > 0 (got {chunk_size})"
    MSG_INVALID_PAGE_SIZE = "Salesforce list_objects page_size must be > 0 (got {page_size})"
    MSG_EMPTY_RECORD_ID = "Salesforce record_id must not be empty for operation {operation!r}"
    MSG_MISSING_DELETE_RECORD_ID = (
        "record_id is required when calling Salesforce delete(sobject_name, record_id)"
    )
    MSG_EMPTY_EXTERNAL_ID_VALUE = "Salesforce external_id_value must not be empty for upsert"
    MSG_MISSING_EXTERNAL_ID_FIELD = (
        "Salesforce external_id_field is required when bulk_write operation='upsert'"
    )
    MSG_INVALID_BULK_OPERATION = (
        "Invalid Salesforce bulk_write operation {operation!r}; expected one of {valid_ops}"
    )
    MSG_EMPTY_BULK_RECORDS = "Salesforce bulk_write requires at least one record"
    MSG_OAUTH_FAILED = (
        "Salesforce OAuth2 token exchange failed with HTTP {status_code}: {detail}"
    )
    MSG_OAUTH_MISSING_TOKEN = "Salesforce OAuth2 response missing access_token"
    MSG_CONTENT_VERSION_NOT_FOUND = "Salesforce ContentVersion not found: {uri}"
    MSG_CONTENT_VERSION_CONFLICT = (
        "Salesforce ContentVersion already exists and overwrite=False: {uri}"
    )
    MSG_SHA256_MISMATCH_WRITE = (
        "SHA-256 mismatch on Salesforce put_bytes for {uri}: expected {expected}, got {actual}"
    )
    MSG_SHA256_MISMATCH_READ = "Salesforce ContentVersion SHA-256 mismatch for {uri}"
    MSG_BULK_QUERY_MISSING_JOB_ID = "Salesforce Bulk API 2.0 query job response missing id"
    MSG_BULK_QUERY_TIMEOUT = (
        "Salesforce Bulk API 2.0 query job {job_id} timed out in state {state!r}"
    )
    MSG_BULK_QUERY_FAILED = (
        "Salesforce Bulk API 2.0 query job {job_id} failed ({state}): {detail}"
    )
    MSG_BULK_INGEST_MISSING_JOB_ID = "Salesforce Bulk API 2.0 ingest job response missing id"
    MSG_BULK_INGEST_TIMEOUT = (
        "Salesforce Bulk API 2.0 ingest job {job_id} timed out in state {state!r}"
    )
    MSG_BULK_INGEST_FAILED = (
        "Salesforce Bulk API 2.0 ingest job {job_id} failed ({state}): {detail}"
    )
    MSG_HTTP_ERROR = (
        "Salesforce {operation} failed with HTTP {status_code} [{sf_code}]: {detail}"
    )

    # Salesforce upstream errorCode classifications
    AUTH_ERROR_CODES: frozenset[str] = frozenset(
        {
            "INVALID_SESSION_ID",
            "INSUFFICIENT_ACCESS",
            "INSUFFICIENT_ACCESS_OR_READONLY",
            "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
            "OAUTH_APPROVAL_ERROR_GENERIC",
            "INVALID_LOGIN",
        }
    )
    NOT_FOUND_ERROR_CODES: frozenset[str] = frozenset(
        {
            "NOT_FOUND",
            "ENTITY_IS_DELETED",
            "INVALID_CROSS_REFERENCE_KEY",
        }
    )
    CONFLICT_ERROR_CODES: frozenset[str] = frozenset(
        {
            "DUPLICATE_VALUE",
            "DUPLICATE_EXTERNAL_ID",
        }
    )
    RATE_LIMIT_ERROR_CODES: frozenset[str] = frozenset(
        {
            "REQUEST_LIMIT_EXCEEDED",
            "CONCURRENT_API_LIMIT_EXCEEDED",
            "STORAGE_LIMIT_EXCEEDED",
        }
    )
    TRANSIENT_ERROR_CODES: frozenset[str] = frozenset(
        {
            "UNABLE_TO_LOCK_ROW",
            "SERVER_UNAVAILABLE",
            "QUERY_TIMEOUT",
        }
    )
    CONFIG_OR_VALIDATION_ERROR_CODES: frozenset[str] = frozenset(
        {
            "MALFORMED_QUERY",
            "INVALID_FIELD",
            "INVALID_TYPE",
            "REQUIRED_FIELD_MISSING",
            "INVALID_ID_FIELD",
            "JSON_PARSER_ERROR",
            "FIELD_CUSTOM_VALIDATION_EXCEPTION",
        }
    )


__all__ = ["SalesforceErrorConstants"]
