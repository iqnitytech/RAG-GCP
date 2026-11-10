"""Explicit allowlist registry for Enterprise Data Hub strategies (Open/Closed Principle).

New data hub backends are added by registering a strategy factory here or on a
custom :class:`EdhStrategyRegistry` instance without modifying :class:`EdhConnector`
or any application use case. Arbitrary dynamic imports are prohibited.
"""

from __future__ import annotations

from threading import Lock
from typing import Callable

from sdk.connectors.edh.errors import EdhConfigError
from sdk.models.edh_models import EdhConnectorConfig
from sdk.connectors.edh.ports import EdhStrategyPort
from sdk.connectors.edh.strategies.api_hub import ApiDrivenEdhStrategy
from sdk.connectors.edh.strategies.data_lake import DataLakeStrategy
from sdk.connectors.edh.strategies.memory import InMemoryEdhStrategy
from sdk.connectors.edh.strategies.object_storage_hub import ObjectStorageHubStrategy
from sdk.connectors.edh.strategies.salesforce import SalesforceEdhStrategy

StrategyFactory = Callable[[EdhConnectorConfig], EdhStrategyPort]


class EdhStrategyRegistry:
    """Thread-safe allowlist mapping strategy names to factory callables."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._factories: dict[str, StrategyFactory] = {}

    def register(
        self,
        name: str,
        factory: StrategyFactory,
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register a strategy factory under ``name`` (and optional ``aliases``)."""
        if not name or not name.strip():
            raise EdhConfigError("Strategy name must not be empty")
        with self._lock:
            self._factories[name.strip().lower()] = factory
            for alias in aliases:
                if alias and alias.strip():
                    self._factories[alias.strip().lower()] = factory

    def create(self, config: EdhConnectorConfig) -> EdhStrategyPort:
        """Instantiate the strategy configured in ``config.strategy``."""
        key = config.strategy.strip().lower()
        with self._lock:
            factory = self._factories.get(key)
            allowed = sorted(self._factories.keys())
        if factory is None:
            raise EdhConfigError(
                f"Unknown EDH strategy {config.strategy!r}. Allowed strategies: {allowed}"
            )
        return factory(config)

    @property
    def registered_strategies(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories.keys()))


def create_default_registry() -> EdhStrategyRegistry:
    """Return a registry pre-populated with the built-in core strategies."""
    registry = EdhStrategyRegistry()
    registry.register(
        DataLakeStrategy.STRATEGY_NAME,
        DataLakeStrategy,
        aliases=("datalake", "lake"),
    )
    registry.register(
        ObjectStorageHubStrategy.STRATEGY_NAME,
        ObjectStorageHubStrategy,
        aliases=("object_storage", "s3_hub", "gcs_hub"),
    )
    registry.register(
        ApiDrivenEdhStrategy.STRATEGY_NAME,
        ApiDrivenEdhStrategy,
        aliases=("api", "rest_hub"),
    )
    registry.register(
        InMemoryEdhStrategy.STRATEGY_NAME,
        InMemoryEdhStrategy,
        aliases=("in_memory", "fake"),
    )
    registry.register(
        SalesforceEdhStrategy.STRATEGY_NAME,
        SalesforceEdhStrategy,
        aliases=("sfdc", "salesforce_crm"),
    )
    return registry


default_strategy_registry = create_default_registry()
