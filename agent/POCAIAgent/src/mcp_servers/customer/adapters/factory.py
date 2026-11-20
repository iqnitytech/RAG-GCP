from __future__ import annotations

import os
from typing import Any, Callable

from mcp_servers.customer.adapters.gateway import SdkDataGateway
from mcp_servers.customer.adapters.mock_adapter import MockAccountAdapter, MockCustomerAdapter
from mcp_servers.customer.adapters.sdk_adapter import SdkAccountAdapter, SdkCustomerAdapter
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort
from settings import Settings

CustomerProviderFactory = Callable[[Settings, Any], CustomerRepositoryPort]
AccountProviderFactory = Callable[[Settings, Any], AccountRepositoryPort]


class CustomerDataProviderFactory:
    """Factory resolving customer and account data providers adhering to Open/Closed Principle."""

    _customer_registry: dict[str, CustomerProviderFactory] = {}
    _account_registry: dict[str, AccountProviderFactory] = {}

    @classmethod
    def register_provider(cls, mode: str, factory: CustomerProviderFactory) -> None:
        cls._customer_registry[mode.upper()] = factory

    @classmethod
    def register_account_provider(cls, mode: str, factory: AccountProviderFactory) -> None:
        cls._account_registry[mode.upper()] = factory

    @classmethod
    def get_provider(
        cls,
        settings: Settings | None = None,
        repo: Any = None,
    ) -> CustomerRepositoryPort:
        """Alias for get_customer_provider for backward compatibility."""
        return cls.get_customer_provider(settings=settings, repo=repo)

    @classmethod
    def get_customer_provider(
        cls,
        settings: Settings | None = None,
        repo: Any = None,
        *,
        gateway: SdkDataGateway | None = None,
    ) -> CustomerRepositoryPort:
        if isinstance(repo, CustomerRepositoryPort):
            return repo

        s = settings or Settings.from_env()
        raw_mode = os.environ.get("DATA_PROVIDER_MODE") or s.data_provider_mode or "MOCK"
        mode_upper = raw_mode.strip().upper()

        if mode_upper in cls._customer_registry:
            return cls._customer_registry[mode_upper](s, repo)

        if mode_upper == "MOCK":
            return MockCustomerAdapter(repo=repo)

        return SdkCustomerAdapter(s, gateway=gateway)

    @classmethod
    def get_account_provider(
        cls,
        settings: Settings | None = None,
        repo: Any = None,
        *,
        gateway: SdkDataGateway | None = None,
    ) -> AccountRepositoryPort:
        if isinstance(repo, AccountRepositoryPort):
            return repo

        s = settings or Settings.from_env()
        raw_mode = os.environ.get("DATA_PROVIDER_MODE") or s.data_provider_mode or "MOCK"
        mode_upper = raw_mode.strip().upper()

        if mode_upper in cls._account_registry:
            return cls._account_registry[mode_upper](s, repo)

        if mode_upper == "MOCK":
            return MockAccountAdapter()

        return SdkAccountAdapter(s, gateway=gateway)


# Built-in registrations
CustomerDataProviderFactory.register_provider(
    "MOCK", lambda s, repo: MockCustomerAdapter(repo=repo)
)
CustomerDataProviderFactory.register_provider(
    "SDK", lambda s, repo: SdkCustomerAdapter(s) if not isinstance(repo, SdkCustomerAdapter) else repo
)
CustomerDataProviderFactory.register_account_provider(
    "MOCK", lambda s, repo: MockAccountAdapter()
)
CustomerDataProviderFactory.register_account_provider(
    "SDK", lambda s, repo: SdkAccountAdapter(s) if not isinstance(repo, SdkAccountAdapter) else repo
)
