from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable
if TYPE_CHECKING:
    from mcp.customer_mcp.application.ports import CustomerRepositoryPort
    from mcp.machine_mcp.application.ports import MachineRepositoryPort
    from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort
from config.settings import Settings

ProviderFactory = Callable[[Settings], Any]


class GatewayFactory:
    """Factory and registry for repository/gateway adapters adhering to the
    Open/Closed Principle (ARCH-02).

    Extensible via register_provider() without modifying the factory source code.
    Reuses connection pools and adapter singletons across calls.
    """
    _registry: dict[tuple[str, str], ProviderFactory] = {}
    _cache: dict[str, Any] = {}

    @classmethod
    def register_provider(cls, service: str, mode: str, factory: ProviderFactory) -> None:
        """Register a gateway provider factory. Enables extending data providers
        without modifying GatewayFactory implementation (Open/Closed Principle).
        """
        cls._registry[(service.lower(), mode.upper())] = factory

    @classmethod
    def get_gateway(cls, service: str, settings: Settings) -> Any:
        service_lower = service.lower()
        mode_upper = settings.DATA_PROVIDER_MODE.upper()
        key = f"{service_lower}:{mode_upper}:{settings.SDK_BASE_URL}:{settings.SDK_API_KEY}"

        if key not in cls._cache:
            factory = cls._registry.get((service_lower, mode_upper))
            if factory is None:
                raise ValueError(
                    f"No provider registered for service '{service_lower}' and mode '{mode_upper}'. "
                    f"Available: {[f'{s}:{m}' for s, m in cls._registry.keys()]}"
                )
            cls._cache[key] = factory(settings)
        return cls._cache[key]

    @classmethod
    def get_customer_gateway(cls, settings: Settings) -> "CustomerRepositoryPort":
        return cls.get_gateway("customer", settings)

    @classmethod
    def get_machine_gateway(cls, settings: Settings) -> "MachineRepositoryPort":
        return cls.get_gateway("machine", settings)

    @classmethod
    def get_telemetry_gateway(cls, settings: Settings) -> "TelemetryRepositoryPort":
        return cls.get_gateway("telemetry", settings)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached singleton instances (useful for test isolation)."""
        cls._cache.clear()


# ---------------------------------------------------------------------------
# Default Provider Registrations (Built-in MOCK and SDK providers)
# ---------------------------------------------------------------------------

def _create_mock_customer(s: Settings) -> Any:
    from mcp.customer_mcp.infrastructure.mock_adapter import MockCustomerAdapter
    return MockCustomerAdapter()


def _create_sdk_customer(s: Settings) -> Any:
    from mcp.customer_mcp.infrastructure.sdk_adapter import SdkCustomerAdapter
    return SdkCustomerAdapter(s)


def _create_mock_machine(s: Settings) -> Any:
    from mcp.machine_mcp.infrastructure.mock_adapter import MockMachineAdapter
    return MockMachineAdapter()


def _create_sdk_machine(s: Settings) -> Any:
    from mcp.machine_mcp.infrastructure.sdk_adapter import SdkMachineAdapter
    return SdkMachineAdapter(s)


def _create_mock_telemetry(s: Settings) -> Any:
    from mcp.telemetry_mcp.infrastructure.mock_adapter import MockTelemetryAdapter
    return MockTelemetryAdapter()


def _create_sdk_telemetry(s: Settings) -> Any:
    from mcp.telemetry_mcp.infrastructure.sdk_adapter import SdkTelemetryAdapter
    return SdkTelemetryAdapter(s)


GatewayFactory.register_provider("customer", "MOCK", _create_mock_customer)
GatewayFactory.register_provider("customer", "SDK", _create_sdk_customer)
GatewayFactory.register_provider("machine", "MOCK", _create_mock_machine)
GatewayFactory.register_provider("machine", "SDK", _create_sdk_machine)
GatewayFactory.register_provider("telemetry", "MOCK", _create_mock_telemetry)
GatewayFactory.register_provider("telemetry", "SDK", _create_sdk_telemetry)
