from __future__ import annotations
import dataclasses
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from core.mcp_server import FastMCP

from config.settings import Settings
from core.exceptions import DomainValidationError
from core.logging import bind_context, clear_context, get_logger
from core.telemetry import get_current_trace_id, trace_mcp_tool
from common.domain.value_objects import PaginationParams
from common.infrastructure.resilience import sanitize_error_message
from mcp.customer_mcp.application.ports import CustomerRepositoryPort
from mcp.customer_mcp.application.use_cases import (
    GetAssignedMachinesUseCase,
    GetCustomerEntitlementsUseCase,
    GetCustomerProfileUseCase,
    SearchCustomersUseCase,
)

logger = get_logger(__name__)


def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses, dates, datetimes, and custom objects to JSON-serializable primitives."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, list):
        return [_to_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def _success_response(data: Any, source: str, correlation_id: str) -> dict[str, Any]:
    return {
        "status": "success",
        "data": _to_serializable(data),
        "metadata": {
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "trace_id": get_current_trace_id(),  # OBS-01
        },
    }


def _error_response(exc: Exception, correlation_id: str) -> dict[str, Any]:
    # SEC-05: Sanitize error messages to prevent leaking internal SDK/database details (OWASP A05)
    safe_message, error_type = sanitize_error_message(exc, correlation_id)
    return {
        "status": "error",
        "data": {
            "error_type": error_type,
            "message": safe_message,
        },
        "metadata": {
            "source": "system",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "trace_id": get_current_trace_id(),  # OBS-01
        },
    }


def register_tools(app: FastMCP, settings: Settings, repo: Optional[CustomerRepositoryPort] = None) -> None:
    """Register all Customer MCP tools onto the FastMCP server instance.

    Args:
        app: The FastMCP server instance.
        settings: Application settings (used for source label in responses).
        repo: Pre-constructed, server-scoped customer repository. If omitted,
              the singleton gateway instance from GatewayFactory is used.
    """
    if repo is None:
        from core.factory import GatewayFactory
        repo = GatewayFactory.get_customer_gateway(settings)

    source_name = settings.DATA_PROVIDER_MODE.lower()

    @app.tool()
    async def customer_get_profile(customer_id: str) -> dict[str, Any]:
        """Retrieves full 360-degree customer profile, company metadata, tier, billing status, and designated account representatives.

        Args:
            customer_id: Enterprise customer ID matching format ^[A-Z0-9\\-]{6,36}$ (e.g., CUST-001001).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="customer_get_profile", customer_id=customer_id)
        # OBS-01: OpenTelemetry span across MCP boundary
        with trace_mcp_tool("customer_get_profile", "customer_mcp", correlation_id, customer_id=customer_id):
            try:
                logger.info("Handling customer_get_profile", customer_id=customer_id)
                customer = await GetCustomerProfileUseCase(repo).execute(customer_id)
                return _success_response(customer, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed customer_get_profile", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def customer_search(
        query: str = "",
        region: Optional[str] = None,
        tier: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Performs fuzzy/exact search on customer directory with filtering and pagination.

        Args:
            query: Substring to match on company name (case-insensitive, max 100 chars).
            region: Optional region filter (e.g. NA-EAST, NA-WEST, EU-WEST, APAC).
            tier: Optional tier filter (ENTERPRISE, PREMIUM, STANDARD).
            limit: Maximum records to return (1-100, default: 10).
            offset: Zero-based offset for pagination (default: 0).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="customer_search", query=query)
        with trace_mcp_tool("customer_search", "customer_mcp", correlation_id, query=query, region=region, tier=tier):
            try:
                # SEC-04: Length limit
                if len(query) > 100:
                    raise DomainValidationError("Query length cannot exceed 100 characters")
                # ARCH-04: Wire PaginationParams
                pagination = PaginationParams.from_offset_limit(offset=offset, limit=limit)
                logger.info("Handling customer_search", query=query, region=region, tier=tier, limit=limit, offset=offset)
                customers = await SearchCustomersUseCase(repo).execute(
                    query=query,
                    region=region,
                    tier=tier,
                    pagination=pagination,
                )
                return _success_response({"customers": customers, "count": len(customers)}, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed customer_search", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def customer_get_entitlements(customer_id: str) -> dict[str, Any]:
        """Returns active service contracts, SLA levels, purchased features, and expiration dates.

        Args:
            customer_id: Enterprise customer ID (e.g., CUST-001001).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="customer_get_entitlements", customer_id=customer_id)
        with trace_mcp_tool("customer_get_entitlements", "customer_mcp", correlation_id, customer_id=customer_id):
            try:
                logger.info("Handling customer_get_entitlements", customer_id=customer_id)
                entitlements = await GetCustomerEntitlementsUseCase(repo).execute(customer_id)
                return _success_response(entitlements, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed customer_get_entitlements", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def customer_get_assigned_machines(
        customer_id: str,
        site_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetches fleet IDs registered to the customer for cross-referencing.

        Args:
            customer_id: Enterprise customer ID (e.g., CUST-001001).
            site_id: Optional site filter matching SITE-XXXX format (e.g. SITE-A1B2).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="customer_get_assigned_machines", customer_id=customer_id)
        with trace_mcp_tool("customer_get_assigned_machines", "customer_mcp", correlation_id, customer_id=customer_id, site_id=site_id):
            try:
                logger.info("Handling customer_get_assigned_machines", customer_id=customer_id, site_id=site_id)
                assignment = await GetAssignedMachinesUseCase(repo).execute(customer_id, site_id)
                return _success_response(assignment, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed customer_get_assigned_machines", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()
