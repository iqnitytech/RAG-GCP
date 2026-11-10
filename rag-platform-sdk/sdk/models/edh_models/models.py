"""Immutable value objects and configuration models for Enterprise Data Hub (EDH) connectors.

Built purely with Python standard library modules (``dataclasses``, ``datetime``,
``enum``, ``hashlib``, ``posixpath``, ``re``, ``urllib.parse``) so the domain
contract has zero third-party runtime dependencies.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from sdk.errors import EdhConfigError

_SAFE_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._\-=@:,+$!*'() ]+$")


class DataZone(str, Enum):
    """Medallion / Enterprise Data Hub storage zones."""

    LANDING = "landing"
    QUARANTINE = "quarantine"
    RAW = "raw"          # Bronze
    CURATED = "curated"  # Silver
    GOLD = "gold"        # Gold / Serving


class RecordFormat(str, Enum):
    """Supported core-library record serialization formats for Data Lake tables."""

    JSONL = "jsonl"
    CSV = "csv"


def normalize_object_key(key: str) -> str:
    """Validate and normalize a logical object key, preventing path traversal.

    Raises:
        EdhConfigError: If the key is empty, contains null bytes, backslashes,
            or ``..`` path traversal segments.
    """
    if not key or not key.strip():
        raise EdhConfigError("Object key must not be empty")
    if "\x00" in key or "\\" in key:
        raise EdhConfigError(f"Unsafe characters in object key: {key!r}")

    stripped = key.strip().lstrip("/")
    if not stripped:
        raise EdhConfigError("Object key must not resolve to root")

    raw_parts = [p for p in stripped.split("/") if p]
    for part in raw_parts:
        if part in (".", ".."):
            raise EdhConfigError(f"Path traversal segment not allowed in key: {key!r}")
        if not _SAFE_KEY_SEGMENT.match(part):
            raise EdhConfigError(f"Invalid path segment {part!r} in key: {key!r}")

    normalized = posixpath.normpath("/".join(raw_parts))
    if normalized.startswith("..") or normalized == ".":
        raise EdhConfigError(f"Invalid normalized object key: {key!r}")
    return normalized


def compute_sha256(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EdhObjectRef:
    """Immutable reference to an object or dataset partition in the Data Hub."""

    namespace: str
    key: str
    zone: DataZone = DataZone.RAW
    version: str | None = None

    def __post_init__(self) -> None:
        if not self.namespace or not self.namespace.strip():
            raise EdhConfigError("EdhObjectRef.namespace is required")
        normalized_key = normalize_object_key(self.key)
        object.__setattr__(self, "namespace", self.namespace.strip())
        object.__setattr__(self, "key", normalized_key)

    @property
    def qualified_key(self) -> str:
        """Return ``<zone>/<key>`` when the key is not already prefixed with the zone."""
        prefix = f"{self.zone.value}/"
        if self.key.startswith(prefix):
            return self.key
        return f"{prefix}{self.key}"

    @property
    def uri(self) -> str:
        """Canonical ``edh://<namespace>/<zone>/<key>[?version=...]`` URI."""
        base = f"edh://{self.namespace}/{self.qualified_key}"
        return f"{base}?version={self.version}" if self.version else base

    @classmethod
    def from_uri(cls, uri: str, *, default_zone: DataZone = DataZone.RAW) -> EdhObjectRef:
        """Parse ``edh://<namespace>/<zone>/<key>`` or ``s3://`` / ``gs://`` URIs."""
        parsed = urlparse(uri)
        if not parsed.scheme or not parsed.netloc:
            raise EdhConfigError(f"Invalid EDH URI: {uri!r}")
        raw_path = parsed.path.lstrip("/")
        if not raw_path:
            raise EdhConfigError(f"EDH URI missing object key: {uri!r}")

        parts = raw_path.split("/", 1)
        zone = default_zone
        key = raw_path
        if len(parts) == 2:
            try:
                zone = DataZone(parts[0].lower())
                key = parts[1]
            except ValueError:
                zone = default_zone
                key = raw_path

        version: str | None = None
        if parsed.query:
            for item in parsed.query.split("&"):
                if item.startswith("version="):
                    version = item.split("=", 1)[1] or None
                    break
        return cls(namespace=parsed.netloc, key=key, zone=zone, version=version)


@dataclass(frozen=True)
class EdhObjectMetadata:
    """Immutable metadata returned when inspecting, listing, or writing an object."""

    ref: EdhObjectRef
    size_bytes: int
    content_type: str = "application/octet-stream"
    sha256: str = ""
    etag: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    custom_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EdhWriteOptions:
    """Options controlling an object or dataset write operation."""

    content_type: str = "application/octet-stream"
    custom_metadata: Mapping[str, str] = field(default_factory=dict)
    overwrite: bool = True
    expected_sha256: str | None = None
    idempotency_key: str | None = None
    compress_gzip: bool = False


@dataclass(frozen=True)
class EdhListPage:
    """Paginated listing response for object storage or data lake partitions."""

    items: tuple[EdhObjectMetadata, ...]
    next_page_token: str | None = None


@dataclass(frozen=True)
class EdhRecordBatch:
    """Batch of structured records written to or read from a Data Lake dataset."""

    dataset: str
    records: tuple[Mapping[str, Any], ...]
    partition_values: Mapping[str, str] = field(default_factory=dict)
    zone: DataZone = DataZone.CURATED
    record_format: RecordFormat = RecordFormat.JSONL
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.dataset or not self.dataset.strip():
            raise EdhConfigError("EdhRecordBatch.dataset is required")
        normalize_object_key(self.dataset)
        for part_col, part_val in self.partition_values.items():
            if not part_col or "=" in part_col or "/" in part_col or ".." in str(part_val):
                raise EdhConfigError(
                    f"Invalid Hive partition {part_col!r}={part_val!r}"
                )


@dataclass(frozen=True)
class RetryPolicy:
    """Retry with exponential backoff and jitter for idempotent operations."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.15
    max_delay_seconds: float = 2.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise EdhConfigError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise EdhConfigError("RetryPolicy delays must be non-negative")


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    """Fail-fast circuit breaker configuration around an upstream EDH endpoint."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise EdhConfigError("CircuitBreakerPolicy.failure_threshold must be >= 1")
        if self.recovery_timeout_seconds <= 0:
            raise EdhConfigError("CircuitBreakerPolicy.recovery_timeout_seconds must be > 0")


@dataclass(frozen=True)
class EdhConnectorConfig:
    """Runtime configuration injected into an EDH strategy and connector."""

    strategy: str
    namespace: str
    endpoint_url: str = ""
    root_path: str = ""
    region: str = "asia-south1"
    timeout_seconds: float = 10.0
    verify_tls: bool = True
    # Auth for API-driven / REST strategies
    auth_token_getter: Callable[[], str] | None = None
    api_key_getter: Callable[[], str] | None = None
    api_key_header: str = "X-API-Key"
    # S3 / SigV4 or custom credential getters for object-storage hubs
    access_key_getter: Callable[[], str] | None = None
    secret_key_getter: Callable[[], str] | None = None
    default_headers: Mapping[str, str] = field(default_factory=dict)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    circuit_breaker_policy: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)

    def __post_init__(self) -> None:
        if not self.strategy or not self.strategy.strip():
            raise EdhConfigError("EdhConnectorConfig.strategy is required")
        if not self.namespace or not self.namespace.strip():
            raise EdhConfigError("EdhConnectorConfig.namespace is required")
        if self.timeout_seconds <= 0:
            raise EdhConfigError("EdhConnectorConfig.timeout_seconds must be > 0")
