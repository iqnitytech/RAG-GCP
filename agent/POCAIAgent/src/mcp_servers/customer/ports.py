from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CustomerRepositoryPort(Protocol):
    """Port interface defining customer (Contact) data operations."""

    async def get_by_id(self, customer_id: str) -> dict[str, Any] | None:
        """Retrieve customer details by Customer ID (Contact)."""
        ...

    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        """Search customers by name (full or partial match)."""
        ...

    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        """Retrieve customer details by Customer Number."""
        ...

    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        """Retrieve customer details by Phone Number."""
        ...


@runtime_checkable
class AccountRepositoryPort(Protocol):
    """Port interface defining account (Account) data operations."""

    async def get_by_id(self, account_id: str) -> dict[str, Any] | None:
        """Retrieve account details by Account ID (Account)."""
        ...

    async def search_by_name(self, name: str) -> list[dict[str, Any]]:
        """Search accounts by name (full or partial match)."""
        ...

    async def get_by_number(self, number: str) -> dict[str, Any] | None:
        """Retrieve account details by Account Number."""
        ...

    async def get_by_phone(self, phone: str) -> dict[str, Any] | None:
        """Retrieve account details by Phone Number."""
        ...
