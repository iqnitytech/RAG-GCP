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
from mcp.machine_mcp.application.ports import MachineRepositoryPort
from mcp.machine_mcp.application.use_cases import (
    GetMachineDetailsUseCase,
    GetMaintenanceHistoryUseCase,
    ListMachineFleetUseCase,
    UpdateOperationalStateUseCase,
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


def register_tools(app: FastMCP, settings: Settings, repo: Optional[MachineRepositoryPort] = None) -> None:
    """Register all Machine MCP tools onto the FastMCP server instance.

    Args:
        app: The FastMCP server instance.
        settings: Application settings (used for source label in responses).
        repo: Pre-constructed, server-scoped machine repository. If omitted,
              the singleton gateway instance from GatewayFactory is used.
    """
    if repo is None:
        from core.factory import GatewayFactory
        repo = GatewayFactory.get_machine_gateway(settings)

    source_name = settings.DATA_PROVIDER_MODE.lower()

    @app.tool()
    async def machine_get_details(machine_id: str) -> dict[str, Any]:
        """Fetches hardware specs, model series, firmware version, commissioning date, and physical installation location.

        Args:
            machine_id: Machine identifier in MCH-XXXX-XXXX format (e.g., MCH-A1B2-C3D4).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="machine_get_details", machine_id=machine_id)
        with trace_mcp_tool("machine_get_details", "machine_mcp", correlation_id, machine_id=machine_id):
            try:
                logger.info("Handling machine_get_details", machine_id=machine_id)
                machine = await GetMachineDetailsUseCase(repo).execute(machine_id)
                return _success_response(machine, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed machine_get_details", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def machine_list_fleet(
        model_type: Optional[str] = None,
        status: Optional[str] = None,
        site_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Queries fleet registry with optional hardware type, operational status, and physical site filtering.

        Args:
            model_type: Hardware model substring filter (e.g., GENERIC, TURBO, PUMP, max 100 chars).
            status: Operational status (ACTIVE, MAINTENANCE, DECOMMISSIONED).
            site_id: Location site ID in SITE-XXXX format.
            page: 1-based page number (default: 1).
            page_size: Number of records per page (1-100, default: 20).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="machine_list_fleet", status=status, site_id=site_id)
        with trace_mcp_tool("machine_list_fleet", "machine_mcp", correlation_id, model_type=model_type, status=status):
            try:
                # SEC-04: Length limits
                if model_type and len(model_type) > 100:
                    raise DomainValidationError("model_type length cannot exceed 100 characters")
                # ARCH-04: Wire PaginationParams
                pagination = PaginationParams.from_page(page=page, page_size=page_size)
                logger.info(
                    "Handling machine_list_fleet",
                    model_type=model_type,
                    status=status,
                    site_id=site_id,
                    page=page,
                    page_size=page_size,
                )
                machines = await ListMachineFleetUseCase(repo).execute(
                    model_type=model_type,
                    status=status,
                    site_id_str=site_id,
                    pagination=pagination,
                )
                return _success_response(
                    {"machines": machines, "count": len(machines), "page": pagination.page, "page_size": pagination.page_size},
                    source_name,
                    correlation_id,
                )
            except Exception as exc:
                logger.error("Failed machine_list_fleet", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def machine_get_maintenance_history(
        machine_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Retrieves chronological maintenance intervention records, replaced parts, labor duration, and costs.

        Args:
            machine_id: Machine identifier in MCH-XXXX-XXXX format (e.g., MCH-A1B2-C3D4).
            limit: Maximum log records to return (1-100, default: 20).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="machine_get_maintenance_history", machine_id=machine_id)
        with trace_mcp_tool("machine_get_maintenance_history", "machine_mcp", correlation_id, machine_id=machine_id):
            try:
                logger.info("Handling machine_get_maintenance_history", machine_id=machine_id, limit=limit)
                history = await GetMaintenanceHistoryUseCase(repo).execute(machine_id, limit)
                return _success_response(history, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed machine_get_maintenance_history", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def machine_update_operational_state(
        machine_id: str,
        target_state: str,
        reason: str,
    ) -> dict[str, Any]:
        """Transition equipment state (ACTIVE, MAINTENANCE, DECOMMISSIONED) with state-machine transition validation and audit trail logging.

        Args:
            machine_id: Machine identifier in MCH-XXXX-XXXX format (e.g., MCH-A1B2-C3D4).
            target_state: Destination state. Allowed: ACTIVE, MAINTENANCE, DECOMMISSIONED.
            reason: Mandatory human or automated operational rationale (max 500 chars).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(
            correlation_id=correlation_id,
            tool_name="machine_update_operational_state",
            machine_id=machine_id,
            target_state=target_state,
        )
        with trace_mcp_tool("machine_update_operational_state", "machine_mcp", correlation_id, machine_id=machine_id, target_state=target_state):
            try:
                # SEC-04: Length limits
                if len(reason) > 500:
                    raise DomainValidationError("reason length cannot exceed 500 characters")
                logger.info("Handling machine_update_operational_state", machine_id=machine_id, target_state=target_state, reason=reason)
                result = await UpdateOperationalStateUseCase(repo).execute(machine_id, target_state, reason)
                return _success_response(result, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed machine_update_operational_state", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()
