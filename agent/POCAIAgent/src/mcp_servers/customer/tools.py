from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from domain.errors import DomainError, NotFoundError
from mcp_servers.common import require_role
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort

ROLE = "Customer.Read"
logger = logging.getLogger("mcp.customer.tools")


# ==============================================================================
# Customer Tools (Use Cases Layer -> CustomerRepositoryPort)
# ==============================================================================

async def get_customer_handler(
    provider: CustomerRepositoryPort,
    customer_id: str = "",
    number: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Retrieve customer details by Customer ID, Number, or Phone Number."""
    require_role(ROLE)

    clean_id = (customer_id or "").strip()
    clean_num = (number or "").strip()
    clean_phone = (phone or "").strip()

    if not clean_id and not clean_num and not clean_phone:
        raise DomainError("At least one of customer_id, number, or phone must be provided")

    # 1. Lookup by phone if explicitly provided
    if clean_phone:
        if len(clean_phone) > 50:
            raise DomainError("phone length cannot exceed 50 characters")
        c = await provider.get_by_phone(clean_phone)
        if c is not None:
            return c
        raise NotFoundError(f"Customer with phone '{clean_phone}' not found")

    # 2. Lookup by number if explicitly provided
    if clean_num:
        if len(clean_num) > 100:
            raise DomainError("number length cannot exceed 100 characters")
        c = await provider.get_by_number(clean_num)
        if c is not None:
            return c
        raise NotFoundError(f"Customer with number '{clean_num}' not found")

    # 3. Lookup by customer_id: try as ID first, then fall back to number and phone
    if len(clean_id) > 100:
        raise DomainError("customer_id length cannot exceed 100 characters")

    # Try lookup by ID
    c = await provider.get_by_id(clean_id)
    if c is not None:
        return c

    # Try lookup by Number / Account Number
    c = await provider.get_by_number(clean_id)
    if c is not None:
        return c

    # Try lookup by Phone Number
    c = await provider.get_by_phone(clean_id)
    if c is not None:
        return c

    raise NotFoundError(f"Customer {clean_id} not found")


async def search_customers_handler(
    provider: CustomerRepositoryPort,
    name: str = "",
) -> list[dict[str, Any]]:
    """Search customers by (partial) name."""
    require_role(ROLE)
    clean_name = (name or "").strip()
    if len(clean_name) > 100:
        raise DomainError("Query length cannot exceed 100 characters")

    return await provider.search_by_name(clean_name)


async def get_customer_by_number_handler(
    provider: CustomerRepositoryPort,
    number: str,
) -> dict[str, Any]:
    """Retrieve customer details by customer number."""
    clean_number = (number or "").strip()
    if not clean_number:
        raise DomainError("number cannot be empty")
    return await get_customer_handler(provider, number=clean_number)


async def get_customer_by_phone_handler(
    provider: CustomerRepositoryPort,
    phone: str,
) -> dict[str, Any]:
    """Retrieve customer details by phone number."""
    clean_phone = (phone or "").strip()
    if not clean_phone:
        raise DomainError("phone cannot be empty")
    return await get_customer_handler(provider, phone=clean_phone)


# ==============================================================================
# Account Tools (Use Cases Layer -> AccountRepositoryPort)
# ==============================================================================

async def get_account_handler(
    provider: AccountRepositoryPort,
    account_id: str = "",
    number: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Retrieve account details by Account ID, Number, or Phone Number."""
    require_role(ROLE)

    clean_id = (account_id or "").strip()
    clean_num = (number or "").strip()
    clean_phone = (phone or "").strip()

    if not clean_id and not clean_num and not clean_phone:
        raise DomainError("At least one of account_id, number, or phone must be provided")

    # 1. Lookup by phone if explicitly provided
    if clean_phone:
        if len(clean_phone) > 50:
            raise DomainError("phone length cannot exceed 50 characters")
        acc = await provider.get_by_phone(clean_phone)
        if acc is not None:
            return acc
        raise NotFoundError(f"Account with phone '{clean_phone}' not found")

    # 2. Lookup by number if explicitly provided
    if clean_num:
        if len(clean_num) > 100:
            raise DomainError("number length cannot exceed 100 characters")
        acc = await provider.get_by_number(clean_num)
        if acc is not None:
            return acc
        raise NotFoundError(f"Account with number '{clean_num}' not found")

    # 3. Lookup by account_id: try as ID first, then fall back to number and phone
    if len(clean_id) > 100:
        raise DomainError("account_id length cannot exceed 100 characters")

    acc = await provider.get_by_id(clean_id)
    if acc is not None:
        return acc

    acc = await provider.get_by_number(clean_id)
    if acc is not None:
        return acc

    acc = await provider.get_by_phone(clean_id)
    if acc is not None:
        return acc

    raise NotFoundError(f"Account {clean_id} not found")


async def search_accounts_handler(
    provider: AccountRepositoryPort,
    name: str = "",
) -> list[dict[str, Any]]:
    """Search accounts by (partial) name."""
    require_role(ROLE)
    clean_name = (name or "").strip()
    if len(clean_name) > 100:
        raise DomainError("Query length cannot exceed 100 characters")

    return await provider.search_by_name(clean_name)


# ==============================================================================
# Tool Registration
# ==============================================================================

def register_customer_tools(
    app: FastMCP,
    provider: CustomerRepositoryPort,
    account_provider: AccountRepositoryPort | None = None,
    extended_tools: bool = False,
) -> None:
    """Register customer and account MCP tools onto FastMCP instance."""

    # Customer Tools
    @app.tool()
    async def get_customer(
        customer_id: str,
        number: str = "",
        phone: str = "",
    ) -> dict[str, Any]:
        """Get customer or contact details (name, tier) by ID, number, or phone."""
        return await get_customer_handler(
            provider,
            customer_id=customer_id,
            number=number,
            phone=phone,
        )

    @app.tool()
    async def search_customers(name: str) -> list[dict[str, Any]]:
        """Search customers by (partial) name."""
        return await search_customers_handler(provider, name=name)

    # Account Tools
    if account_provider is not None:
        @app.tool()
        async def get_account(
            account_id: str,
            number: str = "",
            phone: str = "",
        ) -> dict[str, Any]:
            """Get account (company/organization details, tier, billing status) by Account ID (e.g. ACC-1), number, or phone."""
            return await get_account_handler(
                account_provider,
                account_id=account_id,
                number=number,
                phone=phone,
            )

    if extended_tools:
        @app.tool()
        async def get_customer_by_number(number: str) -> dict[str, Any]:
            """Retrieve customer details by customer or account number."""
            return await get_customer_by_number_handler(provider, number=number)

        @app.tool()
        async def get_customer_by_phone(phone: str) -> dict[str, Any]:
            """Retrieve customer details by phone number."""
            return await get_customer_by_phone_handler(provider, phone=phone)
