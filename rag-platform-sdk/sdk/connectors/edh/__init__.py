"""Generic Enterprise Data Hub (EDH) Connector package.

Provides a Strategy-Pattern connector and Python core-library strategies for:
- **Data Lake** (:class:`DataLakeStrategy`): Medallion zones, Hive-partitioned
  datasets, atomic file replacement, and SHA-256 manifest verification.
- **Object Storage Hub** (:class:`ObjectStorageHubStrategy`): S3/GCS/MinIO-compatible
  XML/HTTP object storage with SigV4 or Bearer/WIF auth.
- **API-Driven Data Hub** (:class:`ApiDrivenEdhStrategy`): REST/JSON + binary
  data hub client with ``X-Request-ID`` propagation and idempotency keys.
- **In-Memory Fake** (:class:`InMemoryEdhStrategy`): Fast contract-tested double.
"""

from sdk.connectors.edh.connector import EdhConnector
from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhConfigError,
    EdhConflictError,
    EdhError,
    EdhIntegrityError,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhTransientError,
)
from sdk.models.edh_models import (
    CircuitBreakerPolicy,
    DataZone,
    EdhConnectorConfig,
    EdhListPage,
    EdhObjectMetadata,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
    RetryPolicy,
    compute_sha256,
    normalize_object_key,
)
from sdk.connectors.edh.ports import (
    EdhLifecyclePort,
    EdhReadStrategyPort,
    EdhRecordStrategyPort,
    EdhStrategyPort,
    EdhWriteStrategyPort,
)
from sdk.connectors.edh.registry import (
    EdhStrategyRegistry,
    create_default_registry,
    default_strategy_registry,
)
from sdk.connectors.edh.resilience import CircuitBreaker, CircuitState, execute_with_resilience
from sdk.connectors.edh.strategies import (
    ApiDrivenEdhStrategy,
    DataLakeStrategy,
    InMemoryEdhStrategy,
    ObjectStorageHubStrategy,
    RawHttpResponse,
    SalesforceEdhStrategy,
    TransportCallable,
    make_salesforce_oauth_token_getter,
    sign_s3_v4_headers,
)

__all__ = [
    "ApiDrivenEdhStrategy",
    "CircuitBreaker",
    "CircuitBreakerPolicy",
    "CircuitState",
    "DataLakeStrategy",
    "DataZone",
    "EdhAuthError",
    "EdhConfigError",
    "EdhConflictError",
    "EdhConnector",
    "EdhConnectorConfig",
    "EdhError",
    "EdhIntegrityError",
    "EdhLifecyclePort",
    "EdhListPage",
    "EdhNotFoundError",
    "EdhObjectMetadata",
    "EdhObjectRef",
    "EdhRateLimitError",
    "EdhReadStrategyPort",
    "EdhRecordBatch",
    "EdhRecordStrategyPort",
    "EdhStrategyPort",
    "EdhStrategyRegistry",
    "EdhTransientError",
    "EdhWriteOptions",
    "EdhWriteStrategyPort",
    "InMemoryEdhStrategy",
    "ObjectStorageHubStrategy",
    "RawHttpResponse",
    "RecordFormat",
    "RetryPolicy",
    "SalesforceEdhStrategy",
    "TransportCallable",
    "compute_sha256",
    "create_default_registry",
    "default_strategy_registry",
    "execute_with_resilience",
    "make_salesforce_oauth_token_getter",
    "normalize_object_key",
    "sign_s3_v4_headers",
]
