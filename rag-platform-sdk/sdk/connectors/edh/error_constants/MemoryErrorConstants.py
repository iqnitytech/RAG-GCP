"""In-Memory Strategy error codes, public messages, and log templates."""

from __future__ import annotations


class MemoryErrorConstants:
    """Centralized error codes and message templates for ``InMemoryEdhStrategy``."""

    PROVIDER = "memory"

    ERR_NAMESPACE_MISMATCH = "MEMORY_CONFIG_NAMESPACE_MISMATCH"
    ERR_INVALID_CHUNK_SIZE = "MEMORY_CONFIG_INVALID_CHUNK_SIZE"
    ERR_INVALID_PAGE_SIZE = "MEMORY_CONFIG_INVALID_PAGE_SIZE"
    ERR_NOT_FOUND = "MEMORY_NOT_FOUND"
    ERR_CONFLICT = "MEMORY_CONFLICT_EXISTS"
    ERR_SHA256_MISMATCH_WRITE = "MEMORY_INTEGRITY_SHA256_MISMATCH_WRITE"

    PUBLIC_CONFIG_ERROR = "Invalid in-memory connector configuration or request parameters"
    PUBLIC_NOT_FOUND = "Requested in-memory object was not found"
    PUBLIC_CONFLICT = "In-memory object already exists and overwrite is disabled"
    PUBLIC_INTEGRITY_ERROR = "In-memory object checksum verification failed"

    MSG_NAMESPACE_MISMATCH = (
        "Namespace mismatch in memory strategy: bound to {expected!r}, got {actual!r}"
    )
    MSG_INVALID_CHUNK_SIZE = "In-memory stream_chunks chunk_size must be > 0 (got {chunk_size})"
    MSG_INVALID_PAGE_SIZE = "In-memory list_objects page_size must be > 0 (got {page_size})"
    MSG_NOT_FOUND = "Object not found in memory strategy: {uri}"
    MSG_CONFLICT = "Object already exists in memory strategy and overwrite=False: {uri}"
    MSG_SHA256_MISMATCH_WRITE = "SHA-256 mismatch on memory put_bytes for {uri}"


__all__ = ["MemoryErrorConstants"]
