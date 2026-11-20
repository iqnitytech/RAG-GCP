from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.customer.adapters.factory import CustomerDataProviderFactory
from mcp_servers.customer.ports import AccountRepositoryPort, CustomerRepositoryPort
from mcp_servers.customer.tools import register_customer_tools
from settings import Settings

logger = logging.getLogger("mcp.customer.server")


def build_server(
    repo: Any = None,
    settings: Settings | None = None,
    sf: Any = None,
    extended_tools: bool | None = None,
    account_repo: Any = None,
) -> FastMCP:
    """Build and configure the customer FastMCP server instance.

    Resolves CustomerRepositoryPort and AccountRepositoryPort data providers based on
    DATA_PROVIDER_MODE (MOCK vs SDK/non-mock) or pre-constructed repositories.
    """
    s = settings or Settings.from_env()
    customer_provider: CustomerRepositoryPort = CustomerDataProviderFactory.get_customer_provider(
        settings=s, repo=repo
    )
    account_provider: AccountRepositoryPort = CustomerDataProviderFactory.get_account_provider(
        settings=s, repo=account_repo
    )

    if extended_tools is None:
        extended_tools = os.environ.get("EXTENDED_CUSTOMER_TOOLS", "false").lower() == "true"

    mcp = FastMCP("customer-info", stateless_http=True, json_response=True)
    register_customer_tools(
        mcp,
        customer_provider,
        account_provider=account_provider,
        extended_tools=extended_tools,
    )
    return mcp
