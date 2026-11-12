"""Data Lake Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class DataLakeErrorConstants:
    """Centralized error codes and message templates for ``DataLakeStrategy``."""

    PROVIDER = "data_lake"

    ERR_MISSING_ROOT_PATH = "DATA_LAKE_CONFIG_MISSING_ROOT_PATH"
    ERR_NAMESPACE_MISMATCH = "DATA_LAKE_CONFIG_NAMESPACE_MISMATCH"
    ERR_PATH_ESCAPE = "DATA_LAKE_CONFIG_PATH_ESCAPE"
    ERR_INVALID_CHUNK_SIZE = "DATA_LAKE_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "DATA_LAKE_CONFIG_INVALID_PAGE_SIZE"
    ERR_NOT_FOUND = "DATA_LAKE_NOT_FOUND"
    ERR_CONFLICT = "DATA_LAKE_CONFLICT_EXISTS"
    ERR_SHA256_MISMATCH_WRITE = "DATA_LAKE_INTEGRITY_SHA256_MISMATCH_WRITE"
    ERR_SHA256_MISMATCH_READ = "DATA_LAKE_INTEGRITY_SHA256_MISMATCH_READ"
    ERR_SHA256_MISMATCH_STREAM = "DATA_LAKE_INTEGRITY_SHA256_MISMATCH_STREAM"

    PUBLIC_CONFIG_ERROR = "Invalid Data Lake configuration or object path"
    PUBLIC_NOT_FOUND = "Requested Data Lake object was not found"
    PUBLIC_CONFLICT = "Data Lake object already exists and overwrite is disabled"
    PUBLIC_INTEGRITY_ERROR = "Data Lake object checksum verification failed"

    MSG_MISSING_ROOT_PATH = "DataLakeStrategy requires EdhConnectorConfig.root_path"
    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in Data Lake connector: bound to {expected!r}, got {actual!r}"
    )
    MSG_PATH_ESCAPE = "Resolved path escapes data lake root: {key!r}"
    MSG_INVALID_CHUNK_SIZE = "Data Lake stream_chunks chunk_size must be > 0 (got {chunk_size})"
    MSG_INVALID_PAGE_SIZE = "Data Lake list_objects page_size must be > 0 (got {page_size})"
    MSG_NOT_FOUND = "Data lake object not found: {uri}"
    MSG_CONFLICT = "Object already exists in data lake and overwrite=False: {uri}"
    MSG_SHA256_MISMATCH_WRITE = (
        "SHA-256 mismatch on Data Lake put_bytes for {uri}: expected {expected}, got {actual}"
    )
    MSG_SHA256_MISMATCH_READ = "Stored object failed SHA-256 verification: {uri}"
    MSG_SHA256_MISMATCH_STREAM = "Streamed object failed SHA-256 verification: {uri}"


__all__ = ["DataLakeErrorConstants"]
