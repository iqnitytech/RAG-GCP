"""Generic Enterprise Data Hub (EDH) Connector context (Strategy Pattern).

Wraps any :class:`EdhStrategyPort` with unified resilience (circuit breaker +
exponential backoff with jitter), telemetry logging with ``request_id``,
input validation, and context-manager lifecycle support.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator, Mapping

from sdk.models.edh_models import (
    DataZone,
    EdhConnectorConfig,
    EdhListPage,
    EdhObjectMetadata,
    EdhObjectRef,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)
from sdk.connectors.edh.ports import EdhStrategyPort
from sdk.connectors.edh.registry import EdhStrategyRegistry, default_strategy_registry
from sdk.connectors.edh.resilience import CircuitBreaker, execute_with_resilience

logger = logging.getLogger("sdk.edh.connector")


class EdhConnector:
    """Production-ready Enterprise Data Hub connector delegating to a pluggable strategy."""

    def __init__(
        self,
        config: EdhConnectorConfig,
        *,
        strategy: EdhStrategyPort | None = None,
        registry: EdhStrategyRegistry = default_strategy_registry,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._strategy: EdhStrategyPort = strategy or registry.create(config)
        self._circuit_breaker = CircuitBreaker(
            config.circuit_breaker_policy,
            provider=self._strategy.strategy_name,
        )
        self._sleep_fn = sleep_fn

    @property
    def config(self) -> EdhConnectorConfig:
        return self._config

    @property
    def strategy(self) -> EdhStrategyPort:
        return self._strategy

    @property
    def strategy_name(self) -> str:
        return self._strategy.strategy_name

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    def ref(
        self,
        key: str,
        *,
        zone: DataZone = DataZone.RAW,
        version: str | None = None,
    ) -> EdhObjectRef:
        """Build an :class:`EdhObjectRef` scoped to this connector's namespace."""
        return EdhObjectRef(
            namespace=self._config.namespace,
            key=key,
            zone=zone,
            version=version,
        )

    def put_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        data: bytes,
        *,
        zone: DataZone = DataZone.RAW,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Store raw bytes at ``ref_or_key`` with retry/circuit-breaker protection."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        opts = options or EdhWriteOptions()
        # Overwrite=True or explicit idempotency_key makes PUT safely idempotent
        is_idempotent = opts.overwrite or bool(opts.idempotency_key)
        started = time.perf_counter()

        meta = execute_with_resilience(
            "put_bytes",
            lambda: self._strategy.put_bytes(ref, data, options=opts),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=is_idempotent,
            sleep_fn=self._sleep_fn,
        )
        logger.info(
            "EDH put_bytes strategy=%s uri=%s size=%d sha256=%s (%.1f ms)",
            self.strategy_name,
            meta.ref.uri,
            meta.size_bytes,
            meta.sha256[:12],
            (time.perf_counter() - started) * 1000,
        )
        return meta

    def get_bytes(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bytes:
        """Download and verify raw bytes for ``ref_or_key``."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        return execute_with_resilience(
            "get_bytes",
            lambda: self._strategy.get_bytes(ref),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=True,
            sleep_fn=self._sleep_fn,
        )

    def stream_chunks(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        """Stream payload chunks for ``ref_or_key``."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        self._circuit_breaker.before_call("stream_chunks")
        try:
            for chunk in self._strategy.stream_chunks(ref, chunk_size=chunk_size):
                yield chunk
            self._circuit_breaker.record_success()
        except Exception:
            self._circuit_breaker.record_failure()
            raise

    def head(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> EdhObjectMetadata:
        """Inspect object metadata without downloading payload bytes."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        return execute_with_resilience(
            "head",
            lambda: self._strategy.head(ref),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=True,
            sleep_fn=self._sleep_fn,
        )

    def delete(
        self,
        ref_or_key: EdhObjectRef | str,
        *,
        zone: DataZone = DataZone.RAW,
    ) -> bool:
        """Delete ``ref_or_key`` (idempotent)."""
        ref = self._coerce_ref(ref_or_key, zone=zone)
        return execute_with_resilience(
            "delete",
            lambda: self._strategy.delete(ref),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=True,
            sleep_fn=self._sleep_fn,
        )

    def list_objects(
        self,
        *,
        zone: DataZone = DataZone.RAW,
        prefix: str = "",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> EdhListPage:
        """List objects under ``zone`` and ``prefix``."""
        return execute_with_resilience(
            "list_objects",
            lambda: self._strategy.list_objects(
                zone=zone,
                prefix=prefix,
                page_size=page_size,
                page_token=page_token,
            ),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=True,
            sleep_fn=self._sleep_fn,
        )

    def write_records(
        self,
        batch: EdhRecordBatch,
        *,
        options: EdhWriteOptions | None = None,
    ) -> EdhObjectMetadata:
        """Write a structured record batch to a partitioned dataset."""
        opts = options or EdhWriteOptions()
        is_idempotent = opts.overwrite or bool(opts.idempotency_key)
        return execute_with_resilience(
            "write_records",
            lambda: self._strategy.write_records(batch, options=opts),
            retry_policy=self._config.retry_policy,
            circuit_breaker=self._circuit_breaker,
            idempotent=is_idempotent,
            sleep_fn=self._sleep_fn,
        )

    def read_records(
        self,
        dataset: str,
        *,
        zone: DataZone = DataZone.CURATED,
        partition_filter: Mapping[str, str] | None = None,
        record_format: RecordFormat = RecordFormat.JSONL,
    ) -> Iterator[dict[str, Any]]:
        """Stream structured records from ``dataset`` matching ``partition_filter``."""
        self._circuit_breaker.before_call("read_records")
        try:
            for row in self._strategy.read_records(
                dataset,
                zone=zone,
                partition_filter=partition_filter,
                record_format=record_format,
            ):
                yield row
            self._circuit_breaker.record_success()
        except Exception:
            self._circuit_breaker.record_failure()
            raise

    def ping(self) -> bool:
        """Readiness probe compatible with ``create_base_app(readiness_check=...)``."""
        if self._circuit_breaker.state.value == "open":
            return False
        return self._strategy.ping()

    def close(self) -> None:
        self._strategy.close()

    def __enter__(self) -> EdhConnector:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _coerce_ref(self, ref_or_key: EdhObjectRef | str, *, zone: DataZone) -> EdhObjectRef:
        if isinstance(ref_or_key, EdhObjectRef):
            return ref_or_key
        return self.ref(ref_or_key, zone=zone)
