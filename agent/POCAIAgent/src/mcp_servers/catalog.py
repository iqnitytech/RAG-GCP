"""Catalog of MCP servers shipped in this repo. Add one line to ship another server."""
from __future__ import annotations

from typing import Callable

from mcp.server.fastmcp import FastMCP

from domain.ports import SalesforcePort
from infrastructure.salesforce import repositories as repos
from mcp_servers.customer.server import build_server as build_customer
from mcp_servers.machine.server import build_server as build_machine
from mcp_servers.telemetry.server import build_server as build_telemetry

CATALOG: dict[str, Callable[[SalesforcePort], FastMCP]] = {
    "machine": lambda sf: build_machine(repos.SalesforceMachineRepository(sf)),
    "customer": lambda sf: build_customer(repos.SalesforceCustomerRepository(sf)),
    "telemetry": lambda sf: build_telemetry(repos.SalesforceTelemetryRepository(sf)),
}
